import numpy as np
import pandas as pd
import pytest

from sentinel.engine.analyze import analyze, build_baseline, lab_scorecard
from sentinel.engine.checks import RuleError, compile_formula, psi
from sentinel.engine.faults import inject
from sentinel.engine.generator import generate_orders
from sentinel.engine.schema import CsvError, coerce, infer_schema, read_csv


def raw_of(df):
    return read_csv(df.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S").encode(), 10**6)


def test_generator_is_reproducible_and_streams_continue():
    a, b = generate_orders(5, days=10), generate_orders(5, days=10)
    assert a.equals(b)
    later = generate_orders(5, days=10, day_offset=10)
    assert not set(a["order_id"]) & set(later["order_id"])
    assert pd.Timestamp(later["order_ts"].min()) > pd.Timestamp(a["order_ts"].max()) - pd.Timedelta(days=1)


def test_schema_roles_and_na_is_a_value():
    raw = raw_of(generate_orders(1, days=20))
    s = infer_schema(raw)
    roles = {c["name"]: c["role"] for c in s["columns"]}
    assert roles["order_id"] == "id" and roles["unit_price"] == "numeric" and roles["order_ts"] == "datetime"
    assert s["key"] == "order_id" and s["time"] == "order_ts" and s["ingest_time"] == "ingested_at"
    # "NA" is a region code (or an answer), not a missing value.
    assert read_csv(b"region,x\nNA,1\nEU,2\n", 10)["region"].tolist() == ["NA", "EU"]


@pytest.mark.parametrize("payload,msg", [(b"\x00\x01\x02", "binary"), (b"a,b\n", "no rows"),
                                         ("é,b\n1,2".encode("latin-1"), "UTF-8")])
def test_read_csv_rejects_bad_files(payload, msg):
    with pytest.raises(CsvError, match=msg):
        read_csv(payload, 100)


def test_read_csv_row_limit_and_header_cleanup():
    with pytest.raises(CsvError, match="Too many rows"):
        read_csv(b"a\n1\n2\n3\n", 2)
    assert list(read_csv(b" a ,a,  \n1,2,3\n", 10).columns) == ["a", "a_2", "column"]


def test_formula_rules_are_sandboxed():
    df = pd.DataFrame({"q": [1, 2], "p": [3.0, 4.0], "r": [3.0, 9.0]})
    ok = compile_formula("abs(r - q * p) <= 0.01", list(df.columns))(df)
    assert ok.tolist() == [True, False]
    for evil in ["__import__('os').system('true')", "q.__class__ == 1", "open('x') == 1", "q", "q < p < r",
                 "unknown > 1", "[1][0] == 1"]:
        with pytest.raises(RuleError):
            compile_formula(evil, list(df.columns))


def test_psi_detects_shift_not_noise():
    rng = np.random.default_rng(0)
    from sentinel.engine.checks import dist_of
    ref = dist_of(pd.Series(rng.normal(0, 1, 5000)), {"kind": "numeric", "edges": np.linspace(-3, 3, 11).tolist()})
    assert psi(pd.Series(rng.normal(0, 1, 5000)), ref) < 0.05
    assert psi(pd.Series(rng.normal(1, 1, 5000)), ref) > 0.25


def test_injection_labels_match_changes():
    raw = raw_of(generate_orders(2, days=30))
    schema = infer_schema(raw)
    df, _ = coerce(raw, schema)
    out, truth, labels = inject(df, schema, [
        {"type": "unit_error", "columns": ["unit_price"], "factor": 100, "rate": 0.01},
        {"type": "duplicates", "rate": 0.02},
        {"type": "volume_drop", "segment": "web · EMEA", "days": ["2026-01-10", "2026-01-10"], "rate": 1.0},
    ], seed=1)
    pos = truth["rows"]["unit_error"]
    assert len(pos) == round(len(df) * 0.01)
    assert (out.loc[pos, "unit_price"] > 400).all()  # cheapest SKU is ~4.99 before the x100
    assert 0 < round(len(df) * 0.02) - len(truth["rows"]["duplicates"]) <= 5  # a few copies fell in the dropped slice
    assert {"segment": "web · EMEA", "day": "2026-01-10", "fault": "volume_drop"} in truth["periods"]
    assert len(labels) == len(out)


@pytest.fixture(scope="module")
def baseline():
    raw = raw_of(generate_orders(9, days=60, base_daily=25))
    return raw, build_baseline(raw)


def test_baseline_trains_both_models(baseline):
    _, b = baseline
    assert b["row_model"] is not None and b["period_model"] is not None and not b["notes"]
    assert any(r["type"] == "min" and r["column"] == "quantity" for r in b["contract"]["rules"])


def test_analysis_finds_injected_problems_and_explains_them(baseline):
    _, b = baseline
    cur = raw_of(generate_orders(9, days=21, base_daily=25, day_offset=60))
    typed, _ = coerce(cur, b["schema"])
    seg_day = ("web · NORAM", str(pd.Timestamp(typed["order_ts"].min()).date() + pd.Timedelta(days=5)))
    faulty, truth, _ = inject(typed, b["schema"], [
        {"type": "volume_drop", "segment": seg_day[0], "days": [seg_day[1]] * 2, "rate": 0.95},
        {"type": "unit_error", "columns": ["unit_price", "revenue"], "factor": 100, "rate": 0.003},
        {"type": "sign_flip", "column": "quantity", "rate": 0.002},
        {"type": "drop_column", "column": "currency"},
    ], seed=4)
    res = analyze(raw_of(faulty), b["schema"], b["contract"], [], b["profile"], b["row_model"], b["period_model"], False)
    kinds = {f["kind"] for f in res["findings"]}
    assert {"schema", "contract", "record_anomaly", "feed_anomaly"} <= kinds
    card = lab_scorecard(truth, res)
    rows = {r["fault"]: r for r in card["rows"]}
    assert rows["unit_error"]["by_model"] == rows["unit_error"]["injected"] > 0
    assert rows["sign_flip"]["by_rules"] == rows["sign_flip"]["injected"]
    assert all(p["detected"] for p in card["periods"] if p["fault"] == "volume_drop")
    ex = res["records"]["examples"][0]["reasons"][0]
    assert ex["ratio"] and ex["ratio"] > 20 and ex["peers"]["rows"] > 0


def test_clean_data_raises_few_alarms(baseline):
    _, b = baseline
    cur = raw_of(generate_orders(9, days=21, base_daily=25, day_offset=60))
    res = analyze(cur, b["schema"], b["contract"], [], b["profile"], b["row_model"], b["period_model"], False)
    assert res["summary"]["rule_flagged_rows"] == 0
    assert res["summary"]["model_flagged_rows"] / len(cur) < 0.01
    assert not [f for f in res["findings"] if f["severity"] in ("high", "critical")]


def test_dropped_context_column_is_reported_not_crashing(baseline):
    _, b = baseline
    cur = raw_of(generate_orders(9, days=21, base_daily=25, day_offset=60)).drop(columns=["sku"])
    res = analyze(cur, b["schema"], b["contract"], [], b["profile"], b["row_model"], b["period_model"], False)
    assert res["findings"][0]["kind"] == "schema" and res["findings"][0]["severity"] == "critical"
    assert "records" not in res and any("Record model skipped" in n for n in res["notes"])
    assert "feed" not in res  # the feed model's mix adjustment also uses sku
