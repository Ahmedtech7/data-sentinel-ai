# Model card

## Intended use
Flag records and feed segments in a recurring tabular business feed (CSV) that deviate from that feed's own baseline, so that a person can review them. Not intended for automated correction or deletion of data, and not a fraud model: an anomaly is evidence to look at, not proof of an error.

## Models
Both are trained per dataset on its baseline version (`services.execute_run → engine.analyze.build_baseline`).

| | Record model | Feed model |
|---|---|---|
| Unit scored | one row | one (segment, day); segment = up to two low-cardinality columns |
| Learned component | per numeric column: `HistGradientBoostingRegressor` for E[slog(x) \| categorical context] and for E[\|residual\| \| context]; 3-fold out-of-fold residuals on the baseline | reuses the record model's context model to compute mix-adjusted residual levels |
| Features | residual z per numeric column | log(rows / weekday median), empty-cell rate, duplicate-key rate, median ingest delay, clipped mean residual per numeric column (up to 4) |
| Scoring rule | max over fields of \|z\| / q99.5(\|z\| on baseline) | max robust z per segment (median/MAD from the baseline) |
| Extra | — | Poisson volume test: count ≤ 50% of expected and P(X ≤ count) < 1e-5 |
| Threshold | 2.21 (max validation F1) | 4.45 (max validation F1) |

`slog(x) = sign(x)·log(1+|x|)`. Context = categorical columns with 2–200 values (up to 6). With no categorical context, the model falls back to global median/MAD.

Candidates compared (all in `engine/modeling.py`, results in `artifacts/report.json`): z-score, IQR fences, robust z per group, Isolation Forest (group z and residual features), LOF, max-deviation, calibrated max-deviation, and a LOF + max-z ensemble for records; per-segment z, weekday-adjusted robust z on raw features, robust z / Isolation Forest / LOF / ensemble on learned features for the feed.

## Data and labels
`engine/generator.py` produces a fictional retailer: 4 regions × 3 channels, 40 SKUs with log-normal prices, weekday seasonality per channel, mild growth, minute-level web ingestion, partner ingestion within hours, nightly store batches.

**Natural irregularities, never labelled:** 3% bulk partner orders (20–60 units), 0.3% clearance discount of 40%, 2% guest web orders with no customer id, Poisson volume noise. If flagged, they count as false positives.

**Baseline contamination, unlabelled:** 0.1% quantity×revenue spikes injected into each 90-day baseline.

**Injected faults** (`engine/faults.py`, applied to the scored 60-day window):

| Level | Label | Definition |
|---|---|---|
| record | `price_x100` | 0.3% of rows: unit_price and revenue × 100 (consistent, so the revenue rule cannot see it) |
| record | `price_div100` | 0.2% of rows: unit_price and revenue ÷ 100 |
| record | `quantity_spike` | 0.3% of rows: quantity and revenue × U(8, 40) |
| record | `sku_price_x10` | every row of one random SKU on 3 consecutive days: unit_price and revenue × 10 |
| feed | `volume_drop` | 14 random segment-days: drop U(50%, 100%) of rows |
| feed | `delay` | 8 segment-days: ingestion timestamp + U(3, 12) h |
| feed | `mean_shift` | 8 segment-days: unit_price × U(1.25, 1.5) |
| feed | `missing` | 5 segment-days: discount or customer_id blanked for U(30%, 90%) of rows |
| feed | `duplicates` | 5 segment-days: U(20%, 50%) of rows replayed |
| rules | `sign_flip`, `type_corruption`, `duplicates`, `price_x100_no_revenue`, `missing order_id` | evaluated separately against the inferred contract + revenue formula |

**Feed labelling rule:** a segment-day is positive if an injected fault touches ≥ 20% of its rows (and ≥ 5 rows for row-type faults; ≥ 1 row for volume drop, delay and drift).

## Protocol
- Validation seeds 11, 12, 13 select the detector (average precision) and threshold (max F1, pooled).
- Test seeds 101–105 are scored once with those fixed choices. Reported: pooled precision, recall, F1, FPR, AP, ROC AUC, per-fault recall, mean ± sd across streams, runtime per 10k items.
- Everything is seeded and deterministic; re-running `python -m sentinel.ml_eval` reproduces every metric in the report exactly (only the timestamp and timings change).

## Results (test)
Record level: AP 0.978, precision 0.937, recall 0.915, FPR 0.06% (best statistical baseline: AP 0.625).
Feed level, shipped: precision 0.856, recall 0.745, FPR 0.67% (best purely statistical: AP 0.494 vs 0.775).
Full tables are in the README and on the in-app **Model evidence** page.

## Known failure modes
- Volume drops in segments with < 10 rows/day: 22% recall.
- Delays in feeds that already arrive in nightly batches.
- Quantity spikes that look like legitimate bulk orders (≈ 74% recall).
- Concept drift: a sustained real change (new prices, new channel) will keep alerting until a new baseline is set (one click on the Versions tab).
- Evaluation is on synthetic data only.
