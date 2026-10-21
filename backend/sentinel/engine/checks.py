"""Deterministic checks: where a rule is exact, no model is needed.

A data contract is inferred from the baseline version (what was true then) and
every later version is validated against it, plus user-defined formula rules.
"""
from __future__ import annotations

import ast
import operator

import numpy as np
import pandas as pd

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_CMP = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
        ast.Eq: operator.eq}


class RuleError(ValueError):
    pass


def compile_formula(expr: str, columns: list[str]):
    """Parse a tiny, safe expression language, e.g. `abs(revenue - quantity * unit_price * (1 - discount)) <= 0.01`.

    Only column names, numbers, + - * /, abs() and one comparison are allowed;
    nothing is ever passed to eval().
    """
    try:
        tree = ast.parse(expr, mode="eval").body
    except SyntaxError:
        raise RuleError("Formula is not valid syntax.")
    if not isinstance(tree, ast.Compare) or len(tree.ops) != 1 or type(tree.ops[0]) not in _CMP:
        raise RuleError("Formula must be a single comparison like `a <= b`.")

    def build(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return lambda df: node.value
        if isinstance(node, ast.Name):
            if node.id not in columns:
                raise RuleError(f"Unknown column `{node.id}`.")
            return lambda df: df[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            f, a, b = _OPS[type(node.op)], build(node.left), build(node.right)
            return lambda df: f(a(df), b(df))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            a = build(node.operand)
            return lambda df: -a(df)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "abs"
                and len(node.args) == 1 and not node.keywords):
            a = build(node.args[0])
            return lambda df: abs(a(df))
        raise RuleError("Only columns, numbers, + - * /, and abs() are allowed.")

    left, right, cmp = build(tree.left), build(tree.comparators[0]), _CMP[type(tree.ops[0])]
    names = sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in columns})

    def check(df):  # rows missing an input are "not checkable", not violations (missing values are reported separately)
        ok = cmp(left(df), right(df))
        return ok | df[names].isna().any(axis=1)
    return check


def infer_contract(df: pd.DataFrame, schema: dict) -> dict:
    """Rules that held on the baseline version."""
    rules = []
    for col in schema["columns"]:
        name, role = col["name"], col["role"]
        s = df[name]
        if s.notna().all():
            rules.append({"type": "not_null", "column": name})
        if role == "numeric" and s.notna().any() and s.min() >= 0:
            rules.append({"type": "min", "column": name, "value": 0})
        if role == "categorical" and col["nunique"] <= 50:
            rules.append({"type": "allowed_values", "column": name,
                          "values": sorted(map(str, s.dropna().unique()))})
    return {"rules": rules}


def _rule_mask(df: pd.DataFrame, rule: dict) -> pd.Series | None:
    """Boolean mask of violating rows, or None if the rule can't run (column missing)."""
    col = rule.get("column")
    if col and col not in df:
        return None
    t = rule["type"]
    if t == "not_null":
        return df[col].isna()
    if t == "min":
        return pd.to_numeric(df[col], errors="coerce") < rule["value"]
    if t == "allowed_values":
        return df[col].notna() & ~df[col].astype(str).isin(rule["values"])
    if t == "formula":
        try:
            ok = compile_formula(rule["expr"], list(df.columns))(df)
        except RuleError:
            return None
        return ~ok.astype(bool)
    raise RuleError(f"Unknown rule type {t}")


def describe_rule(rule: dict) -> str:
    t, c = rule["type"], rule.get("column")
    return {
        "not_null": f"`{c}` is never empty",
        "min": f"`{c}` ≥ {rule.get('value')}",
        "allowed_values": f"`{c}` is one of {len(rule.get('values', []))} known values",
        "formula": f"`{rule.get('expr')}`",
    }.get(t, t)


