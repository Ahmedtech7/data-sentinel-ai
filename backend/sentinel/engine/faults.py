"""Controlled fault injection with ground-truth labels.

Used by the offline evaluation (labelled test sets) and by the Fault Lab in the
product (inject into a real dataset version, then score what the analysis found).
Every injector works on any typed frame + schema, not only the demo data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import day_of, segment_key

FAULTS = {
    "unit_error": dict(level="row", label="Unit / scale error",
                       description="Numeric values multiplied by a factor, like cents written as dollars.",
                       defaults=dict(factor=100.0, rate=0.01)),
    "spike": dict(level="row", label="Extreme values",
                  description="Numeric values inflated 8-40x on a small share of records.",
                  defaults=dict(rate=0.005)),
    "sign_flip": dict(level="row", label="Sign flip",
                      description="Positive amounts written as negatives.",
                      defaults=dict(rate=0.005)),
    "missing": dict(level="row", label="Missing values",
                    description="A column goes blank for part of the feed.",
                    defaults=dict(rate=0.5)),
    "duplicates": dict(level="row", label="Duplicate records",
                       description="Records replayed twice by an upstream retry.",
                       defaults=dict(rate=0.05)),
    "volume_drop": dict(level="period", label="Volume drop",
                        description="A segment silently stops delivering most of its records.",
                        defaults=dict(rate=0.8)),
    "delay": dict(level="period", label="Late arrival",
                  description="Records ingested hours later than usual.",
                  defaults=dict(hours=8.0)),
    "mean_shift": dict(level="period", label="Subtle drift",
                       description="A numeric column drifts upward for a segment.",
                       defaults=dict(factor=1.3)),
    "type_corruption": dict(level="schema", label="Type corruption",
                            description="Text tokens like 'ERR' appear in a numeric column.",
                            defaults=dict(rate=0.02)),
    "drop_column": dict(level="schema", label="Dropped column",
                        description="An upstream change removes a column.",
                        defaults=dict()),
}
ROW_LEVEL = {k for k, v in FAULTS.items() if v["level"] == "row"} | {"type_corruption"}
# A segment-day counts as faulty when >= 20% and >= 5 of its rows are affected.
PERIOD_SHARE, PERIOD_MIN_ROWS = 0.2, 5


def _mask(df, schema, spec, seg, day):
    m = pd.Series(True, index=df.index)
    if spec.get("segment"):
        m &= seg == spec["segment"]
    if spec.get("days") and day is not None:
        lo, hi = (pd.Timestamp(d) for d in spec["days"])
        m &= (day >= lo) & (day <= hi)
    for col, val in (spec.get("where") or {}).items():
        m &= df[col].astype(str) == str(val)
    return m


def _sample(m, rate, rng):
    idx = np.flatnonzero(m.to_numpy())
    k = int(round(len(idx) * rate))
    return np.sort(rng.choice(idx, k, replace=False)) if k else np.array([], dtype=int)


def inject(df: pd.DataFrame, schema: dict, specs: list[dict], seed: int):
    """Return (faulty_df, truth). truth holds positional row labels, faulty periods and schema faults."""
    rng = np.random.default_rng(seed)
    df = df.reset_index(drop=True).copy()
    df["__fault__"] = ""
    dropped_periods, schema_truth, row_names = [], [], set()

    for spec in specs:
        kind = spec["type"]
        p = {**FAULTS[kind]["defaults"], **spec}
        col = p.get("column")
        name = p.get("label", kind)  # lets an evaluation tell apart two scenarios of one fault type
        if kind in ROW_LEVEL:
            row_names.add(name)
        seg, day = segment_key(df, schema), day_of(df, schema)
        m = _mask(df, schema, p, seg, day)

        if kind in ("unit_error", "spike", "sign_flip", "missing", "type_corruption"):
            rows = _sample(m, p["rate"], rng)
            cols = p.get("columns") or [col]
            for c in cols:  # ints can't hold NaN or scaled floats
                if pd.api.types.is_numeric_dtype(df[c]):
                    df[c] = df[c].astype(float)
            if kind == "unit_error":
                df.loc[rows, cols] = df.loc[rows, cols] * p["factor"]
            elif kind == "spike":
                f = rng.uniform(8, 40, len(rows))
                for c in cols:
                    df.loc[rows, c] = df.loc[rows, c] * f
            elif kind == "sign_flip":
                df.loc[rows, cols] = -df.loc[rows, cols].abs()
            elif kind == "missing":
                df.loc[rows, cols] = np.nan
            else:
                df[col] = df[col].astype(object)
                df.loc[rows, col] = rng.choice(["ERR", "#VALUE!", "n/a?"], len(rows))
                schema_truth.append({"column": col, "fault": kind})
            df.loc[rows, "__fault__"] = name
        elif kind == "duplicates":
            rows = _sample(m, p["rate"], rng)
            dup = df.loc[rows].copy()
            dup["__fault__"] = name
            df = pd.concat([df, dup], ignore_index=True)
        elif kind == "volume_drop":
            rows = _sample(m, p["rate"], rng)
            if day is not None and len(rows):
                lost = pd.DataFrame({"segment": seg[rows], "day": day[rows]}).value_counts()
                total = pd.DataFrame({"segment": seg[m], "day": day[m]}).value_counts()
                share = (lost / total.reindex(lost.index)).fillna(0)
                dropped_periods += [{"segment": s, "day": d.date().isoformat(), "fault": name}
                                    for (s, d), v in share.items() if v >= PERIOD_SHARE]
            df = df.drop(index=rows).reset_index(drop=True)
        elif kind == "delay":
            t = schema.get("ingest_time")
            if not t:
                raise ValueError("This dataset has no ingestion timestamp to delay.")
            df[t] = df[t] + pd.to_timedelta(np.where(m, round(p["hours"] * 3600), 0), unit="s")
            df.loc[m & (df["__fault__"] == ""), "__fault__"] = name
        elif kind == "mean_shift":
            df[col] = df[col].astype(float)
            df.loc[m, col] = df.loc[m, col] * p["factor"]
            df.loc[m & (df["__fault__"] == ""), "__fault__"] = name
        elif kind == "drop_column":
            df = df.drop(columns=[col])
            schema_truth.append({"column": col, "fault": kind})
        else:
            raise ValueError(f"Unknown fault type: {kind}")

    labels = df.pop("__fault__")
    seg, day = segment_key(df, schema), day_of(df, schema)
    periods = list(dropped_periods)
    if day is not None:
        frame = pd.DataFrame({"segment": seg, "day": day, "fault": labels})
        size = frame.groupby(["segment", "day"]).size()
        hits = frame[frame["fault"] != ""].groupby(["segment", "day", "fault"]).size()
        seen = set()
        for (s, d, f), k in hits.items():
            # Row faults need several affected rows; a period fault (delay, drift) is the whole slice.
            need = PERIOD_MIN_ROWS if f in row_names else 1
            if k >= need and k / size[(s, d)] >= PERIOD_SHARE and (s, d) not in seen:
                seen.add((s, d))
                periods.append({"segment": s, "day": d.date().isoformat(), "fault": f})
    row_labels = labels.where(labels.isin(row_names), "")
    truth = {
        "rows": {k: np.flatnonzero(row_labels.to_numpy() == k).tolist() for k in sorted(set(row_labels) - {""})},
        "periods": periods,
        "schema": schema_truth,
    }
    return df, truth, row_labels.to_numpy()
