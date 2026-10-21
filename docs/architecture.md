# Architecture

## Request path
The React app is built by Vite and served by FastAPI from the same origin (`sentinel/main.py`). Auth is a signed JWT in an HttpOnly cookie. All API routes live under `/api`; anything else returns `index.html` for client-side routing.

## Analysis pipeline
```
upload ─▶ services.add_version      validate (size, encoding, rows, columns) → gzip → dataset_versions
       └▶ analysis_runs(queued) ─▶ jobs.submit ─▶ worker thread
                                       │
            services.execute_run ──────┤ baseline version (or models from an older ENGINE_VERSION)?
                                       │   yes → engine.analyze.build_baseline:
                                       │         infer_schema → infer_contract → profile
                                       │         Residualizer (GBM context model) → RowModel, PeriodModel
                                       │         → model_artifacts (joblib, versioned)
                                       ▼
                         engine.analyze.analyze:
                           coerce types → run_checks (schema, types, contract, formulas, duplicates,
                           missing vs baseline, PSI drift) → RowModel.score + explain →
                           PeriodModel.table + explain → fold symptoms into causes → findings
                                       ▼
                         services.apply_findings: match open incidents by fingerprint
                           (recurrence → occurrence + event; new → incident + event;
                            open but not seen → "not detected in version N" event)
```

Run state (`queued → running(stage) → succeeded|failed`) is in the database, so the UI polls it, and runs left unfinished by a restart are re-queued on startup.

## Data model
`organizations` 1─* `users`, `datasets` 1─* `dataset_versions`, `model_artifacts`, `analysis_runs`, `incidents` 1─* `incident_events`, `lab_experiments` (spec, seed, ground truth, scorecard). Migrations: Alembic (`backend/migrations`), applied on startup.

## Why these choices
- **One container, one process**: cheapest reliable hosting, and no CORS or cross-site cookie issues. The worker is a thread because analyses take seconds; the code path through `jobs.submit` is the only thing that would change for a real queue.
- **CSV content in the database** (gzip): free hosts have ephemeral disks, and Postgres gives backups and tenant deletion by cascade.
- **Per-dataset models with a baseline**: "normal" differs per feed. Re-baselining is explicit (a button), so a real business change is acknowledged rather than silently learned.
- **Deterministic first**: anything expressible as a rule is a rule. Models only score what rules cannot express.
- **Fold symptoms into causes**: a replayed load shows up as duplicate keys in many segment-days; it is one incident, with the affected segment-days as evidence.
- **Single-threaded numerics** (`OMP_NUM_THREADS=1` by default): the models are small, and OpenMP oversubscription caused a measured 19× slowdown on a busy machine.
