import subprocess

accounts = [
    ("803964703583", "pdi-pos-prod"),
    ("415641701024", "pdi-pos-stage"),
    ("100161908138", "pdi-pos-legacy-prod"),
    ("294818304262", "pdi-pos-legacy-uat"),
    ("226168396949", "pdi-pos-legacy-demo"),
    ("607378507561", "gb-bp-client"),
    ("386397235394", "ce-cstore-essentials-prod"),
    ("179669678732", "ce-cstore-essentials-staging"),
    ("185077157484", "ce-koupon-prod"),
    ("025524405457", "ce-skupos-legacy-prod"),
    ("271593336501", "pdi-cipos-prod"),
    ("436020120639", "pdi-cipos-stage"),
    ("903333983563", "pdi-transpac-prod"),
    ("711387130277", "pdi-comdata-petroleader-prod"),
    ("090790636866", "pdi-data-services-prod"),
    ("077614951579", "pdi-erp-prod"),
    ("929611976443", "pdi-erp-stage"),
    ("510376924091", "pdi-lmp-elevate-prod"),
    ("008048648984", "pdi-lmp-elevate-staging"),
    ("179616421945", "pdi-mcs-payments-prod"),
    ("856536366562", "pdi-mcs-payments-staging"),
    ("211125545481", "pdi-operations-prod"),
    ("211125652818", "pdi-operations-stage"),
    ("921714353219", "pdi-platform-prod"),
    ("019652197448", "pdi-platform-stage"),
    ("974458387942", "pdi-pos-stage-2"),
    ("651006557025", "pdi-rm-loyalty-prod"),
    ("506628524632", "pdi-rm-loyalty-staging"),
    ("473106049869", "pdi-rm-loyalty-pci-prod"),
    ("582802577213", "pdi-rm-loyalty-pci-staging"),
]

passed = 0
failed = 0

for acct_id, name in accounts:
    role_arn = f"arn:aws:iam::{acct_id}:role/HolmesReadOnly"
    result = subprocess.run(
        [
            "aws", "sts", "assume-role",
            "--role-arn", role_arn,
            "--role-session-name", "trust-check",
            "--profile", "pdi-platform-all",
            "--region", "us-east-1",
            "--duration-seconds", "900",
            "--query", "Credentials.AccessKeyId",
            "--output", "text",
        ],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode == 0:
        print(f"  OK  {name} ({acct_id})")
        passed += 1
    else:
        err = result.stderr.strip().split("\n")[-1] if result.stderr else "unknown"
        print(f"  FAIL {name} ({acct_id}) - {err[:80]}")
        failed += 1

print(f"\nPassed: {passed}/{passed + failed}")
