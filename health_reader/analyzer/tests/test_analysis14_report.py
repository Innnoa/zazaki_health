import re
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import analysis14_report as A14  # noqa: E402

LONG = re.compile(r"[0-9]+\.[0-9]{3,}")


def _per_day(base, n=14):
    out = []
    for i in range(n):
        out.append({"date": f"08-{i + 1:02d}",
                    "value": round(base + (i % 4) * 0.1, 2)})
    return out


def _comp(key, name, value, unit, ref, status, score, per_day=None, weight=0.2):
    return {"key": key, "name": name, "value": value, "unit": unit, "ref": ref,
            "status": status, "score": score, "weight": weight,
            "per_day": per_day if per_day is not None else _per_day(value or 0.0)}


def make_history():
    return [
        {"date": "2026-08-31", "overall": 76, "sleep_recovery": 80,
         "cardio_autonomic": 72, "activity_fitness": 70, "circadian_regularity": 83},
        {"date": "2026-09-03", "overall": 77, "sleep_recovery": 81,
         "cardio_autonomic": 73, "activity_fitness": 69, "circadian_regularity": 84},
        {"date": "2026-09-07", "overall": 78, "sleep_recovery": 82,
         "cardio_autonomic": 74, "activity_fitness": 69, "circadian_regularity": 85},
        {"date": "2026-09-13", "overall": 78, "sleep_recovery": 82,
         "cardio_autonomic": 74, "activity_fitness": 69, "circadian_regularity": 85},
    ]


def make_result():
    caps = {
        "sleep_recovery": {
            "score": 82, "label": "良好",
            "components": [
                _comp("tst", "总睡眠时长", 7.8, "h", "7–9 h", "good", 85),
                _comp("se", "睡眠效率", 88.0, "%", "85–95 %", "good", 80),
                _comp("deep", "深睡占比", 15.2, "%", "10–20 %", "ok", 70),
                _comp("rem", "REM 占比", 20.1, "%", "15–25 %", "ok", 68),
                _comp("waso", "夜间清醒", 32.0, "min", "≤30 min", "warn", 55),
                _comp("debt", "睡眠负债", 0.8, "h", "0–1 h", "good", 90),
            ],
            "caveats": ["深睡/REM 比例为设备算法估算"],
        },
        "cardio_autonomic": {
            "score": 74, "label": "良好",
            "components": [
                _comp("rhr", "静息心率", 58.0, "bpm", "55–65 bpm", "good", 85),
                _comp("hr_dip", "夜间心率降幅", 14.2, "%", "10–20 %", "ok", 72),
                _comp("spo2_mean", "夜间血氧均值", 96.1, "%", "≥95 %", "good", 80),
                _comp("spo2_min", "夜间血氧最低", 91.0, "%", "≥92 %", "warn", 50),
            ],
            "caveats": ["血氧约 10 分钟/点"],
        },
        "activity_fitness": {
            "score": 69, "label": "一般",
            "components": [
                _comp("steps", "日均步数", 8200.0, "步", "8000–10000 步", "good", 75),
                _comp("active_min_week", "每周活跃分钟", 210.0, "min",
                      "≥150 min", "good", 80),
                _comp("active_kcal", "日均活动消耗", 420.0, "kcal",
                      "400–700 kcal", "ok", 70),
            ],
            "caveats": [],
        },
        "circadian_regularity": {
            "score": 85, "label": "优秀",
            "components": [
                _comp("sri", "睡眠规律指数", 78.0, "", "40–85", "good", 75),
                _comp("midpoint_sd", "睡眠中点标准差", 42.0, "min",
                      "≤60 min", "good", 80),
                _comp("social_jetlag", "社交时差", 0.8, "h", "≤1 h", "ok", 70),
            ],
            "caveats": ["SRI 基于睡眠分期网格估算"],
        },
    }
    return {
        "rdate": "2026-09-13",
        "window": {"start": "2026-08-31", "end": "2026-09-13",
                   "days": 14, "days_present": 12},
        "quality": {
            "days_present": 12, "days_expected": 14, "sufficient": True,
            "sleep_stage_days": 12, "hr_days": 12, "spo2_days": 12,
            "spo2_per_day": 45.2, "sri_coverage": 0.92, "sri_computed": True,
            "hrv_available": False, "odi_available": False,
            "notes": ["无 HRV 原始数据（1/分心率）",
                      "血氧约 10 分钟/点，ODI/T90 不可靠",
                      "深睡/REM 为设备分期估算"],
        },
        "capabilities": caps,
        "overall": {"score": 78, "label": "良好",
                    "weights": {"sleep_recovery": 0.35, "cardio_autonomic": 0.25,
                                "activity_fitness": 0.20,
                                "circadian_regularity": 0.20}},
        "aux": {
            "temp_dev": {"value": 0.1, "unit": "°C", "per_day": _per_day(0.1)},
            "sleep_score": {"value": 80, "unit": "", "per_day": _per_day(80.0)},
            "energy_score": {"value": 75, "unit": "", "per_day": _per_day(75.0)},
        },
        "history": make_history(),
        "advice": {
            "sleep": ["保持入睡时刻相对稳定", "逐步提前 30 分钟入睡"],
            "circadian": ["固定起床时间以稳定生物钟"],
            "activity": ["每周累计 150 分钟中等强度活动"],
        },
        "llm_input": ("综合就绪度 78；睡眠恢复 82；心肺-自主神经 74；"
                      "活动与体能 69；作息规律 85。"),
    }


