#!/usr/bin/env python3
"""Health daily analyzer CLI (task A7).

Usage:
  python analyzer.py --file <daily-json>            # analyze one file
  python analyzer.py [--data-dir DIR]               # analyze newest by-day file
Options:
  --config PATH          config json (default: <script_dir>/config.json)
  --data-dir DIR         data dir (default: --file's parent, or config)
  --reports-dir DIR      reports dir (default: <data-dir>/reports)

Exit codes: 0 ok; 2 invalid input / nothing to do; 1 internal error.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import stats  # noqa: E402
import llm  # noqa: E402
import report_builder  # noqa: E402
import analysis14  # noqa: E402

CONFIG_DEFAULTS = {
    "analysis": {
        "baseline_days": 14,
        "min_baseline_days": 3,
        "min_hr_samples": 60,
        "trend_days": 7,
        "thresholds": {"deep_min": 45, "deep_ratio": 0.5, "sleep_hours": 5.5,
                       "sleep_ratio": 0.7, "hr_delta_bpm": 8,
                       "bedtime_drift_min": 90,
                       "energy_below_ratio": 0.6, "spo2_low": 92.0,
                       "temp_delta_c": 0.8},
    },
    "llm": dict(llm.LLM_DEFAULTS),
}


def _deep_merge(base: dict, extra: dict) -> dict:
    """Recursively merge extra over base; nested dicts merge key-wise so a
    user config missing new sub-keys never drops defaults or crashes."""
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path) -> dict:
    cfg = json.loads(json.dumps(CONFIG_DEFAULTS))  # deep copy
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if isinstance(raw, dict):
        cfg = _deep_merge(cfg, raw)
    return cfg


def run_file(file_path: Path, cfg: dict, data_dir: Path,
             reports_dir: Path, run14: bool = True) -> int:
    payload = stats.load_payload(file_path)
    if payload is None:
        print(f"[analyzer] invalid input: {file_path.name} (no usable data)",
              file=sys.stderr)
        return 2
    rdate = stats.infer_report_date(payload, file_path.name)
    if rdate is None:
        print(f"[analyzer] cannot infer report date: {file_path.name}",
              file=sys.stderr)
        return 2
    day = stats.compute_day_stats(payload)
    ana = cfg["analysis"]
    base = stats.build_baseline(data_dir, rdate,
                                int(ana.get("baseline_days", 14)),
                                int(ana.get("min_hr_samples", 60)))
    comp = stats.compare_baseline(day, base, ana)
    trend = stats.build_trend(data_dir, int(ana.get("trend_days", 7)),
                              end_date=rdate)
    multiday = stats.build_multiday_context(
        data_dir, rdate, int(ana.get("trend_days", 7)), ana)
    text, src = llm.get_interpretation(cfg, day, comp, multiday)

    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"{rdate.strftime('%Y%m%d')}_report.html"
    meta = {"date": rdate.isoformat(), "source_name": file_path.name,
            "generated_at_local": _dt.datetime.now().strftime("%Y-%m-%d %H:%M")}
    span = stats.collected_span(payload, rdate)
    if span is None:  # no timestamp at all: state the report's calendar day
        span = (_dt.datetime.combine(rdate, _dt.time(0, 0)),
                _dt.datetime.combine(rdate, _dt.time(23, 59)))
    meta["span_label"] = "数据时间跨度"
    meta["span_start"] = span[0].strftime("%Y-%m-%d %H:%M")
    meta["span_end"] = span[1].strftime("%Y-%m-%d %H:%M")
    out.write_text(report_builder.build_html(meta, day, comp, text, src, trend),
                   encoding="utf-8")
    s = day["sleep"]
    print(f"[analyzer] report written: {out}")
    print(f"[analyzer] date={rdate} sleep={'yes' if s['available'] else 'no'} "
          f"hr_points={day['hr']['total_count']} llm={src}")
    if run14:
        try:
            p14 = analysis14.run_analysis14(data_dir, reports_dir, rdate, cfg)
            print(f"[analyzer] analysis14 written: {p14}")
        except Exception as exc:  # never let the 14-day pass break the daily report
            print(f"[analyzer] analysis14 failed: {exc}", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="analyzer.py", description=__doc__)
    ap.add_argument("--file", help="single daily JSON to analyze")
    ap.add_argument("--config", default=str(SCRIPT_DIR / "config.json"),
                    help="config json path")
    ap.add_argument("--data-dir", help="data dir for baseline/reports")
    ap.add_argument("--reports-dir", help="reports output dir")
    ap.add_argument("--no-14", action="store_true",
                    help="skip the 14-day analysis14 pass")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.file:
        file_path = Path(args.file).resolve()
        if not file_path.exists():
            print(f"[analyzer] file not found: {file_path}", file=sys.stderr)
            return 2
        data_dir = Path(args.data_dir or file_path.parent).resolve()
        reports_dir = Path(args.reports_dir or data_dir / "reports").resolve()
        return run_file(file_path, cfg, data_dir, reports_dir,
                        run14=not args.no_14)

    data_dir = Path(args.data_dir or cfg.get("data_dir", str(Path.cwd()))).resolve()
    latest = stats.latest_day_file(data_dir)
    if latest is None:
        print("[analyzer] no by-day file found", file=sys.stderr)
        return 2
    _, newest = latest
    reports_dir = Path(args.reports_dir or data_dir / "reports").resolve()
    return run_file(newest, cfg, data_dir, reports_dir, run14=not args.no_14)


if __name__ == "__main__":
    sys.exit(main())
