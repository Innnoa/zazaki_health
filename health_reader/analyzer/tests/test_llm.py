import sys, unittest
import unittest.mock
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import llm
import stats

DAY = {"sleep": {"available": True, "score": 85.0, "duration_s": 38100.0,
                 "duration_text": "10h 35m", "bedtime": "01:10", "wake": "11:30",
                 "deep_min": 90.0},
       "hr": {"sleep_window": {"mean": 65.2, "min": 58.0, "max": 83.0, "count": 599,
                               "first_local": "01:11", "last_local": "11:31", "gap": "1min"},
              "awake": None, "total_count": 599}}
BASE_EMPTY = {"deviations": []}
BASE_HIT = {"deviations": ["深睡仅 40 分钟，显著低于基线均值 95 分钟"]}


class TestFallback(unittest.TestCase):
    def test_disabled_key_returns_template(self):
        cfg = {"llm": {"enabled": True, "api_key": ""}}
        text, src = llm.get_interpretation(cfg, DAY, BASE_EMPTY)
        self.assertEqual(src, "template")
        self.assertIn("85", text)
        self.assertLessEqual(len(text), llm.MAX_CHARS + 1)

    def test_enabled_but_unreachable_falls_back(self):
        cfg = {"llm": {"enabled": True, "api_key": "k",
                       "base_url": "http://127.0.0.1:1", "model": "m"}}
        text, src = llm.get_interpretation(cfg, DAY, BASE_EMPTY, )
        self.assertEqual(src, "template")
        self.assertIn("85", text)

    def test_template_mentions_deviation(self):
        cfg = {"llm": {"enabled": False}}
        text, src = llm.get_interpretation(cfg, DAY, BASE_HIT)
        self.assertEqual(src, "template")
        self.assertIn("偏离", text)


V3_DAY = {**DAY, "vitals": {"spo2_window": {"mean": 96.0, "count": 1},
                            "temp_window": {"mean": 36.4, "count": 2}},
          "energy": {"score": 88.0},
          "exercise": {"count": 1,
                       "types": [{"type": "WALKING", "count": 1,
                                  "duration_s": 1380.0, "calories": 96.0}]},
          "water": {"total_ml": 750.0, "count": 2},
          "body_comp": {"weight": 74.2, "t_text": "08:30"}}


class TestV3Mini(unittest.TestCase):
    def test_mini_stats_includes_v3(self):
        out = llm._mini_stats(V3_DAY, BASE_EMPTY)
        self.assertEqual(out["能量得分"], 88)
        self.assertIsInstance(out["能量得分"], int)
        self.assertEqual(out["夜均血氧(%)"], 96)
        self.assertEqual(out["运动会话"], 1)
        self.assertEqual(out["饮水(ml)"], 750)

    def test_template_mentions_energy(self):
        text, src = llm.get_interpretation(
            {"llm": {"enabled": False}}, V3_DAY, BASE_EMPTY)
        self.assertEqual(src, "template")
        self.assertIn("能量", text)
        self.assertLessEqual(len(text), llm.MAX_CHARS + 1)

    def test_v3_absent_template_stable(self):
        text, src = llm.get_interpretation({"llm": {"enabled": False}},
                                           DAY, BASE_EMPTY)
        self.assertEqual(src, "template")
        self.assertIn("85", text)


class TestV4Segments(unittest.TestCase):
    def test_template_has_five_segments(self):
        text, src = llm.get_interpretation({"llm": {"enabled": False}},
                                           DAY, BASE_EMPTY)
        self.assertEqual(src, "template")
        for seg in ("【昨夜总结】", "【连续多天趋势】", "【作息调整建议】",
                    "【健康建议】", "【风险提示】"):
            self.assertIn(seg, text)
        self.assertLessEqual(len(text), llm.MAX_CHARS + 1)

    def test_template_risk_mentions_when_deviations(self):
        text, src = llm.get_interpretation({"llm": {"enabled": False}},
                                           DAY, BASE_HIT)
        self.assertEqual(src, "template")
        self.assertIn("深睡", text)

    def test_new_clip_cap(self):
        self.assertEqual(len(llm._clip("a" * 2000)), llm.MAX_CHARS)
        self.assertEqual(llm.MAX_CHARS, 1200)


