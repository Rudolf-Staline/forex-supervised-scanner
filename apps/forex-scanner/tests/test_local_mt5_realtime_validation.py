from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "local_mt5_realtime_validation.py"


def load_module():
    spec = importlib.util.spec_from_file_location("local_mt5_realtime_validation", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeMT5:
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5

    def __init__(self, *, initialize_ok: bool = True, account_ok: bool = True, stale: bool = False, wide_spread: bool = False):
        self.initialize_ok = initialize_ok
        self.account_ok = account_ok
        self.stale = stale
        self.wide_spread = wide_spread
        self.selected: list[tuple[str, bool]] = []
        self.order_send_called = False

    def initialize(self):
        return self.initialize_ok

    def account_info(self):
        if not self.account_ok:
            return None
        return SimpleNamespace(login=123, server="Local Demo")

    def terminal_info(self):
        return SimpleNamespace(name="MetaTrader 5", connected=True)

    def symbol_info(self, symbol):
        if symbol == "EURUSD":
            return SimpleNamespace(name=symbol)
        return None

    def symbol_select(self, symbol, selected):
        self.selected.append((symbol, selected))
        return symbol == "EURUSD" and selected is True

    def symbol_info_tick(self, symbol):
        spread = 0.0001 if not self.wide_spread else 0.02
        return SimpleNamespace(time=1_900_000_000, bid=1.1000, ask=1.1000 + spread)

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        step = 60 if timeframe == self.TIMEFRAME_M1 else 300
        latest = 1_700_000_000 if self.stale else __import__("time").time()
        start = int(latest) - (20 * step)
        return [
            {
                "time": start + (idx * step),
                "open": 1.1000 + (idx * 0.0001),
                "high": 1.1010 + (idx * 0.0001),
                "low": 1.0990 + (idx * 0.0001),
                "close": 1.1005 + (idx * 0.0001),
            }
            for idx in range(21)
        ]


def make_config(module, tmp_path: Path, **updates):
    values = dict(
        symbols=["EUR/USD"],
        watchlist=None,
        timeframes=["M1"],
        duration_minutes=0,
        interval_seconds=0,
        max_candle_age_seconds=180,
        max_spread_atr_ratio=0.25,
        reports_dir=tmp_path,
        export_json=True,
        export_txt=True,
        export_csv=True,
        strict=False,
    )
    values.update(updates)
    return module.ValidationConfig(**values)


def test_mocked_mt5_ready_exports_reports(tmp_path: Path):
    module = load_module()
    mt5 = FakeMT5()
    report = module.run_validation(make_config(module, tmp_path), mt5=mt5)

    assert report.final_status == module.STATUS_READY
    assert report.mt5_import_ok is True
    assert report.terminal_initialized is True
    assert report.account_info_available is True
    assert report.terminal_info_available is True
    assert report.symbol_selected["EUR/USD"] is True
    assert report.sample_count == 1
    assert report.safety_flags["read_only_market_data_only"] is True
    assert report.safety_flags["order_send_called"] is False
    assert mt5.order_send_called is False
    assert (tmp_path / module.JSON_REPORT_NAME).exists()
    assert (tmp_path / module.TXT_REPORT_NAME).exists()
    assert (tmp_path / module.CSV_REPORT_NAME).exists()

    payload = json.loads((tmp_path / module.JSON_REPORT_NAME).read_text(encoding="utf-8"))
    assert payload["final_status"] == module.STATUS_READY
    assert payload["output_paths"]["json"].endswith(module.JSON_REPORT_NAME)
    assert payload["samples"][0]["spread_atr_ratio"] is not None


def test_missing_mt5_is_ci_safe_and_exports_blocked_report(tmp_path: Path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "import_mt5", lambda: None)

    report = module.run_validation(make_config(module, tmp_path), mt5=None)

    assert report.final_status == module.BLOCKED_MT5_UNAVAILABLE
    assert report.mt5_import_ok is False
    assert report.sample_count == 0
    assert module.BLOCKED_MT5_UNAVAILABLE in report.blocking_reasons
    assert (tmp_path / module.JSON_REPORT_NAME).exists()


def test_terminal_init_failure_blocks_without_sampling(tmp_path: Path):
    module = load_module()
    report = module.run_validation(make_config(module, tmp_path), mt5=FakeMT5(initialize_ok=False))

    assert report.final_status == module.BLOCKED_TERMINAL_INIT_FAILED
    assert report.terminal_initialized is False
    assert report.sample_count == 0


def test_account_info_failure_blocks_without_real_mt5(tmp_path: Path):
    module = load_module()
    report = module.run_validation(make_config(module, tmp_path), mt5=FakeMT5(account_ok=False))

    assert report.final_status == module.BLOCKED_ACCOUNT_INFO_UNAVAILABLE
    assert report.account_info_available is False
    assert report.sample_count == 0


def test_stale_candle_and_wide_spread_are_blocking(tmp_path: Path):
    module = load_module()
    report = module.run_validation(
        make_config(module, tmp_path, max_candle_age_seconds=1, max_spread_atr_ratio=0.001),
        mt5=FakeMT5(stale=True, wide_spread=True),
    )

    assert report.final_status in {module.BLOCKED_STALE_DATA, module.BLOCKED_SPREAD_TOO_WIDE}
    assert module.BLOCKED_STALE_DATA in report.blocking_reasons
    assert module.BLOCKED_SPREAD_TOO_WIDE in report.blocking_reasons


def test_cli_without_real_mt5_exits_zero_unless_strict(tmp_path: Path):
    command = [
        sys.executable,
        str(SCRIPT_PATH),
        "--symbols",
        "EUR/USD",
        "--timeframes",
        "M1",
        "--duration-minutes",
        "0",
        "--interval-seconds",
        "0",
        "--reports-dir",
        str(tmp_path),
        "--export-json",
        "--export-txt",
        "--export-csv",
    ]
    result = subprocess.run(command, cwd=SCRIPT_PATH.parents[1], capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert "read-only" in result.stdout
    assert (tmp_path / "local_mt5_realtime_validation.json").exists()


def test_strict_cli_returns_non_zero_when_mt5_unavailable(tmp_path: Path):
    command = [
        sys.executable,
        str(SCRIPT_PATH),
        "--symbols",
        "EUR/USD",
        "--timeframes",
        "M1",
        "--duration-minutes",
        "0",
        "--interval-seconds",
        "0",
        "--reports-dir",
        str(tmp_path),
        "--export-json",
        "--strict",
    ]
    result = subprocess.run(command, cwd=SCRIPT_PATH.parents[1], capture_output=True, text=True, check=False)

    assert result.returncode == 2
    assert "BLOCKED_MT5_UNAVAILABLE" in result.stdout
