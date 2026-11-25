#!/bin/sh
# Internal batch consumer: refreshes carrier quotes for a fixed set of
# routes all day. Traffic is objectively light (12 routes x 5 weights,
# sequential, with pauses) — a working route cache would serve it in
# microseconds.
ROUTES="AMS:JFK RTM:OSL HAM:SIN BCN:DXB LIS:GRU CDG:NRT MAD:MEX VIE:ORD ZRH:YYZ DUB:SFO CPH:ICN ATH:CPT"
WEIGHTS="0.5 2 5 20 100"
echo "rate-sync-worker started"
while true; do
  for route in $ROUTES; do
    origin=${route%%:*}
    dest=${route##*:}
    for w in $WEIGHTS; do
      curl -s -m 60 "http://quote-service:8080/api/v1/quote?origin=${origin}&dest=${dest}&weight_kg=${w}" > /dev/null || true
      sleep 0.2
    done
  done
  sleep 5
done
