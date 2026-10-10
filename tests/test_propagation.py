import unittest

from exception_ai import OfflineDemoAnalyzer
from propagation import (
    AuditLogger, MockDestination, PropagationEngine, Employee,
    ValidationError, validate_employee,
)


def valid_event(event_id="evt-1"):
    return {
        "event_id": event_id,
        "employee": {
            "employee_id": "E101",
            "name": "Rahul Sharma",
            "email": "rahul@example.com",
            "location": "Bengaluru",
            "start_date": "2026-10-30",
        },
    }


class PropagationTests(unittest.TestCase):
    def setUp(self):
        self.audit = AuditLogger()
        self.destinations = {
            name: MockDestination(name, self.audit)
            for name in ("workday", "okta_lumos", "equipment", "cohort_tracker")
        }
        self.analyzer = OfflineDemoAnalyzer()
        self.engine = PropagationEngine(
            self.destinations, self.audit, max_retries=3,
            exception_analyzer=self.analyzer,
        )

    def test_valid_event_updates_all_destinations(self):
        result = self.engine.process_event(valid_event())
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["destinations"]), 4)
        for dest in self.destinations.values():
            self.assertIn("E101", dest.records)

    def test_duplicate_event_does_not_write_twice(self):
        self.engine.process_event(valid_event())
        writes_before = sum(d.write_count for d in self.destinations.values())
        result = self.engine.process_event(valid_event())
        writes_after = sum(d.write_count for d in self.destinations.values())
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(writes_before, writes_after)

    def test_invalid_email_is_rejected_before_writes(self):
        event = valid_event()
        event["employee"]["email"] = "bad-email"
        result = self.engine.process_event(event)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(sum(d.write_count for d in self.destinations.values()), 0)

    def test_transient_failure_is_retried(self):
        self.destinations["equipment"].fail_next_attempts = 1
        result = self.engine.process_event(valid_event())
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["destinations"]["equipment"]["attempts"], 2)

    def test_exhausted_failure_is_reported_as_partial_failure(self):
        self.destinations["equipment"].fail_next_attempts = 5
        result = self.engine.process_event(valid_event())
        self.assertEqual(result["status"], "partial_failure")
        self.assertEqual(result["destinations"]["equipment"]["status"], "failed")
        self.assertTrue(result["destinations"]["equipment"]["manual_review_required"])

    def test_exhausted_failure_includes_ai_exception_analysis(self):
        self.destinations["equipment"].fail_next_attempts = 5
        result = self.engine.process_event(valid_event())
        equipment = result["destinations"]["equipment"]
        self.assertEqual(equipment["status"], "failed")
        self.assertEqual(equipment["ai_analysis"]["provider"], "offline_demo_mock")
        self.assertTrue(equipment["ai_analysis"]["requires_human_review"])

    def test_retry_after_partial_failure_skips_already_successful_destinations(self):
        self.destinations["equipment"].fail_next_attempts = 3
        first = self.engine.process_event(valid_event())
        self.assertEqual(first["status"], "partial_failure")
        writes_before = {
            name: dest.write_count for name, dest in self.destinations.items()
        }
        second = self.engine.process_event(valid_event())
        self.assertEqual(second["status"], "success")
        for name in ("workday", "okta_lumos", "cohort_tracker"):
            self.assertEqual(self.destinations[name].write_count, writes_before[name])

    def test_bad_date_is_rejected(self):
        event = valid_event()
        event["employee"]["start_date"] = "30-10-2026"
        result = self.engine.process_event(event)
        self.assertEqual(result["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
