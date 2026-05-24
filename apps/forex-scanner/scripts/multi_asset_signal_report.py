"""Aggregate multi-asset signals from reports/signal_journal.jsonl."""
from __future__ import annotations
import argparse, csv, json
from collections import Counter, defaultdict
from pathlib import Path

JOURNAL_PATH = Path("reports/signal_journal.jsonl")
CSV_PATH = Path("reports/multi_asset_signal_report.csv")
SUMMARY_PATH = Path("reports/multi_asset_signal_report_summary.json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    parser.add_argument("--watchlist", default="multi_asset_demo")
    parser.add_argument("--min-score", type=float, default=55.0)
    parser.add_argument("--export-csv", action="store_true")
    args = parser.parse_args()
    rows = load_signal_journal(JOURNAL_PATH)
    if not rows:
        print("signal_journal=missing_or_empty; run python scripts/run_one_cycle.py ... first")
        return
    filtered = filter_report_records(rows, asset_class=args.asset_class, watchlist=args.watchlist)
    report = build_multi_asset_signal_report(filtered, min_score=args.min_score)
    print_multi_asset_signal_report(report, min_score=args.min_score)
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"summary_json={SUMMARY_PATH}")
    if args.export_csv:
        export_near_miss_csv(report["near_miss_records"], CSV_PATH)
        print(f"csv_export={CSV_PATH}")

def load_signal_journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

def filter_report_records(records: list[dict], *, asset_class: str, watchlist: str) -> list[dict]:
    rows = [r for r in records if r.get("watchlist") == watchlist]
    if asset_class != "all":
        rows = [r for r in rows if r.get("asset_class") == asset_class]
    return rows

def build_multi_asset_signal_report(records: list[dict], *, min_score: float) -> dict:
    by_asset=defaultdict(list)
    for r in records: by_asset[r.get("asset_class","unknown")].append(r)
    near=[r for r in records if is_near_miss(r,min_score=min_score)]
    rej=Counter(reason for r in records for reason in (r.get("rejection_reasons") or []))
    report={
      "total_signals":len(records),"signals_by_asset_class":{k:len(v) for k,v in by_asset.items()},
      "signals_by_status":dict(Counter(r.get("status") for r in records)),
      "best_score_by_asset_class":_best_score_by_asset_class(by_asset),"best_score_by_symbol":_best_score_by_symbol(records),
      "best_setup_by_asset_class":_best_setup_by_asset_class(by_asset),"detected_patterns_by_asset_class":_patterns_by_asset_class(by_asset),
      "average_spread_atr_by_symbol":_avg_spread(records),"rejection_reasons_top":dict(rej.most_common(10)),
      "near_miss_signals":len(near),"near_miss_records":near,"off_hours_count":sum(1 for r in records if _has_reason(r,["off-hours","session"])),
      "scan_only_count":sum(1 for r in records if _has_reason(r,["scan_only"])),"recommended_focus":_recommended_focus(records)
    }
    return report

def is_near_miss(r: dict, *, min_score: float) -> bool:
    reasons=" ".join(r.get("rejection_reasons") or []).lower(); setup=bool(r.get("setup") and r.get("setup")!="none")
    return (float(r.get("score") or 0)>=min_score or float(r.get("pattern_score") or 0)>0 or r.get("status") in {"watchlist","detected"} or (setup and any(x in reasons for x in ["session","off-hours","scan_only"])))

def export_near_miss_csv(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        path.write_text("",encoding="utf-8"); return
    fields=sorted({k for r in records for k in r.keys()})
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); [w.writerow(r) for r in records]

def _best_score_by_asset_class(by_asset): return {k:f"{max([float(r.get('score') or 0) for r in v],default=0):.2f}" for k,v in by_asset.items()}
def _best_score_by_symbol(records):
    d={}
    for r in records: d[r.get("logical_symbol")]=max(d.get(r.get("logical_symbol"),0),float(r.get("score") or 0))
    return {k:f"{v:.2f}" for k,v in sorted(d.items(), key=lambda x:x[1], reverse=True)}
def _best_setup_by_asset_class(by_asset):
    out={}
    for k,v in by_asset.items():
        setups=defaultdict(float)
        for r in v:
            if r.get("setup"): setups[r["setup"]]=max(setups[r["setup"]], float(r.get("score") or 0))
        out[k]=next(iter(sorted(setups,key=setups.get,reverse=True)),"n/a")
    return out
def _patterns_by_asset_class(by_asset):
    return {k:dict(Counter(p for r in v for p in (r.get("detected_patterns") or [])).most_common(10)) for k,v in by_asset.items()}
def _avg_spread(records):
    vals=defaultdict(list)
    for r in records:
        if r.get("spread_atr") is not None: vals[r.get("logical_symbol")].append(float(r["spread_atr"]))
    return {k:f"{(sum(v)/len(v)):.4f}" for k,v in vals.items()}
def _recommended_focus(records):
    out={}
    for asset in ["forex","commodities","indices"]:
        c=[r for r in records if r.get("asset_class")==asset]; best={}
        for r in c: best[r.get("logical_symbol")]=max(best.get(r.get("logical_symbol"),0), float(r.get("score") or 0)+float(r.get("pattern_score") or 0))
        out[asset]=[k for k,_ in sorted(best.items(), key=lambda x:x[1], reverse=True)[:3]]
    return out

def _has_reason(r, snippets):
    s=" ".join(r.get("rejection_reasons") or []).lower(); return any(x in s for x in snippets)

def print_multi_asset_signal_report(report: dict, *, min_score: float) -> None:
    print("multi_asset_signal_report=no_orders")
    for k in ["total_signals","near_miss_signals","off_hours_count","scan_only_count"]: print(f"{k}={report[k]}")
    for k in ["signals_by_asset_class","signals_by_status","best_score_by_asset_class","best_score_by_symbol","best_setup_by_asset_class","detected_patterns_by_asset_class","average_spread_atr_by_symbol","rejection_reasons_top","recommended_focus"]:
        print(f"{k}={json.dumps(report[k], ensure_ascii=False)}")
    print(f"near_miss_definition=score>={min_score} OR pattern_score>0 OR status in [watchlist,detected] OR setup rejected session/off-hours/scan_only")

if __name__=="__main__":
    main()
