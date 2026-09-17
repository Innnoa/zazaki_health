"""14-day overall capability analysis report (self-contained inline CSS + SVG).

Consumes the frozen ``build_analysis14`` result dict (ANALYSIS14_SPEC.md §5.2)
and renders a single-file HTML document. LLM narration is optional; any failure
falls back to a deterministic 4-segment template (mirrors ``llm.render_template``).

Every emitted number goes through ``stats.round_num`` so the rendered HTML never
contains a long float (``[0-9]+\\.[0-9]{3,}``); free-text inputs are additionally
sanitised before display.
"""
from __future__ import annotations

import html
import json
import math
import re

import llm
import stats as S

CAP_ORDER = ("sleep_recovery", "cardio_autonomic", "activity_fitness",
             "circadian_regularity")
CAP_LABELS = {
    "sleep_recovery": "睡眠恢复",
    "cardio_autonomic": "心肺-自主神经",
    "activity_fitness": "活动与体能",
    "circadian_regularity": "作息规律",
}
CAP_COLORS = {
    "sleep_recovery": "#4263eb",
    "cardio_autonomic": "#e64980",
    "activity_fitness": "#37b24d",
    "circadian_regularity": "#f59f00",
}
ADVICE_TITLES = ("综合评估", "能力明细", "重点改善", "建议")

_STATUS_COLORS = {"good": "#2f9e44", "ok": "#1c7ed6",
                  "warn": "#f08c00", "bad": "#e03131"}
_STATUS_LABELS = {"good": "良好", "ok": "一般", "warn": "注意", "bad": "偏差",
                  "na": "—"}

_SVG_W = 680
_AXIS = 42
_PLOT_W = _SVG_W - _AXIS - 14
_PLOT_H = 160

_LONG_FLOAT = re.compile(r"\d+\.\d{3,}")


# --------------------------------------------------------------------------- #
# small formatting helpers
# --------------------------------------------------------------------------- #
def _sanitize(text: str) -> str:
    """Collapse any long decimal in free text to 2 decimals."""
    return _LONG_FLOAT.sub(lambda m: f"{float(m.group(0)):.2f}", text or "")


def _esc(value) -> str:
    if value is None:
        return ""
    return html.escape(_sanitize(str(value)))


def _ns(value, nd: int = 1) -> str:
    """Round via stats.round_num and stringify; None -> em dash."""
    r = S.round_num(value, nd)
    return "—" if r is None else str(r)


def _score_color(score) -> str:
    if score is None:
        return "#adb5bd"
    try:
        v = float(score)
    except (TypeError, ValueError):
        return "#adb5bd"
    if v >= 85:
        return "#2f9e44"
    if v >= 70:
        return "#1c7ed6"
    if v >= 60:
        return "#f08c00"
    return "#e03131"


def _status_chip(status) -> str:
    key = str(status or "").lower()
    color = _STATUS_COLORS.get(key, "#868e96")
    label = _STATUS_LABELS.get(key, "—")
    return (f'<span style="display:inline-block;padding:1px 8px;border-radius:10px;'
            f'background:{color}1a;color:{color};font-size:12px;'
            f'border:1px solid {color}55">{_esc(label)}</span>')


def _mmdd(date_str) -> str:
    s = str(date_str or "")
    return s[5:] if len(s) >= 10 else s


def _score_ring(score, label) -> str:
    size = 140
    c = size / 2.0
    r = 54
    circ = 2 * math.pi * r
    frac = 0.0
    if score is not None:
        try:
            frac = max(0.0, min(100.0, float(score))) / 100.0
        except (TypeError, ValueError):
            frac = 0.0
    dash = S.round_num(circ * frac, 1) or 0
    color = _score_color(score)
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
        f'<circle cx="{c:g}" cy="{c:g}" r="{r}" fill="none" stroke="#f1f3f5" '
        f'stroke-width="12"/>'
        f'<circle cx="{c:g}" cy="{c:g}" r="{r}" fill="none" stroke="{color}" '
        f'stroke-width="12" stroke-linecap="round" '
        f'stroke-dasharray="{dash} {circ:.1f}" transform="rotate(-90 {c:g} {c:g})"/>'
        f'<text x="{c:g}" y="{c + 2:g}" text-anchor="middle" font-size="34" '
        f'font-weight="700" fill="{color}">{_esc(_ns(score, 0))}</text>'
        f'<text x="{c:g}" y="{c + 22:g}" text-anchor="middle" font-size="12" '
        f'fill="#868e96">{_esc(label or "")}</text></svg>')


