"""Core propagation logic with validation, idempotency, retries, and audit logging."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
import hashlib
import json
import re
from typing import Any

from exception_ai import ExceptionAnalyzer, OfflineDemoAnalyzer


class ValidationError(ValueError):
    """Raised when an employee record fails validation."""


@dataclass(frozen=True)
class Employee:
    employee_id: str
    name: str
    email: str
    location: str
    start_date: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Employee":
        required = ("employee_id", "name", "email", "location", "start_date")
        missing = [key for key in required if key not in data]
        if missing:
            raise ValidationError(f"Missing required fields: {', '.join(missing)}")
        return cls(**{key: str(data[key]).strip() for key in required})

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def validate_employee(employee: Employee) -> None:
    """Validate required fields and basic formats before any downstream write."""
    required = {
        "employee_id": employee.employee_id,
        "name": employee.name,
        "email": employee.email,
        "location": employee.location,
        "start_date": employee.start_date,
    }
    empty = [key for key, value in required.items() if not value]
    if empty:
        raise ValidationError(f"Empty required fields: {', '.join(empty)}")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", employee.email):
        raise ValidationError("Invalid email format")
    try:
        date.fromisoformat(employee.start_date)
    except ValueError as exc:
        raise ValidationError("start_date must use YYYY-MM-DD format") from exc


class AuditLogger:
    """In-memory audit logger. Replace with durable storage in production."""

    def __init__(self):
        self.entries: list[dict[str, Any]] = []

    def log(self, **entry: Any) -> None:
        self.entries.append(entry)


class MockDestination:
    """Simulates a downstream system connector."""

    def __init__(self, name: str, audit: AuditLogger):
        self.name = name
        self.audit = audit
        self.records: dict[str, dict[str, Any]] = {}
        self.fail_next_attempts = 0
        self.write_count = 0

    def upsert(self, employee_id: str, payload: dict[str, Any], event_id: str) -> None:
        if self.fail_next_attempts > 0:
            self.fail_next_attempts -= 1
            raise ConnectionError(f"Simulated temporary failure in {self.name}")
        # Upsert makes repeating a payload safer than creating duplicate records.
        self.records[employee_id] = dict(payload)
        self.write_count += 1


def transform_for_destination(employee: Employee, destination: str) -> dict[str, Any]:
    """Map canonical employee fields to each system's expected schema."""
    if destination == "workday":
        return {
            "worker_id": employee.employee_id,
            "legal_name": employee.name,
            "work_email": employee.email,
            "work_location": employee.location,
            "hire_date": employee.start_date,
        }
    if destination == "okta_lumos":
        username = employee.email.lower()
        return {
            "employee_id": employee.employee_id,
            "username": username,
            "display_name": employee.name,
            "location": employee.location,
        }
    if destination == "equipment":
        return {
            "employee_id": employee.employee_id,
            "recipient_name": employee.name,
            "shipping_location": employee.location,
            "needed_by": employee.start_date,
        }
    if destination == "cohort_tracker":
        return {
            "employee_id": employee.employee_id,
            "name": employee.name,
            "location": employee.location,
            "cohort_start_date": employee.start_date,
        }
    raise ValueError(f"Unknown destination: {destination}")


class PropagationEngine:
    def __init__(
        self,
        destinations: dict[str, MockDestination],
        audit: AuditLogger,
        max_retries: int = 3,
        exception_analyzer: ExceptionAnalyzer | None = None,
    ):
        self.destinations = destinations
        self.audit = audit
        self.max_retries = max_retries
        self.exception_analyzer = exception_analyzer or OfflineDemoAnalyzer()
        self.processed_events: set[str] = set()
        # Remember successful destinations across retries within this process.
        # Production should persist this state in a database.
        self.completed_destinations: dict[str, set[str]] = {}

    @staticmethod
    def _event_key(event_id: str, employee: Employee) -> str:
        canonical = json.dumps(
            {"event_id": event_id, "employee": employee.to_dict()},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def process_event(self, event: dict[str, Any]) -> dict[str, Any]:
        event_id = str(event.get("event_id", "")).strip()
        if not event_id:
            message = "event_id is required"
            self.audit.log(event_id=None, status="rejected", message=message)
            return {"status": "rejected", "reason": message, "destinations": {}}

        try:
            employee = Employee.from_dict(event.get("employee", {}))
            validate_employee(employee)
        except (ValidationError, TypeError) as exc:
            message = str(exc)
            self.audit.log(event_id=event_id, status="rejected", message=message)
            return {"status": "rejected", "reason": message, "destinations": {}}

        key = self._event_key(event_id, employee)
        if key in self.processed_events:
            self.audit.log(event_id=event_id, status="duplicate", message="Already processed")
            return {
                "status": "duplicate",
                "event_id": event_id,
                "reason": "Event already processed; no downstream writes performed",
                "destinations": {},
            }

        outcomes: dict[str, dict[str, Any]] = {}
        completed = self.completed_destinations.setdefault(key, set())
        for destination_name, destination in self.destinations.items():
            if destination_name in completed:
                outcomes[destination_name] = {"status": "success", "skipped": True, "reason": "Already succeeded for this event"}
                continue
            payload = transform_for_destination(employee, destination_name)
            success = False
            last_error = None
            for attempt in range(1, self.max_retries + 1):
                try:
                    destination.upsert(employee.employee_id, payload, event_id)
                    self.audit.log(
                        event_id=event_id, employee_id=employee.employee_id,
                        destination=destination.name, status="success", attempt=attempt,
                    )
                    outcomes[destination_name] = {"status": "success", "attempts": attempt}
                    completed.add(destination_name)
                    success = True
                    break
                except ConnectionError as exc:
                    last_error = str(exc)
                    self.audit.log(
                        event_id=event_id, employee_id=employee.employee_id,
                        destination=destination.name, status="retry" if attempt < self.max_retries else "failed",
                        attempt=attempt, message=last_error,
                    )
            if not success:
                # Claude is an advisory exception handler, not the system of control.
                try:
                    analysis = self.exception_analyzer.analyze(
                        destination=destination.name,
                        error=last_error or "Unknown connector error",
                        attempts=self.max_retries,
                    )
                except Exception as ai_exc:
                    # AI/API outages must not hide the original connector failure.
                    analysis = {
                        "provider": "unavailable",
                        "issue": "AI exception analysis was unavailable.",
                        "recommendation": "Use the technical error and audit trail for manual diagnosis.",
                        "requires_human_review": True,
                        "analysis_error": str(ai_exc),
                    }
                outcomes[destination_name] = {
                    "status": "failed",
                    "attempts": self.max_retries,
                    "error": last_error,
                    "manual_review_required": True,
                    "ai_analysis": analysis,
                }
                self.audit.log(
                    event_id=event_id, employee_id=employee.employee_id,
                    destination=destination.name, status="exception_analysis",
                    provider=analysis.get("provider", "unknown"),
                    recommendation=analysis.get("recommendation", ""),
                )

        # Mark only fully successful events as processed. Failed events may be retried.
        all_succeeded = all(item["status"] == "success" for item in outcomes.values())
        if all_succeeded:
            self.processed_events.add(key)
        overall = "success" if all_succeeded else "partial_failure"
        self.audit.log(event_id=event_id, employee_id=employee.employee_id, status=overall)
        return {
            "status": overall,
            "event_id": event_id,
            "employee_id": employee.employee_id,
            "destinations": outcomes,
        }