MD = {
    "days": ["09-11", "09-12", "09-13"],
    "n_days": 3, "enough": True, "target_index": 2,
    "rows": [
        {"date": "09-11", "sleep_score": 80.0, "sleep_hours": 7.0, "bedtime": "01:30",
         "hr_night_mean": 66.0, "spo2_night_mean": 96.0, "energy": 80.0,
         "steps": 6000.0, "water_ml": 1200.0},
        {"date": "09-12", "sleep_score": 82.0, "sleep_hours": 7.2, "bedtime": "01:20",
         "hr_night_mean": 65.0, "spo2_night_mean": 95.0, "energy": 84.0,
         "steps": 7000.0, "water_ml": 1300.0},
        {"date": "09-13", "sleep_score": 85.0, "sleep_hours": 10.6, "bedtime": "01:10",
         "hr_night_mean": 65.2, "spo2_night_mean": 96.0, "energy": 88.0,
         "steps": 8123.0, "water_ml": 750.0},
    ],
    "series": {
        "sleep_score": [80.0, 82.0, 85.0], "sleep_hours": [7.0, 7.2, 10.6],
        "bedtime": ["01:30", "01:20", "01:10"], "hr_night_mean": [66.0, 65.0, 65.2],
        "spo2_night_mean": [96.0, 95.0, 96.0], "energy": [80.0, 84.0, 88.0],
        "steps": [6000.0, 7000.0, 8123.0], "water_ml": [1200.0, 1300.0, 750.0],
    },
    "baseline": {"sleep_score": 81.0, "sleep_hours": 7.1, "hr_night_mean": 65.5,
                 "spo2_night_mean": 95.5, "energy": 82.0, "steps": 6500.0,
                 "water_ml": 1250.0, "bedtime_min": 85.0},
    "deltas": {"sleep_score": 4.0, "sleep_hours": 3.5, "hr_night_mean": -0.3,
               "spo2_night_mean": 0.5, "energy": 6.0, "steps": 1623.0,
               "water_ml": -500.0, "bedtime_min": -20.0},
    "deviations": [],
}


class TestMultiday(unittest.TestCase):
    def test_mini_stats_includes_multiday(self):
        out = llm._mini_stats(DAY, BASE_EMPTY, MD)
        self.assertEqual(out["连续多天"]["天数"], 3)
        self.assertEqual(out["连续多天"]["与基线差"]["sleep_score"], 4.0)
        self.assertEqual(len(out["连续多天"]["逐日"]), 3)

    def test_mini_stats_without_multiday_unchanged(self):
        out = llm._mini_stats(DAY, BASE_EMPTY)
        self.assertNotIn("连续多天", out)

    def test_multiday_sentence_summarizes_series(self):
        text = llm._multiday_sentence(MD)
        self.assertIn("近 3 天", text)
        self.assertIn("睡眠得分", text)
        self.assertIn("80→85", text)
        self.assertIn("01:30", text)
        self.assertIn("01:10", text)
        # concise prose: several sentences, not one giant comma-run
        self.assertGreaterEqual(text.count("。"), 2)
        self.assertNotIn("；", text)

    def test_template_five_segments_with_multiday(self):
        text, src = llm.get_interpretation({"llm": {"enabled": False}},
                                           DAY, BASE_EMPTY, MD)
        self.assertEqual(src, "template")
        for seg in llm.SEGMENT_TITLES:
            self.assertIn("【" + seg + "】", text)
        self.assertIn("近 3 天", text)
        self.assertLessEqual(len(text), llm.MAX_CHARS + 1)