def _kv_table(rows) -> str:
    body = "".join(
        f'<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>' for k, v in rows)
    return f'<table><tr><th>项目</th><th>值</th></tr>{body}</table>'


def _pd_points(per_day):
    """[(original_index, date_label, float_value)] keeping gaps (None skipped)."""
    out = []
    for i, p in enumerate(per_day or []):
        if not isinstance(p, dict):
            continue
        v = p.get("value")
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        out.append((i, str(p.get("date") or ""), fv))
    return out


def _sparkline(items, color: str, unit: str = "") -> str:
    if len(items) < 2:
        return '<span class="muted">逐日数据不足</span>'
    w, h, pad = 320, 66, 10
    imax = max(i for i, _, _ in items) or 1
    vals = [v for _, _, v in items]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-6:
        lo, hi = lo - 1.0, hi + 1.0
    span = hi - lo
    x_of = lambda i: pad + i * (w - 2 * pad) / imax
    y_of = lambda v: 10 + (hi - v) * (h - 26) / span
    pts = " ".join(f"{x_of(i):.1f},{y_of(v):.1f}" for i, _, v in items)
    first, last = items[0], items[-1]
    ly = y_of(last[2])
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>'
        f'<circle cx="{x_of(last[0]):.1f}" cy="{ly:.1f}" r="2.5" fill="{color}"/>'
        f'<text x="{pad}" y="{h - 2}" font-size="9" fill="#adb5bd">'
        f'{_esc(first[1])}</text>'
        f'<text x="{w - pad}" y="{h - 2}" font-size="9" fill="#adb5bd" '
        f'text-anchor="end">{_esc(last[1])}</text>'
        f'<text x="{w - pad}" y="{max(9.0, ly - 5):.0f}" font-size="10" '
        f'fill="#495057" text-anchor="end">{_esc(_ns(last[2], 1))}'
        f'{_esc(unit)}</text></svg>')


def _history_chart(history) -> str:
    rows = [h for h in (history or []) if isinstance(h, dict)]
    if len(rows) < 2:
        return ('<p class="muted">历史分数积累中（至少 2 次分析后显示长期趋势曲线）。</p>')
    n = len(rows)
    x_of = lambda i: _AXIS + i * _PLOT_W / (n - 1)
    y_of = lambda v: 16 + (100 - v) * (_PLOT_H - 28) / 100.0
    grid = ""
    for g in (0, 25, 50, 75, 100):
        y = y_of(g)
        grid += (f'<line x1="{_AXIS}" y1="{y:.1f}" x2="{_SVG_W - 14}" y2="{y:.1f}" '
                 f'stroke="#f1f3f5"/>'
                 f'<text x="{_AXIS - 4}" y="{y + 3:.0f}" font-size="9" fill="#adb5bd" '
                 f'text-anchor="end">{g}</text>')
    series = [("overall", "综合", "#212529", 2.4)]
    series += [(k, CAP_LABELS[k], CAP_COLORS[k], 1.6) for k in CAP_ORDER]
    body = ""
    for key, _name, color, width in series:
        pts = []
        for i in range(n):
            raw = rows[i].get(key)
            if raw is None:
                continue
            try:
                pts.append((i, float(raw)))
            except (TypeError, ValueError):
                continue
        if len(pts) < 2:
            continue
        coords = " ".join(f"{x_of(i):.1f},{y_of(v):.1f}" for i, v in pts)
        body += (f'<polyline points="{coords}" fill="none" stroke="{color}" '
                 f'stroke-width="{width}"/>')
        li, lv = pts[-1]
        body += (f'<circle cx="{x_of(li):.1f}" cy="{y_of(lv):.1f}" r="2.5" '
                 f'fill="{color}"/>')
    xlabels = (
        f'<text x="{_AXIS}" y="{_PLOT_H + 14}" font-size="9" fill="#868e96">'
        f'{_esc(_mmdd(rows[0].get("date")))}</text>'
        f'<text x="{_SVG_W - 14}" y="{_PLOT_H + 14}" font-size="9" fill="#868e96" '
        f'text-anchor="end">{_esc(_mmdd(rows[-1].get("date")))}</text>')
    legend = "".join(
        f'<span style="margin-right:12px;font-size:12px;color:#495057">'
        f'<span style="display:inline-block;width:10px;height:10px;background:{c};'
        f'border-radius:2px;margin-right:4px"></span>{_esc(nm)}</span>'
        for _k, nm, c, _w in series)
    return (f'<svg width="{_SVG_W}" height="{_PLOT_H + 22}" '
            f'viewBox="0 0 {_SVG_W} {_PLOT_H + 22}">{grid}{body}{xlabels}</svg>'
            f'<p class="muted" style="margin-top:2px">{legend}</p>')


