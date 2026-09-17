"""DeepSeek (OpenAI-compatible) interpretation layer with template fallback."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import stats as _stats  # noqa: N813  (round_num lives in stats)

LLM_DEFAULTS = {"base_url": "https://api.deepseek.com",
                "model": "deepseek-chat", "api_key": "", "enabled": True}
MAX_CHARS = 1200
SEGMENT_TITLES = ("昨夜总结", "连续多天趋势", "作息调整建议", "健康建议", "风险提示")
# Reasoning models (e.g. deepseek-v4-flash) spend `max_tokens` on reasoning +
# final content combined; a small budget can yield empty `content`. Live probe
# of the real prompt: max_tokens=4096 -> finish=length, reasoning=4096,
# content="" (budget fully consumed). 8192 leaves headroom (reasoning ~4.9k +
# content ~1k, same ~16s latency). Empty responses are additionally retried.
LLM_MAX_TOKENS = 8192
LLM_TIMEOUT = 60.0
EMPTY_RETRIES = 2


def _clip(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= MAX_CHARS:
        return text
    return text[:MAX_CHARS - 1].rstrip() + "…"


def _mini_stats(day: dict, baseline: dict, multiday: dict | None = None) -> dict:
    s, hr = day["sleep"], day["hr"]
    _r = _stats.round_num
    out = {"睡眠得分": _r(s.get("score"), 0),
           "睡眠总时长(分)": round((s.get("duration_s") or 0) / 60),
           "入睡": s.get("bedtime"),
           "醒来": s.get("wake"),
           "深睡(分)": _r(s.get("deep_min"), 1),
           "睡眠窗口均心率(bpm)": _r((hr.get("sleep_window") or {}).get("mean"), 1)}
    vit = day.get("vitals") or {}
    spo2w = vit.get("spo2_window") or {}
    if spo2w:
        out["夜均血氧(%)"] = _r(spo2w.get("mean"), 1)
    tempw = vit.get("temp_window") or {}
    if tempw:
        out["夜均皮温(°C)"] = _r(tempw.get("mean"), 1)
    en = (day.get("energy") or {}).get("score")
    if en is not None:
        out["能量得分"] = _r(en, 0)
    ex = day.get("exercise") or {}
    if ex.get("count"):
        out["运动会话"] = ex["count"]
    w = day.get("water") or {}
    if w.get("total_ml"):
        out["饮水(ml)"] = _r(w["total_ml"], 0)
    bc = day.get("body_comp")
    if bc and "weight" in bc:
        out["体重(kg)"] = _r(bc["weight"], 1)
    st = (day.get("steps") or {}).get("count")
    if st:
        out["步数"] = int(st)
    bp = day.get("blood_pressure")
    if bp and bp.get("systolic"):
        out["血压"] = f"{bp['systolic']:.0f}/{bp['diastolic']:.0f}"
        if bp.get("pulse"):
            out["脉率(bpm)"] = _r(bp["pulse"], 1)
    bg = day.get("blood_glucose")
    if bg and bg.get("glucose"):
        out["血糖(mmol/L)"] = _r(bg["glucose"], 1)
    bt = day.get("body_temperature")
    if bt and bt.get("temp"):
        out["体温(°C)"] = _r(bt["temp"], 1)
    nutr = day.get("nutrition") or {}
    if nutr.get("total_kcal"):
        out["营养合计(kcal)"] = _r(nutr["total_kcal"], 0)
    if baseline.get("deviations"):
        out["显著偏离"] = baseline["deviations"]
    if multiday:
        rows = []
        for row in multiday.get("rows") or []:
            rows.append({"date": row.get("date"),
                         "sleep_score": _r(row.get("sleep_score"), 0),
                         "sleep_hours": _r(row.get("sleep_hours"), 1),
                         "bedtime": row.get("bedtime"),
                         "hr_night_mean": _r(row.get("hr_night_mean"), 1),
                         "spo2_night_mean": _r(row.get("spo2_night_mean"), 1),
                         "energy": _r(row.get("energy"), 0),
                         "steps": _r(row.get("steps"), 0),
                         "water_ml": _r(row.get("water_ml"), 0)})
        delta_nd = {"sleep_score": 0, "sleep_hours": 1, "hr_night_mean": 1,
                    "spo2_night_mean": 1, "energy": 1, "steps": 0,
                    "water_ml": 0, "bedtime_min": 1}
        deltas = {k: _r(v, delta_nd.get(k, 1))
                  for k, v in (multiday.get("deltas") or {}).items()}
        out["连续多天"] = {"天数": multiday.get("n_days"),
                          "逐日": rows,
                          "与基线差": deltas,
                          "显著偏离": multiday.get("deviations") or []}
    return out


def request_llm(cfg: dict, messages: list, timeout: float = LLM_TIMEOUT,
                max_tokens: int = LLM_MAX_TOKENS) -> str:
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    body = json.dumps({"model": cfg["model"], "messages": messages,
                       "temperature": 0.4, "max_tokens": max_tokens}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg["api_key"]})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return str(data["choices"][0]["message"]["content"]).strip()


def request_llm_nonempty(cfg: dict, messages: list, timeout: float = LLM_TIMEOUT,
                         retries: int = EMPTY_RETRIES) -> str:
    """Call request_llm, retrying while the content is empty/whitespace.

    Reasoning models can spend the whole token budget on reasoning tokens and
    return empty `content`; retrying is bounded (retries+1 attempts) and
    deterministic. Network/parse errors are not retried here (they propagate
    so get_interpretation falls back to the template)."""
    text = ""
    for _ in range(max(1, int(retries) + 1)):
        text = request_llm(cfg, messages, timeout=timeout)
        if text and text.strip():
            return text.strip()
    return ""


def _seg(title: str, content: str) -> str:
    return f"【{title}】{content}"


def _multiday_sentence(multiday: dict | None) -> str:
    """Concise '近 N 天' trend prose from build_multiday_context output.

    One short sentence per notable change (direction + magnitude + meaning),
    not a raw enumeration of every value — the report's per-day table carries
    the concrete numbers."""
    if not multiday:
        return ""
    labels = multiday.get("days") or []
    series = multiday.get("series") or {}
    deltas = multiday.get("deltas") or {}
    n = multiday.get("n_days") or len(labels)
    if n < 2:
        return f"近 {n} 天数据积累中，暂不足以判断连续趋势。"

    def _ends(name):
        col = [v for v in (series.get(name) or []) if v is not None]
        return (col[0], col[-1]) if len(col) >= 2 else (None, None)

    sentences = []
    a, b = _ends("sleep_score")
    if a is not None:
        d = deltas.get("sleep_score")
        if d is not None and abs(d) >= 3:
            sentences.append(f"睡眠得分近 {n} 天整体{'上升' if d > 0 else '下降'}（{a:.0f}→{b:.0f}）。")
        else:
            sentences.append(f"睡眠得分近 {n} 天大致平稳（{a:.0f}→{b:.0f}）。")

    a, b = _ends("sleep_hours")
    if a is not None:
        d = deltas.get("sleep_hours")
        if d is not None and abs(d) >= 0.5:
            sentences.append(f"睡眠时长{'增加' if d > 0 else '缩短'}约 {abs(d):.1f} 小时。")
        else:
            sentences.append("睡眠时长基本稳定。")

    bed = [v for v in (series.get("bedtime") or []) if v]
    if len(bed) >= 2 and bed[0] != bed[-1]:
        sentences.append(f"入睡时刻由 {bed[0]} 变为 {bed[-1]}，需留意作息是否规律。")

    a, b = _ends("hr_night_mean")
    if a is not None:
        d = deltas.get("hr_night_mean")
        if d is not None and abs(d) >= 3:
            sentences.append(f"夜均心率{'上升' if d > 0 else '下降'}约 {abs(d):.0f} bpm。")

    a, b = _ends("spo2_night_mean")
    if a is not None and abs(b - a) >= 1:
        sentences.append(f"夜均血氧由 {a:.0f}% 变为 {b:.0f}%。")

    a, b = _ends("energy")
    if a is not None:
        d = deltas.get("energy")
        if d is not None and abs(d) >= 5:
            sentences.append(f"能量得分{'回升' if d > 0 else '回落'}（{a:.0f}→{b:.0f}），提示近期恢复状态有变化。")

    a, b = _ends("steps")
    if a is not None:
        d = deltas.get("steps")
        if d is not None and abs(d) >= 1500:
            sentences.append(f"日均步数{'增加' if d > 0 else '减少'}约 {abs(d):.0f} 步。")

    if not sentences:
        return f"近 {n} 天各项指标整体平稳，未见明显变化。"
    return "".join(sentences)


def _schedule_advice(day: dict, multiday: dict | None) -> str:
    """Concrete sleep-schedule tips (bedtime / wake / duration / regularity)."""
    s = day["sleep"]
    tips = []
    if s.get("bedtime"):
        tips.append(f"保持入睡时刻相对稳定（昨夜 {s['bedtime']}），每日波动尽量不超过 30 分钟")
    dur_s = s.get("duration_s")
    if dur_s:
        hours = dur_s / 3600.0
        if hours < 7:
            tips.append(f"昨夜睡眠 {hours:.1f} 小时，建议逐步提前 30–60 分钟入睡，向 7–8 小时靠拢")
        else:
            tips.append("睡眠时长处于适宜区间，建议固定起床时间以稳定生物钟")
    drift = ((multiday or {}).get("deltas") or {}).get("bedtime_min")
    if drift is not None and abs(drift) >= 60:
        tips.append(f"入睡时刻较近期基线漂移 {drift:+.0f} 分钟，建议固定上床/起床时刻，避免周末补觉")
    if not tips:
        tips.append("建议固定上床与起床时刻，保证 7–8 小时睡眠，睡前 1 小时减少屏幕与咖啡因")
    return "；".join(dict.fromkeys(tips))


def _health_advice(day: dict) -> str:
    """Generalized activity / hydration / recovery tips."""
    tips = []
    st = (day.get("steps") or {}).get("count")
    if st is not None and st < 6000:
        tips.append(f"昨日步数 {int(st)}，建议逐步提升到 6000 步以上")
    w = (day.get("water") or {}).get("total_ml")
    if w is not None and w < 1500:
        tips.append(f"昨日饮水 {w:.0f} ml，建议每日 1500–2000 ml")
    ex = day.get("exercise") or {}
    if not ex.get("count"):
        tips.append("建议每周累计 150 分钟中等强度活动，如快走、骑行")
    tips.append("注意恢复与放松，保持规律饮食与充足饮水")
    return "；".join(dict.fromkeys(tips))


def render_template(day: dict, baseline: dict,
                    multiday: dict | None = None) -> str:
    s, hr = day["sleep"], day["hr"]
    hw = hr.get("sleep_window") or {}
    vit = day.get("vitals") or {}
    spo2w = vit.get("spo2_window") or {}
    tempw = vit.get("temp_window") or {}
    en = (day.get("energy") or {}).get("score")
    ex = day.get("exercise") or {}

    sum_parts = []
    if s.get("available"):
        sum_parts.append(f"睡眠得分 {s['score']:.0f}，总时长 {s['duration_text']}")
        if s.get("bedtime") and s.get("wake"):
            sum_parts.append(f"{s['bedtime']} 至 {s['wake']} 入睡")
        deep = s.get("deep_min")
        if deep is not None:
            sum_parts.append(f"深睡约 {deep:.0f} 分钟")
    if hw.get("mean") is not None:
        sum_parts.append(f"夜均心率 {hw['mean']} bpm")
    summary = "，".join(sum_parts) if sum_parts else "本日无睡眠记录"

    dev = baseline.get("deviations") or []
    trend = _multiday_sentence(multiday)
    if not trend:
        trend_parts = []
        if en is not None:
            trend_parts.append(f"能量得分 {en:.0f}")
        if spo2w:
            trend_parts.append(f"夜均血氧 {spo2w['mean']:.0f}%")
        if tempw:
            trend_parts.append(f"夜均皮温 {tempw['mean']:.1f}°C")
        if ex.get("count"):
            trend_parts.append(f"运动会话 {ex['count']} 次")
        w = day.get("water") or {}
        if w.get("total_ml"):
            trend_parts.append(f"饮水 {w['total_ml']:.0f} ml")
        st = (day.get("steps") or {}).get("count")
        if st:
            trend_parts.append(f"步数 {int(st)}")
        bp_t = day.get("blood_pressure")
        if bp_t and bp_t.get("systolic") and bp_t.get("diastolic"):
            trend_parts.append(f"血压 {bp_t['systolic']:.0f}/{bp_t['diastolic']:.0f}")
        if dev:
            trend_parts.append("偏离：" + "；".join(dev[:3]))
        trend = "，".join(trend_parts) if trend_parts else "各指标与近期基线接近"

    schedule = _schedule_advice(day, multiday)
    health = _health_advice(day)
    risk = "；".join(dev) if dev else "未发现显著风险（仅基于给定数值）"

    return _clip("\n".join([_seg("昨夜总结", summary),
                            _seg("连续多天趋势", trend),
                            _seg("作息调整建议", schedule),
                            _seg("健康建议", health),
                            _seg("风险提示", risk)]))


def get_interpretation(config: dict, day: dict, baseline: dict,
                       multiday: dict | None = None):
    """Return (text, source); source in {'llm','template'}."""
    llm = config.get("llm", {})
    llm = {**LLM_DEFAULTS, **llm}
    if not (llm.get("enabled") and llm.get("api_key")):
        return render_template(day, baseline, multiday), "template"
    system = ("你是健康数据分析助手。规则：只能引用给定 JSON 统计值；禁止编造任何数字；"
              "禁止诊断疾病或推荐药物；按固定 5 段输出，每段独立成行且以"
              "【昨夜总结】【连续多天趋势】【作息调整建议】【健康建议】【风险提示】开头："
              "① 昨夜总结：睡眠与心率核心数值；"
              "② 连续多天趋势：用 2-4 句简短连贯的话说明整体方向、值得注意的变化及其含义，"
              "不要逐项罗列所有数值（数值已由报告表格呈现），缺失值说明缺失；"
              "③ 作息调整建议：针对入睡/起床时刻、睡眠时长、规律性给具体可执行建议；"
              "④ 健康建议：活动/饮水/恢复的泛化建议；"
              "⑤ 风险提示：仅在给定值触发阈值时列风险，无则写未发现显著风险。"
              "总输出不超过 1200 个中文字符。")
    user = ("昨夜健康统计 JSON：\n" + json.dumps(_mini_stats(day, baseline, multiday),
                                                ensure_ascii=False) +
            "\n请按上述 5 段框架输出昨夜健康解读。")
    try:
        text = request_llm_nonempty(llm, [{"role": "system", "content": system},
                                          {"role": "user", "content": user}])
        text = _clip(text)
        if not text:
            raise ValueError("empty")
        return text, "llm"
    except Exception:
        return render_template(day, baseline, multiday), "template"
