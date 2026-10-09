# -*- coding: utf-8 -*-
"""L2 管线集成测试：mock 假模型驱动，验证确定性逻辑。

覆盖：
- translate_lines 校验循环：模型顽疾（原样照抄/译文+残留/宽度错）被检出并补译
- ckpt 指纹：源文件行数变化 → 旧 ckpt 丢弃重译
- 章节级并行：加速比断言
零 API 消耗（全部 mock）。写操作在临时目录。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
sys.path.insert(0, os.path.join(SCRIPTS, "common"))
sys.path.insert(0, os.path.join(SCRIPTS, "works"))

import aa_api as A
import aa_translate as T
import aa_common as C
import registry


def make_chapter(aa_dir, name, n_lines, text_every=4):
    """造一个测试章：文本行(text_every 间隔) + 艺术行"""
    lines = [f"# 测试{name}", "# 原帖地址: https://example.com/blog-entry-1.html"]
    for i in range(n_lines):
        if i % text_every == 0:
            lines.append("こんにちは世界")
        else:
            lines.append("　───　｜　絵　｜　───　")
    with open(os.path.join(aa_dir, f"{name}.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


class _FakeAPI:
    """可编程假模型：mock call_api（translate_lines 的真实入口），按模式返回"""

    def __init__(self):
        self.calls = 0
        self.mode = "good"          # good | copy_jp | residual | wrong_width

    def call_api(self, system, body, **kw):
        """body 为「序号|行」多行文本，按模式产出对应译文"""
        import re
        self.calls += 1
        out = []
        for m in re.finditer(r'^(\d{1,5})\|(.*)$', body, re.M):
            i, l = int(m.group(1)), m.group(2)
            if self.mode == "good":
                r = "你好"                     # 宽度不足自动 pad，无假名残留
            elif self.mode == "copy_jp":
                r = l                          # 顽疾1：原样照抄日语行
            elif self.mode == "residual":
                r = "你好 こんにちは"           # 顽疾2：译文+原文残留
            else:                              # wrong_width
                r = "这是一句很长很长的译文句子塞满了整个气泡区域"
            out.append(f"{i:05d}|{r}")
        return "\n".join(out)

    def translate_lines(self, flagged, *args, **kwargs):
        """供 process_chapter 路径 mock：直接返回合格译文。*args 吸收 work_r18/low_r18/context/base。"""
        self.calls += 1
        return {i: "你好" for i, l in flagged}, [], []


class TestTranslateLinesValidation(unittest.TestCase):
    """校验循环：假模型输出不合格 → 检出并补译（mock call_api，走真实校验逻辑）"""

    def setUp(self):
        self.fake = _FakeAPI()
        # mock 目标：薄壳转发层 FLAT_MOD（common/aa_api.py 2026-08-18 去重后为薄壳，
        # translate_lines 内部引用 FLAT_MOD.call_api，故 mock 必须打到 FLAT_MOD）
        self.orig = A.FLAT_MOD.call_api
        A.FLAT_MOD.call_api = self.fake.call_api
        # 长假名行（剔除艺术假名后仍 ≥3），短残留（また明日 之类）本就该放过
        self.flagged = [(0, "こんにちは世界"), (5, "さようなら"), (10, "おはようございます")]

    def tearDown(self):
        A.FLAT_MOD.call_api = self.orig

    def test_good_output_passes(self):
        fixed, missed, warns = A.translate_lines(self.flagged)
        self.assertEqual(len(fixed), 3)
        self.assertEqual(missed, [])
        self.assertEqual(self.fake.calls, 1)

    def test_copy_jp_detected(self):
        # 原样照抄：残留假名 → 补译轮触发；假模型永远照抄 → 最终仍失败（但走完重试）
        self.fake.mode = "copy_jp"
        fixed, missed, warns = A.translate_lines(self.flagged)
        self.assertEqual(fixed, {})
        self.assertGreaterEqual(self.fake.calls, 2)     # 主译 + ≥1 轮补译

    def test_residual_detected(self):
        self.fake.mode = "residual"
        fixed, missed, warns = A.translate_lines(self.flagged)
        self.assertEqual(fixed, {})
        self.assertGreaterEqual(self.fake.calls, 2)

    def test_wrong_width_detected(self):
        self.fake.mode = "wrong_width"
        fixed, missed, warns = A.translate_lines(self.flagged)
        self.assertEqual(fixed, {})
        self.assertGreaterEqual(self.fake.calls, 2)


class TestCkptFingerprint(unittest.TestCase):
    """源文件行数变化 → 旧 ckpt 必须被丢弃"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hermes-test-ckpt-")
        self.aa = os.path.join(self.tmp, "aa")
        self.out = os.path.join(self.tmp, "汉化")
        self.wk = os.path.join(self.tmp, "work")
        for d in (self.aa, self.out, self.wk):
            os.makedirs(d)
        make_chapter(self.aa, "ch1", 30)
        self.fake = _FakeAPI()
        self.orig = A.FLAT_MOD.translate_lines
        A.FLAT_MOD.translate_lines = self.fake.translate_lines
        self.orig_dirs = (T.AA_DIR, T.OUT_DIR, T.WORK_DIR)
        T.AA_DIR, T.OUT_DIR, T.WORK_DIR = self.aa, self.out, self.wk

    def tearDown(self):
        A.FLAT_MOD.translate_lines = self.orig
        T.AA_DIR, T.OUT_DIR, T.WORK_DIR = self.orig_dirs
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fingerprint_discard(self):
        T.process_chapter(os.path.join(self.aa, "ch1.txt"))
        ckpt = os.path.join(self.wk, "ch1.txt.ckpt.json")
        self.assertTrue(os.path.exists(ckpt))
        calls1 = self.fake.calls
        # 改源文件行数 → 指纹不符
        with open(os.path.join(self.aa, "ch1.txt"), "a", encoding="utf-8") as f:
            f.write("\n追加一行")
        T.process_chapter(os.path.join(self.aa, "ch1.txt"))
        self.assertGreater(self.fake.calls, calls1)      # 重新翻译了
        import json
        data = json.load(open(ckpt, encoding="utf-8"))
        self.assertEqual(data["__lines"], 33)            # 新指纹（32 + 追加 1 行）