V5_DAY = {**DAY, "steps": {"count": 8123},
          "blood_pressure": {"systolic": 118.0, "diastolic": 76.0, "pulse": 62.0},
          "blood_glucose": {"glucose": 5.6, "measurement_type": "FASTING"},
          "nutrition": {"count": 1, "total_kcal": 650.0},
          "body_temperature": {"temp": 36.7, "t_text": "12:00"}}


class TestV5Mini(unittest.TestCase):
    def test_mini_stats_includes_v5(self):
        out = llm._mini_stats(V5_DAY, BASE_EMPTY)
        self.assertEqual(out["步数"], 8123)
        self.assertEqual(out["血压"], "118/76")
        self.assertEqual(out["血糖(mmol/L)"], 5.6)

    def test_template_mentions_steps_and_bp(self):
        text, src = llm.get_interpretation({"llm": {"enabled": False}},
                                           V5_DAY, BASE_EMPTY)
        self.assertEqual(src, "template")
        self.assertIn("步数", text)
        self.assertIn("血压", text)

    def test_v5_absent_template_stable(self):
        text, src = llm.get_interpretation({"llm": {"enabled": False}},
                                           DAY, BASE_EMPTY)
        self.assertNotIn("血压", text)


GOOD_LLM_TEXT = ("【昨夜总结】睡眠得分 85，总时长 10h 35m\n"
                 "【连续多天趋势】近 3 天睡眠得分 80→85\n"
                 "【作息调整建议】保持规律作息\n"
                 "【健康建议】适量活动与饮水\n"
                 "【风险提示】未发现显著风险")


class TestEmptyContentRetry(unittest.TestCase):
    """Reasoning models can return empty content when reasoning eats the
    max_tokens budget; request_llm_nonempty must retry before falling back."""

    def test_retry_recovers_on_second_call(self):
        calls = []

        def fake_request(cfg, messages, timeout=llm.LLM_TIMEOUT):
            calls.append(1)
            return "" if len(calls) == 1 else GOOD_LLM_TEXT

        with unittest.mock.patch.object(llm, "request_llm", fake_request):
            text, src = llm.get_interpretation(
                {"llm": {"enabled": True, "api_key": "k"}}, DAY, BASE_EMPTY)
        self.assertEqual(src, "llm")
        self.assertIn("睡眠得分 85", text)
        self.assertIn("【连续多天趋势】", text)
        self.assertEqual(len(calls), 2)          # one empty + one good

    def test_retry_skips_whitespace_only_content(self):
        calls = []

        def fake_request(cfg, messages, timeout=llm.LLM_TIMEOUT):
            calls.append(1)
            return "   \n  " if len(calls) < 2 else GOOD_LLM_TEXT

        with unittest.mock.patch.object(llm, "request_llm", fake_request):
            text, src = llm.get_interpretation(
                {"llm": {"enabled": True, "api_key": "k"}}, DAY, BASE_EMPTY)
        self.assertEqual(src, "llm")
        self.assertEqual(len(calls), 2)

    def test_persistent_empty_falls_back_to_template(self):
        calls = []

        def fake_request(cfg, messages, timeout=llm.LLM_TIMEOUT):
            calls.append(1)
            return "  "

        with unittest.mock.patch.object(llm, "request_llm", fake_request):
            text, src = llm.get_interpretation(
                {"llm": {"enabled": True, "api_key": "k"}}, DAY, BASE_EMPTY)
        self.assertEqual(src, "template")
        self.assertEqual(len(calls), llm.EMPTY_RETRIES + 1)   # bounded attempts
        for seg in llm.SEGMENT_TITLES:
            self.assertIn("【" + seg + "】", text)

    def test_request_defaults_have_reasoning_headroom(self):
        self.assertEqual(llm.LLM_MAX_TOKENS, 8192)
        self.assertEqual(llm.LLM_TIMEOUT, 60.0)
        import inspect
        sig = inspect.signature(llm.request_llm)
        self.assertEqual(sig.parameters["max_tokens"].default, 8192)
        self.assertEqual(sig.parameters["timeout"].default, 60.0)


