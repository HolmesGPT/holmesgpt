#!/usr/bin/env python3
import argparse
import yaml
import os
import subprocess

parser = argparse.ArgumentParser(
    description="Run before_test or after_test from eval(s)"
)
parser.add_argument(
    "eval_names",
    nargs="+",
    help="Name(s) of eval test(s) (e.g., 84_network_policy_blocking_traffic)",
)
parser.add_argument(
    "-a", "--after", action="store_true", help="Run after_test instead of before_test"
)
args = parser.parse_args()

script_dir = os.path.dirname(os.path.abspath(__file__))
base_dir = os.path.join(script_dir, "..", "tests/llm/fixtures/test_ask_holmes")
section = "after_test" if args.after else "before_test"

for eval_name in args.eval_names:
    eval_dir = os.path.join(base_dir, eval_name)
    if not os.path.exists(eval_dir):
        print(f"❌ Eval directory not found: {eval_name}")
        continue

    print(f"\n{'='*60}")
    print(f"Running {section} for {eval_name}...")
    print(f"{'='*60}")

    # Change to eval directory
    os.chdir(eval_dir)

    # Load and run test
    try:
        with open("test_case.yaml") as f:
            data = yaml.safe_load(f)

        if section in data:
            result = subprocess.run(data[section], shell=True, executable="/bin/bash")
            if result.returncode != 0:
                print(f"⚠️  {section} failed with exit code {result.returncode}")
        else:
            print(f"⚠️  No {section} found in test_case.yaml")
    except Exception as e:
        print(f"❌ Error: {e}")

print(f"\n✅ Completed running {section} for {len(args.eval_names)} eval(s)")