def _disabled_cfg():
    return {"llm": {"enabled": False}}


class TestAnalysis14Report(unittest.TestCase):
    def test_renders_capabilities_overall_and_quality(self):
        h = A14.render_html(make_result(), make_history(), _disabled_cfg())
        for name in A14.CAP_LABELS.values():
            self.assertIn(name, h, name)
        self.assertIn("78", h)                     # overall score
        self.assertIn("82", h)                     # sleep_recovery score
        self.assertIn("数据质量", h)
        self.assertIn("总体就绪度", h)
        for note in make_result()["quality"]["notes"]:
            self.assertIn(note, h, note)
        self.assertIn("<svg", h)
        # components table + reference ranges surfaced
        self.assertIn("参考区间", h)
        self.assertIn("7–9 h", h)
        # rule advice + narration section
        self.assertIn("建议", h)
        self.assertIn("模板叙述", h)

    def test_none_capability_score_shows_insufficient(self):
        r = make_result()
        r["capabilities"]["circadian_regularity"]["score"] = None
        r["capabilities"]["circadian_regularity"]["label"] = None
        h = A14.render_html(r, make_history(), _disabled_cfg())
        self.assertIn("数据不足", h)
        self.assertIn("作息规律", h)
        # the other capabilities still carry their scores
        self.assertIn("睡眠恢复", h)
        self.assertIn("82", h)

    def test_llm_failure_falls_back_deterministically(self):
        cfg = {"llm": {"enabled": True, "api_key": "k",
                       "base_url": "http://127.0.0.1:1", "model": "m"}}
        with unittest.mock.patch.object(A14.llm, "request_llm",
                                        side_effect=RuntimeError("boom")):
            h = A14.render_html(make_result(), make_history(), cfg)
        for seg in A14.ADVICE_TITLES:
            self.assertIn("【" + seg + "】", h, seg)
        self.assertIn("模板叙述", h)
        self.assertIn("睡眠恢复", h)
        self.assertIn("78", h)

    def test_llm_success_is_used(self):
        cfg = {"llm": {"enabled": True, "api_key": "k",
                       "base_url": "http://x", "model": "m"}}
        text = ("【综合评估】综合就绪度 78\n【能力明细】睡眠恢复 82\n"
                "【重点改善】规律作息\n【建议】保持活动")
        with unittest.mock.patch.object(A14.llm, "request_llm",
                                        return_value=text) as m:
            h = A14.render_html(make_result(), make_history(), cfg)
        self.assertIn("LLM 叙述", h)
        self.assertIn("规律作息", h)
        m.assert_called_once()

    def test_empty_history_is_graceful(self):
        r = make_result()
        r["history"] = []
        h = A14.render_html(r, [], _disabled_cfg())
        self.assertIn("积累中", h)
        self.assertIn("睡眠恢复", h)

    def test_no_long_floats(self):
        r = make_result()
        r["capabilities"]["sleep_recovery"]["components"][0]["value"] = 7.812345
        r["capabilities"]["sleep_recovery"]["components"][0]["per_day"] = [
            {"date": "08-01", "value": 7.123456},
            {"date": "08-02", "value": 8.987654},
        ]
        r["aux"]["temp_dev"]["value"] = 0.1234567
        r["quality"]["notes"].append("皮温偏离 0.123456 度")
        r["advice"]["sleep"].append("建议睡眠 7.123456 小时")
        hist = [
            {"date": "2026-08-31", "overall": 76.123456,
             "sleep_recovery": 80.987654},
            {"date": "2026-09-01", "overall": 78.654321,
             "sleep_recovery": 82.123456},
        ]
        h = A14.render_html(r, hist, _disabled_cfg())
        self.assertEqual(LONG.findall(h), [])

    def test_llm_output_long_floats_sanitised(self):
        cfg = {"llm": {"enabled": True, "api_key": "k",
                       "base_url": "http://x", "model": "m"}}
        text = ("【综合评估】数值 1.234567\n【能力明细】睡眠恢复 82\n"
                "【重点改善】x\n【建议】y")
        with unittest.mock.patch.object(A14.llm, "request_llm",
                                        return_value=text):
            h = A14.render_html(make_result(), make_history(), cfg)
        self.assertEqual(LONG.findall(h), [])
        self.assertIn("1.23", h)


if __name__ == "__main__":
    unittest.main()
