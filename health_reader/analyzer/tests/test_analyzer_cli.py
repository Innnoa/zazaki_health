import json, subprocess, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
ANALYZER = Path(__file__).resolve().parent.parent / "analyzer.py"


def hr_day():
    return {"exported_at": "2026-09-07T04:54:19Z", "tz": "Asia/Shanghai",
            "days": [], "heart_rate": [
                {"t": "2026-09-05T17:11:00Z", "hr": 60.0},
                {"t": "2026-09-05T18:11:00Z", "hr": 70.0}],
            "sleep": [{"t": "2026-09-06T03:40:00Z", "score": 85,
                       "duration_s": 3600,
                       "sessions": [{"start": "2026-09-05T17:10:00Z",
                                     "end": "2026-09-06T03:30:00Z",
                                     "stages": []}]}]}


class TestCli(unittest.TestCase):
    def test_byday_file_generates_report(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "20260906_sleep_hr.json"
            src.write_text(json.dumps(hr_day()), encoding="utf-8")
            r = subprocess.run([sys.executable, str(ANALYZER), "--file", str(src)],
                               capture_output=True, text=True, cwd=str(src.parent))
            self.assertEqual(r.returncode, 0, r.stderr)
            rep = Path(td) / "reports" / "20260906_report.html"
            self.assertTrue(rep.exists())
            content = rep.read_text(encoding="utf-8")
            self.assertIn("85", content)

    def test_stub_rejected_no_report(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "20260906_sleep_hr.json"
            src.write_text('{"test": "hello"}', encoding="utf-8")
            r = subprocess.run([sys.executable, str(ANALYZER), "--file", str(src)],
                               capture_output=True, text=True, cwd=str(src.parent))
            self.assertEqual(r.returncode, 2)
            self.assertFalse((Path(td) / "reports" / "20260906_report.html").exists())

    def test_no_file_mode_newest(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "20260905_sleep_hr.json").write_text(
                json.dumps(hr_day()), encoding="utf-8")
            (Path(td) / "20260906_sleep_hr.json").write_text(
                json.dumps(hr_day()), encoding="utf-8")
            r = subprocess.run([sys.executable, str(ANALYZER), "--data-dir", td],
                               capture_output=True, text=True, cwd=td)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("20260906", r.stdout)


class TestConfigMerge(unittest.TestCase):
    def test_old_config_missing_new_keys_keeps_defaults(self):
        import analyzer
        with tempfile.TemporaryDirectory() as td:
            old = Path(td) / "config.json"
            old.write_text(json.dumps({
                "llm": {"api_key": "abc"},
                "analysis": {"thresholds": {"deep_min": 40}}}),
                encoding="utf-8")
            cfg = analyzer.load_config(old)
        th = cfg["analysis"]["thresholds"]
        self.assertEqual(th["deep_min"], 40)               # user value kept
        self.assertIn("energy_below_ratio", th)            # default merged in
        self.assertEqual(th["energy_below_ratio"], 0.6)
        self.assertIn("spo2_low", th)
        self.assertIn("temp_delta_c", th)


class TestV3Cli(unittest.TestCase):
    def test_v3_day_file_report_contains_new_sections(self):
        with tempfile.TemporaryDirectory() as td:
            p = dict(hr_day())
            p["blood_oxygen"] = [{"t": "2026-09-05T18:00:00Z", "spo2": 96.0}]
            p["energy_score"] = [{"t": "2026-09-06T12:00:00Z", "score": 88.0}]
            src = Path(td) / "20260906_sleep_hr.json"
            src.write_text(json.dumps(p), encoding="utf-8")
            r = subprocess.run([sys.executable, str(ANALYZER), "--file", str(src)],
                               capture_output=True, text=True, cwd=str(src.parent))
            self.assertEqual(r.returncode, 0, r.stderr)
            content = (Path(td) / "reports" / "20260906_report.html").read_text(
                encoding="utf-8")
            self.assertIn("⑤ 每日能量与活动", content)
            self.assertIn("能量得分", content)

    def test_legacy_file_without_v3_arrays_still_reports(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "20260906_sleep_hr.json"
            src.write_text(json.dumps(hr_day()), encoding="utf-8")
            r = subprocess.run([sys.executable, str(ANALYZER), "--file", str(src)],
                               capture_output=True, text=True, cwd=str(src.parent))
            self.assertEqual(r.returncode, 0, r.stderr)
            content = (Path(td) / "reports" / "20260906_report.html").read_text(
                encoding="utf-8")
            self.assertIn("① 昨晚睡眠总览", content)
            self.assertNotIn("Traceback", content)


class TestV4TrendCli(unittest.TestCase):
    def _seed(self, td, days):
        for d in days:
            p = hr_day()
            p["energy_score"] = [{"t": "2026-09-06T12:00:00Z", "score": 85.0}]
            p["blood_oxygen"] = [{"t": "2026-09-05T18:00:00Z", "spo2": 96.0}]
            (Path(td) / f"{d}_sleep_hr.json").write_text(json.dumps(p), encoding="utf-8")

    def test_report_with_trend_full(self):
        with tempfile.TemporaryDirectory() as td:
            self._seed(td, ["20260903", "20260904", "20260905"])
            src = Path(td) / "20260906_sleep_hr.json"
            src.write_text(json.dumps(hr_day()), encoding="utf-8")
            r = subprocess.run([sys.executable, str(ANALYZER), "--file", str(src)],
                               capture_output=True, text=True, cwd=str(src.parent))
            self.assertEqual(r.returncode, 0, r.stderr)
            content = (Path(td) / "reports" / "20260906_report.html").read_text(encoding="utf-8")
            self.assertIn("近 7 天趋势", content)
            self.assertNotIn("趋势数据积累中", content)

    def test_report_trend_accumulating_on_real_dir(self):
        with tempfile.TemporaryDirectory() as td:
            self._seed(td, ["20260905"])   # only 1 baseline + target day => n=1
            src = Path(td) / "20260906_sleep_hr.json"
            src.write_text(json.dumps(hr_day()), encoding="utf-8")
            r = subprocess.run([sys.executable, str(ANALYZER), "--file", str(src)],
                               capture_output=True, text=True, cwd=str(src.parent))
            self.assertEqual(r.returncode, 0, r.stderr)
            content = (Path(td) / "reports" / "20260906_report.html").read_text(encoding="utf-8")
            self.assertIn("积累中", content)


class TestV5Cli(unittest.TestCase):
    def test_v5_day_file_report(self):
        with tempfile.TemporaryDirectory() as td:
            p = hr_day()
            p["steps"] = [{"t": "2026-09-06T16:00:00Z", "count": 8123}]
            p["blood_pressure"] = [{"t": "2026-09-06T00:10:00Z",
                                    "systolic": 118, "diastolic": 76}]
            src = Path(td) / "20260906_sleep_hr.json"
            src.write_text(json.dumps(p), encoding="utf-8")
            r = subprocess.run([sys.executable, str(ANALYZER), "--file", str(src)],
                               capture_output=True, text=True, cwd=str(src.parent))
            self.assertEqual(r.returncode, 0, r.stderr)
            content = (Path(td) / "reports" / "20260906_report.html").read_text(encoding="utf-8")
            self.assertIn("步数", content)
            self.assertIn("血压", content)


class TestV6Cli(unittest.TestCase):
    def test_report_contains_five_segment_markers(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "20260906_sleep_hr.json"
            src.write_text(json.dumps(hr_day()), encoding="utf-8")
            r = subprocess.run([sys.executable, str(ANALYZER), "--file", str(src)],
                               capture_output=True, text=True, cwd=str(src.parent))
            self.assertEqual(r.returncode, 0, r.stderr)
            content = (Path(td) / "reports" / "20260906_report.html").read_text(
                encoding="utf-8")
            for seg in ("【昨夜总结】", "【连续多天趋势】", "【作息调整建议】",
                        "【健康建议】", "【风险提示】"):
                self.assertIn(seg, content)


if __name__ == "__main__":
    unittest.main()
