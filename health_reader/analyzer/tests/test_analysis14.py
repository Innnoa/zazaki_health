import json
import math
import re
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import analysis14
import stats

LONG_FLOAT = re.compile(r"[0-9]+\.[0-9]{3,}")


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _session(start_utc, end_utc, stages):
    return {"start": _iso(start_utc), "end": _iso(end_utc),
            "stages": [{"type": t, "start": _iso(a), "end": _iso(b)}
                       for t, a, b in stages]}


def _payload(sessions, hr=None, spo2=None, temp=None, steps=None,
             activity=None, floors=None, energy=None, score=85):
    p = {"heart_rate": hr or [],
         "sleep": [{"t": "2026-09-06T00:00:00Z", "score": score,
                    "duration_s": 0, "sessions": sessions}]}
    if spo2 is not None:
        p["blood_oxygen"] = spo2
    if temp is not None:
        p["skin_temperature"] = temp
    if steps is not None:
        p["steps"] = steps
    if activity is not None:
        p["activity"] = activity
    if floors is not None:
        p["floors"] = floors
    if energy is not None:
        p["energy_score"] = energy
    return p


# cross-midnight night: local 23:30 (09-05) -> 01:50 (09-06)
X_START = datetime(2026, 9, 5, 15, 30)
X_END = datetime(2026, 9, 5, 17, 50)
X_STAGES = [
    ("AWAKE", datetime(2026, 9, 5, 15, 30), datetime(2026, 9, 5, 15, 40)),
    ("DEEP", datetime(2026, 9, 5, 15, 40), datetime(2026, 9, 5, 16, 10)),
    ("LIGHT", datetime(2026, 9, 5, 16, 10), datetime(2026, 9, 5, 17, 10)),
    ("AWAKE", datetime(2026, 9, 5, 17, 10), datetime(2026, 9, 5, 17, 20)),
    ("REM", datetime(2026, 9, 5, 17, 20), datetime(2026, 9, 5, 17, 50)),
]


def cross_midnight_payload():
    hr = []
    for i in range(0, 141, 5):
        t = X_START + timedelta(minutes=i)
        hr.append({"t": _iso(t), "hr": 60.0 + (i % 10)})
    # awake samples on the same local day but outside the sleep window
    hr.append({"t": _iso(datetime(2026, 9, 5, 10, 0)), "hr": 78.0})
    hr.append({"t": _iso(datetime(2026, 9, 5, 11, 0)), "hr": 76.0})
    spo2 = [{"t": _iso(X_START + timedelta(minutes=i)), "spo2": 96.0 - (i % 3)}
            for i in range(10, 140, 20)]
    temp = [{"t": _iso(X_START + timedelta(minutes=i)), "temp": 36.4 + (i % 3) * 0.1}
            for i in range(10, 140, 30)]
    return _payload([_session(X_START, X_END, X_STAGES)],
                    hr=hr, spo2=spo2, temp=temp)


def full_payload(wake_date: date):
    """A complete day whose sleep ends on `wake_date` (local)."""
    start = datetime(wake_date.year, wake_date.month, wake_date.day, 15, 30) - timedelta(days=1)
    end = start + timedelta(minutes=140)
    stages = [(t, a - timedelta(days=1), b - timedelta(days=1))
              for t, a, b in X_STAGES]
    hr = [{"t": _iso(start + timedelta(minutes=i)), "hr": 58.0 + (i % 8)}
          for i in range(0, 141, 5)]
    hr.append({"t": _iso(start - timedelta(hours=6)), "hr": 78.0})
    spo2 = [{"t": _iso(start + timedelta(minutes=i)), "spo2": 96.0 - (i % 3)}
            for i in range(10, 140, 20)]
    temp = [{"t": _iso(start + timedelta(minutes=i)), "temp": 36.4}
            for i in range(10, 140, 30)]
    return _payload([_session(start, end, stages)], hr=hr, spo2=spo2, temp=temp,
                    steps=[{"t": _iso(end), "count": 8000 + wake_date.day}],
                    activity=[{"t": _iso(end), "active_time_s": 3600,
                               "active_calories": 350.0, "distance_m": 5000.0}],
                    floors=[{"t": _iso(end), "count": 10.0}],
                    energy=[{"t": _iso(end), "score": 85.0}], score=85)


def _write_day(td, d: date, payload=None):
    path = Path(td) / f"{d.strftime('%Y%m%d')}_sleep_hr.json"
    path.write_text(json.dumps(payload if payload is not None else full_payload(d)),
                    encoding="utf-8")
    return path


