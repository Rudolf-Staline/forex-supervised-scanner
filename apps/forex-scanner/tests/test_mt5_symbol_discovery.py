"""MT5 symbol discovery helper tests."""

from __future__ import annotations

from types import SimpleNamespace

from scripts.mt5_discover_symbols import _matches, _symbol_payload


def test_symbol_discovery_matches_keywords_in_name_description_or_path() -> None:
    row = SimpleNamespace(name="XAUUSD.d", description="Gold vs US Dollar", path="Commodities\\Metals")

    assert _matches(row, ["gold"])
    assert _matches(row, ["xau"])
    assert not _matches(row, ["nasdaq"])


def test_symbol_discovery_payload_includes_mt5_contract_fields() -> None:
    row = SimpleNamespace(
        name="NAS100",
        description="NASDAQ 100",
        path="Indices",
        trade_mode=4,
        visible=True,
        volume_min=0.01,
        volume_step=0.01,
        spread=120,
        point=0.01,
        digits=2,
        trade_tick_value=1.0,
        trade_tick_size=0.01,
        trade_contract_size=1.0,
    )

    payload = _symbol_payload(row)

    assert payload["symbol"] == "NAS100"
    assert payload["tick_value"] == 1.0
    assert payload["tick_size"] == 0.01
    assert payload["contract_size"] == 1.0
