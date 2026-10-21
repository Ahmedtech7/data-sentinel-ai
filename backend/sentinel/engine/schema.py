"""Column role inference and type coercion for arbitrary CSVs."""
from __future__ import annotations

import io
import re

import pandas as pd

MAX_COLUMNS = 200
TIME_HINT = re.compile(r"(order|event|created|date|time|ts|timestamp)", re.I)


class CsvError(ValueError):
    pass


def read_csv(raw: bytes, max_rows: int) -> pd.DataFrame:
    """Parse untrusted CSV bytes as strings. Raises CsvError with a user-facing message."""
    if b"\x00" in raw[:65536]:
        raise CsvError("File looks binary, not CSV text.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CsvError("File must be UTF-8 encoded.")
    try:
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False,
                         na_values=["", "N/A", "n/a", "#N/A", "null", "NULL", "None", "NaN", "nan"],
                         nrows=max_rows + 1)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as e:
        raise CsvError(f"Could not parse CSV: {str(e).splitlines()[0]}")
    if len(df) > max_rows:
        raise CsvError(f"Too many rows (limit {max_rows:,}).")
    if df.shape[1] > MAX_COLUMNS:
        raise CsvError(f"Too many columns (limit {MAX_COLUMNS}).")
    if df.empty:
        raise CsvError("CSV has a header but no rows.")
    cols, seen = [], set()
    for c in df.columns:
        name = re.sub(r"\s+", " ", str(c)).strip()[:64] or "column"
        base, k = name, 2
        while name in seen:
            name, k = f"{base}_{k}", k + 1
        seen.add(name)
        cols.append(name)
    df.columns = cols
    return df


def _parse_numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _parse_datetime(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", format="mixed", utc=True)


def infer_schema(df: pd.DataFrame) -> dict:
    """Assign each column a role: id, datetime, numeric, categorical, text."""
    n = len(df)
    columns = []
    for c in df.columns:
        s = df[c].dropna()
        nunique = s.nunique()
        role = "text"
        if len(s) == 0:
            role = "text"
        elif _parse_numeric(s).notna().mean() >= 0.95:
            is_int_like = re.search(r"(^|_)(id|key|code)$", c, re.I)
            role = "id" if is_int_like and nunique / len(s) > 0.9 else "numeric"
        elif s.str.contains(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}", regex=True).mean() >= 0.95 \
                and _parse_datetime(s.head(500)).notna().mean() >= 0.95:
            role = "datetime"
        elif nunique / len(s) > 0.9 and len(s) > 50:
            role = "id"
        elif nunique <= max(50, int(0.05 * n)) and nunique <= 1000:
            role = "categorical"
        columns.append({"name": c, "role": role, "nunique": int(nunique)})

    dts = [c["name"] for c in columns if c["role"] == "datetime"]
    primary_time = next((d for d in dts if TIME_HINT.search(d)), dts[0] if dts else None)
    ingest_time = next((d for d in dts if d != primary_time and re.search(r"ingest|load|arriv|received", d, re.I)), None)
    ids = [c for c in columns if c["role"] == "id"]
    key = max(ids, key=lambda c: c["nunique"])["name"] if ids else None
    cats = [c for c in columns if c["role"] == "categorical"]
    # Segments: low-cardinality dimensions that split the feed into sub-streams.
    segments = [c["name"] for c in sorted(cats, key=lambda c: c["nunique"]) if 2 <= c["nunique"] <= 12][:2]
    context = [c["name"] for c in cats if 2 <= c["nunique"] <= 200][:6]
    numeric = [c["name"] for c in columns if c["role"] == "numeric"]
    return {"columns": columns, "time": primary_time, "ingest_time": ingest_time, "key": key,
            "segments": segments, "context": context, "numeric": numeric}


def coerce(df: pd.DataFrame, schema: dict) -> tuple[pd.DataFrame, dict]:
    """Typed copy of df per schema, plus count of values that failed to parse per column."""
    out = df.copy()
    failures = {}
    for col in schema["columns"]:
        name = col["name"]
        if name not in out:
            continue
        if col["role"] == "numeric":
            parsed = _parse_numeric(out[name])
        elif col["role"] == "datetime":
            parsed = _parse_datetime(out[name])
        else:
            continue
        bad = int((parsed.isna() & out[name].notna()).sum())
        if bad:
            failures[name] = bad
        out[name] = parsed
    return out, failures


def segment_key(df: pd.DataFrame, schema: dict) -> pd.Series:
    segs = [df[s].fillna("∅").astype(str) for s in schema["segments"] if s in df]
    if not segs:
        return pd.Series("all", index=df.index)
    return segs[0].str.cat(segs[1:], sep=" · ") if len(segs) > 1 else segs[0]


def day_of(df: pd.DataFrame, schema: dict) -> pd.Series | None:
    t = schema.get("time")
    if not t or t not in df or not pd.api.types.is_datetime64_any_dtype(df[t]):
        return None
    return df[t].dt.tz_convert(None).dt.floor("D")
