#!/usr/bin/env python3
import time
import random
from datetime import datetime

# Generate checkout page logs with render times
for i in range(50):
    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    render_time = 7 + random.uniform(0, 2)  # 7-9 seconds

    print(f"[{timestamp}] INFO:app:Received request for checkout page.", flush=True)
    print(
        f"[{timestamp}] INFO:app:Connecting to promotions database to see if we should try to upsell user",
        flush=True,
    )
    print(
        f"[{timestamp}] INFO:app:Connecting to database at promotions.example.com",
        flush=True,
    )
    print(
        f"[{timestamp}] INFO:app:Fetching data using stored procedure: sp_CheckUserPromotions",
        flush=True,
    )
    time.sleep(0.1)
    print(
        f"[{timestamp}] INFO:app:Database call completed in {render_time:.2f} seconds.",
        flush=True,
    )
    print(f"[{timestamp}] INFO:app:Promotions result: True", flush=True)
    print(
        f"[{timestamp}] INFO:app:Page rendered in {render_time:.2f} seconds.",
        flush=True,
    )
    print(
        f'[{timestamp}] INFO:     127.0.0.1:{random.randint(30000, 60000)} - "GET /checkout HTTP/1.1" 200 OK',
        flush=True,
    )
    time.sleep(1)

# Keep running
while True:
    time.sleep(60)