# --------------------------------------------------------------------------- #
# LLM narration (reuses llm.request_llm / llm._clip; deterministic fallback)
# --------------------------------------------------------------------------- #
def _llm_cfg(cfg) -> dict:
    """Accept either the full analyzer config ({'llm': {...}}) or a bare llm
    config, and normalise to llm.LLM_DEFAULTS + overrides."""
    base = dict(llm.LLM_DEFAULTS)
    if not isinstance(cfg, dict):
        return base
    sub = cfg.get("llm")
    if isinstance(sub, dict):
        base.update(sub)
        return base
    for k in ("base_url", "model", "api_key", "enabled"):
        if k in cfg:
            base[k] = cfg[k]
    return base


def _build_messages(result: dict) -> list:
    system = (
        "你是健康数据分析助手。规则：只能引用给定的统计值与规则建议；禁止编造任何数字；"
        "禁止诊断疾病或推荐药物；按固定 4 段输出，每段独立成行且以"
        "【综合评估】【能力明细】【重点改善】【建议】开头："
        "① 综合评估：综合就绪度与总体状态；"
        "② 能力明细：四项能力各自的分数与关键指标；"
        "③ 重点改善：最需要关注的短板与可执行方向；"
        "④ 建议：睡眠卫生、昼夜节律、活动量的泛化建议。"
        "总输出不超过 1200 个中文字符。")
    user = (str(result.get("llm_input") or "")
            + "\n规则生成的建议 JSON：\n"
            + json.dumps(result.get("advice") or {}, ensure_ascii=False)
            + "\n请按上述 4 段框架输出最近 14 天整体能力分析。")
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def _seg(title: str, content: str) -> str:
    return f"【{title}】{content}"


def _render_template(result: dict) -> str:
    """Deterministic fallback narration mirroring llm.render_template."""
    r = result or {}
    ov = r.get("overall") or {}
    caps = r.get("capabilities") or {}
    q = r.get("quality") or {}
    win = r.get("window") or {}
    adv = r.get("advice") or {}

    overall_bits = []
    if ov.get("score") is not None:
        label = ov.get("label")
        overall_bits.append(f"综合就绪度 {_ns(ov.get('score'), 0)}"
                            + (f"（{label}）" if label else ""))
    else:
        overall_bits.append("综合就绪度数据不足")
    if win.get("start") and win.get("end"):
        overall_bits.append(
            f"分析窗口 {win['start']} 至 {win['end']}，有效 "
            f"{_ns(win.get('days_present'), 0)}/{_ns(win.get('days'), 0)} 天")
    if q.get("sufficient") is False:
        overall_bits.append("有效天数不足，评分仅供参考")

    cap_bits = []
    for key in CAP_ORDER:
        cap = caps.get(key) or {}
        if cap.get("score") is None:
            cap_bits.append(f"{CAP_LABELS[key]}（数据不足）")
        else:
            label = cap.get("label") or ""
            cap_bits.append(f"{CAP_LABELS[key]} {_ns(cap.get('score'), 0)}"
                            + (f"（{label}）" if label else ""))
    detail = "；".join(cap_bits) if cap_bits else "无可用能力评分"

    focus_items = []
    for group in ("sleep", "circadian", "activity"):
        focus_items.extend(str(x) for x in (adv.get(group) or []))
    focus = ("；".join(focus_items[:4]) if focus_items
             else "各项指标整体平稳，继续保持规律作息与适量活动")

    suggest = ("固定上床与起床时刻，保证 7–8 小时睡眠；每周累计至少 150 分钟中等强度活动；"
               "睡前减少屏幕与咖啡因，保持规律饮食与充足饮水。")

    return llm._clip("\n".join([
        _seg("综合评估", "，".join(overall_bits)),
        _seg("能力明细", detail),
        _seg("重点改善", focus),
        _seg("建议", suggest),
    ]))


