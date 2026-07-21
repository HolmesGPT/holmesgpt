# quote-service

Shipping quote API for the logistics platform.

## Endpoints

- `GET /api/v1/quote?origin=<code>&dest=<code>&weight_kg=<kg>` — returns the
  cheapest carrier and rate for a route
- `GET /healthz` — liveness/readiness

## Design notes

Carrier rate cards refresh at most once per day, so the tariff matrix for a
route is computed once and cached in memory (`tariff_engine.py`). Computing a
matrix is CPU-heavy (rate interpolation across the full zone grid for every
carrier and weight break); serving a quote from the cached matrix takes
microseconds.

## Deployment

Runs as `quote-service` in Kubernetes. Internal batch consumers (rate sync)
refresh quotes on a fixed set of routes throughout the day.