def _sleep_ind(**over):
    ind = {"TST": 480.0, "SE": 90.0, "deep_pct": 20.0, "rem_pct": 25.0,
           "WASO": 10.0, "sleep_debt": 0.0, "rhr": None, "hr_dip": None,
           "spo2_mean": None, "spo2_min": None, "steps": None,
           "active_time_s": None, "active_calories": None,
           "midpoint_min": None, "midpoint": None}
    ind.update(over)
    return ind


def _grid(ones_from, ones_to):
    g = [0] * 1440
    for m in range(ones_from, ones_to):
        g[m] = 1
    return g


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
class TestPiecewise(unittest.TestCase):
    def test_endpoints_and_clamp(self):
        pts = [(0, 0), (10, 100)]
        self.assertEqual(analysis14.piecewise(0, pts), 0)
        self.assertEqual(analysis14.piecewise(10, pts), 100)
        self.assertEqual(analysis14.piecewise(-5, pts), 0)
        self.assertEqual(analysis14.piecewise(99, pts), 100)

    def test_interpolation(self):
        pts = [(0, 0), (10, 100)]
        self.assertAlmostEqual(analysis14.piecewise(5, pts), 50.0)
        self.assertAlmostEqual(analysis14.piecewise(2.5, pts), 25.0)

    def test_none_and_empty(self):
        self.assertIsNone(analysis14.piecewise(None, [(0, 0), (1, 1)]))
        self.assertIsNone(analysis14.piecewise(5, []))

    def test_anchor_boundaries(self):
        anchors = analysis14._COMPONENTS["sleep_recovery"][0]["anchors"]
        self.assertEqual(analysis14.piecewise(5, anchors), 0)
        self.assertEqual(analysis14.piecewise(6, anchors), 20)
        self.assertEqual(analysis14.piecewise(7, anchors), 70)
        self.assertEqual(analysis14.piecewise(8, anchors), 100)
        self.assertEqual(analysis14.piecewise(9, anchors), 100)
        self.assertEqual(analysis14.piecewise(10, anchors), 80)
        self.assertAlmostEqual(analysis14.piecewise(6.5, anchors), 45.0)


class TestDayIndicators(unittest.TestCase):
    def test_cross_midnight_stage_metrics(self):
        ind = analysis14.day_indicators(cross_midnight_payload(), "Asia/Shanghai")
        self.assertEqual(ind["TIB"], 140.0)
        self.assertEqual(ind["TST"], 120.0)
        self.assertEqual(ind["WASO"], 10.0)     # leading AWAKE excluded
        self.assertEqual(ind["SOL"], 10.0)
        self.assertAlmostEqual(ind["SE"], 85.7, places=1)
        self.assertAlmostEqual(ind["deep_pct"], 25.0, places=1)
        self.assertAlmostEqual(ind["rem_pct"], 25.0, places=1)
        self.assertEqual(ind["bedtime"], "23:40")
        self.assertEqual(ind["wake"], "01:50")
        self.assertEqual(ind["midpoint"], "00:45")
        self.assertEqual(ind["midpoint_min"], 45.0)
        self.assertEqual(ind["sleep_debt"], 6.0)
        self.assertIsNotNone(ind["rhr"])
        self.assertIsNotNone(ind["hr_dip"])
        self.assertIsNotNone(ind["spo2_mean"])
        self.assertIsNotNone(ind["spo2_min"])
        self.assertIsNotNone(ind["temp_mean"])

    def test_awake_before_first_sleep_is_sol_not_waso(self):
        ind = analysis14.day_indicators(cross_midnight_payload(), "Asia/Shanghai")
        # total AWAKE = 20 min; only the post-onset 10 min counts as WASO
        self.assertEqual(ind["WASO"], 10.0)
        self.assertEqual(ind["SOL"], 10.0)

    def test_no_sleep_returns_none(self):
        p = {"heart_rate": [{"t": "2026-09-05T10:00:00Z", "hr": 70}]}
        ind = analysis14.day_indicators(p, "Asia/Shanghai")
        for key in ("TIB", "TST", "WASO", "SE", "SOL", "deep_pct", "rem_pct",
                    "bedtime", "wake", "midpoint", "midpoint_min", "sleep_debt"):
            self.assertIsNone(ind[key], key)