def _narrate(result: dict, cfg) -> tuple:
    """Return (text, source); source in {'llm','template'}."""
    lc = _llm_cfg(cfg)
    if not (lc.get("enabled") and lc.get("api_key")):
        return _render_template(result), "template"
    try:
        text = llm.request_llm(lc, _build_messages(result))
        text = llm._clip(text) if text else ""
        if not text:
            raise ValueError("empty")
        return text, "llm"
    except Exception:
        return _render_template(result), "template"


def _split_segments(text: str, titles=ADVICE_TITLES) -> list:
    segs, cur_title, cur = [], None, []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        hit = None
        for t in titles:
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
            return []
    if cur_title is not None:
        segs.append((cur_title, " ".join(cur)))
    return segs


# --------------------------------------------------------------------------- #
# sections
# --------------------------------------------------------------------------- #
def _header_section(result: dict) -> str:
    caps = result.get("capabilities") or {}
    overall = result.get("overall") or {}
    win = result.get("window") or {}
    if win.get("start") and win.get("end"):
        rng = f'{win["start"]} 至 {win["end"]}'
    else:
        rng = str(result.get("rdate") or "")
    chips = ""
    for key in CAP_ORDER:
        cap = caps.get(key) or {}
        sc = cap.get("score")
        if sc is None:
            chips += (f'<span class="chip" style="background:#f1f3f5;color:#868e96">'
                      f'{_esc(CAP_LABELS[key])} 数据不足</span>')
        else:
            color = _score_color(sc)
            chips += (f'<span class="chip" style="background:{color}1a;color:{color};'
                      f'border:1px solid {color}55">{_esc(CAP_LABELS[key])} '
                      f'{_esc(_ns(sc, 0))}</span>')
    return (
        '<h2>① 总体就绪度</h2>'
        '<div class="hero">'
        f'<div>{_score_ring(overall.get("score"), overall.get("label") or "")}</div>'
        '<div class="hero-side">'
        f'<p style="margin:0 0 6px"><b>{_esc(rng)}</b></p>'
        f'<p class="muted" style="margin:0 0 8px">窗口 {_esc(_ns(win.get("days"), 0))} 天'
        f' · 有效 {_esc(_ns(win.get("days_present"), 0))} 天</p>'
        f'<div class="chips">{chips}</div></div></div>')


def _quality_section(result: dict) -> str:
    q = result.get("quality") or {}
    win = result.get("window") or {}
    present = q.get("days_present", win.get("days_present"))
    expected = q.get("days_expected", win.get("days"))
    rows = [
        ("有效天数", f"{_ns(present, 0)} / {_ns(expected, 0)}"),
        ("睡眠分期天数", _ns(q.get("sleep_stage_days"), 0)),
        ("心率天数", _ns(q.get("hr_days"), 0)),
        ("血氧天数", _ns(q.get("spo2_days"), 0)),
        ("血氧采样密度", f'{_ns(q.get("spo2_per_day"), 1)} 点/天'),
        ("SRI 覆盖率", _ns(q.get("sri_coverage"), 2)),
        ("SRI 已计算", "是" if q.get("sri_computed") else "否"),
        ("HRV 可用", "是" if q.get("hrv_available") else "否"),
        ("ODI 可用", "是" if q.get("odi_available") else "否"),
    ]
    warn = ("" if q.get("sufficient", True)
            else '<p style="color:#e03131">样本不足，评分仅供参考。</p>')
    return f'<h2>② 数据质量</h2>{warn}{_kv_table(rows)}'