def run_checks(df: pd.DataFrame, raw: pd.DataFrame, schema: dict, contract: dict,
               user_rules: list[dict], parse_failures: dict, baseline_profile: dict | None) -> tuple[list, list, set]:
    """Returns (checks, findings, flagged_row_positions)."""
    checks, findings, flagged = [], [], set()
    n = len(df)

    def finding(kind, key, severity, title, summary, evidence):
        findings.append({"kind": kind, "fingerprint": f"{kind}:{key}", "severity": severity,
                         "title": title, "summary": summary, "evidence": evidence})

    # Schema: columns that disappeared, appeared, or no longer parse.
    expected = [c["name"] for c in schema["columns"]]
    missing_cols = [c for c in expected if c not in df]
    new_cols = [c for c in df.columns if c not in expected]
    checks.append({"name": "Schema matches baseline", "passed": not missing_cols and not new_cols})
    if missing_cols:
        finding("schema", "missing:" + ",".join(missing_cols), "critical",
                f"Column{'s' if len(missing_cols) > 1 else ''} missing: {', '.join(missing_cols)}",
                "Columns present in the baseline are absent from this version. Downstream "
                "reports that read them will break or silently show blanks.",
                {"missing": missing_cols, "added": new_cols})
    elif new_cols:
        finding("schema", "added:" + ",".join(new_cols), "low", f"New columns: {', '.join(new_cols)}",
                "Columns not in the baseline appeared. They are not monitored until a new baseline is set.",
                {"added": new_cols})
    for col, bad in parse_failures.items():
        role = next(c["role"] for c in schema["columns"] if c["name"] == col)
        mask = raw[col].notna() & df[col].isna()
        examples = raw.loc[mask, col].value_counts().head(5)
        flagged.update(np.flatnonzero(mask))
        finding("type", col, "high" if bad / n > 0.01 else "medium",
                f"{bad:,} values in `{col}` are not {role}",
                f"`{col}` was {role} in the baseline; {bad:,} values ({bad / n:.2%}) failed to parse "
                "and were treated as missing.",
                {"column": col, "count": bad, "share": bad / n,
                 "examples": [{"value": str(k), "count": int(v)} for k, v in examples.items()]})
    checks.append({"name": "All typed columns parse", "passed": not parse_failures})

    # Contract and user rules.
    for rule in contract["rules"] + user_rules:
        mask = _rule_mask(df, rule)
        if mask is None:
            continue
        bad = int(mask.sum())
        checks.append({"name": describe_rule(rule), "passed": bad == 0})
        if not bad:
            continue
        pos = np.flatnonzero(mask.to_numpy())
        flagged.update(pos)
        sample = raw.iloc[pos[:5]]
        cols = [c for c in [schema.get("key"), rule.get("column")] if c and c in raw] or list(raw.columns[:4])
        if rule["type"] == "formula":
            cols = [c for c in raw.columns if c in rule["expr"] or c == schema.get("key")]
        cols = list(dict.fromkeys(cols))
        share = bad / n
        sev = {"not_null": "high" if share > 0.01 else "medium",
               "formula": "high" if share > 0.005 else "medium"}.get(rule["type"], "medium")
        finding("contract", f"{rule['type']}:{rule.get('column') or rule.get('expr')}", sev,
                f"{bad:,} rows break rule: {describe_rule(rule)}",
                f"This rule held on every row of the baseline. {bad:,} rows ({share:.2%}) now violate it.",
                {"rule": rule, "count": bad, "share": share,
                 "examples": _records(sample[cols])})

    # Duplicates: by record key when there is one (exact copies are a subset), else whole rows.
    key = schema.get("key")
    exact = df.duplicated(keep="first")
    if key and key in df:
        dup = df[key].notna() & df[key].duplicated(keep="first")
        name, title = f"`{key}` is unique", f"repeated values of `{key}`"
        text = (f"`{key}` identifies a record, but {{n:,}} rows ({{s:.2%}}) reuse an earlier value; "
                f"{int((dup & exact).sum()):,} of them are exact copies of the whole row, the typical "
                "signature of a retried load. Totals computed from this version are overstated.")
    else:
        dup = exact
        name, title = "No exact duplicate records", "exact duplicate records"
        text = ("{n:,} rows ({s:.2%}) are copies of an earlier row, the typical signature of a "
                "retried load. Totals computed from this version are overstated.")
    nd = int(dup.sum())
    checks.append({"name": name, "passed": nd == 0})
    if nd:
        flagged.update(np.flatnonzero(dup.to_numpy()))
        finding("duplicates", key or "exact", "high" if nd / n > 0.01 else "medium",
                f"{nd:,} {title}", text.format(n=nd, s=nd / n),
                {"column": key, "count": nd, "share": nd / n, "exact_copies": int((dup & exact).sum()),
                 "examples": _records(raw[dup.to_numpy()].head(3))})

    if not baseline_profile:  # first version: no history to compare with
        for col in df.columns:
            rate = float(df[col].isna().mean())
            if rate >= 0.5:
                finding("missing", col, "low", f"`{col}` is {rate:.0%} empty",
                        "Most values in this column are empty. If it should be populated, check the source.",
                        {"column": col, "rate": rate, "baseline_rate": None})
        return checks, findings, flagged

    # Missing-value rate vs baseline.
    if baseline_profile:
        for col, base_rate in baseline_profile["null_rate"].items():
            if col not in df:
                continue
            rate = float(df[col].isna().mean())
            if rate - base_rate >= 0.05 and rate >= 2 * base_rate:
                checks.append({"name": f"`{col}` missing rate stable", "passed": False})
                finding("missing", col, "high" if rate - base_rate >= 0.2 else "medium",
                        f"`{col}` is {rate:.1%} empty (baseline {base_rate:.1%})",
                        "The share of empty values rose sharply versus the baseline.",
                        {"column": col, "rate": rate, "baseline_rate": base_rate})

        # Distribution drift (population stability index) on numeric and categorical columns.
        for col, ref in baseline_profile["dist"].items():
            if col not in df:
                continue
            value = psi(df[col], ref)
            if value is not None and value >= 0.25:
                finding("drift", col, "medium" if value < 1 else "high",
                        f"Distribution of `{col}` shifted (PSI {value:.2f})",
                        "The value distribution differs materially from the baseline "
                        "(PSI ≥ 0.25 is a common threshold for a significant shift). "
                        "This may be a real business change rather than a data error.",
                        {"column": col, "psi": value, "baseline": ref, "current": dist_of(df[col], ref)})
    return checks, findings, flagged


