"""Self-contained single-file HTML report (inline CSS + inline SVG)."""
from __future__ import annotations

import html
from datetime import datetime
from typing import Optional

import stats as S  # noqa: N813  (kept as S to match sibling module naming)

_SVG_W = 680
_AXIS = 46
_PLOT_W = _SVG_W - _AXIS - 12
_PLOT_H = 150

_EXERCISE_CN = {"WALKING": "步行", "RUNNING": "跑步", "CYCLING": "骑行",
                "SWIMMING": "游泳", "HIKING": "徒步", "YOGA": "瑜伽",
                "STRENGTH_TRAINING": "力量训练", "BASKETBALL": "篮球",
                "SOCCER": "足球", "BADMINTON": "羽毛球"}


def _ex_cn(typ: str) -> str:
    return _EXERCISE_CN.get(typ, typ or "UNKNOWN")


_SEG_TITLES = ("昨夜总结", "连续多天趋势", "作息调整建议", "健康建议", "风险提示")


def _split_segments(text: str) -> list:
    """Split LLM/template text into [(title, content), ...]. Titles are the
    fixed segment markers 【昨夜总结】/【趋势与异常】/【恢复建议】/【风险提示】.
    Returns [] when no marker found (caller falls back to plain paragraph)."""
    segs: list = []
    cur_title = None
    cur: list = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        hit = None
        for t in _SEG_TITLES:
            if line.startswith("【" + t + "】"):
                hit = t
                line = line[len("【" + t + "】"):].strip()
                break
        if hit is not None:
            if cur_title is not None:
                segs.append((cur_title, " ".join(cur)))
            cur_title = hit
            cur = [line] if line else []
        elif cur_title is not None:
            cur.append(line)
        else:
            return []  # no marker seen yet -> not structured
    if cur_title is not None:
        segs.append((cur_title, " ".join(cur)))
    return segs


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _row_cell(r: dict, key: str) -> str:
    """Render a baseline row cell. Rows may carry a preformatted `*_text`
    value (e.g. bedtime HH:MM); otherwise value+unit is shown unchanged."""
    text_key = key + "_text"
    if r.get(text_key) is not None:
        return esc(r[text_key])
    return esc(f'{r.get(key, "")}{r.get("unit", "")}')


def _stage_band(charts: dict) -> str:
    timeline = charts.get("timeline") or []
    if not timeline:
        return ""
    t1 = charts.get("window_minutes") or 1
    blocks = []
    for seg in timeline:
        color = S.STAGE_COLORS.get(seg["type"], "#adb5bd")
        x = _AXIS + max(0, seg["s"]) * _PLOT_W / t1
        w = max(0.8, (seg["e"] - seg["s"]) * _PLOT_W / t1)
        blocks.append(
            f'<rect x="{x:.1f}" y="18" width="{w:.1f}" height="26" fill="{color}" '
            f'rx="1"><title>{seg["type"]} {seg["s"]}-{seg["e"]}min</title></rect>')
    labels = ""
    step = 120
    for m in range(0, int(t1) + 1, step):
        x = _AXIS + m * _PLOT_W / t1
        labels += (f'<text x="{x:.0f}" y="58" font-size="10" fill="#868e96" '
                   f'text-anchor="middle">{m // 60}h</text>')
    return (f'<svg width="{_SVG_W}" height="70" viewBox="0 0 {_SVG_W} 70">'
            f'<text x="2" y="32" font-size="10" fill="#868e96">{esc(charts.get("t0_local"))}</text>'
            f'{"".join(blocks)}{labels}</svg>')


