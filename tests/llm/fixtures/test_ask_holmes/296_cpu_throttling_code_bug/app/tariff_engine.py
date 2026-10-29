"""Tariff computation for the quote service.

Carrier rate cards refresh at most once per day, so the full tariff
matrix for a route is computed once and cached in memory. Steady-state
quote requests are expected to be served from that cache.
"""

import logging
import math
import threading
import time

logger = logging.getLogger("quote_service.tariff")

CARRIERS = [
    "northwind",
    "baltic-express",
    "cargolux-freight",
    "transalpine",
    "seabridge",
]

WEIGHT_BREAKS_KG = [0.5, 1, 2, 5, 10, 20, 50, 100, 250, 500]

# Base rates are interpolated over the full zone grid so surcharges stay
# consistent with the carrier rate cards.
ZONE_GRID_RESOLUTION = 250


def _route_seed(origin: str, dest: str) -> int:
    return sum(ord(c) * (i + 1) for i, c in enumerate(origin + dest))


def compute_tariff_matrix(origin: str, dest: str) -> dict:
    """Build the carrier x weight-break rate matrix for a route.

    Interpolates base rates across the zone grid for every carrier and
    weight break. This is the expensive path - callers are expected to
    cache the result per route.
    """
    seed = _route_seed(origin, dest)
    matrix = {}
    for carrier_idx, carrier in enumerate(CARRIERS):
        rates = {}
        for weight in WEIGHT_BREAKS_KG:
            acc = 0.0
            for zone_row in range(ZONE_GRID_RESOLUTION):
                for zone_col in range(ZONE_GRID_RESOLUTION):
                    cell = (seed * 31 + carrier_idx * zone_row + zone_col) % 977
                    acc += math.sqrt(cell + 1.0)
            base = acc / (ZONE_GRID_RESOLUTION * ZONE_GRID_RESOLUTION)
            rates[str(weight)] = round(
                base * (1.0 + math.log1p(weight)) + carrier_idx * 1.75, 2
            )
        matrix[carrier] = rates
    return matrix


class TariffEngine:
    """Serves tariff matrices with an in-memory per-route cache."""

    def __init__(self) -> None:
        self._cache = {}
        self._lock = threading.Lock()

    @staticmethod
    def _cache_key(origin: str, dest: str) -> str:
        """Canonical cache key for a route."""
        return f"{origin.upper()}:{dest.upper()}"

    def get_matrix(self, origin: str, dest: str) -> dict:
        key = self._cache_key(origin, dest)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached

        started = time.monotonic()
        matrix = compute_tariff_matrix(origin, dest)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if elapsed_ms > 500:
            logger.warning(
                "compute_tariff_matrix took %dms for route %s-%s",
                elapsed_ms,
                origin,
                dest,
            )

        with self._lock:
            self._cache[f"{origin.upper()}->{dest.upper()}"] = matrix
        return matrix

    @staticmethod
    def cheapest(matrix: dict, weight_kg: float) -> dict:
        """Pick the cheapest carrier at the smallest weight break >= weight_kg."""
        chosen_break = WEIGHT_BREAKS_KG[-1]
        for wb in WEIGHT_BREAKS_KG:
            if weight_kg <= wb:
                chosen_break = wb
                break
        best_carrier = None
        best_rate = None
        for carrier, rates in matrix.items():
            rate = rates[str(chosen_break)]
            if best_rate is None or rate < best_rate:
                best_carrier = carrier
                best_rate = rate
        return {
            "carrier": best_carrier,
            "rate_eur": best_rate,
            "weight_break_kg": chosen_break,
        }
