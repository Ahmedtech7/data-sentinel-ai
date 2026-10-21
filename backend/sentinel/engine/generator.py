"""Reproducible synthetic enterprise data: order lines for a fictional retailer.

The same (seed, start, days) always produces byte-identical output. The data
contains *natural* irregularities that are not faults (bulk partner orders,
clearance discounts, guest checkouts with no customer id, nightly store
batches), so detectors are tested against realistic noise, not a clean signal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

REGIONS = {"NORAM": 1.0, "EMEA": 0.8, "APAC": 0.6, "LATAM": 0.35}
CHANNELS = {"web": 1.0, "store": 0.7, "partner": 0.3}
CATEGORIES = ["Audio", "Computing", "Home", "Mobile", "Outdoor", "Wearables"]
# Relative weekday demand (Mon..Sun) per channel.
WEEKLY = {
    "web": [1.10, 1.0, 1.0, 1.0, 1.05, 0.90, 0.95],
    "store": [0.75, 0.80, 0.85, 0.90, 1.10, 1.45, 1.15],
    "partner": [1.30, 1.25, 1.20, 1.20, 1.05, 0.25, 0.15],
}
PAYMENTS = {
    "web": (["card", "paypal"], [0.7, 0.3]),
    "store": (["card", "cash"], [0.8, 0.2]),
    "partner": (["invoice", "card"], [0.9, 0.1]),
}


def catalog(seed: int, n_skus: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng([seed, 7])
    prices = np.round(np.exp(rng.normal(4.0, 1.0, n_skus)), 2).clip(4.99, 2999)
    pop = 1.0 / np.arange(1, n_skus + 1) ** 0.8
    return pd.DataFrame({
        "sku": [f"SKU-{i:04d}" for i in rng.choice(9000, n_skus, replace=False) + 1000],
        "category": rng.choice(CATEGORIES, n_skus),
        "base_price": prices,
        "popularity": rng.permutation(pop / pop.sum()),
    })


def generate_orders(seed: int, start: str = "2026-01-05", days: int = 120,
                    base_daily: float = 38.0, day_offset: int = 0) -> pd.DataFrame:
    """Order lines for `days` days. `day_offset` continues the same stream later in time."""
    rng = np.random.default_rng([seed, day_offset])
    cat = catalog(seed)
    t0 = pd.Timestamp(start) + pd.Timedelta(days=day_offset)

    cells = [(d, r, c) for d in range(days) for r in REGIONS for c in CHANNELS]
    lam = np.array([
        base_daily * REGIONS[r] * CHANNELS[c]
        * WEEKLY[c][(t0 + pd.Timedelta(days=d)).dayofweek]
        * (1 + 0.0015 * (d + day_offset))  # mild growth trend
        for d, r, c in cells
    ])
    counts = rng.poisson(lam)
    n = int(counts.sum())
    day = np.repeat([d for d, _, _ in cells], counts)
    region = np.repeat([r for _, r, _ in cells], counts)
    channel = np.repeat([c for _, _, c in cells], counts)

    sku_idx = rng.choice(len(cat), n, p=cat["popularity"].to_numpy())
    is_partner, is_store = channel == "partner", channel == "store"
    qty = 1 + np.where(is_partner, rng.poisson(4, n), rng.poisson(np.where(is_store, 0.6, 0.9)))
    bulk = is_partner & (rng.random(n) < 0.03)  # natural: legitimate bulk orders
    qty = np.where(bulk, rng.integers(20, 61, n), qty)

    price = np.round(cat["base_price"].to_numpy()[sku_idx] * (1 + rng.normal(0, 0.02, n)), 2)
    disc_choices = np.array([0.0, 0.05, 0.10, 0.15, 0.20])
    disc = np.where(
        is_partner,
        rng.choice(disc_choices, n, p=[0.30, 0.20, 0.30, 0.15, 0.05]),
        rng.choice(disc_choices, n, p=[0.82, 0.08, 0.06, 0.03, 0.01]),
    )
    disc = np.where(rng.random(n) < 0.003, 0.40, disc)  # natural: clearance
    revenue = np.round(qty * price * (1 - disc), 2)

    # Order time: daytime-weighted hour of day.
    hour_p = np.exp(-0.5 * ((np.arange(24) - 14) / 4.5) ** 2)
    hours = rng.choice(24, n, p=hour_p / hour_p.sum())
    secs = day * 86400 + hours * 3600 + rng.integers(0, 3600, n)
    order_ts = t0 + pd.to_timedelta(secs, unit="s")

    # Ingestion: web streams in minutes, partners in hours, stores in a nightly batch.
    lag_min = np.where(channel == "web", rng.lognormal(2.5, 0.6, n),
                       rng.lognormal(5.2, 0.5, n))
    ingested = order_ts + pd.to_timedelta(lag_min, unit="m")
    nightly = (t0 + pd.to_timedelta(day + 1, unit="D") + pd.Timedelta(hours=2)
               + pd.to_timedelta(rng.integers(0, 1800, n), unit="s"))
    ingested = pd.DatetimeIndex(np.where(is_store, nightly, ingested))

    customer = np.array([f"C{v:06d}" for v in rng.zipf(1.3, n) % 60000], dtype=object)
    customer[(channel == "web") & (rng.random(n) < 0.02)] = None  # natural: guest checkout
    pay = np.empty(n, dtype=object)
    for c, (opts, p) in PAYMENTS.items():
        m = channel == c
        pay[m] = rng.choice(opts, m.sum(), p=p)

    df = pd.DataFrame({
        "order_ts": order_ts.strftime("%Y-%m-%dT%H:%M:%S"),
        "ingested_at": ingested.strftime("%Y-%m-%dT%H:%M:%S"),
        "region": region,
        "channel": channel,
        "sku": cat["sku"].to_numpy()[sku_idx],
        "category": cat["category"].to_numpy()[sku_idx],
        "quantity": qty,
        "unit_price": price,
        "discount": disc,
        "revenue": revenue,
        "customer_id": customer,
        "payment_method": pay,
        "currency": "USD",
    }).sort_values("order_ts", kind="stable").reset_index(drop=True)
    df.insert(0, "order_id", [f"ORD-{seed % 1000:03d}-{day_offset * 100000 + i:08d}" for i in range(n)])
    return df
