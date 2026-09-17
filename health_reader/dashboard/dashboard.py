#!/usr/bin/env python3
"""HealthReader web dashboard backend (V6 Lane C).

Python standard library only. Serves the frozen JSON API plus the static
single-page UI and generated HTML reports.

Usage:
  python dashboard.py --data-dir DIR [--analyzer-dir DIR]
                      [--port 8890] [--host 0.0.0.0]

Routes:
  GET /                     -> <script>/static/index.html (placeholder if absent)
  GET /static/*             -> files under <script>/static/
  GET /api/status           -> receiver / phone / data / ai status
  GET /api/calendar?month=  -> per-day calendar cells for a month
  GET /api/day?date=        -> one day's summary + deviations
  GET /report/YYYYMMDD      -> <data-dir>/reports/YYYYMMDD_report.html
  GET /analysis14           -> newest <reports_dir>/YYYYMMDD_analysis14.html
  GET /analysis14/YYYYMMDD  -> that day's 14-day analysis report
  GET /api/analysis14/latest -> latest 14-day capability summary (JSON)
  GET /api/analysis14/scores -> 14-day score history, ascending (JSON)
  POST /api/analyze         -> run analyzer.py / analysis14.py for one date (JSON)
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import socket
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, unquote, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
STATIC_DIR = SCRIPT_DIR / "static"

# Filename shapes (mirrors analyzer/stats.py without importing at module load).
DAY_FILE_RE = re.compile(r"^(\d{8})_sleep_hr\.json$")
REPORT_FILE_RE = re.compile(r"^(\d{8})_report\.html$")
ANALYSIS14_FILE_RE = re.compile(r"^(\d{8})_analysis14\.html$")
ANALYSIS14_SCORES_NAME = "analysis14_scores.json"
ANALYSIS14_WINDOW_DAYS = 14
ANALYSIS14_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Capability keys shared with analyzer/analysis14.py (frozen spec §5.1).
ANALYSIS14_CAPS = ("sleep_recovery", "cardio_autonomic",
                   "activity_fitness", "circadian_regularity")

RECEIVER_HOST = "127.0.0.1"
RECEIVER_PORT = 8899

# Manual analyzer trigger (POST /api/analyze): allowed scopes + subprocess budget.
ANALYZE_SCOPES = ("both", "daily", "analysis14")
ANALYZE_TIMEOUT_S = 300

LLM_DEFAULTS = {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "api_key": "",
    "enabled": True,
}
ANALYSIS_DEFAULTS = {
    "baseline_days": 14,
    "min_baseline_days": 3,
    "min_hr_samples": 60,
    "trend_days": 7,
    "thresholds": {
        "deep_min": 45, "deep_ratio": 0.5, "sleep_hours": 5.5,
        "sleep_ratio": 0.7, "hr_delta_bpm": 8, "bedtime_drift_min": 90,
        "energy_below_ratio": 0.6, "spo2_low": 92.0, "temp_delta_c": 0.8,
    },
}
CONFIG_DEFAULTS = {"analysis": ANALYSIS_DEFAULTS, "llm": LLM_DEFAULTS}


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _deep_merge(base: dict, extra: dict) -> dict:
    """Recursively merge extra over base (mirrors analyzer.load_config)."""
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(analyzer_dir: Path) -> dict:
    """Read <analyzer-dir>/config.json merged over defaults; never raises."""
    cfg = json.loads(json.dumps(CONFIG_DEFAULTS))  # deep copy
    try:
        raw = json.loads((analyzer_dir / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if isinstance(raw, dict):
        cfg = _deep_merge(cfg, raw)
    return cfg


def _import_stats(analyzer_dir: Path):
    """Insert analyzer-dir on sys.path and import stats. Returns module or None."""
    try:
        p = str(analyzer_dir)
        if p not in sys.path:
            sys.path.insert(0, p)
        import importlib
        return importlib.import_module("stats")
    except Exception:  # pragma: no cover - defensive
        return None


def _list_data_dates(data_dir: Path) -> list:
    """Sorted list of (YYYYMMDD str, Path) for by-day payload files."""
    out = []
    try:
        for p in data_dir.glob("*_sleep_hr.json"):
            m = DAY_FILE_RE.match(p.name)
            if not m:
                continue
            out.append((m.group(1), p))
    except OSError:
        return []
    out.sort(key=lambda x: x[0])
    return out


def _list_report_dates(reports_dir: Path) -> list:
    """Sorted list of (YYYYMMDD str, Path) for report HTML files."""
    out = []
    try:
        for p in reports_dir.glob("*_report.html"):
            m = REPORT_FILE_RE.match(p.name)
            if not m:
                continue
            out.append((m.group(1), p))
    except OSError:
        return []
    out.sort(key=lambda x: x[0])
    return out


def _report_path(reports_dir: Path, datestr: str) -> Path:
    return reports_dir / f"{datestr}_report.html"


def _list_analysis14_dates(reports_dir: Path) -> list:
    """Sorted list of (YYYYMMDD str, Path) for 14-day analysis HTML files."""
    out = []
    try:
        for p in reports_dir.glob("*_analysis14.html"):
            m = ANALYSIS14_FILE_RE.match(p.name)
            if not m:
                continue
            out.append((m.group(1), p))
    except OSError:
        return []
    out.sort(key=lambda x: x[0])
    return out


def _label_for_score(score) -> str | None:
    """Frozen banding: >=85 优秀 / 70-84 良好 / 60-69 一般 / <60 需关注."""
    if score is None:
        return None
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    if value >= 85:
        return "优秀"
    if value >= 70:
        return "良好"
    if value >= 60:
        return "一般"
    return "需关注"


def _read_analysis14_scores_raw(reports_dir: Path):
    """Parse <reports_dir>/analysis14_scores.json; None when absent/invalid."""
    try:
        return json.loads(
            (reports_dir / ANALYSIS14_SCORES_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _analysis14_entries(raw) -> dict:
    """Normalize the scores file into {YYYY-MM-DD: entry dict}.

    Accepts the frozen dict-of-dates shape (spec §5.3) and, defensively, a
    list of entries carrying a 'date' key. Non-date top-level keys (e.g.
    'window'/'meta') are ignored here but remain available to callers.
    """
    out: dict = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict) and ANALYSIS14_DATE_RE.match(str(key)):
                out[str(key)] = value
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            d = item.get("date")
            if isinstance(d, str) and ANALYSIS14_DATE_RE.match(d):
                out[d] = item
    return out


def _analysis14_window(raw, datestr: str, entry: dict) -> dict:
    """Resolve the analysis window; derive [date-13, date] when absent."""
    candidates = []
    if isinstance(entry, dict):
        candidates.append(entry.get("window"))
    if isinstance(raw, dict):
        candidates.append(raw.get("window"))
        windows = raw.get("windows")
        if isinstance(windows, dict):
            candidates.append(windows.get(datestr))
        meta = raw.get("meta")
        if isinstance(meta, dict):
            candidates.append(meta.get("window"))
    for w in candidates:
        if isinstance(w, dict) and w.get("start") and w.get("end"):
            return {"start": str(w["start"]), "end": str(w["end"])}
    try:
        end = datetime.strptime(datestr, "%Y-%m-%d").date()
    except ValueError:
        return {"start": None, "end": None}
    start = end - timedelta(days=ANALYSIS14_WINDOW_DAYS - 1)
    return {"start": start.isoformat(), "end": end.isoformat()}


def _scan_llm_source(html_path: Path) -> str | None:
    """'llm' if report contains 'LLM 解读', 'template' if '模板解读', else None."""
    try:
        text = html_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if "LLM 解读" in text:
        return "llm"
    if "模板解读" in text:
        return "template"
    return None


def _scan_llm_source14(html_path: Path) -> str | None:
    """'llm' if 14-day report has 'LLM 叙述', 'template' if '模板叙述', else None."""
    try:
        text = html_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if "LLM 叙述" in text:
        return "llm"
    if "模板叙述" in text:
        return "template"
    return None


def _duration_text(sec) -> str | None:
    """Frozen summary format: e.g. 10小时35分 / 8小时 / 42分钟."""
    if sec is None:
        return None
    try:
        minutes = int(round(float(sec) / 60.0))
    except (TypeError, ValueError):
        return None
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}小时{m}分"
    if h:
        return f"{h}小时"
    return f"{m}分钟"


def _receiver_listening(host: str = RECEIVER_HOST, port: int = RECEIVER_PORT) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            return s.connect_ex((host, port)) == 0
    except OSError:
        return False


def _empty_summary() -> dict:
    return {
        "sleep_score": None,
        "sleep_duration": None,
        "hr_night_mean": None,
        "spo2_night_mean": None,
        "steps": None,
        "energy": None,
        "water_ml": None,
    }


# --------------------------------------------------------------------------- #
# request handler
# --------------------------------------------------------------------------- #
class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "HealthDashboard/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def srv(self) -> "DashboardServer":
        """Typed accessor for the custom server carrying app config."""
        return cast("DashboardServer", self.server)

    # ---- low level writers -------------------------------------------------
    def _send_bytes(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _send_html(self, html: str, status: int = 200) -> None:
        self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8", status)

    def _send_text(self, text: str, status: int = 200) -> None:
        self._send_bytes(text.encode("utf-8"), "text/plain; charset=utf-8", status)

    # ---- logging -----------------------------------------------------------
    def log_message(self, format, *args):  # one line per request
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sys.stdout.write(
            f"[dashboard] {stamp} {self.address_string()} {format % args}\n")
        sys.stdout.flush()

    # ---- routing -----------------------------------------------------------
    def do_GET(self):  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            if path == "/" or path == "":
                return self._serve_index()
            if path == "/api/status":
                return self._send_json(self._api_status())
            if path == "/api/calendar":
                return self._api_calendar(query)
            if path == "/api/day":
                return self._api_day(query)
            if path == "/api/analysis14/latest":
                return self._api_analysis14_latest()
            if path == "/api/analysis14/scores":
                return self._api_analysis14_scores()
            if path == "/analysis14":
                return self._serve_analysis14_latest()
            if path.startswith("/analysis14/"):
                return self._serve_analysis14_day(path)
            if path.startswith("/report/"):
                return self._serve_report(path)
            if path.startswith("/static/"):
                return self._serve_static(path[len("/static/"):])
            if path.startswith("/api/"):
                return self._send_json({"error": "not_found", "path": path}, 404)
            return self._send_html(_placeholder_page("404", "页面不存在"), 404)
        except Exception:  # never crash the handler
            traceback.print_exc()
            try:
                self._send_json({"error": "internal_error"}, 500)
            except Exception:
                pass

    def do_POST(self):  # noqa: N802
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path == "/api/analyze":
                return self._api_analyze()
            if path.startswith("/api/"):
                return self._send_json({"error": "not_found", "path": path}, 404)
            return self._send_html(_placeholder_page("404", "页面不存在"), 404)
        except Exception:  # never crash the handler
            traceback.print_exc()
            try:
                self._send_json({"error": "internal_error"}, 500)
            except Exception:
                pass

    # ---- static UI ---------------------------------------------------------
    def _serve_index(self):
        index = STATIC_DIR / "index.html"
        if index.is_file():
            try:
                body = index.read_bytes()
                return self._send_bytes(body, "text/html; charset=utf-8")
            except OSError:
                pass
        return self._send_html(_placeholder_page(
            "看板 UI 尚未安装",
            "缺少 <code>static/index.html</code>。后端 API 已可用："
            "<code>/api/status</code>、<code>/api/calendar</code>、"
            "<code>/api/day</code>、<code>/report/YYYYMMDD</code>。"), 200)

    def _serve_static(self, rel: str):
        rel = rel.lstrip("/")
        if not rel:
            rel = "index.html"
        base = STATIC_DIR.resolve()
        target = (base / rel).resolve()
        try:
            target.relative_to(base)  # path traversal guard
        except ValueError:
            return self._send_text("forbidden", 403)
        if not target.is_file():
            return self._send_text("not found", 404)
        ctype = _guess_type(target)
        try:
            return self._send_bytes(target.read_bytes(), ctype)
        except OSError:
            return self._send_text("not found", 404)

    # ---- report ------------------------------------------------------------
    def _serve_report(self, path: str):
        m = re.match(r"^/report/(\d{8})/?$", path)
        if not m:
            return self._send_html(_placeholder_page("404", "报告地址无效"), 404)
        datestr = m.group(1)
        report = _report_path(self.srv.reports_dir, datestr)
        if report.is_file():
            try:
                return self._send_bytes(report.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                pass
        pretty = f"{datestr[:4]}-{datestr[4:6]}-{datestr[6:]}"
        return self._send_html(_placeholder_page(
            "报告不存在",
            f"未找到 {pretty} 的 HTML 报告。"), 404)

    # ---- 14-day analysis reports ------------------------------------------
    def _serve_analysis14_latest(self):
        entries = _list_analysis14_dates(self.srv.reports_dir)
        if entries:
            try:
                return self._send_bytes(entries[-1][1].read_bytes(),
                                        "text/html; charset=utf-8")
            except OSError:
                pass
        return self._send_html(_placeholder_page(
            "暂无 14 天分析",
            "暂无 14 天分析，等待今日流水线运行。"), 200)

    def _serve_analysis14_day(self, path: str):
        m = re.match(r"^/analysis14/(\d{8})/?$", path)
        if not m:
            return self._send_html(
                _placeholder_page("404", "14 天报告地址无效"), 404)
        datestr = m.group(1)
        report = self.srv.reports_dir / f"{datestr}_analysis14.html"
        if report.is_file():
            try:
                return self._send_bytes(report.read_bytes(),
                                        "text/html; charset=utf-8")
            except OSError:
                pass
        pretty = f"{datestr[:4]}-{datestr[4:6]}-{datestr[6:]}"
        return self._send_html(_placeholder_page(
            "14 天报告不存在",
            f"未找到 {pretty} 的 14 天分析报告。"), 404)

    # ---- API: status -------------------------------------------------------
    def _api_status(self):
        data_dir = self.srv.data_dir
        reports_dir = self.srv.reports_dir
        cfg = self.srv.cfg
        stats = self.srv.stats

        data_entries = _list_data_dates(data_dir)
        report_entries = _list_report_dates(reports_dir)
        report_dates = {d for d, _ in report_entries}
        data_dates = [d for d, _ in data_entries]

        total_days = len(data_entries)
        first_date = data_dates[0] if data_dates else None
        last_date = data_dates[-1] if data_dates else None
        days_without_report = [d for d in data_dates if d not in report_dates]

        # phone: newest upload = newest by-day file mtime; fall back to _latest.json
        last_upload_dt = None
        candidates = [p for _, p in data_entries]
        latest_file = data_dir / "_latest.json"
        if latest_file.is_file():
            candidates.append(latest_file)
        for p in candidates:
            try:
                mt = datetime.fromtimestamp(p.stat().st_mtime)
            except OSError:
                continue
            if last_upload_dt is None or mt > last_upload_dt:
                last_upload_dt = mt
        now = datetime.now()
        last_upload_local = last_upload_dt.strftime("%Y-%m-%d %H:%M") if last_upload_dt else None
        ago_hours = round((now - last_upload_dt).total_seconds() / 3600.0, 1) \
            if last_upload_dt else None

        # ai config
        llm = (cfg.get("llm") or {}) if isinstance(cfg.get("llm"), dict) else {}
        enabled = bool(llm.get("enabled", True))
        api_key = llm.get("api_key") or ""
        key_present = bool(str(api_key).strip())
        model = llm.get("model", LLM_DEFAULTS["model"])

        latest_report_date = report_entries[-1][0] if report_entries else None
        latest_report_source = _scan_llm_source(report_entries[-1][1]) \
            if report_entries else None

        return {
            "receiver": {
                "listening": _receiver_listening(),
                "host": RECEIVER_HOST,
                "port": RECEIVER_PORT,
            },
            "phone": {
                "last_upload_local": last_upload_local,
                "last_upload_ago_hours": ago_hours,
                "latest_date": last_date,
            },
            "data": {
                "total_days": total_days,
                "first_date": first_date,
                "last_date": last_date,
                "days_without_report": days_without_report,
            },
            "ai": {
                "enabled": enabled,
                "key_present": key_present,
                "model": model,
                "latest_report_source": latest_report_source,
                "latest_report_date": latest_report_date,
            },
            "generated_at_local": now.strftime("%Y-%m-%d %H:%M"),
        }

    # ---- API: calendar -----------------------------------------------------
    def _api_calendar(self, query):
        month = (query.get("month") or [None])[0]
        if not month:
            return self._send_json({"error": "missing 'month' (YYYY-MM)"}, 400)
        try:
            start = datetime.strptime(month, "%Y-%m").date()
        except ValueError:
            return self._send_json({"error": "invalid month, expected YYYY-MM"}, 400)

        data_dir = self.srv.data_dir
        reports_dir = self.srv.reports_dir
        data_dates = {d for d, _ in _list_data_dates(data_dir)}
        report_paths = {d: p for d, p in _list_report_dates(reports_dir)}

        _, n_days = calendar.monthrange(start.year, start.month)
        today = date.today()
        days = []
        for daynum in range(1, n_days + 1):
            d = date(start.year, start.month, daynum)
            datestr = d.strftime("%Y%m%d")
            has_data = datestr in data_dates
            report_path = report_paths.get(datestr)
            has_report = report_path is not None
            llm_source = _scan_llm_source(report_path) if report_path else None
            # include days with data, plus elapsed days up to today (greyed)
            if not has_data and d > today:
                continue
            if not has_data:
                status = "no_data"
            elif not has_report:
                status = "no_report"
            elif llm_source == "template":
                status = "no_ai"
            else:
                status = "ok"
            days.append({
                "date": d.strftime("%Y-%m-%d"),
                "has_data": has_data,
                "has_report": has_report,
                "llm_source": llm_source,
                "status": status,
            })
        return self._send_json({"month": month, "days": days})

    # ---- API: day ----------------------------------------------------------
    def _api_day(self, query):
        datestr = (query.get("date") or [None])[0]
        if not datestr:
            return self._send_json({"error": "missing 'date' (YYYYMMDD)"}, 400)
        if not re.match(r"^\d{8}$", datestr):
            return self._send_json({"error": "invalid date, expected YYYYMMDD"}, 400)
        try:
            datetime.strptime(datestr, "%Y%m%d")
        except ValueError:
            return self._send_json({"error": "invalid date, expected YYYYMMDD"}, 400)

        data_dir = self.srv.data_dir
        reports_dir = self.srv.reports_dir
        stats = self.srv.stats
        cfg = self.srv.cfg
        analysis = cfg.get("analysis") or {}

        payload_path = data_dir / f"{datestr}_sleep_hr.json"
        report = _report_path(reports_dir, datestr)
        has_data = payload_path.is_file()
        has_report = report.is_file()
        llm_source = _scan_llm_source(report) if has_report else None

        summary = _empty_summary()
        deviations: list = []

        if has_data and stats is not None:
            try:
                payload = stats.load_payload(payload_path)
            except Exception:
                payload = None
            if payload is not None:
                rdate = None
                try:
                    rdate = stats.infer_report_date(payload, payload_path.name)
                except Exception:
                    rdate = None
                if rdate is None:
                    rdate = datetime.strptime(datestr, "%Y%m%d").date()
                try:
                    day = stats.compute_day_stats(payload)
                except Exception:
                    day = None
                if day is not None:
                    summary = self._summary_from_day(day)
                    try:
                        base = stats.build_baseline(
                            data_dir, rdate,
                            int(analysis.get("baseline_days",
                                             ANALYSIS_DEFAULTS["baseline_days"])),
                            int(analysis.get("min_hr_samples",
                                             ANALYSIS_DEFAULTS["min_hr_samples"])))
                        comp = stats.compare_baseline(day, base, analysis)
                        deviations = comp.get("deviations", []) or []
                    except Exception:
                        traceback.print_exc()
                        deviations = []
            else:
                has_data = False

        return self._send_json({
            "date": f"{datestr[:4]}-{datestr[4:6]}-{datestr[6:]}",
            "has_data": has_data,
            "has_report": has_report,
            "report_url": f"/report/{datestr}",
            "llm_source": llm_source,
            "summary": summary,
            "deviations": deviations,
        })

    # ---- API: 14-day analysis ---------------------------------------------
    def _api_analysis14_latest(self):
        """Latest capability summary. Authoritative source: analysis14_scores.json."""
        reports_dir = self.srv.reports_dir
        data_dir = self.srv.data_dir
        raw = _read_analysis14_scores_raw(reports_dir)
        entries = _analysis14_entries(raw)

        # Coverage / freshness metadata (independent of the scores file).
        report_entries = _list_analysis14_dates(reports_dir)
        exists = bool(report_entries)
        latest14_date = report_entries[-1][0] if report_entries else None
        llm_source = _scan_llm_source14(report_entries[-1][1]) if report_entries else None
        data_entries = _list_data_dates(data_dir)
        latest_data_compact = data_entries[-1][0] if data_entries else None
        latest_data_date = (f"{latest_data_compact[:4]}-{latest_data_compact[4:6]}-"
                            f"{latest_data_compact[6:]}") if latest_data_compact else None
        stale = bool(exists and latest14_date != latest_data_compact)

        meta = {
            "exists": exists,
            "stale": stale,
            "llm_source": llm_source,
            "latest_data_date": latest_data_date,
        }

        if not entries:
            return self._send_json({"date": None, **meta})
        datestr = max(entries)  # ISO dates sort lexicographically
        entry = entries[datestr]
        overall = entry.get("overall")
        capabilities = {
            cap: {"score": entry.get(cap), "label": _label_for_score(entry.get(cap))}
            for cap in ANALYSIS14_CAPS
        }
        return self._send_json({
            "date": datestr,
            "window": _analysis14_window(raw, datestr, entry),
            "overall": overall,
            "label": _label_for_score(overall),
            "capabilities": capabilities,
            **meta,
        })

    def _api_analysis14_scores(self):
        """Ascending score history from analysis14_scores.json."""
        raw = _read_analysis14_scores_raw(self.srv.reports_dir)
        entries = _analysis14_entries(raw)
        scores = []
        for datestr in sorted(entries):
            entry = entries[datestr]
            row = {"date": datestr, "overall": entry.get("overall")}
            for cap in ANALYSIS14_CAPS:
                row[cap] = entry.get(cap)
            scores.append(row)
        return self._send_json({"scores": scores})

    # ---- API: manual analyzer trigger -------------------------------------
    def _read_json_body(self):
        """Read + parse the request body.

        Returns {} for a missing/empty body, a dict for valid JSON objects,
        and None for malformed JSON / non-object payloads.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return {}
        try:
            raw = self.rfile.read(length)
        except OSError:
            return None
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _api_analyze(self):
        """Run the fixed analyzer command for one validated date (synchronous)."""
        body = self._read_json_body()
        if body is None:
            return self._send_json({"error": "invalid JSON body"}, 400)

        datestr = body.get("date")
        scope = body.get("scope") or "both"
        if not isinstance(datestr, str) or not re.match(r"^\d{8}$", datestr):
            return self._send_json(
                {"error": "invalid 'date', expected YYYYMMDD"}, 400)
        try:
            datetime.strptime(datestr, "%Y%m%d")
        except ValueError:
            return self._send_json(
                {"error": "invalid 'date', expected YYYYMMDD"}, 400)
        if scope not in ANALYZE_SCOPES:
            return self._send_json(
                {"error": "invalid 'scope', expected both|daily|analysis14"}, 400)

        data_file = self.srv.data_dir / f"{datestr}_sleep_hr.json"
        if not data_file.is_file():
            return self._send_json(
                {"error": f"no data file for {datestr}"}, 400)

        analyzer_dir = self.srv.analyzer_dir
        script = analyzer_dir / (
            "analysis14.py" if scope == "analysis14" else "analyzer.py")
        if not script.is_file():
            return self._send_json(
                {"error": f"analyzer script not found: {script.name}"}, 500)

        cmd = [sys.executable, str(script), "--file", str(data_file)]
        started = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, cwd=str(analyzer_dir), capture_output=True, text=True,
                timeout=ANALYZE_TIMEOUT_S)
            duration = round(time.monotonic() - started, 3)
            return self._send_json({
                "ok": proc.returncode == 0,
                "date": datestr,
                "scope": scope,
                "returncode": proc.returncode,
                "duration_s": duration,
                "stdout": proc.stdout or "",
                "stderr": proc.stderr or "",
            })
        except subprocess.TimeoutExpired as exc:
            duration = round(time.monotonic() - started, 3)
            out = exc.stdout or ""
            err = exc.stderr or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", "replace")
            note = f"timeout after {ANALYZE_TIMEOUT_S}s"
            return self._send_json({
                "ok": False,
                "date": datestr,
                "scope": scope,
                "returncode": -1,
                "duration_s": duration,
                "stdout": out,
                "stderr": (err + "\n" if err else "") + note,
            })
        except OSError as exc:
            return self._send_json(
                {"error": f"failed to run analyzer: {exc}"}, 500)

    @staticmethod
    def _summary_from_day(day: dict) -> dict:
        s = day.get("sleep") or {}
        hr_win = (day.get("hr") or {}).get("sleep_window") or {}
        spo2 = ((day.get("vitals") or {}).get("spo2_window")) or {}
        return {
            "sleep_score": s.get("score"),
            "sleep_duration": _duration_text(s.get("duration_s")),
            "hr_night_mean": hr_win.get("mean"),
            "spo2_night_mean": spo2.get("mean"),
            "steps": (day.get("steps") or {}).get("count"),
            "energy": (day.get("energy") or {}).get("score"),
            "water_ml": (day.get("water") or {}).get("total_ml"),
        }


