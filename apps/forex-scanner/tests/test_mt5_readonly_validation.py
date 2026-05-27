from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts import mt5_readonly_validation as readonly


class DummyMT5:
    ACCOUNT_TRADE_MODE_DEMO = 1

    def __init__(self, *, demo: bool = True, symbol_failures: set[str] | None = None) -> None:
        self.demo = demo
        self.symbol_failures = symbol_failures or set()
        self.order_send_called = False

    def initialize(self):
        return True

    def account_info(self):
        return SimpleNamespace(
            server="My-Demo" if self.demo else "Live-Server",
            trade_mode=1 if self.demo else 0,
            balance=1000.0,
            equity=1001.0,
            margin=10.0,
            margin_free=991.0,
        )

    def symbol_info(self, symbol: str):
        if symbol in self.symbol_failures:
            return None
        return SimpleNamespace(name=symbol)

    def symbol_info_tick(self, symbol: str):
        if symbol in self.symbol_failures:
            return None
        return SimpleNamespace(bid=1.0, ask=1.1)

    def order_send(self, _payload):
        self.order_send_called = True
        raise AssertionError("order_send must never be called")


def test_mt5_unavailable_message(capsys):
    report = readonly.run_validation(None, watchlist="multi_asset_demo", symbols=["EUR/USD"], show_next_windows=False)
    output = capsys.readouterr().out
    assert "MT5 terminal is not available in cloud environment." in output
    assert report.final_status == "MT5_UNAVAILABLE"


def test_non_demo_account_is_blocked(monkeypatch):
    mt5 = DummyMT5(demo=False)
    monkeypatch.setattr(readonly, "reconcile_mt5_demo", lambda *_args, **_kwargs: SimpleNamespace(reconciliation_status="OK", open_positions=0, foreign_positions=0))
    report = readonly.run_validation(mt5, watchlist="multi_asset_demo", symbols=["EUR/USD"], show_next_windows=False)
    assert report.final_status == "BLOCKED"
    assert report.demo_only is False


def test_order_send_is_never_called(monkeypatch):
    mt5 = DummyMT5(demo=True)
    monkeypatch.setattr(readonly, "reconcile_mt5_demo", lambda *_args, **_kwargs: SimpleNamespace(reconciliation_status="OK", open_positions=0, foreign_positions=0))
    readonly.run_validation(mt5, watchlist="multi_asset_demo", symbols=["EUR/USD"], show_next_windows=False)
    assert mt5.order_send_called is False


def test_export_json_and_txt(tmp_path: Path):
    report = readonly.ValidationReport(
        mt5_available=True,
        initialized=True,
        account_server="Demo",
        demo_only=True,
        symbols_checked=["EUR/USD"],
        symbols_ok=["EUR/USD"],
        symbols_failed=[],
        reconciliation_status="OK",
        open_positions_count=0,
        foreign_positions_count=0,
        next_tradable_windows={},
        final_status="READY_READONLY",
    )
    readonly.REPORTS_DIR = tmp_path
    readonly.JSON_REPORT_PATH = tmp_path / "mt5_readonly_validation.json"
    readonly.TXT_REPORT_PATH = tmp_path / "mt5_readonly_validation.txt"
    readonly.export_report(report)
    assert readonly.JSON_REPORT_PATH.exists()
    assert readonly.TXT_REPORT_PATH.exists()


def test_symbol_failures_are_handled(monkeypatch):
    mt5 = DummyMT5(demo=True, symbol_failures={"XAUUSD", "XAU/USD"})
    monkeypatch.setattr(readonly, "reconcile_mt5_demo", lambda *_args, **_kwargs: SimpleNamespace(reconciliation_status="OK", open_positions=0, foreign_positions=0))
    report = readonly.run_validation(mt5, watchlist="multi_asset_demo", symbols=["XAU/USD"], show_next_windows=False)
    assert report.symbols_failed == ["XAU/USD"]
    assert report.final_status == "BLOCKED"


def test_deriv_symbol_aliases_resolution(monkeypatch):
    class AliasDummyMT5(DummyMT5):
        def symbol_info(self, symbol: str):
            # Only acknowledge specific aliases
            if symbol in {"US SP 500", "US Oil", "UK Brent Oil", "Wall Street 30"}:
                return SimpleNamespace(name=symbol)
            return None

        def symbol_info_tick(self, symbol: str):
            if symbol in {"US SP 500", "US Oil", "UK Brent Oil", "Wall Street 30"}:
                return SimpleNamespace(bid=1.0, ask=1.1)
            return None

    mt5 = AliasDummyMT5(demo=True)
    monkeypatch.setattr(readonly, "reconcile_mt5_demo", lambda *_args, **_kwargs: SimpleNamespace(reconciliation_status="OK", open_positions=0, foreign_positions=0))

    # Passing symbols that have special aliases
    symbols_to_test = ["US500", "WTI/OIL", "BRENT/OIL", "US30"]
    report = readonly.run_validation(mt5, watchlist="multi_asset_demo", symbols=symbols_to_test, show_next_windows=False)

    assert set(report.symbols_ok) == set(symbols_to_test)
    assert not report.symbols_failed


def test_reconciliation_called_readonly(monkeypatch):
    mt5 = DummyMT5(demo=True)
    called = {"ok": False, "account": None}

    def _fake_reconcile(_mt5, *, account=None, **_kwargs):
        called["ok"] = True
        called["account"] = account
        return SimpleNamespace(reconciliation_status="OK", open_positions=1, foreign_positions=0)

    monkeypatch.setattr(readonly, "reconcile_mt5_demo", _fake_reconcile)
    report = readonly.run_validation(mt5, watchlist="multi_asset_demo", symbols=["EUR/USD"], show_next_windows=False)
    assert called["ok"] is True
    assert called["account"] is not None
    assert report.reconciliation_status == "OK"
