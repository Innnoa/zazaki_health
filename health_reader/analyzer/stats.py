"""Deterministic daily statistics for sleep/HR payloads (task A7)."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

LOCAL_OFFSET = timedelta(hours=8)  # Asia/Shanghai, fixed (no DST)
DAY_FILE_RE = re.compile(r"^(\d{8})_sleep_hr\.json$")
STAGE_ORDER = ("DEEP", "LIGHT", "REM", "AWAKE", "OTHER")
KNOWN_STAGES = {"DEEP", "LIGHT", "REM", "AWAKE"}
STAGE_COLORS = {
    "DEEP": "#4263eb",
    "LIGHT": "#74c0fc",
    "REM": "#f783ac",
    "AWAKE": "#dee2e6",
    "OTHER": "#adb5bd",
}


def parse_ts(value: Any) -> Optional[datetime]:
    """ISO-8601 UTC (Z allowed) -> naive UTC datetime. Non-parseable -> None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def to_local(dt: datetime) -> datetime:
    return dt + LOCAL_OFFSET


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt_duration(sec: Optional[float]) -> str:
    if sec is None:
        return "—"
    minutes = int(round(sec / 60.0))
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def load_payload(path: str | Path) -> Optional[dict]:
    """Load + validate a daily payload. None when not a dict / unparseable /
    contains no usable heart_rate or sleep items (stub probes, empty)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _parse_payload_dict(data)


def _parse_payload_dict(data: Any) -> Optional[dict]:
    """Parse a JSON-ish payload dict into the internal shape (datetime
    fields, (datetime, hr) tuples). None when not a dict or empty."""
    if not isinstance(data, dict):
        return None
    hr_items: list = []
    for item in data.get("heart_rate") or []:
        if not isinstance(item, dict):
            continue
        t = parse_ts(item.get("t"))
        hr = _num(item.get("hr"))
        if t is not None and hr is not None:
            hr_items.append((t, hr))
    sleep_items: list = []
    for rec in data.get("sleep") or []:
        if not isinstance(rec, dict):
            continue
        score = _num(rec.get("score"))
        dur = _num(rec.get("duration_s"))
        if score is None or dur is None:
            continue
        rec_t = parse_ts(rec.get("t"))
        sessions = []
        for sess in rec.get("sessions") or []:
            if not isinstance(sess, dict):
                continue
            st = parse_ts(sess.get("start"))
            en = parse_ts(sess.get("end"))
            if st is None or en is None or en <= st:
                continue
            stages = []
            for stg in sess.get("stages") or []:
                if not isinstance(stg, dict):
                    continue
                a = parse_ts(stg.get("start"))
                b = parse_ts(stg.get("end"))
                if a is None or b is None or b <= a:
                    continue
                typ = str(stg.get("type") or "").upper()
                stages.append({"type": typ if typ in KNOWN_STAGES else "OTHER",
                               "start": a, "end": b})
            sessions.append({"start": st, "end": en, "stages": stages})
        sleep_items.append({"t": rec_t, "score": score,
                            "duration_s": dur, "sessions": sessions})
    blood_oxygen = _vec(data.get("blood_oxygen"), ("spo2", "min", "max"))
    skin_temp = _vec(data.get("skin_temperature"), ("temp", "min", "max"))
    energy_score = _vec(data.get("energy_score"), ("score",))
    water_intake = _vec(data.get("water_intake"), ("amount",))
    body_comp = _vec(data.get("body_composition"),
                     ("weight", "height", "body_fat", "skeletal_muscle",
                      "muscle_mass", "basal_metabolic_rate", "total_body_water"),
                     t_required=False)
    steps = _vec(data.get("steps"), ("count",))
    activity = _vec(data.get("activity"),
                    ("active_time_s", "active_calories",
                     "total_calories_burned", "distance_m"))
    floors = _vec(data.get("floors"), ("count",))
    blood_pressure = _vec(data.get("blood_pressure"),
                          ("systolic", "diastolic", "pulse"))
    body_temperature = _vec(data.get("body_temperature"), ("temp",))
    exercise = []
    for it in data.get("exercise") or []:
        if not isinstance(it, dict):
            continue
        t = parse_ts(it.get("t"))
        if t is None:
            continue
        typ = it.get("type")
        typ = str(typ).strip() if typ is not None else "UNKNOWN"
        title = it.get("title")
        title = title if isinstance(title, str) and title.strip() else None
        dur = _num(it.get("duration_s"))
        cal = _num(it.get("calories"))
        exercise.append({"t": t, "type": typ, "title": title,
                         **({"duration_s": dur} if dur is not None else {}),
                         **({"calories": cal} if cal is not None else {})})

    blood_glucose = []
    for it in data.get("blood_glucose") or []:
        if not isinstance(it, dict):
            continue
        t = parse_ts(it.get("t"))
        gl = _num(it.get("glucose"))
        if t is None or gl is None:
            continue
        mt = it.get("measurement_type")
        mt = str(mt).strip() if isinstance(mt, str) and mt.strip() else None
        blood_glucose.append({"t": t, "glucose": gl,
                              **({"measurement_type": mt} if mt else {})})

    nutrition = []
    for it in data.get("nutrition") or []:
        if not isinstance(it, dict):
            continue
        t = parse_ts(it.get("t"))
        if t is None:
            continue
        rec = {"t": t}
        for key in ("calories", "carbs", "protein", "fat"):
            v = _num(it.get(key))
            if v is not None:
                rec[key] = v
        title = it.get("title")
        title = title if isinstance(title, str) and title.strip() else None
        mt = it.get("meal_type")
        mt = mt if isinstance(mt, str) and mt.strip() else None
        if title:
            rec["title"] = title
        if mt:
            rec["meal_type"] = mt
        if len(rec) > 1:  # 至少 t 之外一个字段
            nutrition.append(rec)

    if not hr_items and not sleep_items:
        return None
    return {"heart_rate": hr_items, "sleep": sleep_items,
            "blood_oxygen": blood_oxygen, "skin_temperature": skin_temp,
            "energy_score": energy_score, "exercise": exercise,
            "water_intake": water_intake, "body_composition": body_comp,
            "steps": steps, "activity": activity, "floors": floors,
            "blood_pressure": blood_pressure, "blood_glucose": blood_glucose,
            "body_temperature": body_temperature, "nutrition": nutrition}


def _vec_item(it, keys_numeric, t_required=True):
    """Parse one raw array item into a normalized dict with naive-UTC 't'.
    Items whose dp value is missing/invalid (no usable numeric field) are
    dropped entirely, per spec: 逐 dp，dp 值缺失则该条跳过."""
    if not isinstance(it, dict):
        return None
    t = parse_ts(it.get("t"))
    if t_required and t is None:
        return None
    out = {}
    for k in keys_numeric:
        v = _num(it.get(k))
        if v is not None:
            out[k] = v
    if not out:
        return None
    if t is not None:
        out["t"] = t
    return out


def _vec(items, keys_numeric, t_required=True):
    out = []
    for it in items or []:
        item = _vec_item(it, keys_numeric, t_required)
        if item is not None:
            out.append(item)
    return out


def _is_raw_payload(payload: dict) -> bool:
    """True when the payload still carries raw JSON string timestamps
    (dict HR items / str session bounds) instead of the parsed shape."""
    hr = payload.get("heart_rate") or []
    if hr:
        return not isinstance(hr[0], tuple)
    for rec in payload.get("sleep") or []:
        if not isinstance(rec, dict):
            continue
        for sess in rec.get("sessions") or []:
            if isinstance(sess, dict) and sess.get("start") is not None:
                return isinstance(sess["start"], str)
    return False


def normalize_payload(payload: dict) -> dict:
    """Return the payload in parsed shape (datetime fields). Accepts either
    a raw JSON-ish dict (string timestamps) or an already parsed payload;
    parsed input passes through unchanged (no-op)."""
    if not _is_raw_payload(payload):
        return payload
    out = _parse_payload_dict(payload)
    return payload if out is None else out


def infer_report_date(payload: dict, filename: str = "") -> Optional[date]:
    """Report date. Filename YYYYMMDD first; else content inference:
    sleep wake (latest session end, local) date, else HR local-date mode."""
    payload = normalize_payload(payload)
    m = DAY_FILE_RE.match(filename or "")
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            pass
    ends = [to_local(s["end"]).date() for rec in payload["sleep"]
            for s in rec["sessions"]]
    if not ends:
        ends = [to_local(rec["t"]).date() for rec in payload["sleep"] if rec["t"]]
    if ends:
        return max(ends)
    if payload["heart_rate"]:
        c = Counter(to_local(t).date() for t, _ in payload["heart_rate"])
        return c.most_common(1)[0][0]
    return None


def classify_interval(gap_s: float) -> str:
    if gap_s <= 105:
        return "1min"
    if gap_s <= 420:
        return "5min"
    if gap_s <= 840:
        return "10min"
    return "不规则"


def hr_summary(points: list) -> Optional[dict]:
    """points: [(naive_utc_datetime, hr)]. None when empty."""
    if not points:
        return None
    pts = sorted(points, key=lambda x: x[0])
    hrs = [h for _, h in pts]
    gaps = [(pts[i + 1][0] - pts[i][0]).total_seconds()
            for i in range(len(pts) - 1)]
    cls = Counter(classify_interval(g) for g in gaps) if gaps else Counter()
    return {
        "count": len(pts),
        "mean": round(sum(hrs) / len(hrs), 1),
        "min": round(min(hrs), 1),
        "max": round(max(hrs), 1),
        "first_local": to_local(pts[0][0]).strftime("%H:%M"),
        "last_local": to_local(pts[-1][0]).strftime("%H:%M"),
        "gap": cls.most_common(1)[0][0] if cls else "单点",
    }


def _window_values(points, window):
    """points: [(naive_utc_dt, value)]; values whose t inside [w0,w1]."""
    if window is None:
        return []
    w0, w1 = window
    return [v for t, v in points if w0 <= t <= w1]


def _series_summary(values):
    """{count, mean, min, max} or None when empty."""
    if not values:
        return None
    return {"count": len(values),
            "mean": round(sum(values) / len(values), 2),
            "min": round(min(values), 2),
            "max": round(max(values), 2)}


def compute_day_stats(payload: dict) -> dict:
    """Produce a JSON-able daily stats dict (plus '_charts' for the builder)."""
    payload = normalize_payload(payload)
    sleep_recs = payload["sleep"]
    hr = payload["heart_rate"]

    # Pick the sleep record that carries the longest session; else first record.
    main_rec = None
    if sleep_recs:
        with_sess = [r for r in sleep_recs if r["sessions"]]
        main_rec = max(with_sess, key=lambda r: max(
            (s["end"] - s["start"]).total_seconds()
            for s in r["sessions"])) if with_sess else sleep_recs[0]

    score = dur = None
    bedtime = wake = None
    window = None            # (start_utc, end_utc) of longest session
    deep_min = 0.0
    stage_minutes: dict = {}
    timeline: list = []
    if main_rec is not None:
        score = main_rec["score"]
        dur = main_rec["duration_s"]
        if main_rec["sessions"]:
            s0 = max(main_rec["sessions"],
                     key=lambda s: (s["end"] - s["start"]).total_seconds())
            window = (s0["start"], s0["end"])
            bedtime = to_local(s0["start"]).strftime("%H:%M")
            wake = to_local(s0["end"]).strftime("%H:%M")
            segs = []
            for sess in main_rec["sessions"]:
                for stg in sess["stages"]:
                    a = to_local(stg["start"])
                    b = to_local(stg["end"])
                    segs.append({"type": stg["type"], "start": a, "end": b})
            if segs:
                t0 = to_local(window[0])
                segs.sort(key=lambda x: x["start"])
                for x in segs:
                    minutes = (x["end"] - x["start"]).total_seconds() / 60.0
                    stage_minutes[x["type"]] = stage_minutes.get(x["type"], 0.0) + minutes
                    timeline.append({"type": x["type"],
                                     "s": int(round((x["start"] - t0).total_seconds() / 60.0)),
                                     "e": int(round((x["end"] - t0).total_seconds() / 60.0))})
                for seg in timeline:
                    seg["e"] = max(seg["e"], seg["s"])
                stage_minutes = {k: round(v, 1) for k, v in stage_minutes.items()}
                deep_min = stage_minutes.get("DEEP", 0.0)

    stage_list = []
    total_min = sum(stage_minutes.values())
    for typ in STAGE_ORDER:
        if typ in stage_minutes:
            stage_list.append({"type": typ, "minutes": stage_minutes[typ],
                               "pct": round(100.0 * stage_minutes[typ] / total_min, 1)
                               if total_min else 0.0})

    # HR split by main-sleep window (same local day only for awake side).
    win_summary = awake_summary = None
    win_series: list = []
    if window is not None:
        win_pts = [p for p in hr if window[0] <= p[0] <= window[1]]
        d = to_local(window[0]).date()
        awake_pts = [p for p in hr if to_local(p[0]).date() == d
                     and not (window[0] <= p[0] <= window[1])]
    else:
        win_pts, awake_pts = [], hr
    win_summary = hr_summary(win_pts)
    awake_summary = hr_summary(awake_pts)
    if win_summary:
        t0 = to_local(window[0]) if window else None
        if t0 is not None:
            win_series = [[int(round((to_local(p) - t0).total_seconds() / 60.0)), h]
                          for p, h in win_pts]

    # ---- v3: vitals over main sleep window ----
    bo_pts = [(x["t"], x["spo2"]) for x in payload["blood_oxygen"]
              if x.get("spo2") is not None]
    sk_pts = [(x["t"], x["temp"]) for x in payload["skin_temperature"]
              if x.get("temp") is not None]
    spo2_sum = _series_summary(_window_values(bo_pts, window))
    temp_sum = _series_summary(_window_values(sk_pts, window))

    # ---- v4: window curves + 24h HR distribution ----
    def _win_minutes(points, window):
        """[[minutes since local bedtime, value], ...] ascending, in window."""
        if window is None:
            return []
        w0, w1 = window
        t0 = to_local(w0)
        out = []
        for t, v in points:
            if w0 <= t <= w1:
                out.append([int(round((to_local(t) - t0).total_seconds() / 60.0)), v])
        out.sort(key=lambda x: x[0])
        return out

    spo2_series = _win_minutes(bo_pts, window)
    temp_series = _win_minutes(sk_pts, window)

    hour_buckets: dict = {}
    for t, h in hr:
        hour_buckets.setdefault(to_local(t).hour, []).append(h)
    hr_24h = []
    for hh in range(24):
        vals = hour_buckets.get(hh)
        hr_24h.append({"h": hh,
                       "mean": round(sum(vals) / len(vals), 1) if vals else None,
                       "count": len(vals) if vals else 0})

    # ---- v3: energy = latest score dp in file ----
    en_items = [x for x in payload["energy_score"] if x.get("score") is not None]
    energy_score = None
    if en_items:
        en_items.sort(key=lambda x: x["t"])
        energy_score = float(en_items[-1]["score"])

    # ---- v3: exercise aggregation ----
    ex_items = payload["exercise"]
    ex_types = {}
    total_dur = 0.0
    total_cal = 0.0
    has_dur = has_cal = False
    for x in ex_items:
        typ = x.get("type") or "UNKNOWN"
        row = ex_types.setdefault(typ, {"type": typ, "count": 0,
                                        "duration_s": 0.0, "calories": 0.0})
        row["count"] += 1
        if x.get("duration_s") is not None:
            row["duration_s"] += x["duration_s"]
            total_dur += x["duration_s"]
            has_dur = True
        if x.get("calories") is not None:
            row["calories"] += x["calories"]
            total_cal += x["calories"]
            has_cal = True
    ex_types_list = list(ex_types.values())
    for row in ex_types_list:
        if not has_dur or row["duration_s"] == 0.0:
            row["duration_s"] = None
        if not has_cal or row["calories"] == 0.0:
            row["calories"] = None

    # ---- v3: water total ----
    w_items = [x["amount"] for x in payload["water_intake"]
               if x.get("amount") is not None and x["amount"] > 0]
    water_ml = round(sum(w_items), 1) if w_items else None

    # ---- v3: body composition (latest dp with any core field) ----
    bc_items = payload["body_composition"]
    body_comp = None
    if bc_items:
        best = None
        for x in bc_items:
            if any(k in x for k in ("weight", "height", "body_fat",
                                    "skeletal_muscle", "muscle_mass",
                                    "basal_metabolic_rate", "total_body_water")):
                best = x  # array already ordered; last present wins
        if best is not None:
            body_comp = {k: best[k] for k in (
                "weight", "height", "body_fat", "skeletal_muscle",
                "muscle_mass", "basal_metabolic_rate", "total_body_water")
                if k in best}
            if "t" in best:
                body_comp["t_text"] = to_local(best["t"]).strftime("%H:%M")

    # ---- v5: daily aggregates & tolerant vitals ----
    st_items = [x["count"] for x in payload["steps"]
                if x.get("count") is not None and x["count"] > 0]
    act = payload["activity"]
    activity = {"active_time_s": None, "active_calories": None,
                "total_calories_burned": None, "distance_m": None}
    if act:
        row0 = act[-1]
        if row0.get("active_time_s"):
            activity["active_time_s"] = int(row0["active_time_s"])
        for k in ("active_calories", "total_calories_burned", "distance_m"):
            if row0.get(k):
                activity[k] = row0[k]
    fl_items = [x["count"] for x in payload["floors"]
                if x.get("count") is not None and x["count"] > 0]

    def _last_snapshot(items, keys):
        for x in reversed(items):
            snap = {k: x[k] for k in keys if x.get(k) is not None}
            if snap:
                return snap
        return None

    bp = _last_snapshot(payload["blood_pressure"], ("systolic", "diastolic", "pulse"))
    if bp is not None:
        snap_items = payload["blood_pressure"]
        src = next((x for x in reversed(snap_items)
                    if any(x.get(k) is not None for k in ("systolic", "diastolic", "pulse"))), None)
        if src is not None and "t" in src:
            bp["t_text"] = to_local(src["t"]).strftime("%H:%M")
    bg = _last_snapshot(payload["blood_glucose"], ("glucose",))
    if bg is not None:
        src = next((x for x in reversed(payload["blood_glucose"])
                    if x.get("glucose") is not None), None)
        if src is not None and "t" in src:
            bg["t_text"] = to_local(src["t"]).strftime("%H:%M")
            if src.get("measurement_type"):
                bg["measurement_type"] = src["measurement_type"]
    bt = _last_snapshot(payload["body_temperature"], ("temp",))
    if bt is not None:
        src = next((x for x in reversed(payload["body_temperature"])
                    if x.get("temp") is not None), None)
        if src is not None and "t" in src:
            bt["t_text"] = to_local(src["t"]).strftime("%H:%M")
    kcal_items = [x["calories"] for x in payload["nutrition"]
                  if x.get("calories") is not None and x["calories"] > 0]

    return {
        "sleep": {
            "available": bool(sleep_recs),
            "score": round(score, 0) if score is not None else None,
            "duration_s": round(dur, 0) if dur is not None else None,
            "duration_text": fmt_duration(dur),
            "bedtime": bedtime,
            "wake": wake,
            "deep_min": round(deep_min, 1),
            "stages": stage_list,
        },
        "hr": {
            "sleep_window": win_summary,
            "awake": awake_summary,
            "total_count": len(hr),
        },
        "_charts": {
            "window_minutes": int(round((window[1] - window[0]).total_seconds() / 60.0))
            if window else None,
            "timeline": timeline,
            "win_series": win_series,
            "t0_local": to_local(window[0]).strftime("%H:%M") if window else None,
            "spo2_series": spo2_series,
            "temp_series": temp_series,
            "hr_24h": hr_24h,
        },
        "vitals": {"spo2_window": spo2_sum, "temp_window": temp_sum},
        "energy": {"score": energy_score},
        "exercise": {"count": len(ex_items), "types": ex_types_list},
        "water": {"total_ml": water_ml, "count": len(w_items)},
        "body_comp": body_comp,
        "steps": {"count": int(sum(st_items)) if st_items else None},
        "activity": activity,
        "floors": {"count": round(sum(fl_items), 1) if fl_items else None},
        "blood_pressure": bp, "blood_glucose": bg,
        "body_temperature": bt,
        "nutrition": {"count": len(payload["nutrition"]),
                      "total_kcal": round(sum(kcal_items), 1) if kcal_items else None},
    }


def list_baseline_files(data_dir: str | Path, before_date: date,
                        window_days: int) -> list:
    """By-day files in data_dir with date STRICTLY BEFORE `before_date`,
    newest first, capped at `window_days` entries.

    The strict upper bound guarantees a report for date D only ever uses D's
    past; future files (if any) are never included."""
    out = []
    for p in sorted(Path(data_dir).glob("*.json"), reverse=True):
        m = DAY_FILE_RE.match(p.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if d >= before_date:
            continue
        out.append((d, p))
        if len(out) >= window_days:
            break
    return out


def latest_day_file(data_dir: str | Path) -> Optional[tuple]:
    """Newest by-day file regardless of date -> (date, Path), or None.

    Used by the analyzer's "no --file, pick newest" path; unlike
    `list_baseline_files` it has no upper date bound."""
    for p in sorted(Path(data_dir).glob("*_sleep_hr.json"), reverse=True):
        m = DAY_FILE_RE.match(p.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        return d, p
    return None


def build_trend(data_dir: str | Path, window_days: int,
                end_date: Optional[date] = None) -> dict:
    """Near-N-day trend series, ascending by file date.

    When `end_date` is given, only files with `date <= end_date` are
    considered (inclusive upper bound), so a report for D never includes
    future days. Scans the newest `window_days` valid by-day files (ignores
    stubs/non-day files). `enough` = at least 3 valid parsed days; fewer days
    still returns the available series (report shows 积累中). Metric lists are
    aligned to `days` with None for days lacking that metric."""
    entries: list = []
    for p in sorted(Path(data_dir).glob("*_sleep_hr.json"), reverse=True):
        m = DAY_FILE_RE.match(p.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if end_date is not None and d > end_date:
            continue
        payload = load_payload(p)
        if payload is None:
            continue
        entries.append((d, compute_day_stats(payload)))
        if len(entries) >= window_days:
            break
    entries.sort(key=lambda x: x[0])

    days = [d.strftime("%m-%d") for d, _ in entries]
    sleep_score, sleep_hours = [], []
    hr_night, spo2_night, energy_l, water_l = [], [], [], []
    steps_l = []
    bedtime_l, bedtime_min_l = [], []
    for _, ds in entries:
        s = ds["sleep"]
        sleep_score.append(s["score"] if s.get("available") and s.get("score") is not None else None)
        sleep_hours.append(round(s["duration_s"] / 3600.0, 1) if s.get("duration_s") else None)
        hw = ds["hr"].get("sleep_window")
        hr_night.append(hw["mean"] if hw else None)
        spo2w = ds["vitals"].get("spo2_window")
        spo2_night.append(spo2w["mean"] if spo2w else None)
        en = ds["energy"].get("score")
        energy_l.append(round(en, 1) if en is not None else None)
        w = ds["water"].get("total_ml")
        water_l.append(w)
        st = ds["steps"].get("count")
        steps_l.append(float(st) if st is not None else None)
        bt = s.get("bedtime")
        bedtime_l.append(bt)
        if bt:
            hh, mm = (int(x) for x in bt.split(":"))
            bedtime_min_l.append(hh * 60 + mm)
        else:
            bedtime_min_l.append(None)
    return {"days": days, "sleep_score": sleep_score, "sleep_hours": sleep_hours,
            "hr_night_mean": hr_night, "spo2_night_mean": spo2_night,
            "energy": energy_l, "water_ml": water_l,
            "steps": steps_l, "bedtime": bedtime_l, "bedtime_min": bedtime_min_l,
            "n_days": len(days), "enough": len(days) >= 3}


_MULTIDAY_METRICS = ("sleep_score", "sleep_hours", "hr_night_mean",
                     "spo2_night_mean", "energy", "steps", "water_ml")


def build_multiday_context(data_dir: str | Path, rdate: date,
                           days: int = 7, cfg: Optional[dict] = None) -> dict:
    """Near-N-day compact per-day metrics for the report date + baseline deltas.

    Reuses `build_trend` (bounded to `date <= rdate`, so no future days) for
    the aligned per-day series (which itself reuses `compute_day_stats`) and
    `build_baseline`/`compare_baseline` for the target night's threshold
    deviations. Days/metrics lacking data are None.

    Returns:
      days:       ["MM-DD", ...] ascending (newest `days` valid files <= rdate)
      rows:       [{date, sleep_score, sleep_hours, bedtime, hr_night_mean,
                    spo2_night_mean, energy, steps, water_ml}, ...]
      series:     {metric: [value_or_None, ...]} aligned to `days`
      baseline:   {metric: mean_over_days_strictly_before_rdate_or_None}
      deltas:     {metric: target - baseline_or_None}
      deviations: target-night threshold deviations (compare_baseline)
      n_days / enough / target_index
    """
    window = max(1, int(days))
    trend = build_trend(data_dir, window,
                        end_date=rdate if isinstance(rdate, date) else None)
    labels = list(trend.get("days") or [])
    target_label = rdate.strftime("%m-%d") if isinstance(rdate, date) else None
    if target_label in labels:
        tidx = labels.index(target_label)
    else:
        tidx = len(labels) - 1 if labels else None

    def _col(name: str) -> list:
        col = list(trend.get(name) or [])
        if len(col) < len(labels):
            col += [None] * (len(labels) - len(col))
        return col

    series = {name: _col(name) for name in
              ("sleep_score", "sleep_hours", "bedtime", "hr_night_mean",
               "spo2_night_mean", "energy", "steps", "water_ml")}
    bedtime_min = _col("bedtime_min")

    rows = []
    for i, label in enumerate(labels):
        rows.append({"date": label,
                     "sleep_score": series["sleep_score"][i],
                     "sleep_hours": series["sleep_hours"][i],
                     "bedtime": series["bedtime"][i],
                     "hr_night_mean": series["hr_night_mean"][i],
                     "spo2_night_mean": series["spo2_night_mean"][i],
                     "energy": series["energy"][i],
                     "steps": series["steps"][i],
                     "water_ml": series["water_ml"][i]})

    baseline: dict = {}
    deltas: dict = {}
    for name in _MULTIDAY_METRICS:
        col = series[name]
        vals = [v for i, v in enumerate(col) if i != tidx and v is not None]
        b = _avg(vals)
        baseline[name] = b
        tv = col[tidx] if (tidx is not None and tidx < len(col)) else None
        deltas[name] = round(tv - b, 1) if (tv is not None and b is not None) else None
    bvals = [v for i, v in enumerate(bedtime_min)
             if i != tidx and v is not None]
    baseline["bedtime_min"] = _circular_mean_minutes(bvals)
    tbm = bedtime_min[tidx] if (tidx is not None and tidx < len(bedtime_min)) else None
    deltas["bedtime_min"] = (round(_wrap_minutes(tbm - baseline["bedtime_min"]), 1)
                             if (tbm is not None and baseline["bedtime_min"] is not None)
                             else None)

    deviations: list = []
    if isinstance(rdate, date):
        target_path = Path(data_dir) / f"{rdate.strftime('%Y%m%d')}_sleep_hr.json"
        payload = load_payload(target_path) if target_path.exists() else None
        if payload is not None:
            base = build_baseline(data_dir, rdate, window, 1)
            deviations = compare_baseline(compute_day_stats(payload), base,
                                          cfg or {}).get("deviations", [])

    return {"days": labels, "n_days": trend.get("n_days", len(labels)),
            "enough": bool(trend.get("enough")), "target_index": tidx,
            "rows": rows, "series": series, "baseline": baseline,
            "deltas": deltas, "deviations": deviations}


def build_baseline(data_dir: str | Path, exclude_date: date,
                   window_days: int, min_hr_samples: int) -> dict:
    """Parse baseline day files into per-day metric arrays."""
    scores, durs_s, deep_mins, bed_mins = [], [], [], []
    hr_means = []
    energy_scores = []
    o2_means = []
    temp_means = []
    parsed_days = 0
    for d, p in list_baseline_files(data_dir, exclude_date, window_days):
        payload = load_payload(p)
        if payload is None:
            continue
        parsed_days += 1
        ds = compute_day_stats(payload)
        s = ds["sleep"]
        if s["available"] and s["score"] is not None:
            scores.append(s["score"])
            durs_s.append(s["duration_s"])
            deep_mins.append(s["deep_min"])
        if s["bedtime"] is not None:
            hh, mm = (int(x) for x in s["bedtime"].split(":"))
            bed_mins.append(hh * 60 + mm)
        if ds["hr"]["total_count"] >= min_hr_samples:
            hw = ds["hr"]["sleep_window"]
            if hw is not None:
                hr_means.append(hw["mean"])
        if ds["energy"]["score"] is not None:
            energy_scores.append(ds["energy"]["score"])
        spo2w = ds["vitals"]["spo2_window"]
        if spo2w is not None:
            o2_means.append(spo2w["mean"])
        tempw = ds["vitals"]["temp_window"]
        if tempw is not None:
            temp_means.append(tempw["mean"])
    return {"parsed_days": parsed_days,
            "scores": scores, "durs_s": durs_s,
            "deep_mins": deep_mins, "bed_mins": bed_mins,
            "hr_means": hr_means,
            "energy_scores": energy_scores, "o2_means": o2_means,
            "temp_means": temp_means}


def _avg(vals: list) -> Optional[float]:
    return round(sum(vals) / len(vals), 1) if vals else None


def round_num(value, nd: int = 1):
    """Round a numeric value to `nd` decimals for facts/display.

    Returns an int when `nd <= 0` or the rounded value is integral, so rendered
    HTML keeps `88` rather than `88.0`. None / non-numeric values pass through
    unchanged."""
    if value is None or isinstance(value, bool):
        return value
    try:
        x = float(value)
    except (TypeError, ValueError):
        return value
    if nd <= 0:
        return int(round(x))
    r = round(x, nd)
    return int(r) if float(r).is_integer() else r


def _wrap_minutes(d: float) -> float:
    """Map a minute difference onto the 24h clock into [-720, 720).

    Bedtimes straddle midnight, so a linear difference like 23:54-01:05=+1369
    is meaningless; wrapping gives -71 (23:54 is 71 min earlier than 01:05)."""
    return (d + 720) % 1440 - 720


def _circular_mean_minutes(vals: list) -> Optional[float]:
    """Circular mean of clock-minutes on the 24h circle.

    Uses the vector mean (atan2 of summed sin/cos), so a set straddling
    midnight (e.g. 23:30 + 00:30) averages to ~00:00 rather than ~12:00.
    Returns None for no values; falls back to the linear mean when the
    resultant vector is degenerate (near-zero length, e.g. 00:00 + 12:00)."""
    xs = [float(v) for v in vals if v is not None]
    if not xs:
        return None
    angles = [x * math.pi / 720.0 for x in xs]   # 1440 min -> 2*pi
    s = sum(math.sin(a) for a in angles)
    c = sum(math.cos(a) for a in angles)
    if math.hypot(s, c) < 1e-9:
        return round(sum(xs) / len(xs), 1)
    minutes = (math.atan2(s, c) * 720.0 / math.pi) % 1440.0
    m = round(minutes, 1)
    return 0.0 if m >= 1440.0 else m


def _fmt_hhmm(minutes: Optional[float]) -> str:
    """Clock-minute -> 'HH:MM' (wrapped into a day); '—' when None."""
    if minutes is None:
        return "—"
    m = int(round(minutes)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def compare_baseline(day: dict, base: dict, cfg: dict) -> dict:
    """Rows + deviations for report sections ③/④. cfg = config['analysis']."""
    min_days = int(cfg.get("min_baseline_days", 3))
    th = cfg.get("thresholds", {})
    energy_ratio = float(th.get("energy_below_ratio", 0.6))
    spo2_low = float(th.get("spo2_low", 92.0))
    temp_delta = float(th.get("temp_delta_c", 0.8))
    s = day["sleep"]
    hw = day["hr"]["sleep_window"]

    rows: list = []
    deviations: list = []
    if s["available"] and s["score"] is not None:
        if len(base["scores"]) >= min_days:
            rows.append({"label": "睡眠得分", "day": s["score"],
                         "base": _avg(base["scores"]), "unit": ""})
        if len(base["durs_s"]) >= min_days:
            rows.append({"label": "睡眠时长(h)", "day": round(s["duration_s"] / 3600.0, 1),
                         "base": round((_avg(base["durs_s"]) or 0) / 3600.0, 1), "unit": "h"})
        if len(base["deep_mins"]) >= min_days and s["deep_min"] is not None:
            bd = _avg(base["deep_mins"])
            rows.append({"label": "深睡时长(min)", "day": s["deep_min"],
                         "base": bd, "unit": "min"})
            if s["deep_min"] < 0.5 * (bd or 0) or s["deep_min"] < th.get("deep_min", 45):
                deviations.append(
                    f"深睡仅 {s['deep_min']:.0f} 分钟，显著低于基线均值 {bd:.0f} 分钟")
    if s["bedtime"] is not None and base["bed_mins"]:
        hh, mm = (int(x) for x in s["bedtime"].split(":"))
        today_min = hh * 60 + mm
        bb = _circular_mean_minutes(base["bed_mins"])
        drift = _wrap_minutes(today_min - (bb or 0.0))
        if len(base["bed_mins"]) >= min_days:
            rows.append({"label": f"入睡时刻（漂移 {int(round(drift)):+d}min）",
                         "day": today_min, "base": bb, "unit": "",
                         "drift": round(drift, 1),
                         "day_text": _fmt_hhmm(today_min),
                         "base_text": _fmt_hhmm(bb)})
        if abs(drift) > th.get("bedtime_drift_min", 90):
            deviations.append(
                f"入睡时刻漂移 {int(round(drift)):+d} 分钟（基线均值 {_fmt_hhmm(bb)}）")
    if hw is not None and len(base["hr_means"]) >= min_days:
        bh = _avg(base["hr_means"])
        rows.append({"label": "睡眠时段均心率(bpm)", "day": hw["mean"],
                     "base": bh, "unit": "bpm"})
        if abs(hw["mean"] - (bh or 0)) > th.get("hr_delta_bpm", 8):
            deviations.append(
                f"睡眠均心率 {hw['mean']} bpm，偏离基线 {bh} bpm 超 ±{th.get('hr_delta_bpm', 8)}")

    total_h = (s["duration_s"] or 0) / 3600.0
    if s["duration_s"] and len(base["durs_s"]) >= min_days:
        bb = (_avg(base["durs_s"]) or 0) / 3600.0
        if total_h < 0.7 * bb or total_h < th.get("sleep_hours", 5.5):
            deviations.append(f"总睡眠时长 {total_h:.1f} 小时，低于基线均值 {bb:.1f} 小时")

    # ---- v3 rows & deviations ----
    day_en = day["energy"]["score"]
    if day_en is not None and base["energy_scores"]:
        be = _avg(base["energy_scores"])
        rows.append({"label": "能量得分", "day": round(day_en, 1),
                     "base": be, "unit": ""})
        if be and day_en < be * energy_ratio:
            deviations.append(
                f"能量得分 {day_en:.0f}，显著低于近期基线均值 {be:.0f}")
    day_o2 = (day["vitals"]["spo2_window"] or {}).get("mean")
    if day_o2 is not None and len(base["o2_means"]) >= min_days:
        bo = _avg(base["o2_means"])
        rows.append({"label": "夜间平均血氧(%)", "day": day_o2,
                     "base": bo, "unit": "%"})
        if day_o2 < spo2_low:
            deviations.append(
                f"睡眠平均血氧 {day_o2:.1f}%，低于阈值 {spo2_low:.0f}%")
    day_t = (day["vitals"]["temp_window"] or {}).get("mean")
    if day_t is not None and len(base["temp_means"]) >= min_days:
        bt = _avg(base["temp_means"])
        rows.append({"label": "夜间皮温(°C)", "day": day_t,
                     "base": bt, "unit": "°C"})
        if bt is not None and abs(day_t - bt) > temp_delta:
            deviations.append(
                f"夜间皮温 {day_t:.1f}°C，偏离基线 {bt:.1f}°C 超 ±{temp_delta:g}°C")

    return {"rows": rows, "deviations": deviations,
            "n_sleep_days": len(base["scores"]), "n_hr_days": len(base["hr_means"]),
            "n_energy_days": len(base["energy_scores"]),
            "n_o2_days": len(base["o2_means"]),
            "n_temp_days": len(base["temp_means"]),
            "parsed_days": base["parsed_days"]}