def _guess_type(path: Path) -> str:
    import mimetypes
    ctype, _ = mimetypes.guess_type(str(path))
    return ctype or "application/octet-stream"


def _placeholder_page(title: str, message: str) -> str:
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{title}</title>"
        "<style>body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;"
        "background:#f5f7fb;color:#1f2933;margin:0;padding:48px;}"
        ".card{max-width:560px;margin:0 auto;background:#fff;border-radius:16px;"
        "padding:32px;box-shadow:0 4px 20px rgba(15,23,42,.08);}"
        "h1{font-size:20px;margin:0 0 12px;}p{line-height:1.7;color:#52606d;}"
        "code{background:#eef2f7;border-radius:6px;padding:2px 6px;font-size:13px;}"
        "</style></head><body><div class=\"card\">"
        f"<h1>{title}</h1><p>{message}</p></div></body></html>"
    )


# --------------------------------------------------------------------------- #
# server bootstrap
# --------------------------------------------------------------------------- #
class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, *, data_dir, reports_dir, analyzer_dir,
                 cfg, stats):
        super().__init__(addr, handler)
        self.data_dir = data_dir
        self.reports_dir = reports_dir
        self.analyzer_dir = analyzer_dir
        self.cfg = cfg
        self.stats = stats


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="dashboard.py",
        description="HealthReader web dashboard backend (stdlib only).")
    ap.add_argument("--data-dir", required=True,
                    help="data dir containing daily JSON + reports/ (required)")
    ap.add_argument("--analyzer-dir", default=None,
                    help="analyzer dir for stats.py import "
                         "(default: <script>/../analyzer)")
    ap.add_argument("--port", type=int, default=8890, help="listen port (default 8890)")
    ap.add_argument("--host", default="0.0.0.0", help="bind host (default 0.0.0.0)")
    return ap


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    data_dir = Path(args.data_dir).expanduser().resolve()
    analyzer_dir = (Path(args.analyzer_dir).expanduser().resolve()
                    if args.analyzer_dir else (SCRIPT_DIR.parent / "analyzer").resolve())
    reports_dir = data_dir / "reports"

    stats = _import_stats(analyzer_dir)
    if stats is None:
        print(f"[dashboard] warning: could not import stats from {analyzer_dir}; "
              "/api/day will report null summaries", file=sys.stderr)

    cfg = load_config(analyzer_dir)

    if not data_dir.is_dir():
        print(f"[dashboard] warning: data dir not found: {data_dir}", file=sys.stderr)

    try:
        httpd = DashboardServer(
            (args.host, args.port), DashboardHandler,
            data_dir=data_dir, reports_dir=reports_dir,
            analyzer_dir=analyzer_dir, cfg=cfg, stats=stats)
    except OSError as exc:
        print(f"[dashboard] cannot bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1

    print(f"[dashboard] data-dir     : {data_dir}")
    print(f"[dashboard] reports-dir  : {reports_dir}")
    print(f"[dashboard] analyzer-dir : {analyzer_dir} (stats={'ok' if stats else 'missing'})")
    print(f"[dashboard] serving on   : http://{args.host}:{args.port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] shutting down")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