def _capability_card(key: str, cap: dict) -> str:
    name = CAP_LABELS.get(key, key)
    color = CAP_COLORS.get(key, "#4263eb")
    cap = cap or {}
    score = cap.get("score")
    label = cap.get("label") or ""
    if score is None:
        score_html = ('<span class="chip" style="background:#f1f3f5;color:#868e96">'
                      '数据不足</span>')
    else:
        sc = _score_color(score)
        score_html = (f'<span class="chip" style="background:{sc}1a;color:{sc};'
                      f'border:1px solid {sc}55">{_esc(_ns(score, 0))} · '
                      f'{_esc(label or "—")}</span>')

    rows = ""
    minis = ""
    for c in cap.get("components") or []:
        if not isinstance(c, dict):
            continue
        cname = c.get("name") or c.get("key") or ""
        unit = c.get("unit") or ""
        val = c.get("value")
        vtxt = "—" if val is None else f"{_ns(val, 1)}{unit}"
        rows += (f'<tr><td>{_esc(cname)}</td><td>{_esc(vtxt)}</td>'
                 f'<td>{_esc(c.get("ref") or "—")}</td>'
                 f'<td>{_status_chip(c.get("status"))}</td></tr>')
        pts = _pd_points(c.get("per_day"))
        if pts:
            unit_txt = f" ({_esc(unit)})" if unit else ""
            minis += (f'<div class="mini"><div class="mini-label">{_esc(cname)}'
                      f'{unit_txt}</div>{_sparkline(pts, color, unit)}</div>')
    table = (f'<table class="comp"><tr><th>指标</th><th>数值</th>'
             f'<th>参考区间</th><th>状态</th></tr>{rows}</table>'
             if rows else '<p class="muted">无可用组成指标。</p>')
    minis_html = f'<div class="minis">{minis}</div>' if minis else ""
    caveats = cap.get("caveats") or []
    cav = ("<p class=\"muted\">" + "；".join(_esc(x) for x in caveats) + "</p>"
           if caveats else "")
    return (
        '<section class="card">'
        f'<div class="card-head"><h3 style="border-color:{color}">{_esc(name)}</h3>'
        f'{score_html}</div>{cav}{table}{minis_html}</section>')


def _aux_value(entry):
    if isinstance(entry, dict):
        return entry.get("value"), entry.get("unit") or "", entry.get("per_day")
    return entry, "", None


def _delta_line(device, computed, dev_name, comp_name) -> str:
    if device is None or computed is None:
        return (f'<p>{_esc(dev_name)} {_esc(_ns(device, 0))} · 自算{_esc(comp_name)} '
                f'{_esc(_ns(computed, 0))}（数据不足，无法交叉校验）</p>')
    d = float(device) - float(computed)
    verdict = "基本一致" if abs(d) <= 10 else "存在差异"
    return (f'<p>{_esc(dev_name)} <b>{_esc(_ns(device, 0))}</b> vs 自算'
            f'{_esc(comp_name)} <b>{_esc(_ns(computed, 0))}</b>'
            f'（差 {_esc(_ns(d, 0))}，{_esc(verdict)}）</p>')


def _aux_section(result: dict) -> str:
    aux = result.get("aux") or {}
    caps = result.get("capabilities") or {}
    parts = ["<h2>⑤ 辅助信号</h2>"]
    temp_v, temp_u, temp_pd = _aux_value(aux.get("temp_dev"))
    temp_txt = "—" if temp_v is None else f"{_ns(temp_v, 1)}{temp_u}"
    parts.append(f'<p>皮温偏离（生理恢复信号）<b>{_esc(temp_txt)}</b></p>')
    if temp_pd:
        parts.append(_sparkline(_pd_points(temp_pd), "#e8590c", temp_u))

    dev_sleep, _, _ = _aux_value(aux.get("sleep_score"))
    comp_sleep = (caps.get("sleep_recovery") or {}).get("score")
    parts.append(_delta_line(dev_sleep, comp_sleep, "设备睡眠分", "睡眠恢复分"))

    dev_energy, _, _ = _aux_value(aux.get("energy_score"))
    comp_act = (caps.get("activity_fitness") or {}).get("score")
    parts.append(_delta_line(dev_energy, comp_act, "设备能量分", "活动体能分"))
    return "\n".join(parts)


