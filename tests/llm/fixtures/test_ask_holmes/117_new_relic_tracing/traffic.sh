#!/bin/bash
# Port forward to checkout service
kubectl port-forward -n app-117 svc/checkout 3000:3000 &
PF_PID=$!
sleep 5

# Run traffic patterns multiple times to generate enough traces
for i in {1..5}; do
  # 200 OK - successful order
  curl -sS -X POST "http://localhost:3000/orders" \
    -H "Content-Type: application/json" \
    -d '{"userId":"u1","itemId":"sku-11","qty":9,"amount":120}'

  # 409 Conflict - exceeds inventory limit
  curl -sS -X POST "http://localhost:3000/orders" \
    -H "Content-Type: application/json" \
    -d '{"userId":"u1","itemId":"sku-11","qty":2,"amount":900}'

  # 403 Forbidden - high fraud risk
  curl -sS -X POST "http://localhost:3000/orders" \
    -H "Content-Type: application/json" \
    -d '{"userId":"u1","itemId":"sku-11","qty":2,"amount":120}'

  sleep 0
done

kill $PF_PID