def _hr_curve(charts: dict) -> str:
    series = charts.get("win_series") or []
    if len(series) < 2:
        return ""
    t1 = max(charts.get("window_minutes") or 1, max(x for x, _ in series))
    vals = [h for _, h in series]
    lo = int(min(vals)) - 5
    hi = int(max(vals)) + 5
    lo -= lo % 10
    hi += (10 - hi % 10) % 10
    if hi - lo < 20:
        mid = (lo + hi) // 2
        lo, hi = mid - 10, mid + 10
    y_of = lambda h: 18 + (hi - h) * (_PLOT_H - 36) / (hi - lo)
    pts = " ".join(f"{_AXIS + x * _PLOT_W / t1:.1f},{y_of(h):.1f}" for x, h in series)
    grid = ""
    for g in range(lo, hi + 1, 10):
        y = y_of(g)
        grid += (f'<line x1="{_AXIS}" y1="{y:.1f}" x2="{_SVG_W - 12}" y2="{y:.1f}" '
                 f'stroke="#f1f3f5"/>'
                 f'<text x="{_AXIS - 4}" y="{y + 3:.0f}" font-size="9" fill="#adb5bd" '
                 f'text-anchor="end">{g}</text>')
    mean = round(sum(vals) / len(vals), 1)
    ym = y_of(mean)
    xl = _AXIS + series[-1][0] * _PLOT_W / t1
    return (f'<svg width="{_SVG_W}" height="{_PLOT_H + 30}" '
            f'viewBox="0 0 {_SVG_W} {_PLOT_H + 30}">{grid}'
            f'<polyline points="{pts}" fill="none" stroke="#4263eb" stroke-width="1.5"/>'
            f'<line x1="{_AXIS}" y1="{ym:.1f}" x2="{_SVG_W - 12}" y2="{ym:.1f}" '
            f'stroke="#f03e3e" stroke-width="1" stroke-dasharray="4 3"/>'
            f'<text x="{min(_SVG_W - 12, xl):.1f}" y="{ym - 4:.0f}" font-size="9" fill="#f03e3e">'
            f'均值 {mean}</text></svg>')


def _stage_table(stages: list) -> str:
    if not stages:
        return "<p>阶段明细缺失。</p>"
    legend = "".join(
        f'<span style="margin-right:14px"><span style="display:inline-block;'
        f'width:10px;height:10px;background:{S.STAGE_COLORS.get(x["type"], "#adb5bd")};'
        f'border-radius:2px;margin-right:4px"></span>{esc(x["type"])} '
        f'{x["minutes"]:.0f}min ({x["pct"]:.0f}%)</span>'
        for x in stages)
    return f'<p>{legend}</p>'


def _hr_block(title: str, s: Optional[dict]) -> str:
    if s is None:
        return ""
    return (f"<p><b>{esc(title)}</b> 均值 {s['mean']} bpm · 最低 {s['min']} · "
            f"最高 {s['max']}（样本 {s['count']} 点）</p>")


def _trend_svg(points: list, color: str, unit: str,
               show_values: bool = False) -> str:
    """points: [[idx, val], ...] ascending -> compact line chart. '' if <2 pts.
    When `show_values`, prints the value at each point (trend charts); night
    curves leave it off to avoid label clutter."""
    if len(points) < 2:
        return ""
    xs = [p[0] for p in points]
    vals = [float(p[1]) for p in points]
    xmax = max(xs) or 1
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-6:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.15
    lo, hi = lo - pad, hi + pad
    y_of = lambda v: 18 + (hi - v) * (_PLOT_H - 30) / (hi - lo)
    coords = [(_AXIS + x * _PLOT_W / xmax, y_of(v)) for x, v in points]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    grid = (f'<line x1="{_AXIS}" y1="{y_of(hi):.1f}" x2="{_SVG_W - 12}" y2="{y_of(hi):.1f}" '
            f'stroke="#f1f3f5"/><text x="{_AXIS - 4}" y="{y_of(hi) + 3:.0f}" font-size="9" '
            f'fill="#adb5bd" text-anchor="end">{hi:.0f}</text>'
            f'<line x1="{_AXIS}" y1="{y_of(lo):.1f}" x2="{_SVG_W - 12}" y2="{y_of(lo):.1f}" '
            f'stroke="#f1f3f5"/><text x="{_AXIS - 4}" y="{y_of(lo) + 3:.0f}" font-size="9" '
            f'fill="#adb5bd" text-anchor="end">{lo:.0f}</text>')
    marks = ""
    if show_values:
        for (x, y), v in zip(coords, vals):
            tx = min(max(x, _AXIS + 8), _SVG_W - 14)
            marks += (f'<text x="{tx:.0f}" y="{max(9.0, y - 4):.0f}" font-size="9" '
                      f'fill="#495057" text-anchor="middle">{S.round_num(v, 1):g}{esc(unit)}</text>')
    return (f'<svg width="{_SVG_W}" height="{_PLOT_H + 24}" '
            f'viewBox="0 0 {_SVG_W} {_PLOT_H + 24}">{grid}'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>'
            f'{marks}</svg>')


