import sys, unittest
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import report_builder as RB


def meta():
    return {"date": "2026-09-06", "source_name": "20260906_sleep_hr.json",
            "generated_at_local": "2026-09-07 10:00"}


def fake_day():
    return {
        "sleep": {"available": True, "score": 85.0, "duration_s": 38100.0,
                  "duration_text": "10h 35m", "bedtime": "01:10", "wake": "11:30",
                  "deep_min": 90.0,
                  "stages": [{"type": "DEEP", "minutes": 90.0, "pct": 14.0},
                             {"type": "LIGHT", "minutes": 350.0, "pct": 55.0}]},
        "hr": {"sleep_window": {"count": 599, "mean": 65.2, "min": 58.0, "max": 83.0,
                                "first_local": "01:11", "last_local": "11:31", "gap": "1min"},
               "awake": None, "total_count": 599},
        "_charts": {"window_minutes": 620, "timeline": [], "win_series": [], "t0_local": "01:10"},
    }


class TestHtml(unittest.TestCase):
    def test_all_sections_and_markers(self):
        h = RB.build_html(meta(), fake_day(),
                          {"rows": [], "deviations": [], "parsed_days": 1},
                          "昨晚睡眠 85 分，共 10 小时 35 分。", "template")
        for marker in ("① 昨晚睡眠总览", "② 心率概况", "③ 近期基线对比", "④ 异常偏离提示",
                       "10h 35m", "01:10", "睡眠窗口内", "均值 65.2", "模板解读",
                       "基线待积累", "85.0"):
            self.assertIn(marker, h, marker)

    def test_svg_present_when_series(self):
        d = fake_day()
        d["_charts"] = {"window_minutes": 620, "t0_local": "01:10",
                        "timeline": [{"type": "DEEP", "s": 0, "e": 90}],
                        "win_series": [[0, 60], [60, 62], [120, 65]]}
        h = RB.build_html(meta(), d, {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template")
        self.assertIn("<svg", h)
        self.assertIn("均值", h)


def v3_day():
    d = fake_day()
    d["vitals"] = {"spo2_window": {"count": 1, "mean": 96.0,
                                   "min": 96.0, "max": 96.0},
                   "temp_window": {"count": 2, "mean": 36.4,
                                   "min": 36.2, "max": 36.6}}
    d["energy"] = {"score": 88.0}
    d["exercise"] = {"count": 2, "types": [{"type": "WALKING", "count": 1,
                                            "duration_s": 1380.0,
                                            "calories": 96.0},
                                           {"type": "PILATES", "count": 1,
                                            "duration_s": 600.0,
                                            "calories": 30.0}]}
    d["water"] = {"total_ml": 750.0, "count": 2}
    d["body_comp"] = {"weight": 74.2, "body_fat": 18.3,
                      "skeletal_muscle": 33.1, "t_text": "08:30"}
    return d


class TestV3Html(unittest.TestCase):
    def test_vitals_in_section1(self):
        h = RB.build_html(meta(), v3_day(),
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template")
        self.assertIn("血氧", h)
        self.assertIn("96", h)
        self.assertIn("皮温", h)
        self.assertIn("36.4", h)

    def test_energy_activity_section(self):
        h = RB.build_html(meta(), v3_day(),
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template")
        self.assertIn("⑤ 每日能量与活动", h)
        self.assertIn("能量得分", h)
        self.assertIn("步行", h)            # WALKING mapped to Chinese
        self.assertIn("PILATES", h)         # 容错未知类型显示原始名
        self.assertIn("750", h)             # water ml
        self.assertIn("体成分", h)          # body comp line
        self.assertIn("74.2", h)

    def test_no_v3_content_notes_absence(self):
        d = fake_day()
        d["vitals"] = {"spo2_window": None, "temp_window": None}
        d["energy"] = {"score": None}
        d["exercise"] = {"count": 0, "types": []}
        d["water"] = {"total_ml": None, "count": 0}
        d["body_comp"] = None
        h = RB.build_html(meta(), d,
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template")
        self.assertIn("无血氧/皮温记录", h)
        self.assertIn("⑤ 每日能量与活动", h)
        self.assertIn("当日无能量/运动/饮水记录", h)


class TestV4Segments(unittest.TestCase):
    SEG_TEXT = ("【昨夜总结】睡眠 85 分\n"
                "【连续多天趋势】睡眠得分 82→85\n"
                "【作息调整建议】保持规律作息\n"
                "【健康建议】适量活动与饮水\n"
                "【风险提示】未发现显著风险")

    def test_split_segments(self):
        segs = RB._split_segments(self.SEG_TEXT)
        self.assertEqual(len(segs), 5)
        self.assertEqual(segs[0][0], "昨夜总结")
        self.assertEqual(segs[1][0], "连续多天趋势")
        self.assertEqual(segs[3][0], "健康建议")

    def test_split_no_marker_fallback_empty(self):
        self.assertEqual(RB._split_segments("plain text line"), [])

    def test_trend_svg_and_bars(self):
        svg = RB._trend_svg([[0, 80.0], [1, 85.0], [2, 82.0]], "#4263eb", "")
        self.assertIn("<svg", svg)
        self.assertIn("#4263eb", svg)
        bars = RB._bars_svg([[0, 80.0], [1, 85.0], [2, 82.0]], ["09-04", "09-05", "09-06"],
                            "#f59f00", "")
        self.assertIn("<svg", bars)
        self.assertIn("09-04", bars)
        self.assertEqual(RB._trend_svg([[0, 80.0]], "#4263eb", ""), "")   # <2 pts


def trend_day():
    d = fake_day()
    d["vitals"] = {"spo2_window": {"count": 2, "mean": 95.5,
                                   "min": 95.0, "max": 96.0},
                   "temp_window": {"count": 2, "mean": 36.3,
                                   "min": 36.2, "max": 36.4}}
    d["_charts"]["spo2_series"] = [[50, 96.0], [80, 95.0]]
    d["_charts"]["temp_series"] = [[60, 36.2], [90, 36.4]]
    d["_charts"]["hr_24h"] = [{"h": i, "mean": (60.0 if i in (1, 2) else None), "count": 1 if i in (1, 2) else 0} for i in range(24)]
    return d


def seg_text():
    return ("【昨夜总结】睡眠 85 分\n"
            "【连续多天趋势】睡眠得分 82→85\n"
            "【作息调整建议】保持规律作息与适量活动\n"
            "【健康建议】保证饮水与恢复\n"
            "【风险提示】未发现显著风险")


def trend_dict(enough=False):
    base = {"days": ["09-05", "09-06"], "sleep_score": [82.0, 85.0],
            "sleep_hours": [7.0, 7.5], "hr_night_mean": [64.0, 63.0],
            "spo2_night_mean": [None, 96.0], "energy": [80.0, 88.0],
            "water_ml": [None, 750.0], "n_days": 2, "enough": enough}
    if enough:
        base = {"days": ["09-03", "09-04", "09-05"], "sleep_score": [80.0, 82.0, 85.0],
                "sleep_hours": [6.5, 7.0, 7.5], "hr_night_mean": [65.0, 64.0, 63.0],
                "spo2_night_mean": [96.0, 95.0, 96.0], "energy": [79.0, 80.0, 88.0],
                "water_ml": [None, 500.0, 750.0], "n_days": 3, "enough": True}
    return base


class TestTimeSpan(unittest.TestCase):
    def _meta(self):
        m = meta()
        m["span_label"] = "数据时间跨度"
        m["span_start"] = "2026-09-06 00:00"
        m["span_end"] = "2026-09-06 23:59"
        return m

    def test_header_shows_data_span(self):
        h = RB.build_html(self._meta(), fake_day(),
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template")
        self.assertIn("数据时间跨度 2026-09-06 00:00 – 2026-09-06 23:59（Asia/Shanghai）", h)
        self.assertIn("<title>健康晨报 2026-09-06（2026-09-06 00:00 – 2026-09-06 23:59）</title>", h)

    def test_span_label_defaults_when_missing(self):
        m = meta()
        m["span_start"] = "2026-09-06 00:00"
        m["span_end"] = "2026-09-06 23:59"
        h = RB.build_html(m, fake_day(),
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template")
        self.assertIn("时间跨度 2026-09-06 00:00 – 2026-09-06 23:59", h)

    def test_absent_span_leaves_header_unchanged(self):
        h = RB.build_html(meta(), fake_day(),
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template")
        self.assertNotIn("时间跨度", h)

    def test_sleep_line_prefers_full_datetimes(self):
        d = fake_day()
        d["sleep"]["bedtime_full"] = "2026-09-05 23:04"
        d["sleep"]["wake_full"] = "2026-09-06 11:30"
        h = RB.build_html(meta(), d,
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template")
        self.assertIn("入睡 <b>2026-09-05 23:04</b> → 醒来 <b>2026-09-06 11:30</b>", h)

    def test_sleep_line_falls_back_to_hhmm(self):
        h = RB.build_html(meta(), fake_day(),
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template")
        self.assertIn("入睡 <b>01:10</b> → 醒来 <b>11:30</b>", h)


class TestV4Html(unittest.TestCase):
    def test_segmented_top_box(self):
        h = RB.build_html(meta(), fake_day(), {"rows": [], "deviations": [], "parsed_days": 0},
                          seg_text(), "llm")
        for seg in ("昨夜总结", "连续多天趋势", "作息调整建议", "健康建议", "风险提示"):
            self.assertIn(seg, h)
        self.assertIn("LLM 解读", h)
        self.assertIn("【昨夜总结】", h)

    def test_plain_text_fallback(self):
        h = RB.build_html(meta(), fake_day(), {"rows": [], "deviations": [], "parsed_days": 0},
                          "一句话解读", "template")
        self.assertIn("一句话解读", h)

    def test_curves_in_section1_and_24h_in_section2(self):
        h = RB.build_html(meta(), trend_day(),
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template")
        # spo2/temp curves render as svg inside section 1
        self.assertGreaterEqual(h.count("<svg"), 3)   # band(0 empty)+spo2+temp+24h or more
        self.assertIn("睡眠窗口血氧曲线", h)
        self.assertIn("夜间皮温曲线", h)
        self.assertIn("全天心率分布", h)

    def test_trend_accumulating_and_full(self):
        h = RB.build_html(meta(), fake_day(),
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template", trend=trend_dict(enough=False))
        self.assertIn("趋势数据积累中", h)
        h2 = RB.build_html(meta(), fake_day(),
                           {"rows": [], "deviations": [], "parsed_days": 0},
                           "x", "template", trend=trend_dict(enough=True))
        self.assertNotIn("趋势数据积累中", h2)
        self.assertIn("09-03", h2)
        self.assertIn("能量得分", h2)


def v5_day():
    d = fake_day()
    d["steps"] = {"count": 8123}
    d["activity"] = {"active_time_s": 7200, "active_calories": 210.0,
                     "total_calories_burned": None, "distance_m": 5300.0}
    d["floors"] = {"count": 12.0}
    d["blood_pressure"] = {"systolic": 118.0, "diastolic": 76.0,
                           "pulse": 62.0, "t_text": "08:10"}
    d["blood_glucose"] = {"glucose": 5.6, "t_text": "09:00",
                          "measurement_type": "FASTING"}
    d["body_temperature"] = {"temp": 36.7, "t_text": "12:00"}
    d["nutrition"] = {"count": 1, "total_kcal": 650.0}
    return d


class TestV5Html(unittest.TestCase):
    def test_activity_rows_and_vitals_lines(self):
        h = RB.build_html(meta(), v5_day(),
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template")
        self.assertIn("步数", h)
        self.assertIn("8123", h)
        self.assertIn("血压", h)
        self.assertIn("118/76", h)
        self.assertIn("血糖", h)
        self.assertIn("5.6", h)
        self.assertIn("体温", h)
        self.assertIn("营养", h)
        self.assertIn("650", h)

    def test_no_v5_absent_activity_section_stable(self):
        d = fake_day()
        d["steps"] = {"count": None}
        d["activity"] = {"active_time_s": None, "active_calories": None,
                         "total_calories_burned": None, "distance_m": None}
        d["floors"] = {"count": None}
        d["blood_pressure"] = d["blood_glucose"] = d["body_temperature"] = None
        d["nutrition"] = {"count": 0, "total_kcal": None}
        h = RB.build_html(meta(), d,
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template")
        self.assertNotIn("步数 <b>", h)
        self.assertIn("⑤ 每日能量与活动", h)

    def test_trend_steps_row(self):
        tr = trend_dict(enough=True)
        tr["steps"] = [7000.0, 8000.0, 8123.0]
        h = RB.build_html(meta(), fake_day(),
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template", trend=tr)
        self.assertIn("步数（近 3 天）", h)


class TestBaselineTimeRow(unittest.TestCase):
    def test_bedtime_row_renders_hhmm_not_raw_minutes(self):
        base = {"rows": [{"label": "入睡时刻（漂移 -71min）", "day": 1434,
                          "base": 65.0, "unit": "", "drift": -71.0,
                          "day_text": "23:54", "base_text": "01:05"}],
                "deviations": [], "parsed_days": 4}
        h = RB.build_html(meta(), fake_day(), base, "x", "template")
        self.assertIn("23:54", h)
        self.assertIn("01:05", h)
        self.assertIn("-71min", h)
        self.assertNotIn("1434", h)
        self.assertNotIn("65.0", h)

    def test_other_rows_keep_value_plus_unit(self):
        base = {"rows": [{"label": "睡眠时长(h)", "day": 7.5, "base": 7.0,
                          "unit": "h"}],
                "deviations": [], "parsed_days": 4}
        h = RB.build_html(meta(), fake_day(), base, "x", "template")
        self.assertIn("7.5h", h)
        self.assertIn("7.0h", h)


class TestTrendTable(unittest.TestCase):
    def _trend(self):
        tr = trend_dict(enough=True)
        tr["bedtime"] = ["01:30", "01:20", "01:10"]
        tr["steps"] = [7000.0, 8000.0, 8123.0]
        return tr

    def test_trend_table_present_with_per_day_numbers(self):
        h = RB.build_html(meta(), fake_day(),
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template", trend=self._trend())
        self.assertIn('<table class="trend">', h)
        for header in ("日期", "睡眠得分", "时长(h)", "入睡", "夜均心率",
                       "血氧%", "能量", "步数", "饮水(ml)"):
            self.assertIn(header, h)
        self.assertIn("09-03", h)
        self.assertIn("8123", h)      # steps value visible
        self.assertIn("01:10", h)     # bedtime visible
        self.assertIn("—", h)         # 09-03 has no water -> em dash

    def test_trend_bar_labels_not_colliding(self):
        svg = RB._bars_svg([[0, 80.0], [1, 85.0]], ["09-04", "09-05"],
                           "#f59f00", "", show_day_labels=False,
                           show_values=True)
        # no x-axis day label under the bars
        self.assertNotIn('#868e96" text-anchor="middle">09-04', svg)
        # but the value label is rendered
        self.assertIn('#495057" text-anchor="middle">80', svg)

    def test_24h_chart_keeps_hour_labels(self):
        svg = RB._bars_svg([[0, 60.0], [1, 62.0]], ["00", "01"],
                           "#4263eb", "bpm")
        self.assertIn('#868e96" text-anchor="middle">00', svg)


class TestNoLongFloats(unittest.TestCase):
    LONG = re.compile(r"[0-9]+\.[0-9]{3,}")

    def _day(self):
        d = v5_day()
        d["body_comp"] = {"weight": 74.2345678, "body_fat": 5.006129264831543,
                          "skeletal_muscle": 52.54240798950195,
                          "muscle_mass": 40.123456,
                          "basal_metabolic_rate": 1519.4,
                          "total_body_water": 38.99812698364258,
                          "t_text": "08:30"}
        d["_charts"] = {"window_minutes": 620, "t0_local": "01:10",
                        "timeline": [{"type": "DEEP", "s": 0, "e": 90}],
                        "win_series": [[0, 60.123456], [60, 62.987654],
                                       [120, 65.345678]],
                        "spo2_series": [[0, 96.123456], [60, 95.987654]],
                        "temp_series": [[0, 36.123456], [60, 36.456789]],
                        "hr_24h": [{"h": i, "mean": 60.123456 if i == 1 else None,
                                    "count": 1 if i == 1 else 0}
                                   for i in range(24)]}
        return d

    def test_report_html_has_no_long_floats(self):
        tr = trend_dict(enough=True)
        tr["sleep_score"] = [80.123456, 82.987654, 85.456789]
        h = RB.build_html(meta(), self._day(),
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template", trend=tr)
        matches = sorted(set(self.LONG.findall(h)))
        self.assertEqual(matches, [], f"long floats leaked: {matches}")

    def test_body_comp_rounded(self):
        h = RB.build_html(meta(), self._day(),
                          {"rows": [], "deviations": [], "parsed_days": 0},
                          "x", "template")
        self.assertIn("body_fat=5%", h)
        self.assertIn("skeletal_muscle=52.5 kg", h)
        self.assertIn("total_body_water=39 kg", h)
        self.assertIn("basal_metabolic_rate=1519 kcal", h)


if __name__ == "__main__":
    unittest.main()