def _records(frame: pd.DataFrame) -> list[dict]:
    return frame.astype(object).where(frame.notna(), None).to_dict("records")


def profile(df: pd.DataFrame, schema: dict) -> dict:
    """Baseline statistics that later versions are compared to."""
    dist = {}
    for col in schema["columns"]:
        name = col["name"]
        if col["role"] == "numeric" and df[name].notna().sum() > 20:
            edges = np.unique(np.nanquantile(df[name].astype(float), np.linspace(0, 1, 11)))
            if len(edges) > 2:
                dist[name] = dist_of(df[name], {"kind": "numeric", "edges": edges.tolist()})
        elif col["role"] == "categorical":
            top = df[name].astype(str).value_counts().head(20).index.tolist()
            dist[name] = dist_of(df[name], {"kind": "categorical", "values": top})
    return {
        "rows": len(df),
        "null_rate": {c: float(df[c].isna().mean()) for c in df.columns},
        "dist": dist,
    }


def dist_of(s: pd.Series, ref: dict) -> dict:
    s = s.dropna()
    if ref["kind"] == "numeric":
        x = pd.to_numeric(s, errors="coerce").dropna().to_numpy()
        e = np.array(ref["edges"], dtype=float)
        counts = np.histogram(np.clip(x, e[0], e[-1]), bins=e)[0] if len(x) else np.zeros(len(e) - 1)
        return {**ref, "shares": (counts / max(len(x), 1)).tolist()}
    vc = s.astype(str).value_counts(normalize=True)
    shares = [float(vc.get(v, 0.0)) for v in ref["values"]]
    return {**ref, "shares": shares + [max(0.0, 1 - sum(shares))]}


def psi(s: pd.Series, ref: dict) -> float | None:
    cur = dist_of(s, ref)["shares"]
    if not s.notna().any():
        return None
    a, b = np.array(ref["shares"]) + 1e-4, np.array(cur) + 1e-4
    return float(np.sum((b - a) * np.log(b / a)))
