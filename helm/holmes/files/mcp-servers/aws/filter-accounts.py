#!/usr/bin/env python3
"""Filter accounts.yaml to only include profiles whose cross-account roles are assumable.

Reads /etc/aws-raw/accounts.yaml (from ConfigMap), tests each profile's role_arn
with sts:AssumeRoleWithWebIdentity (the same auth method the MCP server uses),
writes /etc/aws/accounts.yaml with only working profiles.
"""

import logging

import boto3
import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("filter-accounts")

RAW_PATH = "/etc/aws-raw/accounts.yaml"
FILTERED_PATH = "/etc/aws/accounts.yaml"
TOKEN_PATH = "/var/run/secrets/eks.amazonaws.com/serviceaccount/token"


def test_profile(sts_client, profile_name: str, role_arn: str, web_identity_token: str) -> bool:
    """Test if we can assume the given cross-account role via Web Identity."""
    try:
        sts_client.assume_role_with_web_identity(
            RoleArn=role_arn,
            RoleSessionName=f"init-probe-{profile_name}"[:64],
            WebIdentityToken=web_identity_token,
            DurationSeconds=900,  # minimum
        )
        return True
    except Exception as e:
        log.warning("Profile '%s' (%s): %s", profile_name, role_arn, e)
        return False


def main():
    with open(RAW_PATH) as f:
        config = yaml.safe_load(f)

    profiles = config.get("profiles", {})
    if not profiles:
        log.info("No profiles found, writing empty config")
        with open(FILTERED_PATH, "w") as f:
            yaml.dump(config, f)
        return

    with open(TOKEN_PATH) as f:
        token = f.read().strip()

    log.info("Testing %d profiles...", len(profiles))
    sts = boto3.client("sts", region_name=config.get("region", "us-east-1"))

    working = {}
    failed = []
    for name, cfg in profiles.items():
        role_arn = cfg.get("role_arn", "")
        if test_profile(sts, name, role_arn, token):
            log.info("  OK: %s", name)
            working[name] = cfg
        else:
            failed.append(name)

    config["profiles"] = working

    with open(FILTERED_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False)

    log.info(
        "Done: %d/%d profiles available. Failed: %s",
        len(working),
        len(profiles),
        ", ".join(failed) if failed else "none",
    )

    if not working:
        log.error("No working profiles! MCP server will start with no cross-account access.")
        # Exit 0 anyway — let the MCP server start; it still works for the platform account.


if __name__ == "__main__":
    main()
