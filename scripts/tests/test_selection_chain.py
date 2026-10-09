# -*- coding: utf-8 -*-
"""划词翻译通道测试（2026-09-13 换 deepseek-flash 优先 + 艺术护栏/照抄重试；
2026-09-20 短句照抄检测 + 强制重译 + 超时收敛；2026-10-08 链可配 AA_SEL_CHAIN + 开关 AA_SEL_ENABLE）。

运行：python -m unittest discover -s scripts/tests   （需 .venv python）
"""
import os
import sys
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(TESTS_DIR)
_ROOT = os.path.dirname(_SCRIPTS)
for _p in (os.path.join(_ROOT, "aa_web_app"), os.path.join(_SCRIPTS),
           os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import selection as SEL   # noqa: E402

JP_SENT = "やる夫さん、それはちょっと無理だと思いますよ。"
ECHO = JP_SENT                                   # 照抄
GOOD = "亚鲁欧桑，我觉得那有点勉强哦。"              # 正常译文（含保留名 2 假名级）
SHORT_JP = "やる夫「お、お前は！」"                  # 短句（5 假名）—— 旧实现漏检的那一类


class TestLooksEcho(unittest.TestCase):
    def test_echo_detected(self):
        self.assertTrue(SEL._looks_echo(JP_SENT, ECHO))

    def test_translation_ok(self):
        self.assertFalse(SEL._looks_echo(JP_SENT, GOOD))

    def test_kept_name_not_echo(self):
        # 译文里保留了日文专名（やる夫 = 2 假名）不算照抄
        self.assertFalse(SEL._looks_echo(JP_SENT, "やる夫先生，我觉得那有点不可能哦。"))

    def test_short_src_translated_not_echo(self):
        self.assertFalse(SEL._looks_echo("無理。", "不可能。"))

    def test_halfwidth_kana_echo(self):
        src = "ﾀﾞﾒだよ、それは無理だって言ってるだろ。"
        self.assertTrue(SEL._looks_echo(src, src))

    # ---- 2026-09-20：短句照抄必须检出（旧实现源文假名 <8 一律放过 → 弹窗显示日文） ----
    def test_short_echo_detected(self):
        self.assertTrue(SEL._looks_echo(SHORT_JP, SHORT_JP))

    def test_short_echo_detected_with_surrounding_text(self):
        # 混排在艺术/中文里也能认出照抄片段
        self.assertTrue(SEL._looks_echo(SHORT_JP, "　　/⌒ヽ " + SHORT_JP))

    def test_short_translated_ok(self):
        self.assertFalse(SEL._looks_echo(SHORT_JP, "「你、你是！」"))

    def test_short_with_only_kept_name_ok(self):
        # 译文只保留了 2 假名专名（やる）——allow 扣掉后不判照抄
        self.assertFalse(SEL._looks_echo(SHORT_JP, "やる夫：你、你是！", allow=["やる"]))

    def test_short_partial_copy_detected(self):
        # 「すごい」原样留在译文里 → 照抄（3 假名 ≥ 阈值 3 且 = 源文 100%）
        self.assertTrue(SEL._looks_echo("すごい…", "好厉害 すごい"))

    def test_two_kana_too_short_to_judge(self):
        # 2 假名的纯专名句无法判定（阈值 3）—— 宁可放过也不误报
        self.assertFalse(SEL._looks_echo("ミク", "ミク"))

    def test_long_src_keeps_8run_rule(self):
        # 长句仍按 8 连串判：译文含 7 个相同假名不算照抄
        src = "あいうえおかきくけこさしすせそ"
        self.assertFalse(SEL._looks_echo(src, "译文 あいうえおかき"))      # 7 连串 < 8
        self.assertTrue(SEL._looks_echo(src, "译文 あいうえおかきく"))    # 8 连串 → 照抄


class TestChainOrderAndGuard(unittest.TestCase):
    """候选顺序 = deepseek-flash 优先；照抄时去护栏重试；全照抄才降级/标 echo。"""

    def setUp(self):
        import aa_backend
        self.AB = aa_backend
        self.calls = []
        self._noenv = _NoEnvFile()               # 只认真实 env（用例断言默认三通道含 runinfra）
        self._noenv.__enter__()
        self._saved_log = SEL._log_sel
        SEL._log_sel = lambda rec: None          # 测试不落盘 selection_log.jsonl
        self._saved_prompt = SEL._build_prompt
        self._saved_allow = SEL._allow_kana
        SEL._build_prompt = lambda work, base: SEL._SYSTEM + SEL._ART_GUARD
        SEL._allow_kana = lambda work: []

    def tearDown(self):
        self._noenv.__exit__()
        SEL._log_sel = self._saved_log
        SEL._build_prompt = self._saved_prompt
        SEL._allow_kana = self._saved_allow

    def _fake(self, backend, ret):
        def fn(system, text, **kw):
            self.calls.append((backend, SEL._ART_GUARD in system, kw.get("model")))
            return ret
        return fn

    def test_deepseek_first_and_noguard_retry(self):
        seq = []

        def ds(system, text, **kw):
            seq.append(("deepseek", SEL._ART_GUARD in system))
            return ECHO if SEL._ART_GUARD in system else GOOD   # 护栏→照抄, 去护栏→正常

        def di(system, text, **kw):
            seq.append(("deepinfra", SEL._ART_GUARD in system))
            return GOOD

        old_ds, old_di = self.AB.call_deepseek, self.AB.call_deepinfra
        self.AB.call_deepseek, self.AB.call_deepinfra = ds, di
        try:
            res = SEL._do_translate(SEL._SYSTEM + SEL._ART_GUARD, JP_SENT)
        finally:
            self.AB.call_deepseek, self.AB.call_deepinfra = old_ds, old_di
        self.assertEqual(res["text"], GOOD)
        self.assertEqual(res["via"], "deepseek/deepseek-flash")
        self.assertFalse(res["echo"])
        self.assertEqual(seq, [("deepseek", True), ("deepseek", False)])   # 未走到 deepinfra

    def test_both_echo_falls_to_next_channel(self):
        seq = []

        def mk(name, ret):
            def fn(system, text, **kw):
                seq.append(name)
                return ret
            return fn

        old = (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra)
        self.AB.call_deepseek = mk("deepseek", ECHO)
        self.AB.call_deepinfra = mk("deepinfra", GOOD)
        self.AB.call_runinfra = mk("runinfra", GOOD)
        try:
            res = SEL._do_translate(SEL._SYSTEM + SEL._ART_GUARD, JP_SENT)
        finally:
            (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra) = old
        self.assertEqual(res["via"], "deepinfra/deepseek-ai/DeepSeek-V4-Flash-0731")
        self.assertFalse(res["echo"])
        self.assertEqual(seq, ["deepseek", "deepseek", "deepinfra"])

    def test_backend_error_skips_to_next(self):
        def boom(system, text, **kw):
            raise RuntimeError("429")

        old = (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra)
        self.AB.call_deepseek = boom
        self.AB.call_deepinfra = boom
        self.AB.call_runinfra = lambda system, text, **kw: GOOD
        try:
            res = SEL._do_translate(SEL._SYSTEM + SEL._ART_GUARD, JP_SENT)
        finally:
            (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra) = old
        self.assertEqual(res["via"], "runinfra/glm-5-3-flash")

    def test_all_echo_returns_echo_flag_not_raise(self):
        # 2026-09-20: 全通道照抄不再 raise（旧行为抛「三通道均失败」）——返回原文 + echo=True
        old = (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra)
        self.AB.call_deepseek = self._fake("deepseek", ECHO)
        self.AB.call_deepinfra = self._fake("deepinfra", ECHO)
        self.AB.call_runinfra = self._fake("runinfra", ECHO)
        try:
            res = SEL._do_translate(SEL._SYSTEM + SEL._ART_GUARD, JP_SENT)
        finally:
            (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra) = old
        self.assertEqual(res["text"], ECHO)
        self.assertTrue(res["echo"])
        self.assertEqual(res["via"], "deepseek/deepseek-flash")   # 取第一个拿到的结果

    def test_force_avoid_puts_channel_last_and_adds_hint(self):
        seq = []

        def mk(name, ret):
            def fn(system, text, **kw):
                seq.append((name, "_FORCE_HINT" in system or SEL._FORCE_HINT in system))
                return ret
            return fn

        old = (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra)
        self.AB.call_deepseek = mk("deepseek", ECHO)
        self.AB.call_deepinfra = mk("deepinfra", GOOD)
        self.AB.call_runinfra = mk("runinfra", GOOD)
        try:
            res = SEL._do_translate(SEL._SYSTEM + SEL._ART_GUARD, JP_SENT,
                                    force=True, avoid="deepseek/deepseek-flash")
        finally:
            (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra) = old
        # avoid 的通道排到最后 → 先试 deepinfra；force 时 system 带强化提示
        self.assertEqual(res["via"], "deepinfra/deepseek-ai/DeepSeek-V4-Flash-0731")
        self.assertEqual(seq[0], ("deepinfra", True))
        self.assertNotIn("deepseek", [n for n, _ in seq])

    def test_force_accepts_echo(self):
        # force 语义：有非空结果就返回（哪怕照抄），绝不因检测报错
        old = (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra)
        self.AB.call_deepseek = self._fake("deepseek", ECHO)
        self.AB.call_deepinfra = self._fake("deepinfra", ECHO)
        self.AB.call_runinfra = boom = self._fake("runinfra", ECHO)
        try:
            res = SEL._do_translate(SEL._SYSTEM + SEL._ART_GUARD, JP_SENT, force=True)
        finally:
            (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra) = old
        self.assertEqual(res["text"], ECHO)
        self.assertTrue(res["echo"])

    def test_timeout_and_retry_budget_passed(self):
        # 2026-09-20 超时收敛：单候选 timeout=30 / retries=1（原先 120/3）
        seen = []

        def fn(system, text, **kw):
            seen.append((kw.get("timeout"), kw.get("retries")))
            return GOOD

        old = (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra)
        self.AB.call_deepseek = fn
        self.AB.call_deepinfra = fn
        self.AB.call_runinfra = fn
        try:
            SEL._do_translate(SEL._SYSTEM + SEL._ART_GUARD, JP_SENT)
        finally:
            (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra) = old
        self.assertEqual(seen, [(SEL._CALL_TIMEOUT, SEL._CALL_RETRY)])
        self.assertLessEqual(SEL._CALL_TIMEOUT, 45)
        self.assertEqual(SEL._CALL_RETRY, 1)

    def test_all_fail_raises(self):
        def boom(system, text, **kw):
            raise RuntimeError("boom")

        old = (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra)
        self.AB.call_deepseek = self.AB.call_deepinfra = self.AB.call_runinfra = boom
        try:
            with self.assertRaises(RuntimeError):
                SEL._do_translate(SEL._SYSTEM + SEL._ART_GUARD, JP_SENT)
        finally:
            (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra) = old

    def test_build_prompt_has_guard(self):
        # 有 work（术语注入可能失败）与 board 裸翻都必须带护栏
        self.assertIn(SEL._ART_GUARD, self._saved_prompt(None, None))
        self.assertIn(SEL._ART_GUARD, self._saved_prompt("no_such_work_xyz", None))


class TestTranslateTextAutoRetry(unittest.TestCase):
    """非 force 时全照抄 → 自动换通道强制重译一轮；返回 dict 带 via/elapsed/echo。"""

    def setUp(self):
        import aa_backend
        self.AB = aa_backend
        self.logs = []
        self._saved = (SEL._log_sel, SEL._build_prompt, SEL._allow_kana)
        SEL._log_sel = lambda rec: self.logs.append(rec)
        SEL._build_prompt = lambda work, base: SEL._SYSTEM + SEL._ART_GUARD
        SEL._allow_kana = lambda work: []

    def tearDown(self):
        SEL._log_sel, SEL._build_prompt, SEL._allow_kana = self._saved

    def test_auto_force_retry_after_echo(self):
        seq = []

        def ds(system, text, **kw):
            seq.append("deepseek")
            return text                       # 首轮：照抄

        def di(system, text, **kw):
            seq.append("deepinfra")
            return GOOD if SEL._FORCE_HINT in system else text

        old = (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra)
        self.AB.call_deepseek = ds
        self.AB.call_deepinfra = di
        self.AB.call_runinfra = lambda system, text, **kw: text
        try:
            res = SEL.translate_text(SHORT_JP, work=None, base=None)
        finally:
            (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra) = old
        self.assertEqual(res["text"], GOOD)
        self.assertTrue(res["auto_retry"])
        self.assertFalse(res["echo"])
        self.assertIn("deepinfra", res["via"])
        self.assertIn("deepseek", seq)
        self.assertTrue(self.logs and self.logs[-1]["ok"])

    def test_echo_result_logged_when_retry_also_echo(self):
        def mk(ret=None):
            return lambda system, text, **kw: (text if ret is None else ret)

        old = (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra)
        self.AB.call_deepseek = mk()
        self.AB.call_deepinfra = mk()
        self.AB.call_runinfra = mk()
        try:
            res = SEL.translate_text(SHORT_JP, work=None, base=None)
        finally:
            (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra) = old
        self.assertTrue(res["echo"])
        self.assertEqual(res["text"], SHORT_JP)
        self.assertTrue(self.logs[-1]["echo"])

    def test_force_flag_returns_dict_fields(self):
        def mk(ret):
            return lambda system, text, **kw: ret

        old = (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra)
        self.AB.call_deepseek = mk(GOOD)
        self.AB.call_deepinfra = mk(GOOD)
        self.AB.call_runinfra = mk(GOOD)
        try:
            res = SEL.translate_text(SHORT_JP, work=None, base=None, force=True, avoid="x")
        finally:
            (self.AB.call_deepseek, self.AB.call_deepinfra, self.AB.call_runinfra) = old
        for k in ("text", "via", "elapsed", "echo"):
            self.assertIn(k, res)
        self.assertFalse(res["echo"])

    def test_too_long_rejected(self):
        with self.assertRaises(ValueError):
            SEL.translate_text("あ" * (SEL.MAX_SEL_LEN + 1), work=None)

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            SEL.translate_text("   ", work=None)


class _NoEnvFile:
    """屏蔽本机配置层（`<仓库根>/.env`），只读**真实 env**。

    两层都要挡：`aa_env` 在 import 时已把 .env **灌进 os.environ**（所以必须先 pop 掉键），
    而 `selection._env_get` 又会**回落读 .env 文件**（所以还要把该函数换成只读 os.environ）。
    `AA_SEL_CHAIN` 是用户可在设置页写进 `.env` 的合法配置（2026-10-08 起可配），但本文件要断言的是
    **TOML 默认**（`[selection].backends` 旧三通道）。用户改过划词链（本机 .env 就只留了两条）时，
    这些"默认值"用例会红——那是配置差异而非回归。测试该测的是**机制**：env 覆盖 TOML、空串回默认。
    """

    KEYS = ("AA_SEL_CHAIN", "AA_SEL_ENABLE")

    def __enter__(self):
        self._orig = SEL._env_get
        SEL._env_get = lambda name, default=None: os.environ.get(name, default)
        self._saved = {k: os.environ.pop(k) for k in self.KEYS if k in os.environ}
        return self

    def __exit__(self, *exc):
        SEL._env_get = self._orig
        os.environ.update(self._saved)
        return False


class TestSelChainConfig(unittest.TestCase):
    """链可配（2026-10-08 阶段 3）：AA_SEL_CHAIN 覆盖默认三通道；默认值来自 TOML [selection].backends。

    方案 doc/方案归档/工作台设置页与配置文件方案_2026-10-08.md §3 Q4。
    """

    def setUp(self):
        self._noenv = _NoEnvFile()
        self._noenv.__enter__()

    def tearDown(self):
        os.environ.pop("AA_SEL_CHAIN", None)     # 清掉用例自己设的值
        self._noenv.__exit__()                   # 再恢复本机 .env 层的原值

    def test_默认链来自TOML且仍是旧三通道(self):
        self.assertEqual([b for b, _m in SEL.sel_chain()],
                         ["deepseek", "deepinfra", "runinfra"])
        self.assertEqual(SEL.sel_chain()[1][1], "deepseek-ai/DeepSeek-V4-Flash-0731")

    def test_条目id写法(self):
        os.environ["AA_SEL_CHAIN"] = "mimo|mimo-v2.6-flash, deepinfra|m-x"
        self.assertEqual(SEL.sel_chain(),
                         (("mimo", "mimo-v2.6-flash"), ("deepinfra", "m-x")))

    def test_裸后端名用后端默认模型(self):
        os.environ["AA_SEL_CHAIN"] = "deepseek"
        self.assertEqual(SEL.sel_chain(), (("deepseek", None),))

    def test_空串回默认_未识别项忽略(self):
        for val in ("", "   ", ",,", "|"):
            os.environ["AA_SEL_CHAIN"] = val
            self.assertEqual(SEL.sel_chain(), SEL.sel_chain() or SEL.DEFAULT_SEL_ENTRIES)
            self.assertEqual([b for b, _m in SEL.sel_chain()], ["deepseek", "deepinfra", "runinfra"], val)

    def test_候选装配_顺序_avoid排尾_未支持后端跳过(self):
        os.environ["AA_SEL_CHAIN"] = "deepseek|deepseek-flash,nosuch|m"
        self.assertEqual([c[2] for c in SEL._sel_candidates()], ["deepseek/deepseek-flash"])
        os.environ.pop("AA_SEL_CHAIN")
        names = [c[2] for c in SEL._sel_candidates()]
        self.assertEqual(names, ["deepseek/deepseek-flash",
                                 "deepinfra/deepseek-ai/DeepSeek-V4-Flash-0731",
                                 "runinfra/glm-5-3-flash"])
        order = [c[2] for c in SEL._sel_candidates(avoid=names[0])]
        self.assertEqual(len(order), 3)
        self.assertEqual(order[-1], names[0], "avoid 的通道排到最后")

    def test_配置的链真的被_do_translate_用(self):
        import aa_backend as AB
        seq = []
        old = (AB.call_mimo, AB.call_deepseek, AB.call_deepinfra, AB.call_runinfra)
        AB.call_mimo = lambda system, text, **kw: (seq.append("mimo"), "米米译文")[1]
        AB.call_deepseek = lambda system, text, **kw: (seq.append("deepseek"), "站站译文")[1]
        AB.call_deepinfra = AB.call_runinfra = lambda system, text, **kw: (seq.append("other"), "别的")[1]
        os.environ["AA_SEL_CHAIN"] = "mimo|mimo-v2.6-flash"
        try:
            with mock.patch.object(SEL, "_log_sel", lambda rec: None):
                res = SEL._do_translate(SEL._SYSTEM + SEL._ART_GUARD, JP_SENT)
        finally:
            (AB.call_mimo, AB.call_deepseek, AB.call_deepinfra, AB.call_runinfra) = old
        self.assertEqual(seq, ["mimo"])
        self.assertEqual(res["via"], "mimo/mimo-v2.6-flash")


class TestSelEnable(unittest.TestCase):
    """划词开关（AA_SEL_ENABLE，默认 1）：设置页关掉 → 前端不挂监听 + 后端拒（双端硬关）。"""

    def tearDown(self):
        os.environ.pop("AA_SEL_ENABLE", None)

    def test_默认开(self):
        os.environ.pop("AA_SEL_ENABLE", None)
        self.assertTrue(SEL.sel_enabled())

    def test_关的值(self):
        for v in ("0", "false", "False", "no", "off", " OFF "):
            os.environ["AA_SEL_ENABLE"] = v
            self.assertFalse(SEL.sel_enabled(), v)

    def test_开的值(self):
        for v in ("1", "true", "yes", ""):
            os.environ["AA_SEL_ENABLE"] = v
            self.assertTrue(SEL.sel_enabled(), v)


class TestSelectionCatalog(unittest.TestCase):
    """划词链的界面选项（设置页「划词」区块给的可选项）：含付费链条目 + 默认链条目（去重）。"""

    def test_catalog_含非付费链条目且id唯一(self):
        import aa_config as AC
        items, default = AC.selection_catalog()
        ids = [x["id"] for x in items]
        self.assertEqual(len(ids), len(set(ids)), "条目 id 不重复")
        self.assertIn("runinfra|glm-5-3-flash", ids, "runinfra 已从付费链摘除但划词默认链在用")
        for eid in default:
            self.assertIn(eid, ids)
        self.assertEqual(default, ["%s|%s" % (b, m or "") for b, m in AC.selection_defaults()])


if __name__ == "__main__":
    unittest.main()
