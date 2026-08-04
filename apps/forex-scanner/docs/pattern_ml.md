# Machine-learning pattern evidence

The `app.pattern_ml` package adds optional machine-learning evidence to the existing chart-pattern confluence layer. It does not replace rules-based setup detection, create executable setups, select position size, or bypass risk, readiness, policy, and paper/demo safety gates.

## Scope

The intended design is one fitted model per named pattern:

```text
models/patterns/
  double_top/
    metadata.json
    model.joblib
    training_report.json
    weak_labels.csv
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

## Disabled-by-default integration

The current scanner remains unchanged unless the optional wrapper is called with a registry and a non-disabled mode:

```python
from app.pattern_ml import PatternMLMode, detect_setups_with_optional_pattern_ml

result = detect_setups_with_optional_pattern_ml(
    pattern_ml_registry=registry,
    pattern_ml_mode=PatternMLMode.REPORT_ONLY,
    symbol=symbol,
    style=style,
    higher_df=higher_df,
    entry_df=entry_df,
    trigger_df=trigger_df,
    higher_regime=higher_regime,
    entry_regime=entry_regime,
    trigger_regime=trigger_regime,
    levels=levels,
    settings=settings,
)
raw_setups = result.setups
```

Modes:

- `disabled`: no inference and no behavioral change;
- `report_only`: attach `ml_*` evidence and diagnostics, but keep the pattern score unchanged;
- `confluence`: add a bounded fraction of the normal pattern score, still capped at 15.

The scanner's final score adds only `pattern_score * 0.2`, so even a fully saturated pattern score contributes at most three final-score points. The default confluence weight is `0.35`, and `report_only` is the intended first deployment mode.

ML output must not:

- create a trade when no rules-based setup exists;
- set stop-loss or take-profit values;
- directly change execution mode;
- authorize live trading or broker order submission.

## First training pipeline: double top

Install the optional ML dependencies:

```bash
cd apps/forex-scanner
python -m pip install -e ".[ml]"
```

Train the first candidate from local historical CSV files:

```bash
python scripts/train_pattern_model.py \
  --input data/real/EURUSD_M15.csv data/real/GBPUSD_M15.csv data/real/USDJPY_M15.csv \
  --pattern double_top \
  --window-size 64 \
  --stride 2 \
  --minimum-precision 0.75 \
  --model-version double-top-v1 \
  --output-dir models/patterns/double_top
```

The command writes:

- `model.joblib`: the fitted estimator;
- `metadata.json`: inference contract and locked validation threshold;
- `training_report.json`: temporal split metrics and model hash;
- `weak_labels.csv`: all retained samples with empty `human_label` and `review_status` columns.

The current rules detector creates causal weak labels. These are bootstrap labels, not ground truth. Before the model influences scoring, review positives and hard negatives in `weak_labels.csv`, replace disputed labels, then retrain from the reviewed dataset in a later pipeline revision.

## Temporal validation

Each input CSV is split chronologically into train, validation, and test partitions. An embargo equal to `window_size - 1` samples is applied around boundaries by default, preventing almost-identical overlapping windows from crossing partitions.

The decision threshold is selected only on validation data. The test partition remains untouched until final evaluation. Random row-level cross-validation is forbidden because neighboring windows share most of their candles.

Before registering a production candidate model, record at least:

- temporal train/validation/test boundaries;
- labeling rules and ambiguous-sample policy;
- precision, recall, PR-AUC, calibration, and false-positive rate;
- performance by symbol, timeframe, session, and volatility regime;
- threshold chosen only from validation data;
- locked out-of-sample and walk-forward results;
- model and dataset hashes.

## Tests

```bash
cd apps/forex-scanner
python -m pytest tests/test_pattern_ml.py tests/test_pattern_ml_integration.py
```