class TestChapterParallel(unittest.TestCase):
    """章节级并行：加速比断言（mock 延迟）"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hermes-test-para-")
        self.aa = os.path.join(self.tmp, "aa")
        self.out = os.path.join(self.tmp, "汉化")
        self.wk = os.path.join(self.tmp, "work")
        for d in (self.aa, self.out, self.wk):
            os.makedirs(d)
        for n in range(1, 4):
            make_chapter(self.aa, f"ch{n}", 20)
        # 平铺版 main() 用 AA_CHAPTER_WORKERS env 控制章节并行（模块级无 CHAPTER_WORKERS）
        self.orig_env = os.environ.get("AA_CHAPTER_WORKERS")
        self.orig_dirs = (T.AA_DIR, T.OUT_DIR, T.WORK_DIR)
        T.AA_DIR, T.OUT_DIR, T.WORK_DIR = self.aa, self.out, self.wk
        self.orig_translate = A.FLAT_MOD.translate_lines
        from _work_guard import any_work_cfg
        self.orig_cfg = any_work_cfg("章节并行用例（作品配置仅当模板）")

        def slow_translate(flagged):
            time.sleep(0.4)
            return {i: "你好" for i, l in flagged}, [], []
        A.FLAT_MOD.translate_lines = slow_translate
        self.cfg = any_work_cfg("章节并行用例（作品配置仅当模板）")
        self.cfg["aa_dir"], self.cfg["out_dir"], self.cfg["work_dir"] = self.aa, self.out, self.wk

    def tearDown(self):
        A.FLAT_MOD.translate_lines = self.orig_translate
        T.AA_DIR, T.OUT_DIR, T.WORK_DIR = self.orig_dirs
        if self.orig_env is None:
            os.environ.pop("AA_CHAPTER_WORKERS", None)
        else:
            os.environ["AA_CHAPTER_WORKERS"] = self.orig_env
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根
        self.cfg["aa_dir"] = os.path.join(base, "不幸の催眠", "aa")
        self.cfg["out_dir"] = os.path.join(base, "不幸の催眠", "汉化")
        self.cfg["work_dir"] = os.path.join(base, "不幸の催眠", "work")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parallel_speedup(self):
        import io
        import contextlib

        def run(workers):
            os.environ["AA_CHAPTER_WORKERS"] = str(workers)
            sys.argv = ["x", "fukou"]
            t0 = time.time()
            with contextlib.redirect_stdout(io.StringIO()):
                T.main()
            return time.time() - t0

        t1 = run(1)
        t3 = run(3)
        self.assertLess(t3, t1 * 0.7, f"并行未生效: 串行 {t1:.2f}s 并行 {t3:.2f}s")
        n_done = len([f for f in os.listdir(self.out) if f.endswith("_汉化.txt")])
        self.assertEqual(n_done, 3)


if __name__ == "__main__":
    unittest.main()