class TestSRI(unittest.TestCase):
    def test_regular_scores_100(self):
        g = _grid(0, 480)
        sri, coverage, computed = analysis14._sri_value(
            [(date(2026, 9, 6), g), (date(2026, 9, 7), list(g))])
        self.assertTrue(computed)
        self.assertEqual(sri, 100)
        self.assertEqual(coverage, 0.5)

    def test_alternating_scores_low(self):
        g1 = _grid(0, 720)
        g2 = _grid(720, 1440)
        sri, _, computed = analysis14._sri_value(
            [(date(2026, 9, 6), g1), (date(2026, 9, 7), g2)])
        self.assertTrue(computed)
        self.assertLessEqual(sri, 0)

    def test_insufficient_coverage_is_none(self):
        sri, coverage, computed = analysis14._sri_value(
            [(date(2026, 9, 6), _grid(0, 480))])
        self.assertIsNone(sri)
        self.assertFalse(computed)
        self.assertEqual(coverage, 0)

    def test_non_consecutive_days_not_paired(self):
        sri, _, computed = analysis14._sri_value(
            [(date(2026, 9, 6), _grid(0, 480)),
             (date(2026, 9, 9), _grid(0, 480))])
        self.assertIsNone(sri)
        self.assertFalse(computed)

    def test_sleep_grid_marks_clock_minutes(self):
        grid = analysis14._sleep_grid(cross_midnight_payload())
        self.assertEqual(grid[1430], 1)   # 23:50 -> sleep
        self.assertEqual(grid[60], 1)     # 01:00 -> sleep
        self.assertEqual(grid[720], 0)    # noon -> awake
        self.assertEqual(grid[1415], 0)   # 23:35 -> leading AWAKE


class TestCircularSD(unittest.TestCase):
    def test_midnight_straddle_is_small(self):
        sd = analysis14._circular_sd_minutes([1430, 10])   # 23:50 vs 00:10
        self.assertIsNotNone(sd)
        self.assertLess(sd, 30)

    def test_identical_is_zero(self):
        self.assertEqual(analysis14._circular_sd_minutes([60, 60, 60]), 0.0)

    def test_less_than_two_is_none(self):
        self.assertIsNone(analysis14._circular_sd_minutes([60]))
        self.assertIsNone(analysis14._circular_sd_minutes([]))


class TestCapabilityScoring(unittest.TestCase):
    def test_all_components_valid(self):
        days = [(date(2026, 9, 13),
                 _sleep_ind(TST=420.0, SE=85.0, deep_pct=15.0, rem_pct=20.0,
                            WASO=30.0, sleep_debt=3.0))]
        cap = analysis14._build_capability("sleep_recovery", days, 14, None, None)
        self.assertEqual(cap["score"], 67)
        self.assertEqual(cap["label"], "一般")

    def test_missing_component_renormalizes(self):
        days = [(date(2026, 9, 13),
                 _sleep_ind(TST=420.0, SE=85.0, deep_pct=None, rem_pct=20.0,
                            WASO=30.0, sleep_debt=3.0))]
        cap = analysis14._build_capability("sleep_recovery", days, 14, None, None)
        # valid weights .25+.20+.10+.15+.15=.85 -> 56.5/.85 = 66.47 -> 66
        self.assertEqual(cap["score"], 66)
        deep = next(c for c in cap["components"] if c["key"] == "deep_pct")
        self.assertIsNone(deep["score"])
        self.assertIsNone(deep["value"])

    def test_less_than_half_valid_is_none(self):
        days = [(date(2026, 9, 13),
                 _sleep_ind(TST=None, SE=None, deep_pct=None, rem_pct=None,
                            WASO=30.0, sleep_debt=3.0))]
        cap = analysis14._build_capability("sleep_recovery", days, 14, None, None)
        self.assertIsNone(cap["score"])
        self.assertEqual(cap["label"], "数据不足")

    def test_three_of_four_cardio_missing_is_none(self):
        ind = _sleep_ind(rhr=58.0, hr_dip=None, spo2_mean=None, spo2_min=None)
        cap = analysis14._build_capability("cardio_autonomic",
                                           [(date(2026, 9, 13), ind)], 14, None, None)
        self.assertIsNone(cap["score"])


