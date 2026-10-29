"""Exercise the Loki fixture's setup gate without Kubernetes or an LLM."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


FIXTURE = (
    Path(__file__).parent
    / "llm/fixtures/test_ask_holmes/101_loki_historical_logs_pod_deleted/test_case.yaml"
)

MOCK_COMMANDS = r'''
date() {
  read -r clock < "$FIXTURE_CLOCK"
  echo "$((clock + 30))" > "$FIXTURE_CLOCK"
  echo "$clock"
}
sleep() { :; }
kubectl() {
  case "$*" in
    "apply "*) [ "$FIXTURE_SCENARIO" != apply_failure ] ;;
    "wait "*) [ "$FIXTURE_SCENARIO" != readiness_failure ] ;;
    *"/ready"*) echo ready ;;
    *"query_range"*)
      if [ "$FIXTURE_SCENARIO" = query_failure ]; then
        return 1
      fi
      if [ "$FIXTURE_SCENARIO" = missing ] ||
         { [ "$FIXTURE_SCENARIO" = missing_current ] && [[ "$*" != *'start='* ]]; } ||
         { [ "$FIXTURE_SCENARIO" = missing_historical ] && [[ "$*" == *'start='* ]]; } ||
         { [ "$FIXTURE_SCENARIO" = delayed ] && [ "$(cat "$FIXTURE_CLOCK")" -le 60 ]; } ||
         { [ "$FIXTURE_SCENARIO" = late_ingestion ] && [ "$(cat "$FIXTURE_CLOCK")" -le 180 ]; } ||
         { [ "$FIXTURE_SCENARIO" = missing_errors ] && [[ "$*" == *'level="ERROR"'* ]]; }; then
        echo '{"data":{"result":[]}}'
      else
        echo '{"data":{"result":[{"values":[["1","test log"]]}]}}'
      fi
      ;;
    "delete "*) echo "$*" >> "$FIXTURE_DELETIONS" ;;
    "get pods "*) echo "No resources found" ;;
    *) return 0 ;;
  esac
}
'''


class LokiFixtureSetupTest(unittest.TestCase):
    """Check setup outcomes using a deterministic clock and no cluster access."""

    def run_setup(self, scenario):
        """Run the fixture with mocked commands and return its output and deletions."""
        script = yaml.safe_load(FIXTURE.read_text())["before_test"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clock = root / "clock"
            deletions = root / "deletions"
            clock.write_text("0\n")
            result = subprocess.run(
                ["bash", "-c", MOCK_COMMANDS + script],
                env={
                    **os.environ,
                    "FIXTURE_SCENARIO": scenario,
                    "FIXTURE_CLOCK": str(clock),
                    "FIXTURE_DELETIONS": str(deletions),
                },
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            deleted = deletions.read_text() if deletions.exists() else ""
        return result, deleted

    def test_missing_logs_abort_before_deletion(self):
        """Exhausting the ingestion budget must preserve the workload for diagnosis."""
        result, deleted = self.run_setup("missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(deleted, "")
        self.assertIn("Logs never reached Loki", result.stdout + result.stderr)

    def test_missing_error_logs_abort_before_deletion(self):
        """General logs alone must not satisfy the historical error-log scenario."""
        result, deleted = self.run_setup("missing_errors")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(deleted, "")

    def test_each_required_query_must_return_logs(self):
        """Missing query evidence or query errors must prevent workload deletion."""
        for scenario in ("missing_current", "missing_historical", "query_failure"):
            with self.subTest(scenario=scenario):
                result, deleted = self.run_setup(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(deleted, "")
                self.assertIn("Logs never reached Loki", result.stderr)

    def test_failed_infrastructure_setup_aborts_before_deletion(self):
        """Apply and pod-readiness failures must stop setup before deletion."""
        for scenario in ("apply_failure", "readiness_failure"):
            with self.subTest(scenario=scenario):
                result, deleted = self.run_setup(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(deleted, "")

    def test_ingestion_can_succeed_after_an_empty_poll(self):
        """An empty first poll must be retried rather than treated as final failure."""
        result, deleted = self.run_setup("delayed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("delete deployment payment-api-101", deleted)

    def test_ingestion_can_succeed_after_old_timeout(self):
        """Allow logs arriving after 120 seconds within the increased polling budget."""
        result, deleted = self.run_setup("late_ingestion")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Logs ready in Loki", result.stdout)
        self.assertIn("delete deployment payment-api-101", deleted)

    def test_ready_logs_allow_historical_log_scenario(self):
        """Available evidence must allow deletion and the historical-only scenario."""
        result, deleted = self.run_setup("ready")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("delete deployment payment-api-101", deleted)
        self.assertIn("Pod successfully deleted", result.stdout)


if __name__ == "__main__":
    unittest.main()