RICH_DAY = {
    "sleep": {"available": True, "score": 85.0, "duration_s": 38100.0,
              "duration_text": "10h 35m", "bedtime": "01:10", "wake": "11:30",
              "deep_min": 90.123456},
    "hr": {"sleep_window": {"mean": 65.2345678, "min": 58.0, "max": 83.0,
                            "count": 599, "first_local": "01:11",
                            "last_local": "11:31", "gap": "1min"},
           "awake": None, "total_count": 599},
    "vitals": {"spo2_window": {"mean": 95.5849, "count": 3},
               "temp_window": {"mean": 36.4467, "count": 3}},
    "energy": {"score": 83.45378875732422},
    "exercise": {"count": 1, "types": []},
    "water": {"total_ml": 1234.5678},
    "body_comp": {"weight": 74.2345678, "body_fat": 5.006129264831543,
                  "skeletal_muscle": 52.54240798950195,
                  "muscle_mass": 40.123456, "basal_metabolic_rate": 1519.4,
                  "total_body_water": 38.99812698364258},
    "steps": {"count": 8123.7},
    "blood_pressure": {"systolic": 118.0, "diastolic": 76.0, "pulse": 62.3456},
    "blood_glucose": {"glucose": 5.612345},
    "body_temperature": {"temp": 36.712345},
    "nutrition": {"count": 1, "total_kcal": 650.789},
}


class TestNumericRounding(unittest.TestCase):
    def _assert_no_long_float(self, obj, path="root"):
        if isinstance(obj, float):
            self.assertIsNone(re.search(r"\.\d{3,}$", repr(obj)),
                              f"long float at {path}: {obj!r}")
        elif isinstance(obj, dict):
            for k, v in obj.items():
                self._assert_no_long_float(v, f"{path}.{k}")
        elif isinstance(obj, (list, tuple)):
            for i, v in enumerate(obj):
                self._assert_no_long_float(v, f"{path}[{i}]")

    def test_round_num(self):
        self.assertEqual(stats.round_num(83.45378875732422, 0), 83)
        self.assertEqual(stats.round_num(88.7176513671875, 0), 89)
        self.assertEqual(stats.round_num(95.5849, 1), 95.6)
        self.assertEqual(stats.round_num(96.0, 1), 96)
        self.assertIsInstance(stats.round_num(96.0, 1), int)
        self.assertEqual(stats.round_num(None), None)
        self.assertEqual(stats.round_num("x"), "x")

    def test_mini_stats_facts_are_rounded(self):
        out = llm._mini_stats(RICH_DAY, BASE_EMPTY, MD)
        self.assertEqual(out["能量得分"], 83)
        self.assertEqual(out["夜均血氧(%)"], 95.6)
        self.assertEqual(out["夜均皮温(°C)"], 36.4)
        self.assertEqual(out["睡眠窗口均心率(bpm)"], 65.2)
        self.assertEqual(out["体重(kg)"], 74.2)
        self.assertEqual(out["血糖(mmol/L)"], 5.6)
        self.assertEqual(out["体温(°C)"], 36.7)
        self.assertEqual(out["营养合计(kcal)"], 651)
        self.assertEqual(out["饮水(ml)"], 1235)
        self.assertEqual(out["深睡(分)"], 90.1)
        self.assertEqual(out["连续多天"]["与基线差"]["steps"], 1623)
        self.assertIsInstance(out["连续多天"]["与基线差"]["steps"], int)
        self._assert_no_long_float(out)

    def test_mini_stats_no_long_floats_without_multiday(self):
        self._assert_no_long_float(llm._mini_stats(RICH_DAY, BASE_EMPTY))


if __name__ == "__main__":
    unittest.main()