class TestHistory(unittest.TestCase):
    @staticmethod
    def _result(overall, caps):
        return {"overall": {"score": overall},
                "capabilities": {c: {"score": caps.get(c)} for c in analysis14.CAPS}}

    def test_append_overwrite_ascending(self):
        with tempfile.TemporaryDirectory() as td:
            p = analysis14.append_score_history(
                td, date(2026, 9, 13),
                self._result(75, {"sleep_recovery": 80, "cardio_autonomic": 70,
                                  "activity_fitness": 60, "circadian_regularity": 65}))
            analysis14.append_score_history(
                td, date(2026, 9, 12),
                self._result(70, {"sleep_recovery": 75}))
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            self.assertEqual(list(data.keys()), ["2026-09-12", "2026-09-13"])
            # same-day overwrite
            analysis14.append_score_history(
                td, date(2026, 9, 13),
                self._result(88, {"sleep_recovery": 90}))
            data2 = json.loads(Path(p).read_text(encoding="utf-8"))
            self.assertEqual(list(data2.keys()), ["2026-09-12", "2026-09-13"])
            self.assertEqual(data2["2026-09-13"]["overall"], 88)
            self.assertEqual(data2["2026-09-13"]["sleep_recovery"], 90)
            self.assertEqual(len(data2), 2)


class TestBuildAnalysis14(unittest.TestCase):
    def test_window_excludes_days_after_rdate(self):
        with tempfile.TemporaryDirectory() as td:
            for d in (date(2026, 9, 11), date(2026, 9, 12), date(2026, 9, 13)):
                _write_day(td, d)
            # a future file must be ignored
            _write_day(td, date(2026, 9, 14))
            r = analysis14.build_analysis14(td, date(2026, 9, 13), {})
            self.assertEqual(r["window"]["end"], "2026-09-13")
            self.assertEqual(r["window"]["start"], "2026-08-31")
            self.assertEqual(r["window"]["days"], 14)
            self.assertEqual(r["window"]["days_present"], 3)
            for cap in analysis14.CAPS:
                for comp in r["capabilities"][cap]["components"]:
                    for pd in comp["per_day"]:
                        self.assertNotEqual(pd["date"], "09-14")
            self.assertNotIn("09-14", r["llm_input"])

    def test_cfg_window_days_and_sleep_need(self):
        with tempfile.TemporaryDirectory() as td:
            for d in (date(2026, 9, 12), date(2026, 9, 13)):
                _write_day(td, d)
            r = analysis14.build_analysis14(
                td, date(2026, 9, 13),
                {"analysis14": {"window_days": 2, "sleep_need_h": 9}})
            self.assertEqual(r["window"]["days"], 2)
            self.assertEqual(r["window"]["start"], "2026-09-12")
            debt = next(c for c in r["capabilities"]["sleep_recovery"]["components"]
                        if c["key"] == "debt")
            self.assertAlmostEqual(debt["value"], 9.0 - 120.0 / 60.0, places=1)

    def test_result_has_frozen_keys_and_no_long_floats(self):
        with tempfile.TemporaryDirectory() as td:
            for d in (date(2026, 9, 11), date(2026, 9, 12), date(2026, 9, 13)):
                _write_day(td, d)
            r = analysis14.build_analysis14(td, date(2026, 9, 13), {})
            self.assertEqual(list(r), ["rdate", "window", "quality", "capabilities",
                                       "overall", "aux", "history", "advice",
                                       "llm_input"])
            self.assertEqual(set(r["capabilities"]), set(analysis14.CAPS))
            self.assertIn("score", r["overall"])
            self.assertIsInstance(r["quality"]["notes"], list)

            bad = []

            def walk(obj, path="root"):
                if isinstance(obj, float):
                    if LONG_FLOAT.search(repr(obj)):
                        bad.append((path, obj))
                elif isinstance(obj, dict):
                    for k, v in obj.items():
                        walk(v, f"{path}.{k}")
                elif isinstance(obj, (list, tuple)):
                    for i, v in enumerate(obj):
                        walk(v, f"{path}[{i}]")

            walk(r)
            self.assertEqual(bad, [])
            self.assertIsNone(LONG_FLOAT.search(r["llm_input"]))

    def test_sri_computed_with_consecutive_days(self):
        with tempfile.TemporaryDirectory() as td:
            for d in (date(2026, 9, 11), date(2026, 9, 12), date(2026, 9, 13)):
                _write_day(td, d)
            r = analysis14.build_analysis14(td, date(2026, 9, 13), {})
            self.assertTrue(r["quality"]["sri_computed"])
            sri = next(c for c in r["capabilities"]["circadian_regularity"]["components"]
                       if c["key"] == "sri")
            self.assertIsNotNone(sri["value"])

    def test_empty_dir_is_safe(self):
        with tempfile.TemporaryDirectory() as td:
            r = analysis14.build_analysis14(td, date(2026, 9, 13), {})
            self.assertEqual(r["window"]["days_present"], 0)
            self.assertFalse(r["quality"]["sri_computed"])
            self.assertIsNone(r["overall"]["score"])


if __name__ == "__main__":
    unittest.main()
