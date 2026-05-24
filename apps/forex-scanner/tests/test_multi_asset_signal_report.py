from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from multi_asset_signal_report import build_multi_asset_signal_report, filter_report_records, is_near_miss, export_near_miss_csv


def _records():
    return [
        {"logical_symbol":"EUR/USD","asset_class":"forex","watchlist":"multi_asset_demo","status":"watchlist","setup":"ema50_pullback","score":62.0,"pattern_score":0.0,"detected_patterns":[],"spread_atr":0.2,"rejection_reasons":[]},
        {"logical_symbol":"XAU/USD","asset_class":"commodities","watchlist":"multi_asset_demo","status":"rejected","setup":"reversal","score":51.0,"pattern_score":10.0,"detected_patterns":["pin_bar"],"spread_atr":0.1,"rejection_reasons":["scan_only asset class"]},
        {"logical_symbol":"US30","asset_class":"indices","watchlist":"multi_asset_demo","status":"detected","setup":"momentum_breakout","score":49.0,"pattern_score":0.0,"detected_patterns":[],"spread_atr":0.05,"rejection_reasons":["off-hours"]},
    ]

def test_report_basic():
    report = build_multi_asset_signal_report(_records(), min_score=55.0)
    assert report["total_signals"] == 3
    assert report["signals_by_asset_class"]["forex"] == 1
    assert report["near_miss_signals"] >= 2

def test_filter_and_near_miss_and_csv(tmp_path):
    filtered = filter_report_records(_records(), asset_class="commodities", watchlist="multi_asset_demo")
    assert len(filtered) == 1
    assert is_near_miss(filtered[0], min_score=55.0)
    out = tmp_path / "report.csv"
    export_near_miss_csv(filtered, out)
    assert out.exists()
