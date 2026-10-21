"""Anomaly models learned from a dataset's baseline ("what normal looks like").

Residualizer — for every numeric column, a gradient-boosted model learns the
               expected value *and* the expected spread given the record's
               categorical context (SKU, channel, region...). A record is then
               described by how far each field sits from its expectation.
RowModel     — scores individual records.
PeriodModel  — scores each (segment, day) of the feed: volume vs. same weekday,
               completeness, duplication, latency, and mix-adjusted level of
               numeric columns (median residual, so a change in product mix is
               not mistaken for a price change).

Each model can run one of several detectors so the offline evaluation compares
statistical baselines and ML on the same data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.ensemble import HistGradientBoostingRegressor, IsolationForest
from sklearn.model_selection import cross_val_predict
from sklearn.neighbors import LocalOutlierFactor

from .schema import day_of, segment_key

MIN_GROUP = 30
MIN_PERIOD_ROWS = 5
Z_CLIP = 50.0
MAX_NUMERIC = 8


def _slog(x):
    return np.sign(x) * np.log1p(np.abs(x))


def _unslog(y):
    return np.sign(y) * np.expm1(np.abs(y))


def _robust(x: np.ndarray) -> tuple[float, float]:
    x = x[~np.isnan(x)]
    if len(x) == 0:
        return 0.0, 1.0
    med = float(np.median(x))
    scale = 1.4826 * float(np.median(np.abs(x - med)))
    if scale < 1e-9:
        scale = float(np.std(x))
    return med, max(scale, 1e-6)


def _num(v):
    return None if v is None or pd.isna(v) else float(v)


def _clip(X):
    return np.clip(np.nan_to_num(X, nan=0.0, posinf=Z_CLIP, neginf=-Z_CLIP), -Z_CLIP, Z_CLIP)


# ---------------------------------------------------------------- detectors
class MaxAbs:
    """Score = largest absolute standardized deviation (features are already standardized)."""
    def fit(self, X):
        return self

    def score(self, X):
        return np.abs(X).max(axis=1)


class CalibratedMaxAbs:
    """Max over features of |z| / (99.5th percentile of |z| on the baseline).

    Puts every field on the scale of its own normal behaviour: a deviation that the
    baseline shows 1 time in 200 (e.g. a legitimate discount on a mostly-undiscounted
    channel) scores ~1, a value far beyond anything seen scores >> 1.
    """
    def fit(self, X):
        self.q = np.maximum(np.quantile(np.abs(X), 0.995, axis=0), 1.0)
        return self

    def score(self, X):
        return (np.abs(X) / self.q).max(axis=1)


class MeanStdMaxAbs:
    """Classic z-score with mean/std learned from the reference, max over features."""
    def fit(self, X):
        self.mu, self.sd = X.mean(axis=0), X.std(axis=0) + 1e-9
        return self

    def score(self, X):
        return np.abs((X - self.mu) / self.sd).max(axis=1)


class IQRRule:
    """Tukey fences: distance outside [Q1, Q3] in IQR units, max over features."""
    def fit(self, X):
        self.q1, self.q3 = np.percentile(X, 25, axis=0), np.percentile(X, 75, axis=0)
        self.iqr = np.maximum(self.q3 - self.q1, 1e-9)
        return self

    def score(self, X):
        return ((np.maximum(self.q1 - X, 0) + np.maximum(X - self.q3, 0)) / self.iqr).max(axis=1)


class IForest:
    def __init__(self, seed=0):
        self.m = IsolationForest(n_estimators=300, max_samples=256, random_state=seed, n_jobs=1)

    def fit(self, X):
        self.m.fit(X)
        return self

    def score(self, X):
        return -self.m.score_samples(X)  # higher = more anomalous


class LOF:
    def __init__(self, seed=0, max_fit=8000):
        self.seed, self.max_fit = seed, max_fit
        self.m = LocalOutlierFactor(n_neighbors=35, novelty=True)

    def fit(self, X):
        if len(X) > self.max_fit:
            X = X[np.random.default_rng(self.seed).choice(len(X), self.max_fit, replace=False)]
        self.m.fit(X)
        return self

    def score(self, X):
        return -self.m.score_samples(X)  # local outlier factor, ~1 = normal density


class Ensemble:
    """Max of member scores, each divided by its own 99.5th percentile on the baseline.

    Members see different kinds of anomaly (LOF: unusual *combinations*; max-z: one
    extreme metric), and the baseline percentile puts them on a common scale
    without using any labels.
    """
    def __init__(self, members):
        self.members = members

    def fit(self, X):
        self.q = [max(np.quantile(m.fit(X).score(X), 0.995), 1e-9) for m in self.members]
        return self

    def score(self, X):
        return np.max([m.score(X) / q for m, q in zip(self.members, self.q)], axis=0)


# name -> (feature space, detector factory, family)
ROW_DETECTORS = {
    "zscore_global": ("raw", lambda s: MeanStdMaxAbs(), "statistical"),
    "iqr_global": ("raw", lambda s: IQRRule(), "statistical"),
    "robust_z_group": ("group", lambda s: MaxAbs(), "statistical"),
    "isolation_forest_group": ("group", IForest, "ml"),
    "residual_z_max": ("residual", lambda s: MaxAbs(), "ml"),
    "residual_z_calibrated": ("residual", lambda s: CalibratedMaxAbs(), "ml"),
    "isolation_forest_residual": ("residual", IForest, "ml"),
    "lof_residual": ("residual", LOF, "ml"),
    "ensemble_residual": ("residual", lambda s: Ensemble([MaxAbs(), LOF(s)]), "ml"),
}
# "seasonal": weekday-adjusted volume + raw metric levels (no learned model).
# "learned": the same, but numeric levels are mix-adjusted residuals from the Residualizer.
PERIOD_DETECTORS = {
    "zscore_raw": ("raw", lambda s: MeanStdMaxAbs(), "statistical"),
    "robust_z_seasonal": ("seasonal", lambda s: MaxAbs(), "statistical"),
    "robust_z_learned": ("learned", lambda s: MaxAbs(), "ml"),
    "isolation_forest_learned": ("learned", IForest, "ml"),
    "lof_learned": ("learned", LOF, "ml"),
    "ensemble_learned": ("learned", lambda s: Ensemble([MaxAbs(), LOF(s)]), "ml"),
}


# ---------------------------------------------------------------- residualizer
class Residualizer:
    """Learns E[slog(x) | context] and E[|residual| | context] per numeric column."""

    def fit(self, df: pd.DataFrame, schema: dict, seed: int = 0) -> Residualizer:
        self.numeric = [c for c in schema["numeric"] if c in df][:MAX_NUMERIC]
        self.context = [c for c in schema["context"] if c in df]
        self.cats = {c: sorted(df[c].dropna().astype(str).unique()) for c in self.context}
        X = self._X(df)
        self.models, self.ref_z, self.ref_raw = {}, {}, {}  # out-of-fold residuals of the baseline
        for c in self.numeric:
            y = _slog(df[c].to_numpy(float))
            ok = ~np.isnan(y)
            med, sc = _robust(y)
            if not self.context or ok.sum() < 200:
                self.models[c] = (None, None, med, sc)
                self.ref_raw[c] = y - med
                self.ref_z[c] = (y - med) / sc
                continue
            mean_m = self._hgb(seed)
            oof = np.full(len(y), np.nan)
            oof[ok] = cross_val_predict(mean_m, X[ok], y[ok], cv=3)  # honest residuals for the reference
            mean_m.fit(X[ok], y[ok])
            r = np.abs(y - oof)
            spread_m = self._hgb(seed).fit(X[ok], r[ok])
            floor = 0.25 * _robust(y[ok] - oof[ok])[1] + 1e-3
            self.models[c] = (mean_m, spread_m, floor, None)
            self.ref_raw[c] = y - oof
            self.ref_z[c] = (y - oof) / np.maximum(1.2533 * spread_m.predict(X), floor)
        return self

    @staticmethod
    def _hgb(seed):
        return HistGradientBoostingRegressor(max_iter=120, learning_rate=0.1, max_leaf_nodes=31,
                                             min_samples_leaf=20, categorical_features="from_dtype",
                                             random_state=seed)

    def _X(self, df):
        return pd.DataFrame({c: pd.Categorical(df[c].astype(str) if c in df else [None] * len(df), categories=self.cats[c])
                             for c in self.context}, index=df.index) if self.context else None

    def expect(self, df: pd.DataFrame):
        """Returns (z, residual, expected, spread) arrays, shape (n, k)."""
        X = self._X(df)
        z, res, exp_, spr = [], [], [], []
        for c in self.numeric:
            y = _slog(df[c].to_numpy(float)) if c in df else np.full(len(df), np.nan)
            mean_m, spread_m, a, b = self.models[c]
            if mean_m is None:
                mu, s = np.full(len(df), a), np.full(len(df), b)
            else:
                mu, s = mean_m.predict(X), np.maximum(1.2533 * spread_m.predict(X), a)
            res.append(y - mu)
            z.append((y - mu) / s)
            exp_.append(mu)
            spr.append(s)
        return tuple(np.column_stack(v) for v in (z, res, exp_, spr))


# ---------------------------------------------------------------- row model
class RowModel:
    def __init__(self, detector: str = "lof_residual", threshold: float | None = None, seed: int = 0):
        self.detector, self.threshold, self.seed = detector, threshold, seed

    def fit(self, df: pd.DataFrame, schema: dict, residualizer: Residualizer | None = None) -> RowModel:
        self.numeric = [c for c in schema["numeric"] if c in df][:MAX_NUMERIC]
        self.context = [c for c in schema["context"] if c in df]
        if not self.numeric:
            raise ValueError("No numeric columns to model.")
        self.res = residualizer or Residualizer().fit(df, schema, self.seed)
        self.raw_med = {c: float(np.nanmedian(df[c].to_numpy(float))) for c in self.numeric}
        self.glob = {c: _robust(_slog(df[c].to_numpy(float))) for c in self.numeric}
        # Per-group statistics: a baseline for the group detectors and peer evidence for explanations.
        self.groups = {}
        for g in self.context:
            for val, sub in df.groupby(df[g].astype(str), sort=False):
                if len(sub) >= MIN_GROUP:
                    self.groups[(g, val)] = {c: (*_robust(_slog(sub[c].to_numpy(float))),
                                                 float(np.nanmedian(sub[c].to_numpy(float))), len(sub))
                                             for c in self.numeric}
        # For each column, the context whose groups are tightest is the most telling peer group.
        self.peer_col = {}
        for c in self.numeric:
            spread = {g: np.mean([s[c][1] for (gg, _), s in self.groups.items() if gg == g] or [np.inf])
                      for g in self.context}
            self.peer_col[c] = min(spread, key=spread.get) if spread else None
        space, make, _ = ROW_DETECTORS[self.detector]
        X = (_clip(np.column_stack([self.res.ref_z[c] for c in self.res.numeric]))
             if space == "residual" else self.features(df, space))
        self.det = make(self.seed).fit(X)
        self.train_scores = self.det.score(X)  # out-of-fold for residual detectors: honest scores for the baseline itself
        self.n_train = len(df)
        return self

    def features(self, df: pd.DataFrame, space: str | None = None) -> np.ndarray:
        space = space or ROW_DETECTORS[self.detector][0]
        if space == "residual":
            return _clip(self.res.expect(df)[0])
        cols = []
        for c in self.numeric:
            x = df[c].to_numpy(float) if c in df else np.full(len(df), np.nan)
            if space == "raw":
                cols.append(np.where(np.isnan(x), self.raw_med[c], x))
                continue
            t = _slog(x)
            med, sc = self.glob[c]
            cols.append((t - med) / sc)
            for g in self.context:  # space == "group"
                key = df[g].astype(str) if g in df else pd.Series("", index=df.index)
                stats = {v: s[c] for (gg, v), s in self.groups.items() if gg == g}
                gm = key.map({v: s[0] for v, s in stats.items()}).to_numpy(float)
                gs = key.map({v: s[1] for v, s in stats.items()}).to_numpy(float)
                z = (t - gm) / gs
                cols.append(np.where(np.isnan(z), (t - med) / sc, z))
        return _clip(np.column_stack(cols)) if space != "raw" else np.column_stack(cols)

    def score(self, df: pd.DataFrame) -> np.ndarray:
        return self.det.score(self.features(df))

    def explain(self, df: pd.DataFrame, positions, top: int = 3) -> list[dict]:
        """Per record: which fields deviate from the learned expectation, with peer evidence from the baseline."""
        positions = list(positions)
        if not positions:
            return []
        sub = df.iloc[positions]
        z, _, mu, spread = self.res.expect(sub)
        out = []
        for i, p in enumerate(positions):
            row = sub.iloc[i]
            reasons = []
            for j in np.argsort(-np.abs(np.nan_to_num(z[i]))):
                if reasons and abs(z[i, j]) < 3 or len(reasons) >= top:
                    break
                c = self.numeric[j]
                val = row[c]
                if pd.isna(val):
                    continue
                expected = float(_unslog(mu[i, j]))
                lo, hi = (float(_unslog(mu[i, j] + k * spread[i, j])) for k in (-2, 2))
                g = self.peer_col.get(c)
                peers = self.groups.get((g, str(row.get(g)))) if g else None
                reasons.append({
                    "column": c, "value": float(val), "expected": expected, "range": [lo, hi],
                    "z": round(float(z[i, j]), 2),
                    "ratio": float(val) / expected if expected else None,
                    "peers": {"column": g, "value": str(row[g]), "rows": int(peers[c][3]),
                              "median": peers[c][2]} if peers else
                             {"column": None, "value": None, "rows": self.n_train, "median": self.raw_med[c]},
                })
            out.append({"position": int(p), "reasons": reasons,
                        "context": {g: (None if pd.isna(row.get(g)) else str(row.get(g))) for g in self.context[:4]}})
        return out


# ---------------------------------------------------------------- period model
def period_table(df: pd.DataFrame, schema: dict, numeric: list[str], resid: np.ndarray | None,
                 segments=None, days=None, expected: np.ndarray | None = None) -> pd.DataFrame:
    """One row per (segment, day) with feed-health metrics. Missing segment-days appear with count 0."""
    day = day_of(df, schema)
    if day is None:
        raise ValueError("No timestamp column; period monitoring needs one.")
    g = pd.DataFrame({"segment": segment_key(df, schema).to_numpy(), "day": day.to_numpy()})
    g["nulls"] = df.isna().mean(axis=1).to_numpy()
    key = schema.get("key")
    g["dup"] = (df[key].duplicated(keep="first") if key in df else df.duplicated(keep="first")).to_numpy()
    ing, t0 = schema.get("ingest_time"), schema.get("time")
    if ing and ing in df and pd.api.types.is_datetime64_any_dtype(df[ing]):
        g["lag_h"] = ((df[ing] - df[t0]).dt.total_seconds() / 3600).to_numpy()
    for j, c in enumerate(numeric):
        g["med_" + c] = g["avg_" + c] = df[c].to_numpy(float) if c in df else np.nan
        if resid is not None:
            # Clipped mean, not median: integer columns (quantity 1 vs 2) make medians jump,
            # while clipping keeps one extreme row from moving the day's level.
            g["res_" + c] = np.clip(resid[:, j], -1, 1)
        if expected is not None:
            g["exp_" + c] = _unslog(expected[:, j])
    g = g.dropna(subset=["day"])
    agg = {"nulls": "mean", "dup": "mean", **{c: "median" for c in g.columns if c.startswith(("med_", "lag_h"))},
           **{c: "mean" for c in g.columns if c.startswith(("res_", "avg_", "exp_"))}}
    t = g.groupby(["segment", "day"]).agg(agg)
    t["count"] = g.groupby(["segment", "day"]).size()
    segments = segments if segments is not None else sorted(g["segment"].unique())
    days = days if days is not None else pd.date_range(g["day"].min(), g["day"].max(), freq="D")
    t = t.reindex(pd.MultiIndex.from_product([segments, days], names=["segment", "day"]))
    t["count"] = t["count"].fillna(0)
    return t.reset_index()


class PeriodModel:
    def __init__(self, detector: str = "lof_learned", threshold: float | None = None, seed: int = 0):
        self.detector, self.threshold, self.seed = detector, threshold, seed

    def fit(self, df: pd.DataFrame, schema: dict, residualizer: Residualizer | None = None) -> PeriodModel:
        self.schema = schema
        self.res = residualizer or Residualizer().fit(df, schema, self.seed)
        self.numeric = self.res.numeric[:4]
        ref_resid = np.column_stack([self.res.ref_raw[c] for c in self.numeric])
        t = period_table(df, schema, self.numeric, ref_resid)
        self.segments = sorted(t["segment"].unique())
        t["dow"] = t["day"].dt.dayofweek
        self.expected = t.groupby(["segment", "dow"])["count"].median().to_dict()
        t["count_ratio"] = self._ratio(t)
        base = [c for c in ("nulls", "dup", "lag_h") if c in t]
        self.raw_feats = ["count"] + base + ["med_" + c for c in self.numeric]
        self.feats = ["count_ratio"] + base + ["res_" + c for c in self.numeric]
        self.seasonal_feats = ["count_ratio"] + base + ["med_" + c for c in self.numeric]
        self.ref, self.raw = {}, {}
        for s, sub in t.groupby("segment"):
            for f in dict.fromkeys(self.feats + self.seasonal_feats):
                self.ref[(s, f)] = _robust(sub[f].to_numpy(float))
            for f in self.raw_feats:
                v = sub[f].to_numpy(float)
                self.raw[(s, f)] = (np.nanmean(v), np.nanstd(v) + 1e-9, np.nanmedian(v))
        space, make, _ = PERIOD_DETECTORS[self.detector]
        self.det = make(self.seed).fit(self._features(t, space))
        self.n_days = int(t["day"].nunique())
        return self

    def _ratio(self, t):
        exp = np.array([self.expected.get((s, d), np.nan) for s, d in zip(t["segment"], t["dow"])], float)
        return np.log((t["count"].to_numpy(float) + 1) / (exp + 1))

    def _features(self, t: pd.DataFrame, space: str) -> np.ndarray:
        cols = []
        feats, stats = {"raw": (self.raw_feats, self.raw), "seasonal": (self.seasonal_feats, self.ref),
                        "learned": (self.feats, self.ref)}[space]
        for f in feats:
            loc = np.array([stats.get((s, f), (np.nan, 1.0))[0] for s in t["segment"]], float)
            sc = np.array([stats.get((s, f), (np.nan, 1.0))[1] for s in t["segment"]], float)
            cols.append((t[f].to_numpy(float) - loc) / sc)
        return _clip(np.column_stack(cols))

    def table(self, df: pd.DataFrame) -> pd.DataFrame:
        _, resid, mu, _ = self.res.expect(df)
        k = len(self.numeric)
        t = period_table(df, self.schema, self.numeric, resid[:, :k], segments=self.segments, expected=mu[:, :k])
        t["dow"] = t["day"].dt.dayofweek
        t["count_ratio"] = self._ratio(t)
        t["expected_count"] = [self.expected.get((s, d)) for s, d in zip(t["segment"], t["dow"])]
        t["score"] = self.det.score(self._features(t, PERIOD_DETECTORS[self.detector][0]))
        # Too few rows on both sides to say anything (medians of 3 records are noise).
        t.loc[(t["count"] < MIN_PERIOD_ROWS) & (t["expected_count"].fillna(0) < MIN_PERIOD_ROWS), "score"] = 0.0
        # Statistical volume test: half the usual rows or fewer, and p < 1e-5 under Poisson noise (strict,
        # because a 90-day baseline alone has ~1,000 segment-days to test).
        e = t["expected_count"].astype(float)
        t["volume_alert"] = (e >= 5) & (t["count"] <= 0.5 * e) & (poisson.cdf(t["count"], e.fillna(0)) < 1e-5)
        return t

    def explain(self, t: pd.DataFrame, positions) -> list[list[dict]]:
        """Which feed-health metrics moved, with the baseline value for that segment (and weekday for volume)."""
        X = self._features(t.iloc[list(positions)], "learned")
        out = []
        for i, p in enumerate(positions):
            row, reasons = t.iloc[p], []
            for j in np.argsort(-np.abs(X[i])):
                if reasons and abs(X[i, j]) < 3 or len(reasons) >= 3:
                    break
                f = self.feats[j]
                if f == "count_ratio":
                    reasons.append({"metric": "rows", "value": float(row["count"]),
                                    "typical": float(row["expected_count"]) if pd.notna(row["expected_count"]) else None,
                                    "basis": f"median of baseline {row['day']:%A}s", "z": round(float(X[i, j]), 2)})
                elif f.startswith("res_"):
                    c = f[4:]
                    reasons.append({"metric": c, "value": _num(row["avg_" + c]), "typical": _num(row["exp_" + c]),
                                    "basis": "average expected for this day's mix of records",
                                    "z": round(float(X[i, j]), 2), "unit": "mean"})
                else:
                    typical = self.raw.get((row["segment"], f), (0, 0, np.nan))[2]
                    reasons.append({"metric": {"nulls": "empty cells", "dup": "duplicate keys",
                                               "lag_h": "ingestion delay (h)"}[f],
                                    "value": None if pd.isna(row[f]) else float(row[f]),
                                    "typical": None if pd.isna(typical) else float(typical),
                                    "basis": "baseline median for this segment", "z": round(float(X[i, j]), 2),
                                    "unit": "share" if f in ("nulls", "dup") else "hours"})
            out.append(reasons)
        return out