def _bars_svg(points: list, labels: list, color: str, unit: str,
              show_day_labels: bool = True, show_values: bool = False) -> str:
    """points: [[idx, val], ...]; bars. '' if empty.

    `show_day_labels` renders labels under the axis (fine for 24 hourly bars);
    trend charts pass False because 5-char dates collide. `show_values` prints
    the value above each bar so the numbers are visible without hover."""
    if not points:
        return ""
    vals = [float(v) for _, v in points]
    vmax = max(vals) or 1
    n = max(len(labels), 1)
    bw = _PLOT_W / n
    barw = max(2.0, bw * 0.5)
    y_of = lambda v: _PLOT_H + 18 - (v / vmax) * (_PLOT_H - 6)
    body = []
    labels_svg = ""
    for idx, v in points:
        cx = _AXIS + (idx + 0.5) * bw
        x0 = cx - barw / 2
        y = y_of(v)
        body.append(f'<rect x="{x0:.1f}" y="{y:.1f}" width="{barw:.1f}" '
                    f'height="{_PLOT_H + 18 - y:.1f}" fill="{color}" rx="1">'
                    f'<title>{labels[idx] if idx < len(labels) else ""}: {v:.0f}{unit}</title></rect>')
        if show_values:
            body.append(f'<text x="{cx:.0f}" y="{max(9.0, y - 3):.0f}" font-size="9" '
                        f'fill="#495057" text-anchor="middle">{S.round_num(v, 1):g}</text>')
        if show_day_labels and idx < len(labels):
            labels_svg += (f'<text x="{cx:.0f}" y="{_PLOT_H + 32}" font-size="9" '
                           f'fill="#868e96" text-anchor="middle">{esc(labels[idx])}</text>')
    return (f'<svg width="{_SVG_W}" height="{_PLOT_H + 42}" '
            f'viewBox="0 0 {_SVG_W} {_PLOT_H + 42}">{"".join(body)}{labels_svg}</svg>')


