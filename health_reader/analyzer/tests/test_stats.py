import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
import sys, tempfile, json

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import stats


def utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)


SLEEP_UTC = ("2026-09-05T17:10:00Z", "2026-09-06T03:30:00Z")  # local 01:10-11:30 09-06


def sample_payload(hr_n=100, hr_gap_s=60):
    t0 = utc("2026-09-05T17:00:00Z")
    hr = [{"t": (t0 + timedelta(seconds=i * hr_gap_s)).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "hr": 60 + (i % 20)} for i in range(hr_n)]
    sleep = [{"t": "2026-09-06T03:40:00Z", "score": 85, "duration_s": 37200,
              "sessions": [{"start": SLEEP_UTC[0], "end": SLEEP_UTC[1],
                            "stages": [
                                {"type": "DEEP", "start": "2026-09-05T17:10:00Z",
                                 "end": "2026-09-05T17:40:00Z"},
                                {"type": "LIGHT", "start": "2026-09-05T17:40:00Z",
                                 "end": "2026-09-05T19:10:00Z"},
                            ]}]}]
    return {"heart_rate": hr, "sleep": sleep}


class TestLoadPayload(unittest.TestCase):
    def test_stub_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "probe.json"
            p.write_text('{"test": "hello"}', encoding="utf-8")
            self.assertIsNone(stats.load_payload(p))

    def test_valid_payload(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_text(json.dumps(sample_payload()), encoding="utf-8")
            pay = stats.load_payload(p)
            self.assertIsNotNone(pay)
            self.assertEqual(len(pay["heart_rate"]), 100)
            self.assertEqual(pay["sleep"][0]["score"], 85.0)

    def test_missing_fields_dropped(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_text(json.dumps({"heart_rate": [{"t": "bad", "hr": 1},
                                                    {"t": "2026-09-06T00:00:00Z", "hr": "x"},
                                                    {"t": "2026-09-06T00:01:00Z", "hr": 72}]}),
                         encoding="utf-8")
            pay = stats.load_payload(p)
            self.assertEqual(len(pay["heart_rate"]), 1)


class TestDateInference(unittest.TestCase):
    def test_filename_wins(self):
        pay = sample_payload()
        self.assertEqual(str(stats.infer_report_date(pay, "20260908_sleep_hr.json")),
                         "2026-09-08")

    def test_sleep_wake_date(self):
        pay = sample_payload()
        self.assertEqual(str(stats.infer_report_date(pay)), "2026-09-06")

    def test_hr_mode_fallback(self):
        pay = {"heart_rate": sample_payload()["heart_rate"], "sleep": []}
        self.assertEqual(str(stats.infer_report_date(pay)), "2026-09-06")


class TestInterval(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(stats.classify_interval(60), "1min")
        self.assertEqual(stats.classify_interval(300), "5min")
        self.assertEqual(stats.classify_interval(600), "10min")
        self.assertEqual(stats.classify_interval(9999), "不规则")


class TestDayStats(unittest.TestCase):
    def test_sleep_and_hr(self):
        ds = stats.compute_day_stats(sample_payload(hr_n=100))
        self.assertEqual(ds["sleep"]["score"], 85.0)
        self.assertEqual(ds["sleep"]["bedtime"], "01:10")
        self.assertEqual(ds["sleep"]["wake"], "11:30")
        stage_types = [x["type"] for x in ds["sleep"]["stages"]]
        self.assertIn("DEEP", stage_types)
        self.assertIn("LIGHT", stage_types)
        hw = ds["hr"]["sleep_window"]
        self.assertIsNotNone(hw)
        self.assertEqual(hw["gap"], "1min")
        self.assertGreaterEqual(hw["count"], 1)

    def test_empty_hr(self):
        pay = sample_payload(hr_n=0)
        ds = stats.compute_day_stats(pay)
        self.assertIsNone(ds["hr"]["sleep_window"])
        self.assertTrue(ds["sleep"]["available"])


class TestBaseline(unittest.TestCase):
    def test_baseline_excludes_target(self):
        with tempfile.TemporaryDirectory() as td:
            for d in ("20260901", "20260902", "20260903", "20260906"):
                (Path(td) / f"{d}_sleep_hr.json").write_text(
                    json.dumps(sample_payload()), encoding="utf-8")
            files = stats.list_baseline_files(td, datetime.strptime("20260906", "%Y%m%d").date(), 14)
            names = [p.name for _, p in files]
            self.assertNotIn("20260906_sleep_hr.json", names)
            self.assertEqual(len(names), 3)

    def test_compare_activation(self):
        base = stats.build_baseline.__wrapped__ if hasattr(stats.build_baseline, "__wrapped__") else None
        with tempfile.TemporaryDirectory() as td:
            for d in ("20260901", "20260902", "20260903"):
                (Path(td) / f"{d}_sleep_hr.json").write_text(
                    json.dumps(sample_payload()), encoding="utf-8")
            b = stats.build_baseline(td, datetime.strptime("20260906", "%Y%m%d").date(), 14, 60)
            day = stats.compute_day_stats(sample_payload())
            cfg = {"min_baseline_days": 3,
                   "thresholds": {"deep_min": 45, "sleep_hours": 5.5, "hr_delta_bpm": 8,
                                  "bedtime_drift_min": 90}}
            res = stats.compare_baseline(day, b, cfg)
            self.assertGreaterEqual(len(res["rows"]), 1)
            self.assertEqual(res["n_sleep_days"], 3)


def v3_payload():
    """Raw v3-shaped day payload: hr+sleep (existing) plus 6 new arrays."""
    p = sample_payload(hr_n=10)
    p["blood_oxygen"] = [{"t": "2026-09-05T18:00:00Z", "spo2": 96.0, "min": 94.0, "max": 98.0},
                         {"t": "2026-09-06T04:00:00Z", "spo2": 95.0},
                         {"t": "not-a-date", "spo2": 90.0},
                         {"t": "2026-09-06T01:00:00Z", "spo2": "bad"}]
    p["skin_temperature"] = [{"t": "2026-09-05T19:00:00Z", "temp": 36.2},
                             {"t": "2026-09-06T02:00:00Z", "temp": 36.5}]
    p["energy_score"] = [{"t": "2026-09-06T00:00:00Z", "score": 82.0},
                         {"t": "2026-09-06T12:00:00Z", "score": 88.0}]
    p["exercise"] = [{"t": "2026-09-06T09:00:00Z", "type": "WALKING", "title": None,
                      "duration_s": 1380.0, "calories": 96.0}]
    p["water_intake"] = [{"t": "2026-09-06T02:00:00Z", "amount": 250.0},
                         {"t": "2026-09-06T05:00:00Z", "amount": 500.0}]
    p["body_composition"] = [{"t": "2026-09-06T00:30:00Z", "weight": 74.2,
                              "body_fat": 18.3, "skeletal_muscle": 33.1}]
    return p


class TestV3Parse(unittest.TestCase):
    def test_new_arrays_parsed_tolerant(self):
        pay = stats.normalize_payload(v3_payload())
        bo = pay["blood_oxygen"]
        self.assertEqual(len(bo), 2)          # 坏日期 + 坏数值被跳过
        self.assertEqual(bo[0]["spo2"], 96.0)
        self.assertNotIn("not-a-date", [str(x["t"]) for x in bo])
        self.assertEqual(len(pay["skin_temperature"]), 2)
        self.assertEqual(pay["energy_score"][1]["score"], 88.0)
        self.assertEqual(pay["exercise"][0]["type"], "WALKING")
        self.assertEqual(len(pay["water_intake"]), 2)
        self.assertEqual(pay["body_composition"][0]["weight"], 74.2)

    def test_old_payload_missing_arrays_defaults_empty(self):
        pay = stats.normalize_payload(sample_payload())
        for k in ("blood_oxygen", "skin_temperature", "energy_score",
                  "exercise", "water_intake", "body_composition"):
            self.assertEqual(pay.get(k), [])


class TestV3DayStats(unittest.TestCase):
    def test_vitals_window_and_energy_water_exercise(self):
        ds = stats.compute_day_stats(v3_payload())
        v = ds["vitals"]
        # main sleep window = 09-05 17:10Z .. 09-06 03:30Z (sample_payload SLEEP_UTC)
        spo2 = v["spo2_window"]
        self.assertEqual(spo2["count"], 1)          # only 18:00Z entry inside window
        self.assertAlmostEqual(spo2["mean"], 96.0, places=1)
        temp = v["temp_window"]
        self.assertEqual(temp["count"], 2)          # 19:00Z + 02:00Z both inside
        self.assertAlmostEqual(temp["mean"], 36.35, places=1)
        # energy: latest dp value 88.0
        self.assertEqual(ds["energy"]["score"], 88.0)
        self.assertEqual(ds["exercise"]["count"], 1)
        self.assertEqual(ds["exercise"]["types"][0]["type"], "WALKING")
        self.assertEqual(ds["exercise"]["types"][0]["count"], 1)
        self.assertEqual(ds["water"]["total_ml"], 750.0)
        bc = ds["body_comp"]
        self.assertIsNotNone(bc)
        self.assertAlmostEqual(bc["weight"], 74.2, places=1)
        self.assertAlmostEqual(bc["body_fat"], 18.3, places=1)

    def test_v3_absent_day_returns_none_fields(self):
        ds = stats.compute_day_stats(sample_payload())
        self.assertIsNone(ds["vitals"]["spo2_window"])
        self.assertIsNone(ds["energy"]["score"])
        self.assertEqual(ds["exercise"]["count"], 0)
        self.assertIsNone(ds["water"]["total_ml"])
        self.assertIsNone(ds["body_comp"])

    def test_body_comp_prefers_latest_dp(self):
        p = v3_payload()
        p["body_composition"].append({"t": "2026-09-06T11:00:00Z", "weight": 74.0})
        ds = stats.compute_day_stats(p)
        self.assertAlmostEqual(ds["body_comp"]["weight"], 74.0, places=1)


def night_file(day, spo2=None, temp=None, energy=None):
    """Build a baseline day file payload with optional v3 values in-window."""
    p = sample_payload(hr_n=10)
    if spo2 is not None:
        p["blood_oxygen"] = [{"t": "2026-09-05T18:00:00Z", "spo2": spo2}]
    if temp is not None:
        p["skin_temperature"] = [{"t": "2026-09-05T19:00:00Z", "temp": temp}]
    if energy is not None:
        p["energy_score"] = [{"t": "2026-09-06T12:00:00Z", "score": energy}]
    p["t"] = day
    return p


class TestV3Baseline(unittest.TestCase):
    def _write(self, td, pairs):
        for d, spo2, temp, energy in pairs:
            (Path(td) / f"{d}_sleep_hr.json").write_text(
                json.dumps(night_file(d, spo2, temp, energy)), encoding="utf-8")

    def test_baseline_collects_v3_arrays(self):
        with tempfile.TemporaryDirectory() as td:
            self._write(td, [("20260901", 96.0, 36.2, 80.0),
                             ("20260902", 95.0, 36.4, 82.0),
                             ("20260903", None, None, None)])
            b = stats.build_baseline(td,
                                     datetime.strptime("20260906", "%Y%m%d").date(),
                                     14, 60)
            self.assertEqual(b["energy_scores"], [82.0, 80.0])  # newest-first, skip none
            self.assertEqual(len(b["o2_means"]), 2)
            self.assertEqual(len(b["temp_means"]), 2)

    def test_energy_row_immediate_and_deviation(self):
        with tempfile.TemporaryDirectory() as td:
            self._write(td, [("20260905", None, None, 90.0)])
            base = stats.build_baseline(td,
                                        datetime.strptime("20260906", "%Y%m%d").date(),
                                        14, 60)
            day = stats.compute_day_stats(night_file("20260906", energy=40.0))
            cfg = {"min_baseline_days": 3,
                   "thresholds": {"energy_below_ratio": 0.6,
                                  "spo2_low": 92.0, "temp_delta_c": 0.8}}
            res = stats.compare_baseline(day, base, cfg)
            labels = [r["label"] for r in res["rows"]]
            self.assertIn("能量得分", labels)          # 立即：1 样本即出
            self.assertTrue(any("能量" in d for d in res["deviations"]))

    def test_o2_temp_rows_only_after_3_nights(self):
        with tempfile.TemporaryDirectory() as td:
            # only 1 prior night with o2/temp -> rows absent
            self._write(td, [("20260905", 96.0, 36.2, 90.0)])
            base = stats.build_baseline(td,
                                        datetime.strptime("20260906", "%Y%m%d").date(),
                                        14, 60)
            day = stats.compute_day_stats(night_file("20260906",
                                                     spo2=95.0, temp=36.3, energy=91.0))
            cfg = {"min_baseline_days": 3,
                   "thresholds": {"energy_below_ratio": 0.6,
                                  "spo2_low": 92.0, "temp_delta_c": 0.8}}
            res = stats.compare_baseline(day, base, cfg)
            labels = [r["label"] for r in res["rows"]]
            self.assertNotIn("夜间平均血氧(%)", labels)
            self.assertNotIn("夜间皮温(°C)", labels)
        with tempfile.TemporaryDirectory() as td:
            self._write(td, [("20260901", 96.0, 36.2, 80.0),
                             ("20260902", 95.0, 36.4, 81.0),
                             ("20260903", 96.0, 36.1, 82.0)])
            base = stats.build_baseline(td,
                                        datetime.strptime("20260906", "%Y%m%d").date(),
                                        14, 60)
            day = stats.compute_day_stats(night_file("20260906",
                                                     spo2=95.0, temp=36.3, energy=91.0))
            res = stats.compare_baseline(day, base, cfg)
            labels = [r["label"] for r in res["rows"]]
            self.assertIn("夜间平均血氧(%)", labels)
            self.assertIn("夜间皮温(°C)", labels)

    def test_o2_low_and_temp_drift_deviations(self):
        with tempfile.TemporaryDirectory() as td:
            self._write(td, [("20260901", 96.0, 36.2, 80.0),
                             ("20260902", 95.0, 36.4, 81.0),
                             ("20260903", 96.0, 36.1, 82.0)])
            base = stats.build_baseline(td,
                                        datetime.strptime("20260906", "%Y%m%d").date(),
                                        14, 60)
            day = stats.compute_day_stats(night_file("20260906",
                                                     spo2=91.0, temp=37.6, energy=91.0))
            cfg = {"min_baseline_days": 3,
                   "thresholds": {"energy_below_ratio": 0.6,
                                  "spo2_low": 92.0, "temp_delta_c": 0.8}}
            res = stats.compare_baseline(day, base, cfg)
            joined = " | ".join(res["deviations"])
            self.assertIn("血氧", joined)
            self.assertIn("皮温", joined)


class TestV4Charts(unittest.TestCase):
    def test_spo2_and_temp_series_in_window(self):
        ds = stats.compute_day_stats(v3_payload())
        ch = ds["_charts"]
        spo2 = ch["spo2_series"]      # v3_payload: 18:00Z inside window, 04:00Z(+1d) outside
        self.assertEqual(len(spo2), 1)
        self.assertAlmostEqual(spo2[0][1], 96.0, places=1)
        self.assertGreaterEqual(spo2[0][0], 0)      # minutes since local bedtime
        temp = ch["temp_series"]      # 19:00Z + 02:00Z both inside
        self.assertEqual(len(temp), 2)

    def test_hr_24h_buckets_local(self):
        p = sample_payload(hr_n=60, hr_gap_s=60)     # 17:00Z..17:59Z -> local 01:00-01:59
        ds = stats.compute_day_stats(p)
        h24 = ds["_charts"]["hr_24h"]
        self.assertEqual(len(h24), 24)
        self.assertIsNotNone(h24[1]["mean"])         # local hour 01
        self.assertEqual(h24[1]["count"], 60)
        self.assertIsNone(h24[5]["mean"])            # empty hour stays None

    def test_old_chart_keys_still_present(self):
        ds = stats.compute_day_stats(sample_payload())
        self.assertIn("win_series", ds["_charts"])


class TestV4Trend(unittest.TestCase):
    def _write_days(self, td, days, spo2s=None, energy=None):
        for i, d in enumerate(days):
            p = night_file(d, spo2=spo2s[i] if spo2s else None,
                           temp=None,
                           energy=energy[i] if energy else None)
            (Path(td) / f"{d}_sleep_hr.json").write_text(
                json.dumps(p), encoding="utf-8")

    def test_trend_less_than_3_not_enough(self):
        with tempfile.TemporaryDirectory() as td:
            self._write_days(td, ["20260905", "20260906"])
            tr = stats.build_trend(td, window_days=7)
            self.assertFalse(tr["enough"])
            self.assertEqual(tr["n_days"], 2)
            self.assertEqual(len(tr["days"]), 2)

    def test_trend_ascending_aligned_with_none_placeholders(self):
        with tempfile.TemporaryDirectory() as td:
            # day3 has energy only, no spo2 window data in fixture
            self._write_days(td, ["20260904", "20260905", "20260906"],
                             spo2s=[96.0, 95.0, None],
                             energy=[80.0, 82.0, None])
            tr = stats.build_trend(td, window_days=7)
            self.assertTrue(tr["enough"])
            self.assertEqual(tr["days"], ["09-04", "09-05", "09-06"])
            self.assertEqual(len(tr["sleep_score"]), 3)
            self.assertEqual(tr["spo2_night_mean"][:2], [96.0, 95.0])
            self.assertIsNone(tr["spo2_night_mean"][2])
            self.assertEqual(tr["energy"][0], 80.0)
            self.assertIsNone(tr["energy"][2])

    def test_trend_window_caps_and_rejects_stub(self):
        with tempfile.TemporaryDirectory() as td:
            for d in ["20260901", "20260902", "20260903", "20260904"]:
                (Path(td) / f"{d}_sleep_hr.json").write_text(
                    json.dumps(night_file(d, energy=90.0)), encoding="utf-8")
            (Path(td) / "stub.json").write_text('{"x":1}', encoding="utf-8")
            tr = stats.build_trend(td, window_days=3)
            self.assertEqual(tr["n_days"], 3)      # newest 3, stub ignored
            self.assertEqual(tr["days"][-1], "09-04")


def day_payload(score=85, energy=None, steps=None, water=None):
    p = sample_payload(hr_n=10)
    p["sleep"][0]["score"] = score
    if energy is not None:
        p["energy_score"] = [{"t": "2026-09-06T12:00:00Z", "score": energy}]
    if steps is not None:
        p["steps"] = [{"t": "2026-09-06T16:00:00Z", "count": steps}]
    if water is not None:
        p["water_intake"] = [{"t": "2026-09-06T05:00:00Z", "amount": water}]
    return p


class TestMultidayContext(unittest.TestCase):
    def _seed(self, td, specs):
        for d, spec in specs:
            (Path(td) / f"{d}_sleep_hr.json").write_text(
                json.dumps(day_payload(**spec)), encoding="utf-8")

    def test_rows_series_and_deltas(self):
        with tempfile.TemporaryDirectory() as td:
            self._seed(td, [
                ("20260911", {"score": 80, "energy": 80.0, "steps": 5000, "water": 1000.0}),
                ("20260912", {"score": 82, "energy": 84.0, "steps": 7000, "water": 1300.0}),
                ("20260913", {"score": 85, "energy": 88.0, "steps": 8123, "water": 750.0}),
            ])
            ctx = stats.build_multiday_context(
                td, datetime.strptime("20260913", "%Y%m%d").date(), 7)
            self.assertEqual(ctx["n_days"], 3)
            self.assertEqual(len(ctx["rows"]), 3)
            self.assertEqual([r["date"] for r in ctx["rows"]],
                             ["09-11", "09-12", "09-13"])
            self.assertEqual(ctx["rows"][-1]["sleep_score"], 85.0)
            self.assertEqual(ctx["rows"][-1]["bedtime"], "01:10")
            self.assertEqual(ctx["target_index"], 2)
            # baseline excludes target: mean(80, 82) = 81 -> delta +4
            self.assertAlmostEqual(ctx["baseline"]["sleep_score"], 81.0, places=1)
            self.assertAlmostEqual(ctx["deltas"]["sleep_score"], 4.0, places=1)
            # steps baseline mean(5000, 7000) = 6000 -> delta +2123
            self.assertAlmostEqual(ctx["deltas"]["steps"], 2123.0, places=1)
            self.assertEqual(ctx["series"]["bedtime"],
                             ["01:10", "01:10", "01:10"])

    def test_missing_target_metrics_are_none(self):
        with tempfile.TemporaryDirectory() as td:
            for d in ("20260911", "20260912"):
                (Path(td) / f"{d}_sleep_hr.json").write_text(
                    json.dumps(day_payload(energy=80.0)), encoding="utf-8")
            (Path(td) / "20260913_sleep_hr.json").write_text(
                json.dumps(day_payload()), encoding="utf-8")
            ctx = stats.build_multiday_context(
                td, datetime.strptime("20260913", "%Y%m%d").date(), 7)
            last = ctx["rows"][-1]
            self.assertIsNone(last["energy"])
            self.assertIsNone(last["steps"])
            self.assertIsNone(last["water_ml"])
            self.assertIsNotNone(last["sleep_score"])
            self.assertIsNone(ctx["deltas"]["energy"])

    def test_build_trend_includes_bedtime(self):
        with tempfile.TemporaryDirectory() as td:
            for d in ("20260911", "20260912", "20260913"):
                (Path(td) / f"{d}_sleep_hr.json").write_text(
                    json.dumps(day_payload()), encoding="utf-8")
            tr = stats.build_trend(td, 7)
            self.assertEqual(tr["bedtime"], ["01:10", "01:10", "01:10"])
            self.assertEqual(tr["bedtime_min"], [70, 70, 70])

    def test_missing_target_file_still_returns_series(self):
        with tempfile.TemporaryDirectory() as td:
            for d in ("20260911", "20260912", "20260913"):
                (Path(td) / f"{d}_sleep_hr.json").write_text(
                    json.dumps(day_payload()), encoding="utf-8")
            ctx = stats.build_multiday_context(
                td, datetime.strptime("20260914", "%Y%m%d").date(), 7)
            self.assertEqual(ctx["target_index"], 2)   # falls back to newest
            self.assertEqual(ctx["deviations"], [])


def payload_with_bedtime(bedtime_hhmm):
    """sample_payload with the main session shifted so local bedtime matches."""
    p = sample_payload(hr_n=10)
    hh, mm = (int(x) for x in bedtime_hhmm.split(":"))
    utc_start = datetime(2026, 9, 6, hh, mm) - timedelta(hours=8)
    utc_end = utc_start + timedelta(hours=8)
    sess = p["sleep"][0]["sessions"][0]
    sess["start"] = utc_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    sess["end"] = utc_end.strftime("%Y-%m-%dT%H:%M:%SZ")
    sess["stages"] = []
    return p


def _min_day(bedtime):
    """Minimal compute_day_stats-shaped dict for compare_baseline tests."""
    return {"sleep": {"available": True, "score": 85.0, "duration_s": 36000.0,
                      "duration_text": "10h", "bedtime": bedtime, "wake": "07:30",
                      "deep_min": 80.0, "stages": []},
            "hr": {"sleep_window": None, "awake": None, "total_count": 0},
            "vitals": {"spo2_window": None, "temp_window": None},
            "energy": {"score": None}, "exercise": {"count": 0, "types": []},
            "water": {"total_ml": None}, "steps": {"count": None}}


def _base(bed_mins):
    return {"scores": [85.0, 85.0, 85.0], "durs_s": [36000.0] * 3,
            "deep_mins": [80.0] * 3, "bed_mins": list(bed_mins),
            "hr_means": [], "energy_scores": [], "o2_means": [],
            "temp_means": [], "parsed_days": len(bed_mins)}


class TestClockTimeCircular(unittest.TestCase):
    def test_wrap_minutes(self):
        self.assertEqual(stats._wrap_minutes(1369), -71)
        self.assertEqual(stats._wrap_minutes(-1369), 71)
        self.assertEqual(stats._wrap_minutes(0), 0)
        self.assertEqual(stats._wrap_minutes(720), -720)
        self.assertEqual(stats._wrap_minutes(1439), -1)

    def test_circular_mean_straddling_midnight(self):
        # 23:30 + 00:30 -> 00:00, NOT ~12:00
        self.assertAlmostEqual(stats._circular_mean_minutes([1410, 30]),
                               0.0, delta=0.5)

    def test_circular_mean_degenerate_falls_back_linear(self):
        # 00:00 + 12:00: vectors cancel -> linear mean 360 (06:00)
        self.assertAlmostEqual(stats._circular_mean_minutes([0, 720]),
                               360.0, places=1)

    def test_circular_mean_empty_and_none(self):
        self.assertIsNone(stats._circular_mean_minutes([]))
        self.assertIsNone(stats._circular_mean_minutes([None, None]))

    def test_fmt_hhmm(self):
        self.assertEqual(stats._fmt_hhmm(1434), "23:54")
        self.assertEqual(stats._fmt_hhmm(65), "01:05")
        self.assertEqual(stats._fmt_hhmm(0), "00:00")
        self.assertEqual(stats._fmt_hhmm(None), "—")

    def test_compare_baseline_bedtime_drift_wraps(self):
        # 09-10 real window: [09-13 139, 09-12 136, 09-11 5, 09-09 23,
        # 09-08 27, 09-07 68, 09-06 57] -> circular mean 01:05, drift -71.
        base = _base([139, 136, 5, 23, 27, 68, 57])
        res = stats.compare_baseline(_min_day("23:54"), base,
                                     {"min_baseline_days": 3,
                                      "thresholds": {"bedtime_drift_min": 30}})
        row = next(r for r in res["rows"] if r["label"].startswith("入睡时刻"))
        self.assertAlmostEqual(row["drift"], -71.0, delta=1.0)
        self.assertEqual(row["day_text"], "23:54")
        self.assertEqual(row["base_text"], "01:05")
        joined = " | ".join(res["deviations"])
        self.assertNotIn("1369", joined)
        self.assertIn("-71", joined)

    def test_compare_baseline_09_13_circular_mean(self):
        # 09-13 real window (linear 04:10, circular 00:44); today 02:19 -> +95
        base = _base([136, 5, 1434, 23, 27, 68, 57])
        res = stats.compare_baseline(_min_day("02:19"), base,
                                     {"min_baseline_days": 3,
                                      "thresholds": {"bedtime_drift_min": 90}})
        row = next(r for r in res["rows"] if r["label"].startswith("入睡时刻"))
        self.assertEqual(row["base_text"], "00:44")
        self.assertAlmostEqual(row["drift"], 95.0, delta=1.0)
        self.assertNotIn("04:10", " | ".join(res["deviations"]))

    def test_compare_baseline_bedtime_none_no_row(self):
        res = stats.compare_baseline(_min_day(None), _base([23, 27, 68]),
                                     {"min_baseline_days": 3, "thresholds": {}})
        self.assertFalse(any(r["label"].startswith("入睡时刻") for r in res["rows"]))

    def test_build_trend_bedtime_delta_wraps(self):
        with tempfile.TemporaryDirectory() as td:
            specs = {"20260911": "00:10", "20260912": "00:20",
                     "20260913": "23:50"}
            for d, bt in specs.items():
                (Path(td) / f"{d}_sleep_hr.json").write_text(
                    json.dumps(payload_with_bedtime(bt)), encoding="utf-8")
            ctx = stats.build_multiday_context(
                td, datetime.strptime("20260913", "%Y%m%d").date(), 7)
            # baseline circular mean of [10, 20] = 15; wrap(1430 - 15) = -25
            self.assertAlmostEqual(ctx["baseline"]["bedtime_min"], 15.0, delta=1.0)
            self.assertAlmostEqual(ctx["deltas"]["bedtime_min"], -25.0, delta=1.0)
            self.assertLess(abs(ctx["deltas"]["bedtime_min"]), 720)


class TestNoFutureLeakage(unittest.TestCase):
    """A report for date D must only ever use D and earlier days."""

    DAYS = ["20260906", "20260907", "20260908", "20260909",
            "20260910", "20260911", "20260912", "20260913"]

    def _seed_range(self, td):
        for i, d in enumerate(self.DAYS):
            (Path(td) / f"{d}_sleep_hr.json").write_text(
                json.dumps(day_payload(score=80 + i, energy=70.0 + i,
                                       steps=5000 + i)), encoding="utf-8")

    def test_build_trend_end_date_excludes_future(self):
        with tempfile.TemporaryDirectory() as td:
            self._seed_range(td)
            tr = stats.build_trend(
                td, 7, end_date=datetime.strptime("20260910", "%Y%m%d").date())
            self.assertEqual(tr["days"],
                             ["09-06", "09-07", "09-08", "09-09", "09-10"])
            self.assertEqual(tr["n_days"], 5)
            self.assertNotIn("09-11", tr["days"])
            self.assertNotIn("09-13", tr["days"])

    def test_build_trend_without_end_date_keeps_newest(self):
        with tempfile.TemporaryDirectory() as td:
            self._seed_range(td)
            tr = stats.build_trend(td, 7)
            self.assertEqual(tr["days"][-1], "09-13")
            self.assertEqual(len(tr["days"]), 7)   # newest 7 of 8

    def test_build_baseline_excludes_future(self):
        with tempfile.TemporaryDirectory() as td:
            self._seed_range(td)
            rdate = datetime.strptime("20260910", "%Y%m%d").date()
            files = stats.list_baseline_files(td, rdate, 14)
            self.assertEqual([p.name for _, p in files],
                             ["20260909_sleep_hr.json", "20260908_sleep_hr.json",
                              "20260907_sleep_hr.json", "20260906_sleep_hr.json"])
            b = stats.build_baseline(td, rdate, 14, 1)
            self.assertEqual(b["parsed_days"], 4)   # only strictly-earlier days

    def test_multiday_context_no_future_days(self):
        with tempfile.TemporaryDirectory() as td:
            self._seed_range(td)
            ctx = stats.build_multiday_context(
                td, datetime.strptime("20260910", "%Y%m%d").date(), 7)
            self.assertEqual(ctx["days"],
                             ["09-06", "09-07", "09-08", "09-09", "09-10"])
            self.assertEqual(ctx["target_index"], 4)
            for d in ("09-11", "09-12", "09-13"):
                self.assertNotIn(d, ctx["days"])
            # baseline averages only days strictly before the target
            self.assertAlmostEqual(ctx["baseline"]["sleep_score"], 81.5, places=1)
            self.assertAlmostEqual(ctx["deltas"]["sleep_score"], 2.5, places=1)

    def test_single_day_report_has_no_trend(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "20260906_sleep_hr.json").write_text(
                json.dumps(day_payload(score=85)), encoding="utf-8")
            rdate = datetime.strptime("20260906", "%Y%m%d").date()
            tr = stats.build_trend(td, 7, end_date=rdate)
            self.assertEqual(tr["days"], ["09-06"])
            self.assertFalse(tr["enough"])
            b = stats.build_baseline(td, rdate, 14, 1)
            self.assertEqual(b["parsed_days"], 0)


def v5_payload():
    p = v3_payload()
    p["steps"] = [{"t": "2026-09-06T16:00:00Z", "count": 8123}]
    p["activity"] = [{"t": "2026-09-06T16:00:00Z", "active_time_s": 7200,
                      "active_calories": 210.0, "distance_m": 5300.0}]
    p["floors"] = [{"t": "2026-09-06T16:00:00Z", "count": 12.0}]
    p["blood_pressure"] = [{"t": "2026-09-06T00:10:00Z", "systolic": 118,
                            "diastolic": 76, "pulse": 62}]
    p["blood_glucose"] = [{"t": "2026-09-06T01:00:00Z", "glucose": 5.6,
                           "measurement_type": "FASTING"}]
    p["body_temperature"] = [{"t": "2026-09-06T12:00:00Z", "temp": 36.7}]
    p["nutrition"] = [{"t": "2026-09-06T04:00:00Z", "title": "午餐",
                       "meal_type": "LUNCH", "calories": 650.0,
                       "carbs": 80.0, "protein": 30.0, "fat": 20.0}]
    return p


class TestV5Parse(unittest.TestCase):
    def test_new_arrays_parsed_tolerant(self):
        pay = stats.normalize_payload(v5_payload())
        self.assertEqual(pay["steps"][0]["count"], 8123)
        self.assertEqual(pay["activity"][0]["active_time_s"], 7200)
        self.assertEqual(pay["floors"][0]["count"], 12.0)
        self.assertEqual(pay["blood_pressure"][0]["systolic"], 118.0)
        self.assertEqual(pay["blood_glucose"][0]["glucose"], 5.6)
        self.assertEqual(pay["blood_glucose"][0].get("measurement_type"), "FASTING")
        self.assertEqual(pay["body_temperature"][0]["temp"], 36.7)
        self.assertEqual(pay["nutrition"][0]["title"], "午餐")
        self.assertEqual(pay["nutrition"][0]["calories"], 650.0)
        self.assertEqual(len(pay["nutrition"]), 1)

    def test_bad_values_dropped_old_payload_empty(self):
        p = v3_payload()
        p["steps"] = [{"t": "bad", "count": 1}, {"t": "2026-09-06T16:00:00Z", "count": "x"}]
        pay = stats.normalize_payload(p)
        self.assertEqual(pay["steps"], [])
        old = stats.normalize_payload(sample_payload())
        for k in ("steps", "activity", "floors", "blood_pressure",
                  "blood_glucose", "body_temperature", "nutrition"):
            self.assertEqual(old.get(k), [])


class TestV5DayStats(unittest.TestCase):
    def test_daily_v5_metrics(self):
        ds = stats.compute_day_stats(v5_payload())
        self.assertEqual(ds["steps"]["count"], 8123)
        self.assertEqual(ds["activity"]["active_time_s"], 7200)
        self.assertEqual(ds["floors"]["count"], 12.0)
        bp = ds["blood_pressure"]
        self.assertIsNotNone(bp)
        self.assertEqual(bp["systolic"], 118.0)
        self.assertEqual(ds["blood_glucose"]["glucose"], 5.6)
        self.assertEqual(ds["body_temperature"]["temp"], 36.7)
        self.assertEqual(ds["nutrition"]["count"], 1)
        self.assertAlmostEqual(ds["nutrition"]["total_kcal"], 650.0, places=1)

    def test_v5_absent_defaults(self):
        ds = stats.compute_day_stats(sample_payload())
        self.assertIsNone(ds["steps"]["count"])
        self.assertIsNone(ds["blood_pressure"])
        self.assertIsNone(ds["nutrition"]["total_kcal"])

    def test_trend_has_steps_column(self):
        with tempfile.TemporaryDirectory() as td:
            for d in ("20260904", "20260905", "20260906"):
                p = night_file(d, energy=90.0)
                p["steps"] = [{"t": "2026-09-06T16:00:00Z", "count": 5000}]
                (Path(td) / f"{d}_sleep_hr.json").write_text(json.dumps(p), encoding="utf-8")
            tr = stats.build_trend(td, window_days=7)
            self.assertEqual(tr["steps"], [5000.0, 5000.0, 5000.0])


class TestCollectedSpan(unittest.TestCase):
    def test_span_covers_all_samples_on_report_day(self):
        span = stats.collected_span(sample_payload(), date(2026, 9, 6))
        self.assertIsNotNone(span)
        self.assertEqual(span[0].strftime("%Y-%m-%d %H:%M"), "2026-09-06 01:00")
        self.assertEqual(span[1].strftime("%Y-%m-%d %H:%M"), "2026-09-06 11:40")

    def test_span_includes_previous_day_sleep_start(self):
        pay = {"heart_rate": [{"t": "2026-09-06T00:30:00Z", "hr": 60}],  # local 08:30
               "sleep": [{"t": "2026-09-05T15:50:00Z", "score": 80, "duration_s": 28800,
                          "sessions": [{"start": "2026-09-05T15:50:00Z",  # local 23:50
                                        "end": "2026-09-06T00:05:00Z",     # local 08:05
                                        "stages": []}]}]}
        span = stats.collected_span(pay, date(2026, 9, 6))
        self.assertEqual(span[0].strftime("%Y-%m-%d %H:%M"), "2026-09-05 23:50")
        self.assertEqual(span[1].strftime("%Y-%m-%d %H:%M"), "2026-09-06 08:30")

    def test_span_ignores_other_days_history(self):
        pay = sample_payload()
        pay["energy_score"] = [{"t": "2026-08-31T00:00:00Z", "score": 80},
                               {"t": "2026-09-06T00:00:00Z", "score": 90}]
        span = stats.collected_span(pay, date(2026, 9, 6))
        self.assertEqual(span[0].strftime("%Y-%m-%d %H:%M"), "2026-09-06 01:00")

    def test_span_none_without_timestamps(self):
        self.assertIsNone(stats.collected_span(
            {"heart_rate": [], "sleep": []}, date(2026, 9, 6)))

    def test_sleep_carries_full_datetimes(self):
        ds = stats.compute_day_stats(sample_payload())
        self.assertEqual(ds["sleep"]["bedtime_full"], "2026-09-06 01:10")
        self.assertEqual(ds["sleep"]["wake_full"], "2026-09-06 11:30")

    def test_no_session_leaves_full_datetimes_none(self):
        pay = {"heart_rate": sample_payload()["heart_rate"], "sleep": []}
        ds = stats.compute_day_stats(pay)
        self.assertIsNone(ds["sleep"]["bedtime_full"])
        self.assertIsNone(ds["sleep"]["wake_full"])


if __name__ == "__main__":
    unittest.main()
