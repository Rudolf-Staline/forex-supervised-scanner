# Machine-learning pattern evidence

The `app.pattern_ml` package adds optional machine-learning evidence to the existing chart-pattern confluence layer. It does not replace rules-based setup detection, create executable setups, select position size, or bypass risk, readiness, policy, and paper/demo safety gates.

## Scope

The intended design is one fitted model per named pattern:

```text
models/patterns/
  double_top/
    metadata.json
    model.joblib
  double_bottom/
    metadata.json
    model.joblib
  head_and_shoulders/
    metadata.json
    model.joblib
```

Each active model implements the same `PatternDetector` contract and emits a `PatternPrediction`. Detected predictions can be converted to the scanner's existing `ChartPatternSignal` through `to_chart_pattern_signals(...)`. The converted name is prefixed with `ml_` so rule evidence and model evidence remain distinguishable in reports.

## Artifact metadata

Example `metadata.json`:

```json
{
  "pattern_name": "double_top",
  "model_version": "double-top-v1",
  "direction": "short",
  "window_size": 64,
  "decision_threshold": 0.82,
  "feature_schema": "ohlcv-shape-v1",
  "positive_class": 1,
  "estimator_filename": "model.joblib"
}
```

Load and register a model explicitly:

```python
from app.pattern_ml import PatternModelRegistry, SklearnPatternDetector

registry = PatternModelRegistry()
registry.register(SklearnPatternDetector.from_directory("models/patterns/double_top"))
result = registry.scan(entry_frame)
```

A broken model is isolated and reported in `result.failures`; it does not prevent other registered models from running.

## Feature schema

`ohlcv-shape-v1` consumes only the latest completed OHLC(V) bars. It validates timestamps and candle consistency, then builds scale-invariant price-shape features:

- anchored and range-scaled OHLC values;
- candle body and wick proportions;
- log returns;
- range relative to close;
- optional volume z-score.

The transformer is causal: it only reads the requested trailing window. Training and inference must use the same schema and window size.

## Integration rule

ML output is confluence evidence only. It may enrich `RawSetup.detected_patterns`, `pattern_score`, and explanations after explicit scanner wiring, but it must not:

- create a trade when no rules-based setup exists;
- set stop-loss or take-profit values;
- directly change execution mode;
- authorize live trading or broker order submission.

Initial deployment should use the models as confirmation or contradiction evidence. Their weight in final scoring must remain bounded and validated out of sample.

## Validation requirements

Before registering a production candidate model, record at least:

- temporal train/validation/test boundaries;
- labeling rules and ambiguous-sample policy;
- per-pattern precision, recall, PR-AUC, calibration, and false-positive rate;
- performance by symbol, timeframe, session, and volatility regime;
- threshold chosen only from validation data;
- locked out-of-sample and walk-forward results;
- model and dataset hashes.

Random row-level cross-validation is not acceptable for overlapping market windows because it leaks nearly identical neighboring samples across folds.

## Tests

```bash
cd apps/forex-scanner
python -m pytest tests/test_pattern_ml.py
```
