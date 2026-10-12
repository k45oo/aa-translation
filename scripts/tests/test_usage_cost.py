# -*- coding: utf-8 -*-
"""usage 成本字段 + 折算的单测（2026-09-15 成本口径 P0，方案 doc/方案归档/token成本优化方案_2026-09-15.md）。

覆盖:
  · aa_backend._usage_extra：各厂商 usage 字段名差异；**0 与"字段缺失"必须区分**
  · aa_backend._log_usage：cache_hit/reasoning 落盘；AA_USAGE_COST=0 回退旧格式；reasoning=0 不写
  · aa_cost.cost_of / is_peak：命中价×命中 + 未命中价×其余 + 输出价；高峰倍率；未定价 token 计数
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (SCRIPTS, os.path.join(SCRIPTS, "common"), os.path.join(SCRIPTS, "tools")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import aa_backend as AB
import aa_config as C
import aa_cost


class TestUsageExtra(unittest.TestCase):
    def test_deepseek_chat_format(self):
        u = {"prompt_tokens": 100, "prompt_cache_hit_tokens": 80,
             "completion_tokens": 12, "completion_tokens_details": {"reasoning_tokens": 0}}
        self.assertEqual(AB._usage_extra(u), (80, 0))

    def test_openai_compatible_cached_tokens(self):
        u = {"prompt_tokens": 300, "prompt_tokens_details": {"cached_tokens": 256}}
        self.assertEqual(AB._usage_extra(u), (256, None))

    def test_responses_format(self):
        u = {"input_tokens": 500,
             "input_tokens_details": {"cached_tokens": 448},
             "output_tokens_details": {"reasoning_tokens": 37}}
        self.assertEqual(AB._usage_extra(u), (448, 37))

    def test_zero_hit_is_not_missing(self):
        """命中 0（端点支持缓存但没命中）与不支持（字段缺失）必须能区分。"""
        self.assertEqual(AB._usage_extra({"prompt_cache_hit_tokens": 0}), (0, None))
        self.assertEqual(AB._usage_extra({"prompt_tokens": 10}), (None, None))
        self.assertEqual(AB._usage_extra(None), (None, None))
        self.assertEqual(AB._usage_extra({}), (None, None))

    def test_top_level_reasoning_tokens(self):
        self.assertEqual(AB._usage_extra({"reasoning_tokens": 0}), (None, 0))


class TestLogUsageFields(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="aa_usage_")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._old = {k: os.environ.get(k) for k in ("AA_USAGE_FILE", "AA_USAGE_COST", "AA_USAGE_MAX_BYTES", "AA_USAGE_TAG")}
        os.environ["AA_USAGE_FILE"] = os.path.join(self._tmp, "usage.jsonl")
        os.environ["AA_USAGE_MAX_BYTES"] = str(10 * 1024 * 1024)
        os.environ.pop("AA_USAGE_COST", None)
        os.environ["AA_USAGE_TAG"] = "test_cost"
        C.reload()

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        C.reload()

    def _last(self):
        with open(os.environ["AA_USAGE_FILE"], encoding="utf-8") as f:
            return json.loads(f.readlines()[-1])

    def test_cache_hit_and_reasoning_written(self):
        AB._log_usage(1000, 50, model="deepseek-flash", backend="deepseek",
                      u={"prompt_cache_hit_tokens": 960, "completion_tokens_details": {"reasoning_tokens": 12}})
        rec = self._last()
        self.assertEqual(rec["cache_hit"], 960)
        self.assertEqual(rec["reasoning"], 12)
        self.assertEqual(rec["prompt"], 1000)
        self.assertEqual(rec["tag"], "test_cost")

    def test_zero_hit_written_but_zero_reasoning_omitted(self):
        AB._log_usage(100, 5, model="m", backend="b", u={"prompt_cache_hit_tokens": 0, "reasoning_tokens": 0})
        rec = self._last()
        self.assertEqual(rec["cache_hit"], 0)      # 0 是有效值: 端点支持缓存但未命中
        self.assertNotIn("reasoning", rec)         # 0 思考不写, 省体积

    def test_missing_field_not_written(self):
        AB._log_usage(100, 5, model="m", backend="b", u={"prompt_tokens": 100})
        self.assertNotIn("cache_hit", self._last())

    def test_env_rollback(self):
        os.environ["AA_USAGE_COST"] = "0"
        AB._log_usage(100, 5, model="m", backend="b", u={"prompt_cache_hit_tokens": 90})
        rec = self._last()
        self.assertNotIn("cache_hit", rec)
        self.assertNotIn("reasoning", rec)
        self.assertEqual(rec["prompt"], 100)

    def test_no_usage_dict_still_logs(self):
        """老调用点（不传 u）必须继续工作——字段缺失即不写。"""
        AB._log_usage(7, 8, model="m", backend="b")
        rec = self._last()
        self.assertEqual((rec["prompt"], rec["completion"], rec["total"]), (7, 8, 15))


class TestCostOf(unittest.TestCase):
    def test_deepseek_hit_and_miss(self):
        """官网 flash off-peak: 命中 0.02 / 未命中 1.0 / 输出 4.0 元每百万。"""
        ts = time.mktime(time.strptime("2026-09-15 23:00", "%Y-%m-%d %H:%M"))   # 非高峰
        rec = {"backend": "deepseek", "model": "deepseek-flash", "ts": ts,
               "prompt": 1_000_000, "completion": 1_000_000, "cache_hit": 900_000}
        cost, unpriced, approx = aa_cost.cost_of(rec)
        # 900k×0.02 + 100k×1.0 + 1M×4.0 = 0.018 + 0.1 + 4.0
        self.assertAlmostEqual(cost, 4.118, places=4)
        self.assertEqual(unpriced, 0)
        self.assertFalse(approx)

    def test_peak_multiplier(self):
        ts = time.mktime(time.strptime("2026-09-15 10:00", "%Y-%m-%d %H:%M"))   # 工作日高峰(周二)
        rec = {"backend": "deepseek", "model": "deepseek-flash", "ts": ts,
               "prompt": 1_000_000, "completion": 0, "cache_hit": 0}
        cost, _, _ = aa_cost.cost_of(rec)
        self.assertAlmostEqual(cost, 2.0, places=4)      # 未命中 1.0 × 高峰 2

    def test_weekend_not_peak(self):
        ts = time.mktime(time.strptime("2026-09-19 10:00", "%Y-%m-%d %H:%M"))   # 周六
        self.assertFalse(aa_cost.is_peak(ts))
        self.assertTrue(aa_cost.is_peak(time.mktime(time.strptime("2026-09-15 10:00", "%Y-%m-%d %H:%M"))))

    def test_no_cache_hit_field_counts_as_upper_bound(self):
        """历史记录无 cache_hit → 全部按未命中价（上界）。"""
        rec = {"backend": "deepseek", "model": "deepseek-flash", "ts": 0,
               "prompt": 1_000_000, "completion": 0}
        cost, unpriced, _ = aa_cost.cost_of(rec)
        self.assertAlmostEqual(cost, 1.0, places=4)
        self.assertEqual(unpriced, 0)

    def test_unpriced_tokens_counted_not_charged(self):
        """未查证单价的模型：金额 0，token 计入 unpriced（绝不猜价）。"""
        rec = {"backend": "commandcode", "model": "meta/muse-spark-1.3-contributor", "ts": 0,
               "prompt": 1234, "completion": 567, "cache_hit": 0}
        cost, unpriced, approx = aa_cost.cost_of(rec)
        self.assertEqual(cost, 0.0)
        self.assertEqual(unpriced, 1801)
        self.assertTrue(approx)

    def test_free_model_zero(self):
        rec = {"backend": "commandcode", "model": "poolside/laguna-s-2.1-free", "ts": 0,
               "prompt": 999_999, "completion": 999_999, "cache_hit": 0}
        cost, unpriced, _ = aa_cost.cost_of(rec)
        self.assertEqual(cost, 0.0)
        self.assertEqual(unpriced, 0)

    def test_missing_out_price_is_unpriced(self):
        """GLM 条目只填了输入价 → 输出 token 计入 unpriced，而不是按 0 元算。"""
        rec = {"backend": "deepinfra", "model": "zai-org/GLM-5.3-Flash", "ts": 0,
               "prompt": 1000, "completion": 2000, "cache_hit": 0}
        cost, unpriced, approx = aa_cost.cost_of(rec)
        self.assertAlmostEqual(cost, 1000 * 0.8 / 1e6, places=8)
        self.assertEqual(unpriced, 2000)
        self.assertTrue(approx)     # src 含"未查证"


class TestOffWindow(unittest.TestCase):
    """折扣窗口（2026-09-29）：小米 token plan 每日 00:00-08:00 消耗系数 0.8×（**不看周末**）。"""

    MODEL, BACKEND = "mimo-v2.6-flash", "mimo"

    def _rate(self):
        rate, _k = C.pricing_for(self.BACKEND, self.MODEL)
        self.assertTrue(rate, "单价表缺 mimo 条目")
        return rate

    def _rec(self, when, date="2026-09-26"):        # 2026-09-26 = 周六（验证折扣不看周末）
        ts = time.mktime(time.strptime(date + " " + when, "%Y-%m-%d %H:%M"))
        return {"backend": self.BACKEND, "model": self.MODEL, "ts": ts,
                "prompt": 1_000_000, "completion": 1_000_000, "cache_hit": 0}

    def test_rate_declares_off_window(self):
        r = self._rate()
        self.assertEqual(r.get("off"), 0.8)
        self.assertEqual(r.get("off_windows"), ["00:00-08:00"])

    def test_window_bounds_and_weekend(self):
        r = self._rate()
        sat = "2026-09-26"
        self.assertTrue(aa_cost.is_off(time.mktime(time.strptime(sat + " 00:00", "%Y-%m-%d %H:%M")), r))
        self.assertTrue(aa_cost.is_off(time.mktime(time.strptime(sat + " 07:59", "%Y-%m-%d %H:%M")), r))
        self.assertFalse(aa_cost.is_off(time.mktime(time.strptime(sat + " 08:00", "%Y-%m-%d %H:%M")), r))
        self.assertFalse(aa_cost.is_off(time.mktime(time.strptime(sat + " 12:00", "%Y-%m-%d %H:%M")), r))

    def test_time_mult_off_and_peak_combined(self):
        r = self._rate()
        ts_in = time.mktime(time.strptime("2026-09-26 03:00", "%Y-%m-%d %H:%M"))
        self.assertAlmostEqual(aa_cost.time_mult(ts_in, r), 0.8, places=6)
        ts_out = time.mktime(time.strptime("2026-09-26 12:00", "%Y-%m-%d %H:%M"))
        self.assertAlmostEqual(aa_cost.time_mult(ts_out, r), 1.0, places=6)
        # deepseek 条目（有 peak 无 off）：高峰期仍是 ×2
        ds, _k = C.pricing_for("deepseek", "deepseek-flash")
        self.assertAlmostEqual(aa_cost.time_mult(time.mktime(
            time.strptime("2026-09-15 10:00", "%Y-%m-%d %H:%M")), ds), 2.0, places=6)

    def test_cost_of_applies_discount(self):
        """1M 未命中×1.0 + 1M 输出×2.0 = 3.0 元；折扣时段 ×0.8 = 2.4 元。"""
        cost, unpriced, _ = aa_cost.cost_of(self._rec("03:00"))
        self.assertAlmostEqual(cost, 2.4, places=6)
        self.assertEqual(unpriced, 0)
        cost2, _, _ = aa_cost.cost_of(self._rec("12:00"))
        self.assertAlmostEqual(cost2, 3.0, places=6)

    def test_no_off_fields_no_discount(self):
        """没声明 off 的条目不受折扣窗口影响（不猜价、不误折）。"""
        rate = {"miss": 1.0, "out": 2.0}
        ts = time.mktime(time.strptime("2026-09-26 03:00", "%Y-%m-%d %H:%M"))
        self.assertFalse(aa_cost.is_off(ts, rate))
        self.assertAlmostEqual(aa_cost.time_mult(ts, rate), 1.0, places=6)


class TestPricingLookup(unittest.TestCase):
    def test_resolution_order(self):
        r, key = C.pricing_for("deepseek", "deepseek-flash")
        self.assertEqual(key, "deepseek|deepseek-flash")
        r, key = C.pricing_for("deepinfra", "zai-org/GLM-5.3-Flash")
        self.assertEqual(key, "*|zai-org/GLM-5.3-Flash")     # 模型级(跨网关)命中
        self.assertIsNone(C.pricing_for("commandcode", "meta/muse-spark-1.3")[0])   # 未查证 → None
        r, key = C.pricing_for("siliconflow", "THUDM/GLM-4-9B-0414")
        self.assertEqual(key, "siliconflow|*")

    def test_free_detection(self):
        r, key = C.pricing_for("commandcode", "meituan/longcat-2.0:free")
        self.assertEqual((r["miss"], key), (0, "free"))


if __name__ == "__main__":
    unittest.main()
