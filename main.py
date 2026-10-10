"""Runnable demo for employee change propagation with Claude exception analysis."""
from pprint import pprint

from exception_ai import build_exception_analyzer
from propagation import AuditLogger, MockDestination, PropagationEngine


def make_engine():
    audit = AuditLogger()
    destinations = {
        "workday": MockDestination("Workday", audit),
        "okta_lumos": MockDestination("Okta/Lumos", audit),
        "equipment": MockDestination("Equipment", audit),
        "cohort_tracker": MockDestination("Cohort Tracker", audit),
    }
    analyzer = build_exception_analyzer()
    engine = PropagationEngine(
        destinations=destinations,
        audit=audit,
        exception_analyzer=analyzer,
    )
    return engine, destinations, audit, analyzer


def main():
    engine, destinations, audit, analyzer = make_engine()
    print(f"Exception analyzer: {analyzer.__class__.__name__}")
    if analyzer.__class__.__name__ == "OfflineDemoAnalyzer":
        print("Offline mode: Claude API is not called. Set ANTHROPIC_API_KEY to enable live Claude analysis.\n")
    else:
        print("Live Claude API mode: only destination/error diagnostics are sent; no employee PII is sent.\n")

    event = {
        "event_id": "evt-1001",
        "employee": {
            "employee_id": "E101",
            "name": "Rahul Sharma",
            "email": "rahul.sharma@example.com",
            "location": "Bengaluru",
            "start_date": "2026-10-30",
        },
    }

    destinations["equipment"].fail_next_attempts = 1
    print("=== Scenario 1: valid change; transient failure recovers ===")
    pprint(engine.process_event(event))

    print("\n=== Scenario 2: duplicate event is a no-op ===")
    pprint(engine.process_event(event))

    print("\n=== Scenario 3: invalid record is rejected before writes ===")
    bad_event = {
        "event_id": "evt-1002",
        "employee": {
            "employee_id": "E102",
            "name": "",
            "email": "not-an-email",
            "location": "Bengaluru",
            "start_date": "2026-10-30",
        },
    }
    pprint(engine.process_event(bad_event))

    print("\n=== Scenario 4: exhausted failure triggers AI exception analysis ===")
    destinations["equipment"].fail_next_attempts = 10
    failure_event = {
        "event_id": "evt-1003",
        "employee": {
            "employee_id": "E103",
            "name": "Priya Rao",
            "email": "priya.rao@example.com",
            "location": "Hyderabad",
            "start_date": "2026-11-02",
        },
    }
    pprint(engine.process_event(failure_event))

    print("\n=== Audit entries ===")
    pprint(audit.entries)


if __name__ == "__main__":
    main()
