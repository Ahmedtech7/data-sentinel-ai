"""One analysis run: deterministic checks + learned models -> findings with evidence."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .checks import infer_contract, profile, run_checks
from .modeling import PeriodModel, Residualizer, RowModel
from .schema import coerce, day_of, infer_schema, segment_key

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
MAX_EXAMPLES = 200
# Bump when modeling code changes in a way that makes stored (pickled) models incompatible;
# datasets with older models are retrained from their baseline on their next analysis.
ENGINE_VERSION = "2026.10.3"


@lru_cache
def selection() -> dict:
    """Detector + threshold chosen by the offline evaluation (sentinel/ml_eval.py)."""
    return json.loads((ARTIFACTS / "selection.json").read_text())


def build_baseline(raw: pd.DataFrame, seed: int = 0) -> dict:
    """Learn schema, contract, profile and models from a baseline version."""
    schema = infer_schema(raw)
    df, _ = coerce(raw, schema)
    sel = selection()
    out = {"schema": schema, "contract": infer_contract(df, schema), "profile": profile(df, schema),
           "row_model": None, "period_model": None, "notes": []}
    res = None
    try:
        res = Residualizer().fit(df, schema, seed) if schema["numeric"] else None
        out["row_model"] = RowModel(sel["row"]["detector"], sel["row"]["threshold"], seed).fit(df, schema, res)
    except ValueError as e:
        out["notes"].append(f"Record model not trained: {e}")
    if not schema["numeric"]:
        out["notes"].append("Record model not trained: no numeric columns.")
    try:
        if len(df) < 200:
            raise ValueError("fewer than 200 rows")
        out["period_model"] = PeriodModel(sel["period"]["detector"], sel["period"]["threshold"], seed).fit(df, schema, res)
        if out["period_model"].n_days < 14:
            out["period_model"] = None
            raise ValueError("fewer than 14 days of history")
    except ValueError as e:
        out["notes"].append(f"Feed model not trained: {e}")
    return out


def _sev_rows(share: float) -> str:
    return "high" if share > 0.01 else "medium" if share > 0.001 else "low"


def analyze(raw: pd.DataFrame, schema: dict, contract: dict, user_rules: list, base_profile: dict | None,
            row_model: RowModel | None, period_model: PeriodModel | None, is_baseline: bool) -> dict:
    df, failures = coerce(raw, schema)
    n = len(df)
    checks, findings, rule_rows = run_checks(df, raw, schema, contract if not is_baseline else {"rules": []},
                                             user_rules, failures, None if is_baseline else base_profile)
    result = {"rows": n, "columns": int(raw.shape[1]), "notes": []}
    # A model whose input columns are gone would score every row against a broken feature set.
    gone = {c["name"] for c in schema["columns"]} - set(df.columns)
    if row_model is not None and gone & set(row_model.numeric + row_model.context):
        row_model = None
        result["notes"].append("Record model skipped: columns it uses are missing (see the schema finding).")
    if period_model is not None and gone & set(schema["segments"] + [schema.get("time"), schema.get("ingest_time")]
                                               + period_model.numeric + period_model.res.context):
        period_model = None
        result["notes"].append("Feed model skipped: columns it uses are missing (see the schema finding).")

    # ---- records
    model_rows = np.array([], dtype=int)
    if row_model is not None:
        scores = row_model.train_scores if is_baseline else row_model.score(df)
        model_rows = np.flatnonzero(scores >= row_model.threshold)
        top = model_rows[np.argsort(-scores[model_rows])][:MAX_EXAMPLES]
        explained = row_model.explain(df, top)
        key = schema.get("key")
        examples = []
        for e in explained:
            p = e["position"]
            examples.append({**e, "score": float(scores[p]), "row_number": p + 2,  # +header, 1-based
                             "key": None if not key or key not in raw else raw.iloc[p][key],
                             "rule_violation": bool(p in rule_rows)})
        # One finding per column that drives the anomalies, so incidents stay specific.
        by_col: dict[str, list] = {}
        for ex in examples:
            if ex["reasons"]:
                by_col.setdefault(ex["reasons"][0]["column"], []).append(ex)
        lead = pd.Series([ex["reasons"][0]["column"] for ex in examples if ex["reasons"]]).value_counts()
        for col, exs in by_col.items():
            count = int(round(len(model_rows) * lead[col] / max(lead.sum(), 1)))
            r0 = exs[0]["reasons"][0]
            findings.append({
                "kind": "record_anomaly", "fingerprint": f"record_anomaly:{col}",
                "severity": _sev_rows(count / n), "title": f"~{count:,} unusual `{col}` values",
                "summary": (f"The record model flagged {count:,} rows where `{col}` is far from what the baseline "
                            f"predicts for that context. Example: {r0['value']:,.2f} where ≈{r0['expected']:,.2f} "
                            "was expected. Unusual is not the same as wrong: confirm with the source before "
                            "correcting."),
                "evidence": {"column": col, "count": count, "examples": exs[:25]},
            })
        result["records"] = {"scored": n, "flagged": int(len(model_rows)), "threshold": row_model.threshold,
                             "detector": row_model.detector, "examples": examples,
                             "score_histogram": _hist(scores, row_model.threshold)}

    # ---- feed health per segment-day
    if period_model is not None and day_of(df, schema) is not None:
        t = period_model.table(df)
        # On the baseline itself the feed model is scored in-sample, so only the volume test is trusted.
        flag = t["volume_alert"] | (False if is_baseline else t["score"] >= period_model.threshold)
        pos = np.flatnonzero(flag.to_numpy())
        reasons = period_model.explain(t, pos)
        flagged = []
        for p, rs in zip(pos, reasons):
            row = t.iloc[p]
            if row["volume_alert"]:  # the volume test fired: lead with the row count
                rows_reason = next((r for r in rs if r["metric"] == "rows"), None) or {
                    "metric": "rows", "value": float(row["count"]), "typical": float(row["expected_count"]),
                    "basis": f"median of baseline {row['day']:%A}s", "z": None}
                rs = [rows_reason] + [r for r in rs if r is not rows_reason]
            flagged.append({"segment": row["segment"], "day": row["day"].date().isoformat(),
                            "score": float(row["score"]), "volume_alert": bool(row["volume_alert"]),
                            "rows": int(row["count"]), "expected_rows": _num(row["expected_count"]),
                            "reasons": rs})
        # A segment-day whose main symptom is already a dataset-level finding (duplicates, empty
        # cells) becomes evidence on that finding instead of a separate one: one incident per cause.
        owner = {"duplicate keys": ("duplicates",), "empty cells": ("missing", "contract")}
        # Share of each segment-day's rows the record model flagged: a few bad rows that move a
        # day's average belong to the record finding; a majority is a genuine segment-wide shift.
        hit = np.zeros(n, bool)
        hit[model_rows] = True
        share = pd.Series(hit).groupby([segment_key(df, schema).to_numpy(), day_of(df, schema).to_numpy()]).mean()
        standalone = []
        for item in flagged:
            lead = item["reasons"][0]["metric"] if item["reasons"] else "rows"
            host = None
            if not item["volume_alert"]:
                host = next((f for f in findings if f["kind"] in owner.get(lead, ())), None)
                if host is None and share.get((item["segment"], pd.Timestamp(item["day"])), 0) < 0.5:
                    host = next((f for f in findings if f["fingerprint"] == f"record_anomaly:{lead}"), None)
            if host:
                host["evidence"].setdefault("segment_days", []).append(item)
            else:
                standalone.append(item)
        for seg, items in pd.DataFrame(standalone).groupby("segment") if standalone else []:
            items = items.sort_values("day").to_dict("records")
            drop = any(i["volume_alert"] for i in items)
            first = next((i for i in items if i["volume_alert"]), items[0])
            lead = first["reasons"][0]["metric"] if first["reasons"] else "rows"
            days = [i["day"] for i in items]
            findings.append({
                "kind": "feed_anomaly", "fingerprint": f"feed_anomaly:{seg}",
                "severity": "high" if drop else "medium",
                "title": f"{seg}: " + ("volume drop" if drop else f"unusual {lead}")
                         + f" on {len(days)} day{'s' if len(days) > 1 else ''}",
                "summary": _feed_summary(first),
                "evidence": {"segment": seg, "days": items},
            })
        daily = t.groupby("day").agg(rows=("count", "sum"), expected=("expected_count", "sum")).reset_index()
        result["feed"] = {
            "detector": period_model.detector, "threshold": period_model.threshold,
            "segments": period_model.segments, "flagged": flagged,
            "daily": [{"day": d.date().isoformat(), "rows": int(r), "expected": _num(e)}
                      for d, r, e in zip(daily["day"], daily["rows"], daily["expected"])],
            "cells": [{"segment": s, "day": d.date().isoformat(), "rows": int(c), "score": round(float(sc), 3)}
                      for s, d, c, sc in zip(t["segment"], t["day"], t["count"], t["score"])],
        }

    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: sev_rank[f["severity"]])
    passed = sum(c["passed"] for c in checks)
    result["checks"] = checks
    result["summary"] = {
        "rows": n, "checks_passed": passed, "checks_total": len(checks),
        "rule_flagged_rows": len(rule_rows), "model_flagged_rows": int(len(model_rows)),
        "flagged_periods": len(result.get("feed", {}).get("flagged", [])),
        "findings": {s: sum(f["severity"] == s for f in findings) for s in sev_rank},
        "baseline": is_baseline,
    }
    result["findings"] = findings
    result["_flagged_rows"] = sorted(set(rule_rows) | set(model_rows.tolist()))
    result["_rule_rows"], result["_model_rows"] = rule_rows, set(model_rows.tolist())
    return result


def _num(v):
    return None if v is None or pd.isna(v) else float(v)


def _hist(scores, threshold, bins=40):
    hi = max(float(np.quantile(scores, 0.999)), threshold * 1.5)
    counts, edges = np.histogram(np.clip(scores, 0, hi), bins=bins, range=(0, hi))
    return {"edges": edges.round(3).tolist(), "counts": counts.tolist(), "threshold": threshold}


def _feed_summary(i: dict) -> str:
    r = i["reasons"][0] if i["reasons"] else None
    if r and r["metric"] == "rows" and r["typical"] is not None:
        change = f" ({i['rows'] / r['typical'] - 1:+.0%})" if r["typical"] else ""
        cause = (" Missing rows often mean an upstream extract failed or a source stopped sending."
                 if i["rows"] < r["typical"] else "")
        return (f"On {i['day']} this segment delivered {int(i['rows'])} rows; the baseline median for that weekday "
                f"is {r['typical']:.0f}{change}.{cause}")
    if r and r.get("unit") == "mean" and r["value"] is not None and r["typical"] is not None:
        return (f"On {i['day']}, the average `{r['metric']}` was {r['value']:,.2f} where {r['typical']:,.2f} was "
                "expected for that day's mix of records. A shift like this is either a real business change "
                "(pricing, promotion) or a conversion bug upstream.")
    if r:
        return (f"On {i['day']}, {r['metric']} was {r['value']:.2f} versus a typical {r['typical'] or 0:.2f} "
                "for this segment.")
    return "The combination of feed metrics for this segment was unusual compared with the baseline."


def lab_scorecard(truth: dict, result: dict) -> dict:
    """Compare injected faults with what the analysis reported."""
    rule_rows, model_rows = result["_rule_rows"], result["_model_rows"]
    rows = []
    for fault, positions in truth["rows"].items():
        pos = set(positions)
        by_rule, by_model = len(pos & rule_rows), len(pos & model_rows)
        rows.append({"fault": fault, "injected": len(pos), "detected": len(pos & (rule_rows | model_rows)),
                     "by_rules": by_rule, "by_model": by_model})
    flagged = {(f["segment"], f["day"]) for f in result.get("feed", {}).get("flagged", [])}
    periods = [{**p, "detected": (p["segment"], p["day"]) in flagged} for p in truth["periods"]]
    kinds = {f["kind"] for f in result["findings"]}
    schema = [{**s, "detected": bool({"schema", "type"} & kinds)} for s in truth["schema"]]
    injected = set().union(*map(set, truth["rows"].values())) if truth["rows"] else set()
    return {"rows": rows, "periods": periods, "schema": schema,
            "other_flagged_rows": len((rule_rows | model_rows) - injected),
            "note": "Other flagged rows were not injected. They may be natural anomalies already present in the "
                    "source version, or false positives."}
