import requests

BASE_URL = "https://holmesgpt.shared.platform.pditechnologies.com"
API_KEY = "hgpt_5d1c4687feb95ae99919a5b13d0646f80edc1fe6b0c1b6ac8b554a6cedf3e293"

projects = [
    ("c688995139cb4573b0f320b5df36d827", "C-Store Essentials"),
    ("17619cbe7c4a46718e1c59ecb0d5b43a", "CI POS"),
    ("ac39a0ca52f44eb29e9d1a3f42652455", "Comdata PetroLeader"),
    ("c0ded204b8324b2b82282ca7c51de2f2", "Data Services"),
    ("2bc368c0f8474caca02c6d1f38fd741b", "ERP"),
    ("747dc80dca18493eaad1304bbf2671dd", "Fuel Pricing"),
    ("f4c1f91e48534a2392534946de1dff71", "GasBuddy"),
    ("64e8a39d124748fe8b78cb715a5958f3", "LMP Elevate"),
    ("a1e69bf677674ba3b1236294600b0d5f", "Logistics Cloud"),
    ("699e9c04a94049e590be0d418fb61453", "MCS Payments"),
    ("4e575d46c7ee4dcca9e367cd775a4ce0", "Operations"),
    ("7374c389a4644a659c9aea077a8e015b", "Platform"),
    ("3701cbe3c09f4e0b9c7ebc429ac9d75f", "RM Loyalty"),
    ("b61bc5bd2410487faf57b1310c476448", "Transpac"),
    ("2c0c38750a2d4b8499243c89e748a5c4", "pdi-pos"),
    ("bf37b42a314a4b3abcbbf4eec3aafe2e", "pdi-pos-legacy"),
]

results = []

for proj_id, name in projects:
    print(f"Testing {name}...", end=" ", flush=True)
    try:
        resp = requests.post(
            f"{BASE_URL}/api/chat",
            json={
                "ask": "List the AWS accounts you have access to and briefly describe what services are running. Keep it short.",
                "project_id": proj_id,
                "stream": False,
            },
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"},
            timeout=120,
        )
        if resp.status_code == 200:
            data = resp.json()
            answer = data.get("analysis", data.get("answer", data.get("response", "")))
            has_aws = any(
                kw in answer.lower()
                for kw in [
                    "account", "ec2", "ecs", "eks", "rds", "lambda",
                    "s3", "cloudwatch", "region", "us-east", "eu-central",
                ]
            )
            status = "OK" if has_aws else "NO_AWS"
            snippet = answer[:120].replace("\n", " ")
            print(f"{status} -- {snippet}...")
            results.append((name, status, snippet))
        else:
            error = resp.text[:100]
            print(f"HTTP {resp.status_code} -- {error}")
            results.append((name, f"HTTP_{resp.status_code}", error))
    except Exception as e:
        print(f"ERROR -- {str(e)[:80]}")
        results.append((name, "ERROR", str(e)[:80]))

print("")
print("=== SUMMARY ===")
ok = sum(1 for _, s, _ in results if s == "OK")
no_aws = sum(1 for _, s, _ in results if s == "NO_AWS")
failed = sum(1 for _, s, _ in results if s not in ("OK", "NO_AWS"))
print(f"AWS connected: {ok}  |  No AWS data: {no_aws}  |  Failed: {failed}")
for name, status, snippet in results:
    icon = "OK" if status == "OK" else ("??" if status == "NO_AWS" else "FAIL")
    print(f"  {icon:4s} {name}")
