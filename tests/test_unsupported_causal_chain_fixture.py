"""Keep the abstention eval's hidden cause out of inspectable Kubernetes objects."""

import json
import unittest
from pathlib import Path

import yaml

FIXTURE = (
    Path(__file__).parent / "llm/fixtures/test_ask_holmes/291_unsupported_causal_chain"
)


class UnsupportedCausalChainFixtureTest(unittest.TestCase):
    def test_inventory_implementation_is_only_in_a_mounted_secret(self):
        resources = list(yaml.safe_load_all((FIXTURE / "manifest.yaml").read_text()))
        inventory = next(r for r in resources if r["metadata"]["name"] == "inventory-api")
        spec = inventory["spec"]["template"]["spec"]
        self.assertNotIn("time.sleep", json.dumps(resources))
        container = spec["containers"][0]
        mount = next(m for m in container["volumeMounts"] if m["mountPath"] == "/app")
        volume = next(v for v in spec["volumes"] if v["name"] == mount["name"])
        self.assertTrue(mount["readOnly"])
        case = yaml.safe_load((FIXTURE / "test_case.yaml").read_text())
        self.assertIn(
            f"create secret generic {volume['secret']['secretName']}", case["before_test"]
        )
        self.assertIn("--from-file=server.py=./inventory.py", case["before_test"])
        self.assertIn("python3 /app/server.py", " ".join(container["args"]))
        self.assertTrue((FIXTURE / "inventory.py").is_file())

    def test_setup_budget_exceeds_serial_waits(self):
        case = yaml.safe_load((FIXTURE / "test_case.yaml").read_text())
        self.assertGreater(case["setup_timeout"], 180 + 60 * 3 + 40 * 3)


if __name__ == "__main__":
    unittest.main()