def _trend_table(trend: dict) -> str:
    """Deterministic per-day trend table (numbers always visible, independent
    of the LLM). One row per day; missing values render as '—'."""
    days = trend.get("days") or []
    if not days:
        return ""
    headers = ["日期", "睡眠得分", "时长(h)", "入睡", "夜均心率", "血氧%",
               "能量", "步数", "饮水(ml)"]
    arrs = [None, trend.get("sleep_score"), trend.get("sleep_hours"),
            trend.get("bedtime"), trend.get("hr_night_mean"),
            trend.get("spo2_night_mean"), trend.get("energy"),
            trend.get("steps"), trend.get("water_ml")]
    fmts = [None, lambda v: f"{v:.0f}", lambda v: f"{v:.1f}", lambda v: v,
            lambda v: f"{v:.0f}", lambda v: f"{v:.0f}", lambda v: f"{v:.0f}",
            lambda v: f"{v:.0f}", lambda v: f"{v:.0f}"]
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    rows = []
    for i in range(len(days)):
        tds = []
        for j, arr in enumerate(arrs):
            if j == 0:
                val = days[i]
            else:
                v = arr[i] if (arr is not None and i < len(arr)) else None
                val = "—" if v is None else fmts[j](v)
            tds.append(f"<td>{esc(val)}</td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return f'<table class="trend"><tr>{head}</tr>{"".join(rows)}</table>'


def _activity_section(energy, exercise, water_ml, body_comp,
                      steps=None, activity=None, floors=None,
                      blood_pressure=None, blood_glucose=None,
                      body_temperature=None, nutrition=None) -> str:
    parts = ["<h2>⑤ 每日能量与活动</h2>"]
    any_content = False
    if energy is not None:
        parts.append(f'<p>能量得分 <b>{energy:.0f}</b>（SHealth 当日能量分）</p>')
        any_content = True
    if exercise["count"] > 0:
        any_content = True
        items = []
        for row in exercise["types"]:
            cn = _ex_cn(row["type"])
            dur = row.get("duration_s")
            cal = row.get("calories")
            txt = f'{cn} ×{row["count"]}'
            if dur:
                txt += f' · {S.fmt_duration(dur)}'
            if cal:
                txt += f' · {cal:.0f} kcal'
            items.append(f"<li>{esc(txt)}</li>")
        parts.append(f'<p>运动会话 <b>{exercise["count"]}</b> 次：</p>')
        parts.append("<ul>" + "".join(items) + "</ul>")
    st = (steps or {}).get("count")
    if st:
        parts.append(f'<p>步数 <b>{int(st)}</b></p>')
        any_content = True
    act = activity or {}
    act_bits = []
    if act.get("active_time_s"):
        act_bits.append(f"活跃 {S.fmt_duration(float(act['active_time_s']))}")
    if act.get("distance_m"):
        act_bits.append(f"距离 {(act['distance_m'] / 1000.0):.1f} km")
    if act.get("active_calories"):
        act_bits.append(f"消耗 {act['active_calories']:.0f} kcal")
    fl = (floors or {}).get("count")
    if fl:
        act_bits.append(f"楼层 {fl:.0f}")
    if act_bits:
        parts.append(f'<p>{" · ".join(act_bits)}</p>')
        any_content = True
    if water_ml:
        parts.append(f'<p>饮水总量 <b>{water_ml:.0f} ml</b>（当日累计）</p>')
        any_content = True
    if not any_content:
        parts.append('<p>当日无能量/运动/饮水记录。</p>')
    if body_comp:
        bits = []
        for key, unit, nd in (("weight", " kg", 1), ("body_fat", "%", 1),
                              ("skeletal_muscle", " kg", 1), ("muscle_mass", " kg", 1),
                              ("basal_metabolic_rate", " kcal", 0),
                              ("total_body_water", " kg", 1)):
            if key in body_comp:
                v = S.round_num(body_comp[key], nd)
                bits.append(f"{key}={v}{unit}")
        t = body_comp.get("t_text")
        head = f'体成分摘要（测于 {esc(t)}）：' if t else "体成分摘要："
        parts.append(f'<p>{head}{" · ".join(bits)}</p>')
    if blood_pressure:
        t = blood_pressure.get("t_text")
        head = f'血压（测于 {esc(t)}）：' if t else "血压："
        parts.append(f'<p>{head}<b>{blood_pressure["systolic"]:.0f}/{blood_pressure["diastolic"]:.0f}</b>'
                     f' mmHg · 脉率 {blood_pressure.get("pulse", 0):.0f}</p>')
    if blood_glucose:
        t = blood_glucose.get("t_text")
        mt = blood_glucose.get("measurement_type")
        head = f'血糖（测于 {esc(t)}）：' if t else "血糖："
        parts.append(f'<p>{head}<b>{blood_glucose["glucose"]:.1f}</b> mmol/L'
                     + (f' · {esc(mt)}' if mt else "") + "</p>")
    if body_temperature:
        t = body_temperature.get("t_text")
        head = f'体温（测于 {esc(t)}）：' if t else "体温："
        parts.append(f'<p>{head}<b>{body_temperature["temp"]:.1f}</b> °C</p>')
    nutr = nutrition or {}
    if nutr.get("total_kcal"):
        parts.append(f'<p>营养记录 <b>{nutr.get("count", 0)}</b> 条 · '
                     f'合计 {nutr["total_kcal"]:.0f} kcal</p>')
    return "\n".join(parts)


def build_html(meta: dict, day: dict, baseline: dict, llm_text: str,
               llm_source: str, trend: Optional[dict] = None) -> str:
    """meta: {date, source_name, generated_at_local}; baseline from
    stats.compare_baseline -> {rows, deviations, ...}; trend from
    stats.build_trend (optional; omitted keeps old 5-arg behavior)."""
    s = day["sleep"]
    hr = day["hr"]
    charts = day.get("_charts", {})
    vit = day.get("vitals") or {"spo2_window": None, "temp_window": None}
    energy = (day.get("energy") or {}).get("score")
    exercise = day.get("exercise") or {"count": 0, "types": []}
    water_ml = (day.get("water") or {}).get("total_ml")
    body_comp = day.get("body_comp")
    steps = day.get("steps") or {"count": None}
    activity = day.get("activity") or {}
    floors = day.get("floors") or {"count": None}
    blood_pressure = day.get("blood_pressure")
    blood_glucose = day.get("blood_glucose")
    body_temperature = day.get("body_temperature")
    nutrition = day.get("nutrition") or {"count": 0, "total_kcal": None}
    span_start = meta.get("span_start")
    span_end = meta.get("span_end")
    span_label = meta.get("span_label") or "时间跨度"
    span_head = (f'{esc(span_label)} {esc(span_start)} – {esc(span_end)}（Asia/Shanghai） · '
                 if span_start and span_end else "")
    src_note = "LLM 解读" if llm_source == "llm" else "模板解读（LLM 未启用/失败）"
    segs = _split_segments(llm_text)
    if segs:
        top_inner = "".join(f'<p style="margin:4px 0 0"><b>【{esc(t)}】</b> {esc(c)}</p>'
                            for t, c in segs)
    else:
        top_inner = f'<p style="margin:4px 0 0">{esc(llm_text)}</p>'

    sec1 = []
    if s["available"]:
        sec1.append(f'<h2>① 昨晚睡眠总览</h2>')
        sec1.append(f'<p>睡眠得分 <b>{s["score"]}</b> · 总时长 <b>{esc(s["duration_text"])}</b></p>')
        if s.get("bedtime_full") and s.get("wake_full"):
            sec1.append(f'<p>入睡 <b>{esc(s["bedtime_full"])}</b> → 醒来 <b>{esc(s["wake_full"])}</b>（Asia/Shanghai）</p>')
        elif s["bedtime"] and s["wake"]:
            sec1.append(f'<p>入睡 <b>{esc(s["bedtime"])}</b> → 醒来 <b>{esc(s["wake"])}</b>（Asia/Shanghai）</p>')
        else:
            sec1.append("<p>入睡/醒来时刻缺数据。</p>")
        sec1.append(_stage_table(s["stages"]))
        band = _stage_band(charts)
        if band:
            sec1.append(f'<p>阶段时间轴（主睡眠窗口内，分钟相对入睡）</p>{band}')
        spo2w = (vit.get("spo2_window") or {})
        tempw = (vit.get("temp_window") or {})
        if spo2w or tempw:
            if spo2w:
                sec1.append((f'<p>睡眠窗口夜均血氧 <b>{spo2w["mean"]:.0f}%</b>'
                             f'（低 {spo2w["min"]:.0f} · 高 {spo2w["max"]:.0f}'
                             f'，样本 {spo2w["count"]}）</p>'))
                s2 = _trend_svg(charts.get("spo2_series") or [], "#40c057", "%")
                if s2:
                    sec1.append(f'<p class="muted">睡眠窗口血氧曲线</p>{s2}')
            if tempw:
                sec1.append((f'<p>夜间皮温均值 <b>{tempw["mean"]:.1f}°C</b>'
                             f'（低 {tempw["min"]:.1f} · 高 {tempw["max"]:.1f}'
                             f'，样本 {tempw["count"]}）</p>'))
                t2 = _trend_svg(charts.get("temp_series") or [], "#e8590c", "°C")
                if t2:
                    sec1.append(f'<p class="muted">夜间皮温曲线</p>{t2}')
        else:
            sec1.append('<p class="muted">本夜无血氧/皮温记录（如实说明，不臆造均值）。</p>')
    else:
        sec1.append("<h2>① 昨晚睡眠总览</h2><p>本日无睡眠记录，缺数据。</p>")

    sec2 = ["<h2>② 心率概况</h2>"]
    hw = hr["sleep_window"]
    if hw:
        sec2.append(_hr_block("睡眠窗口内", hw))
        sec2.append((f'<p class="muted">覆盖：{hw["first_local"]}–{hw["last_local"]}（本地），'
                     f'样本 {hw["count"]} 点，间隔 {esc(hw["gap"])}</p>'))
        curve = _hr_curve(charts)
        if curve:
            sec2.append(curve)
    h24 = charts.get("hr_24h") or []
    h24_pts = [[i, x["mean"]] for i, x in enumerate(h24)
               if x.get("mean") is not None]
    if h24_pts:
        sec2.append('<p class="muted">全天心率分布（每小时均值）</p>' +
                    _bars_svg(h24_pts, [f"{x:02d}" for x in range(24)], "#4263eb", "bpm"))
    if hr["awake"]:
        sec2.append(_hr_block("清醒段", hr["awake"]))
    if not hw and not hr["awake"]:
        sec2.append("<p>本日无有效心率样本。</p>")
    if hw is None and hr["total_count"]:
        sec2.append('<p class="muted">无睡眠窗口，仅展示全天样本。</p>')

    sec3 = ["<h2>③ 近期基线对比</h2>"]
    if baseline["rows"]:
        trs = "".join(
            f'<tr><td>{esc(r["label"])}</td><td>{_row_cell(r, "day")}</td>'
            f'<td>{_row_cell(r, "base")}</td></tr>'
            for r in baseline["rows"])
        sec3.append(f'<table><tr><th>指标</th><th>当日</th><th>基线均值</th></tr>{trs}</table>')
    else:
        sec3.append(f'<p>基线待积累（已有 {baseline.get("parsed_days", 0)} 天有效数据，'
                    f'需要 ≥3 天）。</p>')

    sec4 = ["<h2>④ 异常偏离提示</h2>"]
    if baseline["deviations"]:
        sec4.append("<ul>" + "".join(f"<li>{esc(d)}</li>"
                                     for d in baseline["deviations"]) + "</ul>")
    else:
        sec4.append("<p>无显著偏离。</p>" if baseline["rows"] else
                    "<p>基线未激活，暂不判定。</p>")

    # ---- v4: 近 7 天趋势（② 之后、③ 之前，无序号）----
    trend_html = ""
    trend = trend or {}
    if trend.get("enough"):
        labels = trend.get("days") or []
        rows = [
            ("睡眠得分", trend.get("sleep_score"), "#4263eb", ""),
            ("睡眠时长(h)", trend.get("sleep_hours"), "#74c0fc", "h"),
            ("夜均心率(bpm)", trend.get("hr_night_mean"), "#f783ac", "bpm"),
            ("夜均血氧(%)", trend.get("spo2_night_mean"), "#40c057", "%"),
            ("能量得分", trend.get("energy"), "#f59f00", ""),
            ("饮水(ml)", trend.get("water_ml"), "#4dabf7", "ml"),
            ("步数", trend.get("steps"), "#37b24d", ""),
        ]
        parts = ["<h2>近 7 天趋势</h2>", _trend_table(trend)]
        for label, arr, color, unit in rows:
            pts = [[i, v] for i, v in enumerate(arr or []) if v is not None]
            if not pts:
                continue
            if label == "能量得分":
                chart = _bars_svg(pts, labels, color, unit,
                                  show_day_labels=False, show_values=True)
            else:
                chart = _trend_svg(pts, color, unit, show_values=True)
            if chart:
                parts.append(f'<p class="muted">{esc(label)}（近 {len(labels)} 天）</p>{chart}')
        if len(parts) == 1:
            parts.append('<p class="muted">趋势数据待积累。</p>')
        trend_html = "\n".join(parts)
    elif trend.get("n_days") is not None:
        trend_html = (f'<h2>近 7 天趋势</h2>'
                      f'<p>趋势数据积累中（已有 {trend.get("n_days", 0)} 天，≥3 天后显示图表）。</p>')

    body = "\n".join(sec1 + sec2 + ([trend_html] if trend_html else []) +
                     sec3 + sec4 +
                     [_activity_section(energy, exercise, water_ml, body_comp,
                                        steps=steps, activity=activity, floors=floors,
                                        blood_pressure=blood_pressure,
                                        blood_glucose=blood_glucose,
                                        body_temperature=body_temperature,
                                        nutrition=nutrition)])
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>健康晨报 {esc(meta['date'])}{('（' + esc(span_start) + ' – ' + esc(span_end) + '）') if span_start and span_end else ''}</title>
<style>
body{{font-family:'Microsoft YaHei',system-ui,sans-serif;max-width:760px;margin:24px auto;
padding:0 16px;color:#212529;line-height:1.6}}
h1{{font-size:22px}}h2{{font-size:17px;border-bottom:1px solid #f1f3f5;padding-bottom:4px}}
p{{margin:8px 0}}b{{color:#1c7ed6}}.muted{{color:#868e96;font-size:13px}}
table{{border-collapse:collapse;margin:10px 0}}
td,th{{border:1px solid #dee2e6;padding:4px 12px;font-size:14px}}
ul{{margin:8px 0 8px 20px}}
</style></head><body>
<h1>健康晨报 {esc(meta['date'])}</h1>
<p class="muted">{span_head}来源 {esc(meta['source_name'])} · 报告生成 {esc(meta['generated_at_local'])}</p>
<div style="padding:10px 12px;background:#f1f8ff;border-radius:6px"><b>{src_note}</b>{top_inner}</div>
{body}
</body></html>"""