def _rule_advice_html(advice: dict) -> str:
    groups = (("sleep", "睡眠卫生"), ("circadian", "昼夜节律"),
              ("activity", "活动指南"))
    parts = []
    for key, title in groups:
        items = advice.get(key) or []
        if not items:
            continue
        lis = "".join(f"<li>{_esc(x)}</li>" for x in items)
        parts.append(f'<p class="muted" style="margin-bottom:2px">{_esc(title)}</p>'
                     f'<ul>{lis}</ul>')
    return ('<div style="margin-top:8px">' + "".join(parts) + "</div>"
            if parts else "")


def _advice_section(result: dict, cfg) -> str:
    text, source = _narrate(result, cfg)
    segs = _split_segments(text)
    if segs:
        inner = "".join(
            f'<p style="margin:6px 0 0"><b>【{_esc(t)}】</b> {_esc(c)}</p>'
            for t, c in segs)
    else:
        inner = f'<p style="margin:6px 0 0">{_esc(text)}</p>'
    src_note = "LLM 叙述" if source == "llm" else "模板叙述（LLM 未启用/失败）"
    rule = _rule_advice_html(result.get("advice") or {})
    return (f'<h2>⑥ 建议</h2>'
            f'<div style="padding:10px 12px;background:#f1f8ff;border-radius:6px">'
            f'<b>{src_note}</b>{inner}</div>{rule}')


def _uncertainty_section(result: dict) -> str:
    notes = (result.get("quality") or {}).get("notes") or []
    if notes:
        body = "<ul>" + "".join(f"<li>{_esc(n)}</li>" for n in notes) + "</ul>"
    else:
        body = '<p class="muted">无。</p>'
    return f'<h2>⑦ 不确定度声明</h2>{body}'


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def render_html(result: dict, history: list, cfg: dict) -> str:
    """Render the frozen analysis14 result dict into a self-contained HTML page.

    `history` is the long-term capability-score series; when empty the
    `result["history"]` fallback is used, and an empty series renders gracefully.
    """
    result = result or {}
    caps = result.get("capabilities") or {}
    hist = list(history or [])
    if not hist:
        hist = list(result.get("history") or [])
    rdate = str(result.get("rdate") or "")
    win = result.get("window") or {}
    rng = (f'{win["start"]} 至 {win["end"]}'
           if win.get("start") and win.get("end") else rdate)

    cards = "".join(_capability_card(k, caps.get(k) or {}) for k in CAP_ORDER)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>14 天能力分析 {_esc(rdate)}</title>
<style>
body{{font-family:'Microsoft YaHei',system-ui,sans-serif;max-width:760px;margin:24px auto;
padding:0 16px;color:#212529;line-height:1.6}}
h1{{font-size:22px}}h2{{font-size:17px;border-bottom:1px solid #f1f3f5;padding-bottom:4px}}
h3{{font-size:15px;margin:0;padding-left:8px;border-left:3px solid #4263eb}}
p{{margin:8px 0}}b{{color:#1c7ed6}}.muted{{color:#868e96;font-size:13px}}
table{{border-collapse:collapse;margin:10px 0}}
td,th{{border:1px solid #dee2e6;padding:4px 12px;font-size:14px}}
ul{{margin:8px 0 8px 20px}}
.hero{{display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin:10px 0}}
.hero-side{{flex:1;min-width:240px}}
.chips{{display:flex;flex-wrap:wrap;gap:8px}}
.chip{{display:inline-block;padding:3px 10px;border-radius:12px;font-size:13px}}
.card{{border:1px solid #e9ecef;border-radius:8px;padding:12px 14px;margin:12px 0}}
.card-head{{display:flex;justify-content:space-between;align-items:center;gap:10px;
flex-wrap:wrap}}
table.comp{{width:100%}}
.minis{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px;
margin-top:8px}}
.mini{{border:1px solid #f1f3f5;border-radius:6px;padding:6px 8px}}
.mini-label{{font-size:12px;color:#495057;margin-bottom:2px}}
</style></head><body>
<h1>14 天整体能力分析</h1>
<p class="muted">报告日期 {_esc(rdate)} · 窗口 {_esc(rng)}</p>
{_header_section(result)}
{_quality_section(result)}
<h2>③ 能力明细</h2>
{cards}
<h2>④ 长期趋势</h2>
{_history_chart(hist)}
{_aux_section(result)}
{_advice_section(result, cfg)}
{_uncertainty_section(result)}
</body></html>"""
