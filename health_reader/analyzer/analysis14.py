"""Analysis14 — 14-day overall capability analysis (frozen spec ANALYSIS14_SPEC.md).

Computes four capability scores (sleep recovery / cardio-autonomic /
activity-fitness / circadian regularity), an overall readiness score, evidence,
trend history and rule-based advice for the trailing window `[rdate-13, rdate]`.

Only days up to and including `rdate` are ever considered. All emitted numbers
go through `stats.round_num`, so no value carries more than a couple of
decimals.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import stats  # noqa: E402

WINDOW_DAYS = 14
CAPS = ("sleep_recovery", "cardio_autonomic", "activity_fitness", "circadian_regularity")
CAP_LABELS = {"sleep_recovery": "睡眠恢复", "cardio_autonomic": "心肺-自主神经",
              "activity_fitness": "活动与体能", "circadian_regularity": "作息规律"}
OVERALL_WEIGHTS = {"sleep_recovery": 0.35, "cardio_autonomic": 0.25,
                   "activity_fitness": 0.20, "circadian_regularity": 0.20}

SLEEP_STAGES = ("DEEP", "LIGHT", "REM")
DEFAULT_SLEEP_NEED_H = 8.0
SRI_MIN_COVERAGE = 0.5

_CAP_CAVEATS = {"sleep_recovery": ["深睡/REM 比例为设备算法估算"]}

_QUALITY_NOTES = ["无 HRV 原始数据（1/分心率）",
                  "血氧约 10 分钟/点，ODI/T90 不可靠",
                  "深睡/REM 为设备分期估算"]

# Capability component definitions: key/name/unit/ref/weight/nd/anchors/src/agg.
_COMPONENTS = {
    "sleep_recovery": [
        {"key": "tst", "name": "总睡眠时长", "unit": "h", "ref": "7–9 h",
         "weight": 0.25, "nd": 1, "src": "tst_h", "agg": "mean",
         "anchors": [(5, 0), (6, 20), (7, 70), (8, 100), (9, 100), (10, 80)]},
        {"key": "se", "name": "睡眠效率", "unit": "%", "ref": "85–95 %",
         "weight": 0.20, "nd": 1, "src": "SE", "agg": "mean",
         "anchors": [(75, 0), (80, 20), (85, 70), (90, 100)]},
        {"key": "deep_pct", "name": "深睡占比", "unit": "%", "ref": "13–23 %",
         "weight": 0.15, "nd": 1, "src": "deep_pct", "agg": "mean",
         "anchors": [(8, 0), (10, 40), (15, 70), (20, 100)]},
        {"key": "rem_pct", "name": "REM 占比", "unit": "%", "ref": "20–25 %",
         "weight": 0.10, "nd": 1, "src": "rem_pct", "agg": "mean",
         "anchors": [(12, 0), (15, 40), (20, 70), (25, 100)]},
        {"key": "waso", "name": "入睡后清醒", "unit": "min", "ref": "≤ 30 min",
         "weight": 0.15, "nd": 1, "src": "WASO", "agg": "mean",
         "anchors": [(10, 100), (30, 70), (60, 20), (90, 0)]},
        {"key": "debt", "name": "睡眠负债", "unit": "h", "ref": "≤ 1 h",
         "weight": 0.15, "nd": 1, "src": "sleep_debt", "agg": "mean",
         "anchors": [(0, 100), (3, 50), (6, 20), (8, 0)]},
    ],
    "cardio_autonomic": [
        {"key": "rhr", "name": "静息心率", "unit": "bpm", "ref": "55–65 bpm",
         "weight": 0.35, "nd": 0, "src": "rhr", "agg": "mean",
         "anchors": [(55, 100), (65, 70), (75, 20), (85, 0)]},
        {"key": "hr_dip", "name": "夜间心率降幅", "unit": "%", "ref": "10–20 %",
         "weight": 0.35, "nd": 1, "src": "hr_dip", "agg": "mean",
         "anchors": [(0, 0), (10, 70), (20, 100)]},
        {"key": "spo2_mean", "name": "夜间平均血氧", "unit": "%", "ref": "≥ 95 %",
         "weight": 0.20, "nd": 1, "src": "spo2_mean", "agg": "mean",
         "anchors": [(92, 20), (95, 70), (97, 100)]},
        {"key": "spo2_min", "name": "夜间最低血氧", "unit": "%", "ref": "≥ 92 %",
         "weight": 0.10, "nd": 1, "src": "spo2_min", "agg": "mean",
         "anchors": [(88, 20), (92, 70), (95, 100)]},
    ],
    "activity_fitness": [
        {"key": "steps", "name": "日均步数", "unit": "步", "ref": "≥ 8000",
         "weight": 0.45, "nd": 0, "src": "steps", "agg": "mean",
         "anchors": [(4000, 20), (8000, 70), (10000, 100)]},
        {"key": "active_min_week", "name": "每周活跃时长", "unit": "min",
         "ref": "≥ 150 min", "weight": 0.35, "nd": 1, "src": "active_min",
         "agg": "week_sum", "anchors": [(0, 0), (150, 70), (300, 100)]},
        {"key": "active_kcal", "name": "日均活动消耗", "unit": "kcal",
         "ref": "≥ 400 kcal", "weight": 0.20, "nd": 0, "src": "active_kcal",
         "agg": "mean", "anchors": [(200, 20), (400, 70), (700, 100)]},
    ],
    "circadian_regularity": [
        {"key": "sri", "name": "睡眠规律指数", "unit": "", "ref": "≥ 73",
         "weight": 0.5, "nd": 1, "src": "sri", "agg": "special",
         "anchors": [(40, 0), (60, 20), (73, 70), (85, 100)]},
        {"key": "midpoint_sd", "name": "睡眠中点波动", "unit": "min",
         "ref": "≤ 60 min", "weight": 0.3, "nd": 1, "src": "midpoint",
         "agg": "circ_sd", "anchors": [(30, 100), (60, 70), (120, 20), (180, 0)]},
        {"key": "social_jetlag", "name": "社交时差", "unit": "h", "ref": "≤ 1 h",
         "weight": 0.2, "nd": 2, "src": "midpoint", "agg": "jetlag",
         "anchors": [(0.5, 100), (1, 70), (2, 20)]},
    ],
}


# --------------------------------------------------------------------------- #
# small numeric helpers (rounding delegated to stats.round_num)
# --------------------------------------------------------------------------- #
def _local_offset(tz) -> timedelta:
    if tz is None:
        return stats.LOCAL_OFFSET
    if isinstance(tz, timedelta):
        return tz
    if isinstance(tz, (int, float)):
        return timedelta(hours=float(tz))
    text = str(tz)
    if "Shanghai" in text or "Beijing" in text or "8" in text:
        return timedelta(hours=8)
    return stats.LOCAL_OFFSET


def _mean(vals):
    xs = [v for v in vals if v is not None]
    return (sum(xs) / len(xs)) if xs else None


def _percentile(vals, pct):
    xs = sorted(float(v) for v in vals)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    rank = (pct / 100.0) * (len(xs) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (rank - lo)


def _circular_sd_minutes(vals):
    """Circular SD (minutes) of clock-minute values; None if <2 or degenerate."""
    xs = [float(v) for v in vals if v is not None]
    n = len(xs)
    if n < 2:
        return None
    angles = [x * math.pi / 720.0 for x in xs]
    s = sum(math.sin(a) for a in angles)
    c = sum(math.cos(a) for a in angles)
    r = math.hypot(s, c) / n
    if r <= 0:
        return None
    if r >= 1:
        return 0.0
    return math.sqrt(-2.0 * math.log(r)) * 720.0 / math.pi


def piecewise(value, points):
    """Piecewise-linear interpolation; clamps outside the endpoints; None->None."""
    if value is None or not points:
        return None
    pts = sorted(points, key=lambda p: p[0])
    if value <= pts[0][0]:
        return float(pts[0][1])
    if value >= pts[-1][0]:
        return float(pts[-1][1])
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= value <= x1:
            if x1 == x0:
                return float(y1)
            t = (value - x0) / (x1 - x0)
            return float(y0 + t * (y1 - y0))
    return float(pts[-1][1])


def _label(score):
    if score is None:
        return "数据不足"
    if score >= 85:
        return "优秀"
    if score >= 70:
        return "良好"
    if score >= 60:
        return "一般"
    return "需关注"


def _parse_ref(ref):
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", ref or "")]
    if not nums:
        return None
    if "≤" in ref or "<" in ref:
        return ("max", nums[0], None)
    if "≥" in ref or ">" in ref:
        return ("min", nums[0], None)
    if len(nums) >= 2:
        return ("range", nums[0], nums[1])
    return None


def _status(value, ref):
    if value is None:
        return "na"
    parsed = _parse_ref(ref)
    if parsed is None:
        return "ok"
    kind, lo, hi = parsed
    if kind == "range":
        span = (hi - lo) or 1.0
        if lo <= value <= hi:
            return "good"
        if lo - 0.1 * span <= value <= hi + 0.1 * span:
            return "ok"
        if lo - 0.25 * span <= value <= hi + 0.25 * span:
            return "warn"
        return "bad"
    if kind == "max":
        if value <= lo:
            return "good"
        if value <= lo * 1.2:
            return "ok"
        if value <= lo * 1.5:
            return "warn"
        return "bad"
    if value >= lo:
        return "good"
    if value >= lo * 0.8:
        return "ok"
    if value >= lo * 0.6:
        return "warn"
    return "bad"


# --------------------------------------------------------------------------- #
# per-day indicators
# --------------------------------------------------------------------------- #
def day_indicators(payload: dict, tz="Asia/Shanghai") -> dict:
    """Per-day metrics from a (raw or parsed) payload; None where absent."""
    payload = stats.normalize_payload(payload)
    off = _local_offset(tz)

    sessions = []
    for rec in payload.get("sleep") or []:
        for sess in rec.get("sessions") or []:
            if sess.get("start") is not None and sess.get("end") is not None:
                sessions.append(sess)

    tib = tst = waso = sol = deep = rem = 0.0
    have_tib = have_tst = have_waso = have_sol = False
    first_sleep_start = None
    last_end = None
    for sess in sessions:
        stages = sorted(sess["stages"], key=lambda s: s["start"])
        if not stages:
            continue
        first_start = stages[0]["start"]
        last_stage_end = stages[-1]["end"]
        tib += (last_stage_end - first_start).total_seconds() / 60.0
        have_tib = True
        sleep_stages = [s for s in stages if s["type"] in SLEEP_STAGES]
        tst += sum((s["end"] - s["start"]).total_seconds() / 60.0
                   for s in sleep_stages)
        deep += sum((s["end"] - s["start"]).total_seconds() / 60.0
                    for s in stages if s["type"] == "DEEP")
        rem += sum((s["end"] - s["start"]).total_seconds() / 60.0
                   for s in stages if s["type"] == "REM")
        fs = sleep_stages[0]["start"] if sleep_stages else None
        if fs is not None:
            have_tst = True
            have_waso = True
            have_sol = True
            if first_sleep_start is None or fs < first_sleep_start:
                first_sleep_start = fs
            for s in stages:
                if s["type"] == "AWAKE" and s["start"] >= fs:
                    waso += (s["end"] - s["start"]).total_seconds() / 60.0
            sol += max(0.0, (fs - sess["start"]).total_seconds() / 60.0)
        if last_end is None or last_stage_end > last_end:
            last_end = last_stage_end

    tib_v = stats.round_num(tib, 1) if have_tib else None
    tst_v = stats.round_num(tst, 1) if have_tst else None
    waso_v = stats.round_num(waso, 1) if have_waso else None
    sol_v = stats.round_num(sol, 1) if have_sol else None
    se_v = (stats.round_num(tst / tib * 100.0, 1)
            if (have_tib and have_tst and tib > 0) else None)
    deep_pct = (stats.round_num(deep / tst * 100.0, 1)
                if (have_tst and tst > 0) else None)
    rem_pct = (stats.round_num(rem / tst * 100.0, 1)
               if (have_tst and tst > 0) else None)

    bedtime = ((first_sleep_start + off).strftime("%H:%M")
               if first_sleep_start is not None else None)
    wake = ((last_end + off).strftime("%H:%M") if last_end is not None else None)
    midpoint = midpoint_min = None
    if (first_sleep_start is not None and last_end is not None
            and last_end > first_sleep_start):
        mid = first_sleep_start + (last_end - first_sleep_start) / 2
        local_mid = mid + off
        midpoint = local_mid.strftime("%H:%M")
        midpoint_min = stats.round_num(
            local_mid.hour * 60 + local_mid.minute + local_mid.second / 60.0, 1)

    window = (first_sleep_start, last_end) if (first_sleep_start is not None
                                               and last_end is not None) else None

    hr_points = payload.get("heart_rate") or []
    sleep_hr = [h for t, h in hr_points if window and window[0] <= t <= window[1]]
    rhr = stats.round_num(_percentile(sleep_hr, 5), 0) if sleep_hr else None
    if window is not None:
        d_local = (window[0] + off).date()
        awake_hr = [h for t, h in hr_points
                    if (t + off).date() == d_local and not (window[0] <= t <= window[1])]
    else:
        awake_hr = [h for t, h in hr_points]
    hr_dip = None
    if sleep_hr and awake_hr:
        s_mean = _mean(sleep_hr)
        a_mean = _mean(awake_hr)
        if a_mean:
            hr_dip = stats.round_num((a_mean - s_mean) / a_mean * 100.0, 1)

    spo2_pts = [(x["t"], x["spo2"]) for x in (payload.get("blood_oxygen") or [])
                if x.get("spo2") is not None]
    sw = [v for t, v in spo2_pts if window and window[0] <= t <= window[1]]
    spo2_mean = stats.round_num(_mean(sw), 1) if sw else None
    spo2_min = stats.round_num(min(sw), 1) if sw else None

    temp_pts = [(x["t"], x["temp"]) for x in (payload.get("skin_temperature") or [])
                if x.get("temp") is not None]
    tw = [v for t, v in temp_pts if window and window[0] <= t <= window[1]]
    temp_mean = stats.round_num(_mean(tw), 1) if tw else None

    ds = stats.compute_day_stats(payload)
    sleep_score = stats.round_num(ds["sleep"]["score"], 0)
    energy_score = stats.round_num(ds["energy"]["score"], 1)
    steps = stats.round_num(ds["steps"]["count"], 0)
    activity = ds["activity"]
    floors = stats.round_num(ds["floors"]["count"], 1)

    tst_h = (tst / 60.0) if have_tst else None
    sleep_debt = (stats.round_num(max(0.0, DEFAULT_SLEEP_NEED_H - tst_h), 1)
                  if tst_h is not None else None)

    return {
        "TIB": tib_v, "TST": tst_v, "WASO": waso_v, "SE": se_v, "SOL": sol_v,
        "deep_pct": deep_pct, "rem_pct": rem_pct,
        "bedtime": bedtime, "wake": wake, "midpoint": midpoint,
        "midpoint_min": midpoint_min, "sleep_debt": sleep_debt,
        "rhr": rhr, "hr_dip": hr_dip,
        "spo2_mean": spo2_mean, "spo2_min": spo2_min, "spo2_count": len(sw),
        "hr_count": len(hr_points),
        "steps": steps,
        "active_time_s": activity.get("active_time_s"),
        "active_calories": stats.round_num(activity.get("active_calories"), 1),
        "distance_m": stats.round_num(activity.get("distance_m"), 1),
        "floors": floors,
        "temp_mean": temp_mean,
        "sleep_score": sleep_score, "energy_score": energy_score,
    }


def _sleep_grid(payload: dict, off: Optional[timedelta] = None):
    """1440-length 0/1 grid by local clock minute (sleep=1, else 0). None if no stages."""
    payload = stats.normalize_payload(payload)
    if off is None:
        off = stats.LOCAL_OFFSET
    grid = [0] * 1440
    any_stage = False
    for rec in payload.get("sleep") or []:
        for sess in rec.get("sessions") or []:
            for stg in sess.get("stages") or []:
                any_stage = True
                state = 1 if stg["type"] in SLEEP_STAGES else 0
                t = stg["start"].replace(second=0, microsecond=0)
                if t < stg["start"]:
                    t += timedelta(minutes=1)
                while t < stg["end"]:
                    local = t + off
                    grid[local.hour * 60 + local.minute] = state
                    t += timedelta(minutes=1)
    return grid if any_stage else None


def _sri_value(grids):
    """grids: [(date, grid)] -> (sri|None, coverage, computed)."""
    grids = sorted(grids, key=lambda x: x[0])
    pairs = [(grids[i][1], grids[i + 1][1]) for i in range(len(grids) - 1)
             if (grids[i + 1][0] - grids[i][0]).days == 1]
    coverage = (len(pairs) / len(grids)) if grids else 0.0
    coverage = stats.round_num(coverage, 2)
    if not pairs or coverage < SRI_MIN_COVERAGE:
        return None, coverage, False
    equal = total = 0
    for g1, g2 in pairs:
        for m in range(1440):
            total += 1
            if g1[m] == g2[m]:
                equal += 1
    return stats.round_num(-100 + 200 * (equal / total), 1), coverage, True


def _source_value(ind, src):
    if src == "tst_h":
        return ind["TST"] / 60.0 if ind.get("TST") is not None else None
    if src == "active_min":
        return (ind["active_time_s"] / 60.0
                if ind.get("active_time_s") is not None else None)
    if src == "midpoint":
        return ind.get("midpoint_min")
    return ind.get(src)


def _build_capability(cap, days, window_days, sri_value, jetlag_value):
    comps = []
    valid = []
    for spec in _COMPONENTS[cap]:
        per_day = []
        vals = []
        for d, ind in days:
            v = _source_value(ind, spec["src"])
            if v is not None:
                vals.append(v)
                per_day.append({"date": d.strftime("%m-%d"),
                                "value": stats.round_num(v, spec["nd"])})
        agg = spec["agg"]
        if agg == "special":
            value = sri_value
        elif agg == "jetlag":
            value = jetlag_value
        elif agg == "circ_sd":
            value = _circular_sd_minutes(vals)
        elif agg == "week_sum":
            value = (sum(vals) * 7.0 / window_days) if vals else None
        else:
            value = _mean(vals)
        if value is not None:
            value = stats.round_num(value, spec["nd"])
        score = piecewise(value, spec["anchors"]) if value is not None else None
        if score is not None:
            score = stats.round_num(score, 0)
            valid.append((score, spec["weight"]))
        comps.append({"key": spec["key"], "name": spec["name"], "value": value,
                      "unit": spec["unit"], "ref": spec["ref"],
                      "status": _status(value, spec["ref"]), "score": score,
                      "weight": spec["weight"], "per_day": per_day})
    if not valid or len(valid) < len(_COMPONENTS[cap]) / 2.0:
        cap_score = None
    else:
        wsum = sum(w for _, w in valid)
        cap_score = stats.round_num(sum(s * w for s, w in valid) / wsum, 0)
    return {"score": cap_score, "label": _label(cap_score),
            "components": comps, "caveats": list(_CAP_CAVEATS.get(cap, []))}


def _build_llm_input(result):
    w, q = result["window"], result["quality"]
    lines = [f"14天整体能力分析：{w['start']} ~ {w['end']}"
             f"（有效 {q['days_present']}/{q['days_expected']} 天）"]
    if not q["sufficient"]:
        lines.append("样本不足（有效天数 < 7），评分仅供参考。")
    for cap in CAPS:
        c = result["capabilities"][cap]
        sc = c["score"]
        head = f"{CAP_LABELS[cap]} {sc if sc is not None else '数据不足'}"
        if sc is not None:
            head += f"（{c['label']}）"
        parts = [f"{comp['name']} {comp['value']}{comp['unit']}（参考 {comp['ref']}）"
                 for comp in c["components"] if comp["value"] is not None]
        lines.append(head + "：" + "；".join(parts))
    ov = result["overall"]
    lines.append(f"综合就绪度 {ov['score'] if ov['score'] is not None else '数据不足'}"
                 f"（{ov['label']}）")
    lines.append("不确定度：" + "；".join(q["notes"]))
    return "\n".join(lines)


def _build_advice(caps):
    advice = {"sleep": [], "circadian": [], "activity": []}
    sr = caps["sleep_recovery"]["score"]
    cr = caps["circadian_regularity"]["score"]
    af = caps["activity_fitness"]["score"]
    if sr is None or sr < 70:
        advice["sleep"].append("目标每晚 7–9 小时睡眠，固定起床时间；睡前 1 小时减少屏幕与咖啡因。")
    if cr is None or cr < 70:
        advice["circadian"].append("保持工作日与周末入睡时间差在 1 小时内，白天增加光照。")
    if af is None or af < 70:
        advice["activity"].append("逐步达到每周 150 分钟中等强度活动，并保证日常步数。")
    if not advice["sleep"]:
        advice["sleep"].append("维持当前作息与睡眠时长。")
    if not advice["circadian"]:
        advice["circadian"].append("作息规律性良好，继续保持。")
    if not advice["activity"]:
        advice["activity"].append("活动量达标，注意恢复与补水。")
    return advice


def build_analysis14(data_dir, rdate: date, cfg: dict) -> dict:
    """Build the frozen 14-day analysis result dict (never includes days > rdate)."""
    cfg = cfg or {}
    a14 = cfg.get("analysis14", {}) or {}
    window_days = int(a14.get("window_days", WINDOW_DAYS))
    sleep_need_h = float(a14.get("sleep_need_h", DEFAULT_SLEEP_NEED_H))
    start = rdate - timedelta(days=window_days - 1)

    days = []
    stage_days = hr_days = spo2_days = 0
    spo2_counts = []
    grids = []
    for i in range(window_days):
        d = start + timedelta(days=i)
        if d > rdate:
            break
        path = Path(data_dir) / f"{d.strftime('%Y%m%d')}_sleep_hr.json"
        payload = stats.load_payload(path)
        if payload is None:
            continue
        ind = day_indicators(payload, "Asia/Shanghai")
        if ind["TST"] is not None:
            ind["sleep_debt"] = stats.round_num(
                max(0.0, sleep_need_h - ind["TST"] / 60.0), 1)
        else:
            ind["sleep_debt"] = None
        days.append((d, ind))
        if ind["TST"] is not None:
            stage_days += 1
        if ind["hr_count"]:
            hr_days += 1
        if ind["spo2_mean"] is not None:
            spo2_days += 1
            spo2_counts.append(ind["spo2_count"])
        g = _sleep_grid(payload)
        if g is not None:
            grids.append((d, g))

    days_present = len(days)
    sri_value, sri_coverage, sri_computed = _sri_value(grids)

    midpoint_pairs = [(d, ind["midpoint_min"]) for d, ind in days
                      if ind.get("midpoint_min") is not None]
    weekend = [m for d, m in midpoint_pairs if d.weekday() >= 5]
    weekday = [m for d, m in midpoint_pairs if d.weekday() < 5]
    jetlag = None
    if weekend and weekday:
        we_mean = stats._circular_mean_minutes(weekend)
        wd_mean = stats._circular_mean_minutes(weekday)
        if we_mean is not None and wd_mean is not None:
            jetlag = abs(stats._wrap_minutes(we_mean - wd_mean)) / 60.0

    capabilities = {}
    for cap in CAPS:
        capabilities[cap] = _build_capability(
            cap, days, window_days,
            sri_value if cap == "circadian_regularity" else None,
            jetlag if cap == "circadian_regularity" else None)

    wsum = ssum = 0.0
    for cap in CAPS:
        sc = capabilities[cap]["score"]
        if sc is None:
            continue
        w = OVERALL_WEIGHTS[cap]
        ssum += sc * w
        wsum += w
    overall_score = stats.round_num(ssum / wsum, 0) if wsum > 0 else None
    overall = {"score": overall_score, "label": _label(overall_score),
               "weights": dict(OVERALL_WEIGHTS)}

    # aux signals
    def _aux(unit, values, target_key):
        per_day = [{"date": d.strftime("%m-%d"), "value": stats.round_num(v, 1)}
                   for d, v in values if v is not None]
        target = next((v for d, v in reversed(values)
                       if d == rdate and v is not None), None)
        others = [v for d, v in values if d != rdate and v is not None]
        return per_day, target, others

    temp_vals = [(d, ind["temp_mean"]) for d, ind in days]
    temp_pd, temp_target, temp_others = _aux("°C", temp_vals, "temp_mean")
    temp_dev = (stats.round_num(temp_target - _mean(temp_others), 1)
                if (temp_target is not None and temp_others) else None)
    sleep_pd, sleep_target, _ = _aux("", [(d, ind["sleep_score"]) for d, ind in days], None)
    energy_pd, energy_target, _ = _aux("", [(d, ind["energy_score"]) for d, ind in days], None)
    aux = {
        "temp_dev": {"value": temp_dev, "unit": "°C", "per_day": temp_pd},
        "sleep_score": {"value": sleep_target, "unit": "", "per_day": sleep_pd},
        "energy_score": {"value": energy_target, "unit": "", "per_day": energy_pd},
    }

    notes = list(_QUALITY_NOTES)
    sufficient = days_present >= 7
    if not sufficient:
        notes.append("样本不足（有效天数 < 7），评分仅供参考")
    quality = {
        "days_present": days_present, "days_expected": window_days,
        "sufficient": sufficient, "sleep_stage_days": stage_days,
        "hr_days": hr_days, "spo2_days": spo2_days,
        "spo2_per_day": stats.round_num(_mean(spo2_counts), 1),
        "sri_coverage": sri_coverage, "sri_computed": sri_computed,
        "hrv_available": False, "odi_available": False, "notes": notes,
    }

    history = []
    reports_dir = cfg.get("reports_dir")
    if reports_dir:
        history = _load_history(Path(reports_dir))

    result = {
        "rdate": rdate.isoformat(),
        "window": {"start": start.isoformat(), "end": rdate.isoformat(),
                   "days": window_days, "days_present": days_present},
        "quality": quality,
        "capabilities": capabilities,
        "overall": overall,
        "aux": aux,
        "history": history,
        "advice": _build_advice(capabilities),
    }
    result["llm_input"] = _build_llm_input(result)
    return result


# --------------------------------------------------------------------------- #
# history persistence
# --------------------------------------------------------------------------- #
def _history_path(reports_dir) -> Path:
    return Path(reports_dir) / "analysis14_scores.json"


def _load_history(reports_dir) -> list:
    path = _history_path(reports_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    out = []
    for key in sorted(data):
        entry = data[key] or {}
        row = {"date": key, "overall": entry.get("overall")}
        for cap in CAPS:
            row[cap] = entry.get(cap)
        out.append(row)
    return out


def append_score_history(reports_dir, rdate: date, result: dict) -> Path:
    """Write/merge <reports_dir>/analysis14_scores.json (ascending; same-day overwrite)."""
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = _history_path(reports_dir)
    data = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    caps = result.get("capabilities") or {}
    entry = {"overall": (result.get("overall") or {}).get("score")}
    for cap in CAPS:
        entry[cap] = (caps.get(cap) or {}).get("score")
    data[rdate.isoformat()] = entry
    ordered = {key: data[key] for key in sorted(data)}
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def run_analysis14(data_dir, reports_dir, rdate: date, cfg: dict) -> Path:
    """Build, render HTML (lazy import), write report, append score history."""
    cfg = cfg or {}
    result = build_analysis14(data_dir, rdate, cfg)
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    history = _load_history(reports_dir)
    result["history"] = history
    import analysis14_report  # lazy: built in parallel
    html = analysis14_report.render_html(result, history, cfg)
    out = reports_dir / f"{rdate.strftime('%Y%m%d')}_analysis14.html"
    out.write_text(html, encoding="utf-8")
    append_score_history(reports_dir, rdate, result)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="analysis14.py", description=__doc__)
    ap.add_argument("--file", help="a daily JSON (date inferred from name)")
    ap.add_argument("--data-dir", help="data dir (default: --file's parent)")
    ap.add_argument("--reports-dir", help="reports dir (default: <data-dir>/reports)")
    ap.add_argument("--date", help="report date YYYY-MM-DD")
    ap.add_argument("--config", default=str(SCRIPT_DIR / "config.json"),
                    help="config json path (default: analyzer/config.json)")
    args = ap.parse_args(argv)

    cfg = {}
    if args.config:
        try:
            loaded = json.loads(Path(args.config).read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg = loaded
        except (OSError, ValueError):
            cfg = {}

    if args.file:
        fp = Path(args.file).resolve()
        data_dir = Path(args.data_dir or fp.parent).resolve()
        reports_dir = Path(args.reports_dir or data_dir / "reports").resolve()
        if args.date:
            rdate = datetime.strptime(args.date, "%Y-%m-%d").date()
        else:
            m = stats.DAY_FILE_RE.match(fp.name)
            if not m:
                print(f"[analysis14] cannot infer date from {fp.name}", file=sys.stderr)
                return 2
            rdate = datetime.strptime(m.group(1), "%Y%m%d").date()
    else:
        data_dir = Path(args.data_dir or cfg.get("data_dir", str(Path.cwd()))).resolve()
        reports_dir = Path(args.reports_dir or data_dir / "reports").resolve()
        if args.date:
            rdate = datetime.strptime(args.date, "%Y-%m-%d").date()
        else:
            latest = stats.latest_day_file(data_dir)
            if latest is None:
                print("[analysis14] no by-day file found", file=sys.stderr)
                return 2
            rdate = latest[0]

    try:
        out = run_analysis14(data_dir, reports_dir, rdate, cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"[analysis14] error: {exc}", file=sys.stderr)
        return 1
    print(f"[analysis14] report written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
