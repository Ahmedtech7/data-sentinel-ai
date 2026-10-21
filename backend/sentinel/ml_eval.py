"""Offline evaluation and model selection.

    python -m sentinel.ml_eval            # writes sentinel/artifacts/{report,selection}.json

Protocol (see docs/model-card.md):
  * Each "stream" is an independent synthetic company (seed). The first 90 days are
    the baseline the models learn from (with a little unlabelled contamination, as real
    baselines are never perfectly clean); the next 60 days are scored.
  * Faults with ground-truth labels are injected into the scored window only.
  * Validation streams choose the detector (by average precision) and its decision
    threshold (max F1). Test streams are never used for any choice and give the
    reported numbers.
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score

from .engine.checks import infer_contract, run_checks
from .engine.faults import inject
from .engine.generator import generate_orders
from .engine.modeling import PERIOD_DETECTORS, ROW_DETECTORS, PeriodModel, Residualizer, RowModel
from .engine.schema import coerce, day_of, infer_schema, read_csv, segment_key

VAL_SEEDS = [11, 12, 13]
TEST_SEEDS = [101, 102, 103, 104, 105]
REF_DAYS, CUR_DAYS = 90, 60
REVENUE_RULE = {"type": "formula", "expr": "abs(revenue - quantity * unit_price * (1 - discount)) <= 0.05"}
ARTIFACTS = Path(__file__).parent / "artifacts"


def _roundtrip(df):
    """Go through CSV text exactly as an upload would."""
    return read_csv(df.to_csv(index=False).encode(), max_rows=10**7)


def build_stream(seed: int) -> dict:
    rng = np.random.default_rng(seed)
    raw_ref = _roundtrip(generate_orders(seed, days=REF_DAYS))
    raw_cur = _roundtrip(generate_orders(seed, days=CUR_DAYS, day_offset=REF_DAYS))
    schema = infer_schema(raw_ref)
    ref, _ = coerce(raw_ref, schema)
    cur, _ = coerce(raw_cur, schema)
    ref, _, _ = inject(ref, schema, [{"type": "spike", "columns": ["quantity", "revenue"], "rate": 0.001}], seed + 1)

    start = day_of(cur, schema).min()
    d0 = start + pd.Timedelta(days=int(rng.integers(0, CUR_DAYS - 3)))
    row_specs = [
        {"type": "unit_error", "label": "price_x100", "columns": ["unit_price", "revenue"], "factor": 100, "rate": 0.003},
        {"type": "unit_error", "label": "price_div100", "columns": ["unit_price", "revenue"], "factor": 0.01, "rate": 0.002},
        {"type": "spike", "label": "quantity_spike", "columns": ["quantity", "revenue"], "rate": 0.003},
        # Contextual: x10 is a normal price for *some* SKU, abnormal only for this one.
        {"type": "unit_error", "label": "sku_price_x10", "columns": ["unit_price", "revenue"], "factor": 10,
         "rate": 1.0, "where": {"sku": str(rng.choice(sorted(cur["sku"].unique())))},
         "days": [str(d0.date()), str((d0 + pd.Timedelta(days=2)).date())]},
    ]
    row_df, row_truth, row_labels = inject(cur, schema, row_specs, seed + 2)

    segs, days = sorted(segment_key(cur, schema).unique()), sorted(day_of(cur, schema).unique())
    grid = [(s, d) for s in segs for d in days]
    cells = [grid[i] for i in rng.choice(len(grid), 40, replace=False)]
    plan = ["volume_drop"] * 14 + ["delay"] * 8 + ["mean_shift"] * 8 + ["missing"] * 5 + ["duplicates"] * 5
    period_specs = []
    for (s, d), kind in zip(cells, plan):
        day = [str(pd.Timestamp(d).date())] * 2
        spec = {"type": kind, "segment": s, "days": day}
        if kind == "volume_drop":
            spec["rate"] = float(rng.uniform(0.5, 1.0))
        elif kind == "delay":
            spec["hours"] = float(rng.uniform(3, 12))
        elif kind == "mean_shift":
            spec |= {"column": "unit_price", "factor": float(rng.uniform(1.25, 1.5))}
        elif kind == "missing":
            spec |= {"column": str(rng.choice(["discount", "customer_id"])), "rate": float(rng.uniform(0.3, 0.9))}
        else:
            spec["rate"] = float(rng.uniform(0.2, 0.5))
        period_specs.append(spec)
    period_df, period_truth, _ = inject(cur, schema, period_specs, seed + 3)

    rule_specs = [
        {"type": "sign_flip", "column": "quantity", "rate": 0.003},
        {"type": "type_corruption", "column": "unit_price", "rate": 0.003},
        {"type": "duplicates", "rate": 0.005},
        {"type": "unit_error", "label": "price_x100_no_revenue", "column": "unit_price", "factor": 100, "rate": 0.003},
        {"type": "missing", "column": "order_id", "rate": 0.002},
    ]
    rule_df, rule_truth, rule_labels = inject(cur, schema, rule_specs, seed + 4)
    return dict(seed=seed, schema=schema, ref=ref, cur=cur, row_df=row_df, row_labels=row_labels,
                period_df=period_df, period_truth=period_truth, rule_df=rule_df, rule_labels=rule_labels)


def _period_labels(table: pd.DataFrame, truth: dict) -> tuple[np.ndarray, np.ndarray]:
    lab = {(p["segment"], p["day"]): p["fault"] for p in truth["periods"]}
    keys = zip(table["segment"], table["day"].dt.strftime("%Y-%m-%d"))
    faults = np.array([lab.get(k, "") for k in keys], dtype=object)
    return (faults != "").astype(int), faults


def score_stream(st: dict) -> dict:
    out = {"row": {}, "period": {}}
    t = time.perf_counter()
    res = Residualizer().fit(st["ref"], st["schema"], st["seed"])
    res_secs = time.perf_counter() - t  # shared by every detector, charged to each below
    for name in ROW_DETECTORS:
        t = time.perf_counter()
        m = RowModel(name, seed=st["seed"]).fit(st["ref"], st["schema"], res)
        s = m.score(st["row_df"])
        out["row"][name] = dict(scores=s, labels=(st["row_labels"] != "").astype(int),
                                faults=st["row_labels"], seconds=time.perf_counter() - t + res_secs, n=len(s))
    for name in PERIOD_DETECTORS:
        t = time.perf_counter()
        m = PeriodModel(name, seed=st["seed"]).fit(st["ref"], st["schema"], res)
        tab = m.table(st["period_df"])
        y, faults = _period_labels(tab, st["period_truth"])
        out["period"][name] = dict(scores=tab["score"].to_numpy(), labels=y, faults=faults,
                                   volume_alert=tab["volume_alert"].to_numpy(),
                                   expected=tab["expected_count"].to_numpy(float),
                                   seconds=time.perf_counter() - t + res_secs, n=len(tab))
    return out


def best_threshold(scores, labels) -> float:
    p, r, th = precision_recall_curve(labels, scores)
    f1 = 2 * p * r / np.maximum(p + r, 1e-12)
    return float(th[int(np.argmax(f1[:-1]))])


def metrics_at(scores, labels, faults, threshold) -> dict:
    pred = scores >= threshold
    tp, fp = int((pred & (labels == 1)).sum()), int((pred & (labels == 0)).sum())
    fn, tn = int((~pred & (labels == 1)).sum()), int((~pred & (labels == 0)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    per_fault = {f: float(pred[faults == f].mean()) for f in sorted(set(faults) - {""})}
    return dict(precision=prec, recall=rec, f1=2 * prec * rec / (prec + rec) if prec + rec else 0.0,
                fpr=fp / (fp + tn) if fp + tn else 0.0, tp=tp, fp=fp, fn=fn, tn=tn,
                average_precision=float(average_precision_score(labels, scores)),
                roc_auc=float(roc_auc_score(labels, scores)), per_fault_recall=per_fault)


def _pool(runs, level, name):
    keys = ("scores", "labels", "faults")
    return [np.concatenate([r[level][name][k] for r in runs]) for k in keys]


def _summary(per_stream: list[dict]) -> dict:
    out = {}
    for k in ("precision", "recall", "f1", "fpr", "average_precision", "roc_auc"):
        v = np.array([m[k] for m in per_stream])
        out[k] = {"mean": float(v.mean()), "std": float(v.std())}
    return out


def _curve(scores, labels, points=51):
    """Interpolated precision (best precision at recall >= r) on a fixed recall grid."""
    p, r, _ = precision_recall_curve(labels, scores)
    return [{"recall": round(float(g), 3), "precision": round(float(p[r >= g].max()), 4)}
            for g in np.linspace(0, 1, points)]


def evaluate_rules(streams) -> dict:
    per, clean_fp, clean_n = [], 0, 0
    for st in streams:
        contract = infer_contract(st["ref"], st["schema"])
        for df, labels, is_clean in ((st["rule_df"], st["rule_labels"], False),
                                     (st["cur"], np.full(len(st["cur"]), ""), True)):
            raw = _roundtrip(df)
            typed, failures = coerce(raw, st["schema"])
            _, _, flagged = run_checks(typed, raw, st["schema"], contract, [REVENUE_RULE], failures, None)
            pred = np.zeros(len(df), bool)
            pred[list(flagged)] = True
            if is_clean:
                clean_fp += int(pred.sum())
                clean_n += len(pred)
                continue
            y = labels != ""
            per.append({f: float(pred[labels == f].mean()) for f in sorted(set(labels) - {""})}
                       | {"_precision": float(y[pred].mean()) if pred.any() else 0.0})
    faults = sorted(k for k in per[0] if not k.startswith("_"))
    return {"recall_by_fault": {f: float(np.mean([p[f] for p in per])) for f in faults},
            "precision": float(np.mean([p["_precision"] for p in per])),
            "flagged_rows_on_clean_data": clean_fp, "clean_rows": clean_n}


def main():
    t0 = time.time()
    print("building streams...")
    val = [build_stream(s) for s in VAL_SEEDS]
    test = [build_stream(s) for s in TEST_SEEDS]
    print("scoring...")
    val_runs = [score_stream(s) for s in val]
    test_runs = [score_stream(s) for s in test]

    report = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
              "protocol": {"validation_seeds": VAL_SEEDS, "test_seeds": TEST_SEEDS,
                           "baseline_days": REF_DAYS, "scored_days": CUR_DAYS},
              "levels": {}}
    selection = {}
    for level, registry in (("row", ROW_DETECTORS), ("period", PERIOD_DETECTORS)):
        rows = []
        for name, (space, _, family) in registry.items():
            vs, vy, vf = _pool(val_runs, level, name)
            th = best_threshold(vs, vy)
            val_ap = float(average_precision_score(vy, vs))
            per_stream = [metrics_at(r[level][name]["scores"], r[level][name]["labels"],
                                     r[level][name]["faults"], th) for r in test_runs]
            ts, ty, tf = _pool(test_runs, level, name)
            pooled = metrics_at(ts, ty, tf, th)
            secs = sum(r[level][name]["seconds"] for r in test_runs)
            n = sum(r[level][name]["n"] for r in test_runs)
            rows.append({"detector": name, "family": family, "features": space, "threshold": th,
                         "validation_average_precision": val_ap,
                         "test": pooled, "test_by_stream": _summary(per_stream),
                         "seconds_per_10k": secs / n * 10_000, "pr_curve": _curve(ts, ty)})
            print(f"{level:6s} {name:28s} valAP={val_ap:.3f} testAP={pooled['average_precision']:.3f} "
                  f"P={pooled['precision']:.3f} R={pooled['recall']:.3f} FPR={pooled['fpr']:.4f}")
        best = max(rows, key=lambda r: r["validation_average_precision"])
        stat = max((r for r in rows if r["family"] == "statistical"), key=lambda r: r["validation_average_precision"])
        ts, ty, _ = _pool(test_runs, level, best["detector"])
        report["levels"][level] = {
            "candidates": rows, "selected": best["detector"], "best_statistical": stat["detector"],
            "test_positives": int(ty.sum()), "test_items": int(len(ty)),
        }
        selection[level] = {"detector": best["detector"], "threshold": best["threshold"]}
        if level == "period":
            # What the product actually ships: the selected model OR the Poisson volume test.
            name = best["detector"]
            per_stream = []
            for r in test_runs:
                d = r["period"][name]
                combo = np.maximum(d["scores"] >= best["threshold"], d["volume_alert"]).astype(float)
                per_stream.append(metrics_at(combo, d["labels"], d["faults"], 0.5))
            ts, ty, tf = _pool(test_runs, level, name)
            va = np.concatenate([r["period"][name]["volume_alert"] for r in test_runs])
            combo = np.maximum(ts >= best["threshold"], va).astype(float)
            ex = np.concatenate([r["period"][name]["expected"] for r in test_runs])
            bins = [(0, 10), (10, 20), (20, 40), (40, 10**9)]
            by_size = [{"expected_rows": f"{lo}-{hi}" if hi < 10**9 else f"{lo}+",
                        "volume_drops": int(((tf == "volume_drop") & (ex >= lo) & (ex < hi)).sum()),
                        "recall": float(combo[(tf == "volume_drop") & (ex >= lo) & (ex < hi)].mean())
                        if ((tf == "volume_drop") & (ex >= lo) & (ex < hi)).any() else None}
                       for lo, hi in bins]
            report["levels"][level]["shipped"] = {
                "volume_drop_recall_by_segment_size": by_size,
                "description": f"{name} OR Poisson volume test",
                "test": {k: v for k, v in metrics_at(combo, ty, tf, 0.5).items()
                         if k not in ("average_precision", "roc_auc")},
                "test_by_stream": {k: v for k, v in _summary(per_stream).items()
                                   if k not in ("average_precision", "roc_auc")},
            }
    report["rules"] = evaluate_rules(test)
    report["runtime_seconds"] = round(time.time() - t0, 1)
    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / "report.json").write_text(json.dumps(report, indent=1))
    (ARTIFACTS / "selection.json").write_text(json.dumps(selection, indent=1))
    print(json.dumps(selection, indent=1), json.dumps(report["rules"], indent=1))
    print(f"done in {report['runtime_seconds']}s")


if __name__ == "__main__":
    main()
