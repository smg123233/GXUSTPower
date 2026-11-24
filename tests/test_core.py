"""核心逻辑单元测试（不联网）。

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import store  # noqa: E402
from app import freeze_seconds, summarize, usage_between  # noqa: E402
from power_client import (  # noqa: E402
    match_building, match_campus, match_room, normalize_name,
    parse_antiforgery, parse_card_info, parse_recharge_history,
    parse_room_query, parse_student_number, parse_unit,
)
import stats  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ParsePageTest(unittest.TestCase):
    def setUp(self):
        self.page = (FIXTURES / "elec_page.html").read_text(encoding="utf-8")

    def test_antiforgery(self):
        self.assertEqual(parse_antiforgery(self.page), "CfDJ8-TestToken_Value+abc/def==")

    def test_antiforgery_missing(self):
        self.assertEqual(parse_antiforgery("<html></html>"), "")

    def test_student_number_entity_decoded(self):
        self.assertEqual(parse_student_number(self.page), "D3SU3+lrV7czQU81ML5RWQ==")


class UnitTest(unittest.TestCase):
    def test_parse_unit(self):
        self.assertEqual(parse_unit("剩余电量（度）"), "度")
        self.assertEqual(parse_unit("剩余金额(元)"), "元")
        self.assertEqual(parse_unit(""), "度")
        self.assertEqual(parse_unit("剩余电量"), "度")


class RoomQueryTest(unittest.TestCase):
    def test_variants(self):
        cases = {
            "文昌 南区12 412": ("文昌", "南区12", "412"),
            "南12-412": (None, "南12", "412"),
            "南区12栋公寓412": (None, "南区12栋公寓", "412"),
            "文昌校区 南区12栋公寓 412": ("文昌校区", "南区12栋公寓", "412"),
            "412": (None, None, "412"),
        }
        for text, (campus, building, room) in cases.items():
            with self.subTest(text=text):
                hints = parse_room_query(text)
                self.assertEqual(hints["campus"], campus)
                self.assertEqual(hints["building"], building)
                self.assertEqual(hints["room"], room)


CAMPUSES = [
    {"name": "文昌校区", "code": "c1"},
    {"name": "柳石校区", "code": "c2"},
    {"name": "文昌校区", "code": "c3"},
]
BUILDINGS = [
    {"name": "东环校区南区11栋商铺", "code": "b1"},
    {"name": "南区12栋公寓", "code": "b2"},
    {"name": "北区7栋公寓", "code": "b3"},
]
ROOMS = [{"name": "411", "code": "r1"}, {"name": "412", "code": "r2"}]


class MatchTest(unittest.TestCase):
    def test_campus(self):
        self.assertEqual([n["code"] for n in match_campus(CAMPUSES, "文昌")], ["c1", "c3"])
        self.assertEqual([n["code"] for n in match_campus(CAMPUSES, "柳石校区")], ["c2"])
        self.assertEqual(match_campus(CAMPUSES, "不存在"), [])

    def test_building(self):
        self.assertEqual([n["code"] for n in match_building(BUILDINGS, "南12")], ["b2"])
        self.assertEqual([n["code"] for n in match_building(BUILDINGS, "南区12栋公寓")], ["b2"])
        self.assertEqual([n["code"] for n in match_building(BUILDINGS, "北区7栋")], ["b3"])

    def test_room(self):
        self.assertEqual([n["code"] for n in match_room(ROOMS, "412")], ["r2"])
        self.assertEqual(match_room(ROOMS, "999"), [])

    def test_normalize(self):
        self.assertEqual(normalize_name(" 南区12栋公寓 "), "南区12栋公寓")


class UsageTest(unittest.TestCase):
    def _samples(self):
        base = 1_700_000_000
        return [
            {"ts": base, "balance": 100.0},
            {"ts": base + 3600, "balance": 98.5},
            {"ts": base + 7200, "balance": 97.0},
            {"ts": base + 10800, "balance": 120.0},   # 充值
            {"ts": base + 14400, "balance": 118.0},
        ]

    def test_usage_ignores_recharge(self):
        samples = self._samples()
        self.assertAlmostEqual(usage_between(samples, 0, 10 ** 12), 5.0, places=3)

    def test_usage_window(self):
        samples = self._samples()
        start = samples[2]["ts"]
        self.assertAlmostEqual(usage_between(samples, start, 10 ** 12), 2.0, places=3)

    def test_summarize_empty(self):
        stats = summarize([], time.time(), None)
        self.assertIsNone(stats["dailyAvg"])
        self.assertIsNone(stats["daysLeft"])

    def test_summarize_days_left(self):
        base = time.time() - 8 * 86400
        samples = [{"ts": base + i * 3600, "balance": 200.0 - i * 0.5}
                   for i in range(8 * 24)]
        stats_data = summarize(samples, time.time(), 50.0)
        self.assertTrue(stats_data["reliable"])
        self.assertIsNotNone(stats_data["dailyAvg"])
        self.assertIsNotNone(stats_data["daysLeft"])
        self.assertEqual(stats_data["note"], "仅供参考")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self.tmp.name)
        self._old = (store.CONFIG_PATH, store.HISTORY_PATH, store.DATA_DIR)
        store.CONFIG_PATH = tmp_path / "config.json"
        store.HISTORY_PATH = tmp_path / "history.csv"
        store.DATA_DIR = tmp_path

    def tearDown(self):
        store.CONFIG_PATH, store.HISTORY_PATH, store.DATA_DIR = self._old
        self.tmp.cleanup()

    def test_config_defaults_and_roundtrip(self):
        cfg = store.load_config()
        self.assertEqual(cfg["refresh_seconds"], 1800)
        self.assertEqual(cfg["history_keep_days"], 1095)
        self.assertEqual(cfg["room"]["room"], "")
        cfg["account"] = "202400000000"
        cfg["refresh_seconds"] = 120
        store.save_config(cfg)
        loaded = store.load_config()
        self.assertEqual(loaded["account"], "202400000000")
        self.assertEqual(loaded["refresh_seconds"], 120)

    def test_config_clamps_values(self):
        cfg = store.load_config()
        cfg["refresh_seconds"] = 5
        cfg["low_threshold"] = 0
        store.save_config(cfg)
        loaded = store.load_config()
        self.assertEqual(loaded["refresh_seconds"], 60)
        self.assertEqual(loaded["low_threshold"], 1.0)

    def test_password_roundtrip(self):
        encrypted = store.encrypt_password("Aa@133016")
        self.assertTrue(encrypted)
        self.assertNotIn("Aa@133016", encrypted)
        self.assertEqual(store.decrypt_password(encrypted), "Aa@133016")

    def test_history_append_dedupe_and_load(self):
        now = time.time()
        self.assertTrue(store.append_sample(now, "南区12栋公寓/412", 119.72, "度"))
        self.assertFalse(store.append_sample(now + 30, "南区12栋公寓/412", 119.72, "度"))
        self.assertTrue(store.append_sample(now + 600, "南区12栋公寓/412", 118.5, "度"))
        rows = store.load_history(keep_days=1)
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[-1]["balance"], 118.5)

    def test_prune(self):
        old = time.time() - 40 * 86400
        store.append_sample(old, "k", 1.0, "度")
        store.append_sample(time.time(), "k", 2.0, "度")
        removed = store.prune_history(keep_days=30)
        self.assertEqual(removed, 1)
        self.assertEqual(len(store.load_history()), 1)


class StatsSeriesTest(unittest.TestCase):
    def _samples(self):
        base = time.time() - 10 * 86400
        out = []
        for i in range(10 * 48):
            out.append({"ts": base + i * 1800, "balance": 300.0 - i * 0.1})
        return out

    def test_daily_series(self):
        series = stats.daily_series(self._samples(), time.time(), days=10)
        self.assertEqual(len(series), 10)
        self.assertGreater(sum(p["value"] for p in series), 0)
        self.assertTrue(all(p["value"] >= 0 for p in series))

    def test_monthly_and_yearly(self):
        samples = self._samples()
        months = stats.monthly_series(samples, time.time(), months=12)
        years = stats.yearly_series(samples, time.time(), years=5)
        self.assertEqual(len(months), 12)
        self.assertEqual(len(years), 5)
        self.assertGreater(sum(p["value"] for p in months), 0)
        self.assertGreater(sum(p["value"] for p in years), 0)

    def test_estimate_needs_a_week(self):
        samples = [{"ts": time.time() - i * 3600, "balance": 100.0 - i * 0.1}
                   for i in range(10, 0, -1)]
        est = stats.estimate(samples, time.time(), 100.0)
        self.assertFalse(est["available"])
        self.assertIn("天", est["reason"])

    def test_estimate_ignores_recharge(self):
        base = time.time() - 8 * 86400
        samples = []
        for day in range(8):
            for half in range(2):
                samples.append({"ts": base + day * 86400 + half * 43200,
                                "balance": 100.0 - day * 2 - half * 0.5})
        samples.append({"ts": time.time() - 3600, "balance": 150.0})  # 充值
        est = stats.estimate(samples, time.time(), 150.0)
        self.assertTrue(est["available"])
        self.assertAlmostEqual(est["dailyAvg"], 2.0, delta=0.6)


class CardParseTest(unittest.TestCase):
    def test_card_info(self):
        page = (FIXTURES / "card_page.html").read_text(encoding="utf-8")
        info = parse_card_info(page)
        self.assertEqual(info.name, "同学")
        self.assertEqual(info.student_number, "202400000000")
        self.assertAlmostEqual(info.balance, 1.69)

    def test_recharge_history(self):
        payload = [{"YearsMonth": "2026-11",
                    "RechargeHistorys": [
                        {"RoomInfo": "文昌校区/南区12栋公寓/412", "IsEntry": True,
                         "RechargeRsult": "充值成功", "Money": "1", "DateTime": "2026-11-24 10:00:00"},
                        {"RoomInfo": "文昌校区/南区12栋公寓/412", "IsEntry": False,
                         "RechargeRsult": "失败", "Money": "5", "DateTime": "2026-11-23 09:00:00"}]}]
        records = parse_recharge_history(payload)
        self.assertEqual(len(records), 2)
        self.assertTrue(records[0].success)
        self.assertEqual(records[0].time, "2026-11-24 10:00:00")
        self.assertEqual(records[0].room, "文昌校区/南区12栋公寓/412")

    def test_freeze_seconds(self):
        self.assertEqual(freeze_seconds("账号或密码输入错误超过3次，冻结1小时!"), 3660)
        self.assertEqual(freeze_seconds("请稍后再试"), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
