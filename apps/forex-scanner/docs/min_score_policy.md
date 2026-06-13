# Minimum-score policy

This document explains the different minimum-score values in the system and how
they relate. It is diagnostic only — nothing here loosens a threshold or makes
the bot more permissive. Thresholds are never auto-mutated.

## The values

| Value                            | Source                                              | Meaning |
| -------------------------------- | --------------------------------------------------- | ------- |
| `instrument_min_score`           | `app/config/instruments.py`                         | Static per-instrument floor. |
| `adaptive_base_min_score`        | adaptive engine                                     | Starting point for adaptive adjustments (defaults to the instrument min). |
| `adaptive_recommended_min_score` | adaptive engine                                     | Recommendation from style/history adjustments, before safety bounds. |
| `adaptive_effective_min_score`   | adaptive engine                                     | Recommendation after hard floor / cap / max-daily-change bounds. |
| `effective_scanner_threshold`    | policy report                                       | The minimum the **scanner** actually applies for approval. |
| `demo_bot_min_score`             | `demo_bot.min_score` (env `AUTO_BOT_MIN_SCORE`)     | The configured paper/demo bot gate. |
| `effective_bot_threshold`        | policy report                                       | The minimum the **demo bot** actually applies. |

## Why Forex defaults to 75

Forex instruments use `min_score = 75.0` (`FOREX_DEFAULT` in
`app/config/instruments.py`). This is a deliberately conservative paper/demo
floor: it keeps only higher-conviction setups while leaving lower-scoring rows
visible as `watchlist`/`detected`/`rejected` for diagnostics. Commodities
(80.0) and indices (82.0) default higher because of wider spreads and noisier
microstructure.

## Static vs adaptive

Adaptive thresholds are configured under `adaptive_thresholds` in
`default_settings.json`:

- **disabled** (default): the scanner and bot use the static instrument /
  configured thresholds. The adaptive provider returns a fallback equal to the
  instrument min.
- **`report_only`**: the adaptive engine computes a recommendation for
  diagnostics, but the scanner and bot keep using the static thresholds. This
  mode never relaxes a gate.
- **`scanner_effective`**: only in this mode is the adaptive `effective` value
  applied as the scanner threshold. Even then the engine enforces hard floors
  (Forex 70, commodities 78, indices 80), a hard cap (92), and a max daily
  change.

## Scanner threshold vs demo bot threshold

The scanner threshold decides the opportunity `status` (premium/approved/
watchlist/detected/rejected). The demo bot threshold (`demo_bot.min_score`) is a
**second, independent** gate that decides whether the paper bot will create a
paper order. They can legitimately differ:

- The bot uses the stricter configured paper/demo gate unless
  `scanner_effective` adaptive mode is active.
- If `demo_bot.min_score` is higher than the scanner threshold, the bot is
  stricter than the scanner (it rejects setups the scanner approved). This is
  surfaced as a `mismatch_warnings` entry — it is informational and never
  relaxes scanner approval.

## Inspecting the policy

```bash
python scripts/min_score_policy_report.py --symbols EUR/USD --style day_trading --export-json --export-txt
```

Exports `reports/min_score_policy_report.json` and
`reports/min_score_policy_report.txt` (both git-ignored). The report lists every
value above plus `adaptive_enabled`, `adaptive_mode`, `threshold_source`, and
any mismatch warnings.
