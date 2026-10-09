# -*- coding: utf-8 -*-
"""L4 测试：aa_web_app.logic 纯逻辑（保存校验 / 备份轮转 / 术语应用 / glossary 读写）。

运行：python -m unittest discover -s scripts/tests -v
零依赖（unittest + 临时目录），不启动服务器。
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))   # scripts/tests -> 仓库根
sys.path.insert(0, _REPO_ROOT)                             # aa_web_app 已移到仓库根
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts", "common"))

from aa_web_app import logic  # noqa: E402
from aa_web_app import reader_win  # noqa: E402  (阅读器窗口化上下文；不依赖 fastapi)


class Fixture(unittest.TestCase):
    """临时作品结构：aa/（原文）+ tr/（汉化）+ backup/"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aaweb_test_")
        self.aa_dir = os.path.join(self.tmp, "aa")
        self.tr_dir = os.path.join(self.tmp, "tr")
        self.bk_dir = os.path.join(self.tmp, "backup")
        os.makedirs(self.aa_dir)
        os.makedirs(self.tr_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, d, name, lines, crlf=True, newline=False):
        p = os.path.join(d, name)
        text = "\n".join(lines)
        if newline:
            text += "\n"
        with open(p, "w", encoding="utf-8", newline="\r\n" if crlf else "\n") as f:
            f.write(text)
        return p


class TestReadWrite(Fixture):
    def test_crlf_roundtrip(self):
        p = self.write(self.tr_dir, "entry_1_汉化.txt", ["あいう", "abc"], newline=True)
        lines = logic.read_lines(p)
        self.assertEqual(lines, ["あいう", "abc", ""])
        logic.write_lines(p, lines)
        with open(p, "rb") as f:
            raw = f.read()
        self.assertIn(b"\r\n", raw)          # Windows 文本模式保持 CRLF
        self.assertEqual(logic.read_lines(p), ["あいう", "abc", ""])


class TestSaveLine(Fixture):
    def setUp(self):
        super().setUp()
        self.src = self.write(self.aa_dir, "entry_1.txt",
                              ["# 标题", "あいう", "あああ│", "abc"])
        self.tr = self.write(self.tr_dir, "entry_1_汉化.txt",
                             ["# 标题", "中文", "文", "abc"])

    def test_pad_short_line(self):
        # src あいう(6) vs tr 中文(4) -> 自动补 U+3000 到 6
        r = logic.save_line(self.src, self.tr, 1, "文文")
        self.assertEqual(r["result"], "ok")
        self.assertTrue(r["padded"])
        self.assertEqual(r["tr_width"], 6)
        self.assertEqual(logic.disp_width(logic.read_lines(self.tr)[1]), 6)

    def test_text_line_allow_overwide(self):
        # 2026-08-28: 宽度校验区分行类型——text 行(あいう)允许超宽(译文自然扩展), 不再拒绝
        r = logic.save_line(self.src, self.tr, 1, "abcdefgh")
        self.assertEqual(r["result"], "ok")
        self.assertEqual(logic.read_lines(self.tr)[1], "abcdefgh")

    def test_border_pad_before_border_char(self):
        # src あああ│(8) vs tr "文│　"(6) -> 补 2 格，插在边框字符前（行尾原有空白）
        r = logic.save_line(self.src, self.tr, 2, "文│　")
        self.assertEqual(r["result"], "ok")
        saved = logic.read_lines(self.tr)[2]
        self.assertEqual(logic.disp_width(saved), 8)
        self.assertEqual(saved.rstrip(), "文　│")   # pad 在边框前

    def test_no_backup_dir_at_all(self):
        # 2026-10-01 用户要求：保存行**不再**整文件备份到 work/backup/<ts>/
        # （改前/改后由 main._log_edit 逐行记进单文件 work/edits.jsonl）
        r = logic.save_line(self.src, self.tr, 1, "文文")
        self.assertEqual(r["result"], "ok")
        self.assertFalse(os.path.exists(self.bk_dir), "保存行不建备份目录")

    def test_unchanged_no_backup(self):
        r = logic.save_line(self.src, self.tr, 3, "abc")
        self.assertEqual(r["result"], "unchanged")
        self.assertEqual(logic.list_backups(self.bk_dir), [])
        self.assertFalse(os.path.exists(self.bk_dir))

    def test_skip_line_no_width_rule(self):
        # # 行不参与宽度校验，随便改
        r = logic.save_line(self.src, self.tr, 0, "# 新标题")
        self.assertEqual(r["result"], "ok")
        self.assertIsNone(r["src_width"])

    def test_multiline_rejected(self):
        r = logic.save_line(self.src, self.tr, 1, "a\nb")
        self.assertEqual(r["result"], "error")

    def test_out_of_range(self):
        r = logic.save_line(self.src, self.tr, 999, "x")
        self.assertEqual(r["result"], "error")


class TestUnsyncedEdits(Fixture):
    def _mk_log(self, *timestamps):
        d = os.path.join(self.tmp, "work")
        os.makedirs(d, exist_ok=True)
        log = os.path.join(d, "edits.jsonl")
        with open(log, "w", encoding="utf-8") as f:
            for ts in timestamps:
                f.write(json.dumps({"ts": ts, "type": "错译",
                                    "before": "a", "after": "b"},
                                   ensure_ascii=False) + "\n")
        return d

    def test_no_log_is_zero(self):
        d = os.path.join(self.tmp, "work")
        os.makedirs(d)
        self.assertEqual(logic.count_unsynced_edits(d, 1e9), 0)

    def test_no_html_counts_all(self):
        d = self._mk_log("2026-08-09 10:00:00", "2026-08-09 11:00:00")
        self.assertEqual(logic.count_unsynced_edits(d, None), 2)

    def test_all_synced_after_regen(self):
        d = self._mk_log("2026-08-09 10:00:00", "2026-08-09 11:00:00")
        import time as _t
        later = _t.mktime(_t.strptime("2026-08-09 12:00:00", "%Y-%m-%d %H:%M:%S"))
        self.assertEqual(logic.count_unsynced_edits(d, later), 0)

    def test_partial_unsynced(self):
        d = self._mk_log("2026-08-09 10:00:00", "2026-08-09 11:00:00",
                         "2026-08-09 12:30:00")
        import time as _t
        mtime = _t.mktime(_t.strptime("2026-08-09 12:00:00", "%Y-%m-%d %H:%M:%S"))
        self.assertEqual(logic.count_unsynced_edits(d, mtime), 1)

    def test_same_second_counts_unsynced(self):
        """同一秒内的修改按未同步处理（保守方向，防漏报竞态）。"""
        d = self._mk_log("2026-08-09 12:00:00")
        import time as _t
        mtime = _t.mktime(_t.strptime("2026-08-09 12:00:00", "%Y-%m-%d %H:%M:%S"))
        self.assertEqual(logic.count_unsynced_edits(d, mtime), 1)

    def test_bad_lines_skipped(self):
        d = os.path.join(self.tmp, "work")
        os.makedirs(d)
        with open(os.path.join(d, "edits.jsonl"), "w", encoding="utf-8") as f:
            f.write("not json\n")
            f.write(json.dumps({"ts": "2026-08-09 13:00:00"}, ensure_ascii=False) + "\n")
        import time as _t
        mtime = _t.mktime(_t.strptime("2026-08-09 12:00:00", "%Y-%m-%d %H:%M:%S"))
        self.assertEqual(logic.count_unsynced_edits(d, mtime), 1)


class TestWorkGlossary(Fixture):
    def test_load_missing_returns_empty(self):
        self.assertEqual(logic.load_work_glossary("__no_such_work__"),
                         {"glossary": [], "known_names": []})

    def test_save_roundtrip_compact_no_bom(self):
        p = os.path.join(self.tmp, "glossary_w.json")
        ok, err = logic.save_work_glossary("w", [["灰かぶりの魔女", "染灰魔女"], ["坏对"]],
                                           ["キング", "  ", ""], path=p)
        self.assertTrue(ok, err)
        raw = open(p, encoding="utf-8").read()
        self.assertFalse(raw.startswith("\ufeff"), "无 BOM")
        self.assertNotIn("\n", raw.rstrip("\n"), "紧凑单行（除结尾换行外无换行）")
        data = json.loads(raw)
        # 2026-09-01: v2 对象格式, 来源缺省 manual; 非对象/二元组防呆跳过
        # 2026-09-06: 分类重构 cat 三类, 二元组/旧 cat 落"专名"
        self.assertEqual(data["glossary"],
                         [{"jp": "灰かぶりの魔女", "zh": "染灰魔女", "cat": "专名", "来源": "manual"}])
        self.assertEqual(data["known_names"], ["キング"])                      # 空白项防呆跳过
        self.assertEqual(data["更新日期"], time.strftime("%Y-%m-%d"))
        self.assertTrue(raw.endswith("\n"))
        self.assertEqual(logic.load_work_glossary("w", path=p),
                         {"glossary": [{"jp": "灰かぶりの魔女", "zh": "染灰魔女", "cat": "专名",
                                        "来源": "manual", "role_cn": None, "role_works": None}],
                          "known_names": ["キング"]})

    def test_residual_suspect_extra_names(self):
        # 注意：ミ 属艺术假名会被 kana_runs 剥离，用全保留的串
        line = "ポコリンヌコが来た"
        self.assertTrue(any("ポコリンヌコが" in s for s in logic.residual_suspect(line)))
        self.assertEqual(logic.residual_suspect(line, ["ポコリンヌコが"]), [])

    def test_chapter_stats_work_names(self):
        aa = self.write(self.aa_dir, "c1.txt", ["ポコリンヌコ", "普通の行"])
        tr = self.write(self.tr_dir, "c1_汉化.txt", ["ポコリンヌコ", "普通的行"])
        st = logic.chapter_stats(aa, tr)
        self.assertEqual(st["residual"], 1)
        st2 = logic.chapter_stats(aa, tr, work_names=["ポコリンヌコ"])
        self.assertEqual(st2["residual"], 0)


class TestAddWorkGlossaryPair(Fixture):
    """add_work_glossary_pair：历史替换记录 → 作品术语表（单条手动加入，2026-09-14）。"""

    def setUp(self):
        super().setUp()
        self.p = os.path.join(self.tmp, "glossary_w.json")

    def test_add_new_pair_manual_specimen(self):
        r = logic.add_work_glossary_pair("w", "やる夫", "亚鲁欧", path=self.p)
        self.assertTrue(r["ok"] and r["added"])
        self.assertEqual(r["reason"], "added")
        self.assertEqual(r["total"], 1)
        d = logic.load_work_glossary("w", path=self.p)
        self.assertEqual(d["glossary"],
                         [{"jp": "やる夫", "zh": "亚鲁欧", "cat": "专名", "来源": "manual",
                           "role_cn": None, "role_works": None}])

    def test_keeps_existing_entries_and_known_names(self):
        logic.save_work_glossary("w", [{"jp": "白頭", "zh": "白头", "cat": "专名", "来源": "manual"}],
                                 ["キング"], path=self.p)
        logic.add_work_glossary_pair("w", "やる夫", "亚鲁欧", path=self.p)
        d = logic.load_work_glossary("w", path=self.p)
        self.assertEqual([e["jp"] for e in d["glossary"]], ["白頭", "やる夫"])
        self.assertEqual(d["known_names"], ["キング"])        # 白名单不能被写丢

    def test_same_jp_same_zh_is_exists_no_dup(self):
        logic.add_work_glossary_pair("w", "やる夫", "亚鲁欧", path=self.p)
        r = logic.add_work_glossary_pair("w", "やる夫", "亚鲁欧", path=self.p)
        self.assertFalse(r["added"])
        self.assertEqual(r["reason"], "exists")
        self.assertEqual(len(logic.load_work_glossary("w", path=self.p)["glossary"]), 1)

    def test_same_jp_diff_zh_is_conflict_not_overwritten(self):
        logic.add_work_glossary_pair("w", "やる夫", "亚鲁欧", path=self.p)
        r = logic.add_work_glossary_pair("w", "やる夫", "不做夫", path=self.p)
        self.assertFalse(r["added"])
        self.assertEqual(r["reason"], "conflict")
        self.assertEqual(r["current_zh"], "亚鲁欧")            # 现有译名回给前端提示
        self.assertEqual(logic.load_work_glossary("w", path=self.p)["glossary"][0]["zh"], "亚鲁欧")

    def test_empty_repl_invalid(self):
        for zh in ("", "   "):
            r = logic.add_work_glossary_pair("w", "やる夫", zh, path=self.p)
            self.assertFalse(r["added"])
            self.assertEqual(r["reason"], "invalid")
        self.assertFalse(os.path.exists(self.p), "非法输入不落盘")

    def test_cat_normalized_and_role_kept(self):
        logic.add_work_glossary_pair("w", "青蛙子", "青蛙子", cat="角色", path=self.p)
        logic.add_work_glossary_pair("w", "謎用語", "谜语", cat="乱写", path=self.p)
        cats = {e["jp"]: e["cat"] for e in logic.load_work_glossary("w", path=self.p)["glossary"]}
        self.assertEqual(cats, {"青蛙子": "角色", "謎用語": "专名"})


class TestBackupRotation(Fixture):
    def test_backup_creates_ts_dir(self):
        p = self.write(self.tr_dir, "entry_1_汉化.txt", ["あ"])
        ts = logic.backup_files([p], self.bk_dir)
        self.assertTrue(os.path.isdir(os.path.join(self.bk_dir, ts)))
        self.assertTrue(os.path.exists(os.path.join(self.bk_dir, ts, "entry_1_汉化.txt")))

    def test_rotate_keeps_10(self):
        for i in range(15):
            d = os.path.join(self.bk_dir, f"20260101{i:02d}00")
            os.makedirs(d)
            with open(os.path.join(d, "f.txt"), "w") as f:
                f.write(str(i))
        logic.rotate_backups(self.bk_dir)
        dirs = sorted(os.listdir(self.bk_dir))
        self.assertEqual(len(dirs), 10)
        self.assertEqual(dirs[-1], "202601011400")   # 保留最新 10 份

    def test_restore_backup(self):
        p = self.write(self.tr_dir, "entry_1_汉化.txt", ["旧内容"])
        ts = logic.backup_files([p], self.bk_dir)
        logic.write_lines(p, ["新内容"])
        restored = logic.restore_backup(self.bk_dir, ts, self.tr_dir)
        self.assertEqual(restored, ["entry_1_汉化.txt"])
        self.assertEqual(logic.read_lines(p), ["旧内容"])


class TestChapterStats(Fixture):
    def test_mismatch_and_line_count(self):
        src = self.write(self.aa_dir, "entry_1.txt", ["# 标题", "あいう", "abc"])
        tr = self.write(self.tr_dir, "entry_1_汉化.txt", ["# 标题", "中文"])
        st = logic.chapter_stats(src, tr)
        self.assertFalse(st["line_count_ok"])
        self.assertEqual(st["src_lines"], 3)
        self.assertEqual(st["tr_lines"], 2)
        self.assertEqual(st["mismatches"], 1)

    def test_skip_marker_lines(self):
        src = self.write(self.aa_dir, "entry_1.txt", ["===== AA块 1 =====", "あいう"])
        tr = self.write(self.tr_dir, "entry_1_汉化.txt", ["===== AA块 1 =====", "中文"])
        st = logic.chapter_stats(src, tr)
        self.assertEqual(st["mismatches"], 1)     # 标记行跳过，正文 1 处

    def test_residual_detection(self):
        src = self.write(self.aa_dir, "entry_1.txt", ["あいう", "しろかぶりのまじょ", "abc"])
        tr = self.write(self.tr_dir, "entry_1_汉化.txt",
                        ["染灰魔女", "しろかぶりのまじょ", "abc"])
        st = logic.chapter_stats(src, tr)
        # 第一行译文已汉化无残留；第二行原文照抄（残留）；第三行无假名
        self.assertEqual(st["residual"], 1)


class TestApplyGlossary(Fixture):
    PAIRS = [("白頭", "白头"), ("灰かぶりの魔女", "染灰魔女")]

    def test_dry_run_and_real(self):
        src = self.write(self.aa_dir, "entry_1.txt", ["白頭の魔女", "あいう"])
        tr = self.write(self.tr_dir, "entry_1_汉化.txt", ["白頭の魔女", "中文"])
        changed, skipped = logic.apply_glossary_chapter(src, tr, self.PAIRS, dry_run=True)
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0][2], "白头の魔女")
        self.assertEqual(skipped, [])
        # dry_run 不落盘
        self.assertEqual(logic.read_lines(tr), ["白頭の魔女", "中文"])
        # 真应用
        changed, skipped = logic.apply_glossary_chapter(src, tr, self.PAIRS,
                                                        dry_run=False)
        self.assertEqual(len(changed), 1)
        lines = logic.read_lines(tr)
        self.assertEqual(lines[0], "白头の魔女")
        self.assertEqual(lines[1], "中文")          # 未命中行不动
        # 宽度按 SRC 校正：白頭の魔女(10) -> 白头の魔女(8)，补 1 全角空格
        self.assertEqual(logic.disp_width(lines[0]), logic.disp_width(
            logic.read_lines(src)[0]))
        # 2026-10-01: 不再整文件备份（改前/改后由路由逐行记 work/edits.jsonl）
        self.assertFalse(os.path.exists(self.bk_dir))

    def test_overflow_not_skipped_but_flagged(self):
        # 2026-09-01: 超宽不跳过——替换生效, changed 元素带第4字段 overwide=True
        src = self.write(self.aa_dir, "entry_1.txt", ["あ"])
        tr = self.write(self.tr_dir, "entry_1_汉化.txt", ["白頭"])
        changed, skipped = logic.apply_glossary_chapter(src, tr, self.PAIRS, dry_run=True)
        self.assertEqual(skipped, [])
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0][2], "白头")   # 替换生效
        self.assertEqual(changed[0][3], True)      # overwide 标记

    def test_skip_hash_lines(self):
        src = self.write(self.aa_dir, "entry_1.txt", ["# 白頭", "あ"])
        tr = self.write(self.tr_dir, "entry_1_汉化.txt", ["# 白頭", "あ"])
        changed, skipped = logic.apply_glossary_chapter(src, tr, self.PAIRS, dry_run=True)
        self.assertEqual(changed, [])

    def test_skip_idx_protects_art_line(self):
        # 2026-09-09 翻译替换：skip_idx 指定的行（AA 艺术行）不参与替换也不计入
        src = self.write(self.aa_dir, "entry_1.txt",
                         ["白頭の魔女", "白頭の魔女", "白頭の魔女"])
        tr = self.write(self.tr_dir, "entry_1_汉化.txt",
                        ["白頭の魔女", "白頭の魔女", "白頭の魔女"])
        changed, skipped = logic.apply_glossary_chapter(
            src, tr, self.PAIRS, dry_run=True, skip_idx={1})
        self.assertEqual(skipped, [])
        self.assertEqual(len(changed), 2)
        self.assertEqual(changed[0][0], 0)
        self.assertEqual(changed[1][0], 2)
        # 真应用时 skip 行保持原文不动
        changed, skipped = logic.apply_glossary_chapter(
            src, tr, self.PAIRS, dry_run=False, skip_idx={1})
        lines = logic.read_lines(tr)
        self.assertEqual(len(changed), 2)
        self.assertTrue(lines[0].startswith("白头"))              # 替换生效（可能补宽）
        self.assertEqual(logic.disp_width(lines[0]),
                         logic.disp_width(logic.read_lines(src)[0]))
        self.assertEqual(lines[1], "白頭の魔女")                   # art 行原样
        self.assertTrue(lines[2].startswith("白头"))

    def test_skip_idx_empty_default_unchanged(self):
        # 默认 skip_idx=() 与旧行为一致（全行参与）
        src = self.write(self.aa_dir, "entry_1.txt", ["白頭"])
        tr = self.write(self.tr_dir, "entry_1_汉化.txt", ["白頭"])
        changed, skipped = logic.apply_glossary_chapter(src, tr, self.PAIRS, dry_run=True)
        self.assertEqual(len(changed), 1)

    def test_apply_writes_without_backup(self):
        # 2026-10-01: dry_run=False 照常落盘、不再备份任何整文件
        # （唯一留档 = 路由逐行写 work/edits.jsonl）
        src = self.write(self.aa_dir, "entry_1.txt", ["白頭の魔女", "あいう"])
        tr = self.write(self.tr_dir, "entry_1_汉化.txt", ["白頭の魔女", "中文"])
        changed, skipped = logic.apply_glossary_chapter(
            src, tr, self.PAIRS, dry_run=False)
        self.assertEqual(len(changed), 1)
        self.assertEqual(logic.read_lines(tr)[0].startswith("白头"), True)
        self.assertFalse(os.path.exists(self.bk_dir))          # 未建备份
        self.assertEqual(logic.list_backups(self.bk_dir), [])


class TestGlossaryIO(Fixture):
    def _make_glossary(self):
        g = {
            "schema": "AA翻译术语表 v1",
            "更新日期": "2026-08-08",
            "项目": "测试",
            "作品": [{"id": "saki", "名称": "咲-Saki-", "别名": [], "来源": ""}],
            "角色": [{"日文": "宮永咲", "中文": "宮永咲", "作品": ["saki"], "分类": "角色", "备注": "清澄"}],
            "glossary": [["白頭", "白头"]],
            "known_names": ["宮永咲", "やる夫"],
        }
        p = os.path.join(self.tmp, "glossary.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps(g, ensure_ascii=False, separators=(",", ":")) + "\n")
        return p

    def test_validate_errors(self):
        errs, _ = logic.validate_glossary({
            "known_names": ["a", "a"],                      # 重复
            "glossary": [["ok", "好"], ["bad"]],            # 非二元组
            "角色": [{"日文": "x", "中文": "y"}, {"日文": "x", "中文": "z"}],  # 日文重复
        })
        self.assertTrue(any("重复" in e for e in errs))
        self.assertTrue(any("二元组" in e for e in errs))

    def test_save_keeps_works_and_compact_format(self):
        p = self._make_glossary()
        bk = os.path.join(self.tmp, "gbackup")
        r = logic.save_glossary({
            "known_names": ["新名字"],
            "glossary": [["旧", "新"]],
            "角色": [{"日文": "A", "中文": "B", "作品": ["saki"]}],
        }, path=p, backup_root=bk)
        self.assertTrue(r["ok"])
        raw = open(p, encoding="utf-8").read()
        # 紧凑单行 + 结尾换行（字节风格与现有文件一致）
        self.assertEqual(raw.count("\n"), 1)
        self.assertTrue(raw.endswith("\n"))
        self.assertNotIn('"schema": "', raw.replace('"schema":"AA翻译术语表 v1"', ''))
        data = json.loads(raw)
        # 作品/项目/schema 保留，更新日期刷新
        self.assertEqual(data["作品"][0]["id"], "saki")
        self.assertEqual(data["schema"], "AA翻译术语表 v1")
        self.assertEqual(data["更新日期"], time.strftime("%Y-%m-%d"))   # 保存时刷新为当天
        self.assertEqual(data["known_names"], ["新名字"])
        # 备份存在
        self.assertEqual(len(logic.list_backups(bk)), 1)


class TestSearch(Fixture):
    def test_search_hits(self):
        self.write(self.aa_dir, "entry_1.txt", ["あいう"])
        self.write(self.aa_dir, "entry_2.txt", ["あいうえ"])
        self.write(self.tr_dir, "entry_1_汉化.txt", ["キングは言った", "染灰魔女"])
        self.write(self.tr_dir, "entry_2_汉化.txt", ["キングの登場"])
        hits = logic.search_work(self.tr_dir, self.aa_dir, "キング")
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]["file"], "entry_1_汉化.txt")
        self.assertEqual(hits[0]["line"], 1)
        hits2 = logic.search_work(self.tr_dir, self.aa_dir, "染灰")
        self.assertEqual(len(hits2), 1)
        self.assertEqual(hits2[0]["line"], 2)


class TestOverrides(Fixture):
    """人工行分类 override 读写（line_meta_overrides/<base>.json，1-based 键）。"""

    def setUp(self):
        super().setUp()
        self.work_dir = os.path.join(self.tmp, "work")

    def test_load_missing_returns_empty(self):
        self.assertEqual(logic.load_overrides(self.work_dir, "entry_1"), {})

    def test_save_load_roundtrip_1based(self):
        ok, err = logic.save_override(self.work_dir, "entry_1", 12, "text")
        self.assertTrue(ok, err)
        ok, err = logic.save_override(self.work_dir, "entry_1", 45, "art")
        self.assertTrue(ok, err)
        d = logic.load_overrides(self.work_dir, "entry_1")
        self.assertEqual(d, {"12": "text", "45": "art"})      # 键为 1-based 字符串
        raw = open(logic.overrides_path(self.work_dir, "entry_1"), encoding="utf-8").read()
        self.assertIn('"12": "text"', raw)                     # indent=1 带空格
        self.assertFalse(raw.startswith("\ufeff"), "无 BOM")
        self.assertNotIn("\\u", raw.replace('\\u3000', ''))    # ensure_ascii=False（无 \uXXXX 转义）

    def test_save_rejects_bad_kind(self):
        ok, err = logic.save_override(self.work_dir, "entry_1", 1, "bad")
        self.assertFalse(ok)
        self.assertIn("非法", err)
        self.assertEqual(logic.load_overrides(self.work_dir, "entry_1"), {})

    def test_delete_override_and_idempotent(self):
        logic.save_override(self.work_dir, "entry_1", 12, "mixed")
        ok, err = logic.delete_override(self.work_dir, "entry_1", 12)
        self.assertTrue(ok, err)
        self.assertEqual(logic.load_overrides(self.work_dir, "entry_1"), {})
        ok, err = logic.delete_override(self.work_dir, "entry_1", 999)   # 无记录也 ok
        self.assertTrue(ok, err)

    def test_overwrite_and_partial_delete(self):
        logic.save_override(self.work_dir, "entry_1", 1, "text")
        logic.save_override(self.work_dir, "entry_1", 2, "header")
        logic.delete_override(self.work_dir, "entry_1", 1)
        self.assertEqual(logic.load_overrides(self.work_dir, "entry_1"), {"2": "header"})

    def test_load_filters_dirty_kinds(self):
        p = logic.overrides_path(self.work_dir, "entry_1")
        os.makedirs(os.path.dirname(p))
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"3": "text", "4": "evil"}, f)
        self.assertEqual(logic.load_overrides(self.work_dir, "entry_1"), {"3": "text"})

    def test_load_corrupt_returns_empty(self):
        p = logic.overrides_path(self.work_dir, "entry_1")
        os.makedirs(os.path.dirname(p))
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(logic.load_overrides(self.work_dir, "entry_1"), {})


class TestMaterialResolve(Fixture):
    """2026-08-26: 素材「角色名（作品名）」解析 / 作品识别 / 同名跨作品锁定。

    依赖仓库根 glossary.json 真实角色表（凯露→公主连结、贝尔法斯特→碧蓝航线）。
    """

    def test_split_mat(self):
        self.assertEqual(logic._split_mat("爱丽丝（多娜多娜 一起来干坏事吧）"),
                         ("爱丽丝", "多娜多娜 一起来干坏事吧"))
        self.assertEqual(logic._split_mat("ベルファスト(碧蓝航线)"), ("ベルファスト", "碧蓝航线"))
        self.assertEqual(logic._split_mat("紲星あかり"), ("紲星あかり", None))
        self.assertEqual(logic._split_mat(""), ("", None))

    def test_resolve_single_candidate(self):
        cands = logic.resolve_material("凯露")
        self.assertTrue(cands, "角色表应有凯露")
        self.assertEqual(cands[0]["wname"], "公主连结Re:Dive")
        self.assertEqual(cands[0]["jp"], "キャル")

    def test_resolve_with_work_filter(self):
        cands = logic.resolve_material("凯露（公主连结Re:Dive）")
        self.assertTrue(cands)
        self.assertTrue(all(c["wname"] == "公主连结Re:Dive" for c in cands))
        # 带错误作品名 → 无候选
        self.assertEqual(logic.resolve_material("凯露（不存在的作品）"), [])

    def test_resolve_unknown_returns_empty(self):
        self.assertEqual(logic.resolve_material("__无此角色__"), [])

    def test_display_parts_with_work(self):
        dn, wn, note = logic.material_display_parts("贝尔法斯特（碧蓝航线）")
        self.assertEqual(dn, "贝尔法斯特")
        self.assertEqual(wn, "碧蓝航线")
        self.assertTrue(note)

    def test_material_names_for_with_work(self):
        cn, jp = logic._material_names_for("凯露（公主连结Re:Dive）")
        self.assertEqual((cn, jp), ("凯露", "キャル"))
        # 纯名兼容
        cn2, jp2 = logic._material_names_for("凯露")
        self.assertEqual((cn2, jp2), ("凯露", "キャル"))


class TestReadState(Fixture):
    """2026-09-03 阅读状态机纯函数。"""

    def test_tr_sets_reading(self):
        self.assertEqual(logic.read_state_with_auto("", "tr"), "reading")
        self.assertEqual(logic.read_state_with_auto("toread", "tr"), "reading")
        self.assertEqual(logic.read_state_with_auto("", "refine"), "reading")

    def test_done_not_downgraded(self):
        self.assertEqual(logic.read_state_with_auto("done", "tr"), "done")
        self.assertEqual(logic.read_state_with_auto("done", "refine"), "done")

    def test_src_does_not_trigger(self):
        self.assertEqual(logic.read_state_with_auto("", "src"), "")
        self.assertEqual(logic.read_state_with_auto("toread", "src"), "toread")


class TestDeleteSurface(Fixture):
    """2026-09-03 删除面计算（含共享根退化）。"""

    def _mk(self):
        base = os.path.join(self.tmp, "作品")
        for sub in ("aa", "汉化", "work", "raw"):
            os.makedirs(os.path.join(base, sub))
        return base

    def test_exclusive_root(self):
        base = self._mk()
        cfg = dict(aa_dir=os.path.join(base, "aa"), out_dir=os.path.join(base, "汉化"),
                   work_dir=os.path.join(base, "work"), raw_dir=os.path.join(base, "raw"))
        surf = logic.delete_surface(cfg)
        self.assertFalse(surf["shared"])
        self.assertEqual([x[1] for x in surf["items"]], [os.path.normpath(base)])
        removed = logic.delete_work(surf)
        self.assertFalse(os.path.exists(base))
        self.assertEqual(removed, [os.path.normpath(base)])

    def test_shared_root_degrade(self):
        # 模拟 saki/saki_fc2 共用作品根
        base = os.path.join(self.tmp, "さーきー")
        for sub in ("aa", "汉化", "work", "raw", "aa_fc2", "汉化_fc2", "work_fc2", "raw_fc2"):
            os.makedirs(os.path.join(base, sub))
        all_dirs = [os.path.join(base, s) for s in
                    ("aa", "汉化", "work", "raw", "aa_fc2", "汉化_fc2", "work_fc2", "raw_fc2")]
        cfg = dict(aa_dir=os.path.join(base, "aa_fc2"), out_dir=os.path.join(base, "汉化_fc2"),
                   work_dir=os.path.join(base, "work_fc2"), raw_dir=os.path.join(base, "raw_fc2"))
        surf = logic.delete_surface(cfg, all_work_dirs=all_dirs)
        self.assertTrue(surf["shared"])
        items = sorted(os.path.basename(x[1]) for x in surf["items"])
        self.assertEqual(items, ["aa_fc2", "raw_fc2", "work_fc2", "汉化_fc2"])
        logic.delete_work(surf)
        # saki 本体仍在，fc2 全删
        self.assertEqual(sorted(os.listdir(base)),
                         ["aa", "raw", "work", "汉化"])

    def test_no_all_dirs_defaults_exclusive(self):
        base = self._mk()
        cfg = dict(aa_dir=os.path.join(base, "aa"), out_dir=os.path.join(base, "汉化"),
                   work_dir=os.path.join(base, "work"), raw_dir=os.path.join(base, "raw"))
        surf = logic.delete_surface(cfg)   # 不传 all_work_dirs → 视为独占根
        self.assertFalse(surf["shared"])

    def test_remove_paths(self):
        p = os.path.join(self.tmp, "x.html")
        with open(p, "w") as f:
            f.write("x")
        got = logic.remove_paths([p])
        self.assertEqual(got, [p])
        self.assertFalse(os.path.exists(p))


class TestRating(Fixture):
    def test_avg(self):
        self.assertEqual(logic.avg_rating([5, 4, 3]), 4.0)
        self.assertEqual(logic.avg_rating([5, 4]), 4.5)
        self.assertIsNone(logic.avg_rating([]))
        self.assertIsNone(logic.avg_rating([None, ""]))
        # 非法值忽略
        self.assertEqual(logic.avg_rating([1, 9, 3]), 2.0)


class TestLastUpdate(Fixture):
    def test_max_mtime(self):
        aa = os.path.join(self.tmp, "aa_x")
        out = os.path.join(self.tmp, "out_x")
        os.makedirs(aa)
        os.makedirs(out)
        time.sleep(0.05)
        with open(os.path.join(aa, "a.txt"), "w") as f:
            f.write("a")
        time.sleep(0.05)
        p2 = os.path.join(out, "b_汉化.txt")
        with open(p2, "w") as f:
            f.write("b")
        lu = logic.last_update_of(aa, out)
        self.assertGreaterEqual(int(lu), int(os.path.getmtime(p2)) - 1)

    def test_missing_dirs_zero(self):
        self.assertEqual(logic.last_update_of("", ""), 0)
        self.assertEqual(logic.last_update_of(os.path.join(self.tmp, "none"), ""), 0)


class TestIntroTranslateRule(Fixture):
    """简介翻译回退判据（2026-09-20：旧「含任意假名即失败」会误杀保留专名的正常译文）。"""

    def setUp(self):
        super().setUp()
        from aa_web_app import add_work
        self.aw = add_work

    def test_kept_proper_name_is_ok(self):
        # 实测 commandcode mimo 常把 やる夫 保留成日文 —— 这是译文，不是失败
        out = "やる夫穿越到异世界，靠着开挂的能力大杀四方。"
        self.assertFalse(self.aw._looks_japanese(out, "やる夫は異世界に転生し無双する。"))

    def test_untranslated_identical_text_is_fail(self):
        src = "やる夫は異世界に転生し、チート能力で無双する。"
        self.assertTrue(self.aw._looks_japanese(src, src))

    def test_largely_japanese_is_fail(self):
        self.assertTrue(self.aw._looks_japanese("これは日本語のままの文です。まだ翻訳されていません。"))

    def test_empty_and_plain_chinese_ok(self):
        self.assertFalse(self.aw._looks_japanese(""))
        self.assertFalse(self.aw._looks_japanese("这是纯中文简介，没有任何日文。"))

    def test_translate_desc_no_network_fallback(self):
        # 空简介直接返回 ("", True)，不触发任何模型调用
        self.assertEqual(self.aw.translate_desc(""), ("", True))


class TestGlobalKnownName(Fixture):
    """作品白名单（2026-09-20 用户要求：候选「白名单」只写作品表，全局由反哺手动勾选）。

    路由层写作品表走 `logic.merge_work_glossary(work, known_names=[term])`（增量并集，
    表内其余条目保留）——这里只测契约本身，路由行为见 test_repatriate.RouteTest。
    """

    def _mk(self, names=None, pairs=None):
        wd = os.path.join(self.tmp, "w", "work")
        os.makedirs(wd, exist_ok=True)
        p = os.path.join(wd, "glossary_w.json")
        data = {"schema": "wg", "glossary": list(pairs or [{"jp": "あ", "zh": "啊", "cat": "专名"}]),
                "known_names": list(names or [])}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        return p

    def test_merge_adds_name_keeps_entries(self):
        p = self._mk(["既存"])
        ok, err, stat = logic.merge_work_glossary("w", known_names=["新词"], path=p)
        self.assertTrue(ok, err)
        self.assertEqual(stat["names_added"], 1)
        d = json.load(open(p, encoding="utf-8"))
        self.assertEqual(d["known_names"], ["既存", "新词"])
        self.assertEqual([(e["jp"], e["zh"]) for e in d["glossary"]], [("あ", "啊")])   # 条目未被覆盖

    def test_merge_dedup_existing(self):
        p = self._mk(["已有"])
        ok, err, stat = logic.merge_work_glossary("w", known_names=["已有"], path=p)
        self.assertTrue(ok, err)
        self.assertEqual(stat["names_added"], 0)
        self.assertEqual(json.load(open(p, encoding="utf-8"))["known_names"], ["已有"])


class TestEstimate(Fixture):
    """翻译/校对消耗预估（aa_web_app/estimate.py，2026-09-21）。

    口径：体量=所选章原文全字符数；系数 COEF；已译章（翻译）/ 已校对且指纹未变章（校对）不计。
    """

    def setUp(self):
        super().setUp()
        self.wk = os.path.join(self.tmp, "work")
        os.makedirs(self.wk)
        from aa_web_app import estimate as EST
        self.EST = EST
        self.cfg = {"aa_dir": self.aa_dir, "out_dir": self.tr_dir, "work_dir": self.wk,
                    "name_style": "entry_eid"}
        self._old = EST.cfg_of
        EST.cfg_of = lambda w: self.cfg          # 不经 registry，直接用临时目录
        EST._SRC_CACHE.clear()
        EST._SKIP_CACHE.clear()

    def tearDown(self):
        self.EST.cfg_of = self._old
        super().tearDown()

    def _mk_src(self, eid=1, chars=1000):
        self.write(self.aa_dir, "entry_%d.txt" % eid, ["あ" * chars], crlf=False)

    def test_translate_counts_chars_and_cost(self):
        self._mk_src(chars=1000)
        r = self.EST.estimate("w", "translate", ["1"])
        self.assertTrue(r["ok"])
        self.assertEqual((r["chapters"], r["skipped"]), (1, 0))
        self.assertEqual(r["chars"], 1000)
        self.assertEqual(r["prompt"], 800)       # 1000 × 0.80
        self.assertEqual(r["completion"], 190)   # 1000 × 0.19
        self.assertTrue(r["priced"])
        self.assertGreater(r["cost"], 0)
        self.assertGreaterEqual(r["cost_peak"], r["cost"])          # 高峰 ≥ 基础

    def test_translated_chapter_skipped_unless_force(self):
        self._mk_src()
        self.write(self.tr_dir, "entry_1_汉化.txt", ["啊"], crlf=False)
        r = self.EST.estimate("w", "translate", ["1"])
        self.assertEqual((r["chapters"], r["skipped"]), (0, 1))
        self.assertEqual(r["prompt"], 0)
        r2 = self.EST.estimate("w", "translate", ["1"], force=True)
        self.assertEqual((r2["chapters"], r2["skipped"]), (1, 0))

    def test_proofread_skip_by_ckpt_fingerprint(self):
        self._mk_src()
        tr = self.write(self.tr_dir, "entry_1_汉化.txt", ["啊"], crlf=False)
        # 无 ckpt → 需要校对
        self.assertEqual(self.EST.estimate("w", "proofread", ["1"])["chapters"], 1)
        # 写指纹正确的 ckpt → 零调用跳过
        ck = os.path.join(self.wk, "refine_ckpt")
        os.makedirs(ck, exist_ok=True)
        import hashlib

        def sha(p):
            return hashlib.sha1(open(p, "rb").read()).hexdigest()
        json.dump({"done": True,
                   "src_sha1": sha(os.path.join(self.aa_dir, "entry_1.txt")),
                   "tr_sha1": sha(tr)},
                  open(os.path.join(ck, "entry_1.json"), "w", encoding="utf-8"))
        self.EST._SKIP_CACHE.clear()
        r = self.EST.estimate("w", "proofread", ["1"])
        self.assertEqual((r["chapters"], r["skipped"]), (0, 1))
        # 汉化被改动 → 指纹失效 → 重新计入
        self.write(self.tr_dir, "entry_1_汉化.txt", ["啊", "改"], crlf=False)
        self.EST._SKIP_CACHE.clear()
        self.assertEqual(self.EST.estimate("w", "proofread", ["1"])["chapters"], 1)

    def test_free_mode_zero_cost(self):
        self._mk_src()
        r = self.EST.estimate("w", "translate", ["1"], free=True)
        self.assertEqual((r["cost"], r["cost_peak"], r["cost_cached"]), (0.0, 0.0, 0.0))

    def test_eids_filter(self):
        for e in (1, 2, 3):
            self._mk_src(eid=e, chars=100)
        self.assertEqual(self.EST.estimate("w", "translate", ["2"])["chapters"], 1)
        self.assertEqual(self.EST.estimate("w", "translate", [])["chapters"], 3)


class TestSfxReplaceMarks(Fixture):
    """批量替换的 sfx 标记（2026-09-21）：勾了 sfx 的替换对 → 插入片段的位置必须准。

    偏移是「替换后该行文本的字符下标」，渲染端按此包 <span class="sfx">；错位会把别的字
    染成楷体斜体，所以逐条断言 after[off:off+len] == 插入文本。
    """

    def setUp(self):
        super().setUp()
        self.src = self.write(self.aa_dir, "entry_1.txt", ["あ" * 20], crlf=False)
        self.tr = self.write(self.tr_dir, "entry_1_汉化.txt",
                             ["きゃああ 助けて わああ おわり"], crlf=False)

    def test_marks_point_at_inserted_text(self):
        out = {}
        changed, _ = logic.apply_glossary_chapter(
            self.src, self.tr, [("きゃああ", "呀啊啊", True)], dry_run=True,
            sfx_pairs=("きゃああ",), sfx_out=out)
        self.assertEqual(len(changed), 1)
        i, after = changed[0][0], changed[0][2]
        marks = out[i]
        self.assertEqual(len(marks), 1)
        off, txt = marks[0]
        self.assertEqual(txt, "呀啊啊")
        self.assertEqual(after[off:off + len(txt)], "呀啊啊")

    def test_offsets_shift_when_earlier_pair_changes(self):
        """先标 sfx 的那处在前、后一对改它前面 → 已有标记必须跟着平移。"""
        src = self.write(self.aa_dir, "entry_2.txt", ["あ" * 20], crlf=False)
        tr = self.write(self.tr_dir, "entry_2_汉化.txt", ["きゃああ わああ"], crlf=False)
        out = {}
        changed, _ = logic.apply_glossary_chapter(
            src, tr, [("きゃああ", "呀啊啊"), ("わああ", "哇啊啊")], dry_run=True,
            sfx_pairs=("わああ",), sfx_out=out)
        i, after = changed[0][0], changed[0][2]
        self.assertEqual(len(out[i]), 1)
        off, txt = out[i][0]
        self.assertEqual(txt, "哇啊啊")
        self.assertEqual(after[off:off + len(txt)], txt)

    def test_unflagged_pair_has_no_marks(self):
        out = {}
        logic.apply_glossary_chapter(self.src, self.tr, [("きゃああ", "呀啊啊")],
                                     dry_run=True, sfx_out=out)
        self.assertEqual(out, {})

    def test_regex_pair_marks(self):
        out = {}
        src = self.write(self.aa_dir, "entry_3.txt", ["あ" * 20], crlf=False)
        tr = self.write(self.tr_dir, "entry_3_汉化.txt", ["アアア アア"], crlf=False)
        changed, _ = logic.apply_glossary_chapter(
            src, tr, [("re:ア+", "啊啊啊", True)], dry_run=True,
            sfx_pairs=("re:ア+",), sfx_out=out)
        i, after = changed[0][0], changed[0][2]
        self.assertTrue(out.get(i), "正则对也要出标记")
        for off, txt in out[i]:
            self.assertEqual(after[off:off + len(txt)], txt)


class TestMergeSfxMarks(Fixture):
    """批量并入 sfx 标记（⇄ 翻译替换应用时一次写完一章）。"""

    def setUp(self):
        super().setUp()
        self.wd = os.path.join(self.tmp, "work")
        os.makedirs(self.wd)

    def test_merge_dedupe_and_read_back(self):
        ok, err, n = logic.merge_sfx_marks(self.wd, "entry_1", {1: [[0, "呀啊啊"]]})
        self.assertTrue(ok, err)
        self.assertEqual(n, 1)
        ok, err, n = logic.merge_sfx_marks(self.wd, "entry_1",
                                           {1: [[0, "呀啊啊"], [5, "哇"]], 3: [[2, "咚"]]})
        self.assertEqual(n, 2, "同位置同文本重复并入不计入 added")
        self.assertEqual(logic.load_sfx_marks(self.wd, "entry_1"),
                         {"1": [[0, "呀啊啊"], [5, "哇"]], "3": [[2, "咚"]]})

    def test_empty_text_ignored(self):
        ok, err, n = logic.merge_sfx_marks(self.wd, "entry_1", {1: [[0, ""]]})
        self.assertEqual(n, 0)
        self.assertEqual(logic.load_sfx_marks(self.wd, "entry_1"), {})


class TestReaderWin(Fixture):
    """阅读器窗口化上下文（reader_win）：几何/跳行映射/区间渲染/缓存失效。

    章节结构（0-based idx，1-based 行号 = idx+1）：
      0  # 标题          → 跳过行（不渲染，但保留 txt 行号）
      1  # 原帖地址: …   → 跳过行
      2  1 ：author …    → 帖头（首个，mt=0）
      3  （空行）
      4  　  ∧＿∧        → art（非空 → 清空行计数）
      5  （空行）
      6  2 ：author …    → 帖头（前置空行 1 → mt=16）
      7  台词行です       → 台词行（line_meta_v2 标 art → 自动类型 art）
      8  =====           → 跳过行
      9  3 ：author …    → 帖头（紧邻 → mt=32）
      10 普通行
    """

    SRC = [
        "# 标题测试",
        "# 原帖地址: https://example.com/blog-entry-1.html",
        "1 ：author ：2020-01-01 ID:abc",
        "",
        "　  ∧＿∧",
        "",
        "2 ：author ：2020-01-01 ID:def",
        "台词行です",
        "=====",
        "3 ：author ：2020-01-02 ID:ghi",
        "普通行",
    ]
    TR = [
        "# 标题测试",
        "# 原帖地址: https://example.com/blog-entry-1.html",
        "1 ：author ：2020-01-01 ID:abc",
        "",
        "　  ∧＿∧",
        "",
        "2 ：author ：2020-01-01 ID:def",
        "这是台词",
        "=====",
        "3 ：author ：2020-01-02 ID:ghi",
        "普通行",
    ]

    def setUp(self):
        super().setUp()
        self.wd = os.path.join(self.tmp, "work")
        os.makedirs(self.wd)
        self.out_dir = self.tr_dir                     # 汉化目录（与 tr_dir 同址即可）
        self.write(self.aa_dir, "entry_1.txt", self.SRC)
        self.write(self.out_dir, "entry_1_汉化.txt", self.TR)
        self.write_json(self.aa_dir, "entry_1_colormap.json", {"2": "font-weight:bold"})
        self.write_json(os.path.join(self.wd, "line_meta_v2"), "entry_1.json",
                        {"8": {"kind": "art", "confidence": 0.9, "reason": "图形"}})
        self.cfg = {"aa_dir": self.aa_dir, "out_dir": self.out_dir, "work_dir": self.wd,
                    "site": "yaruobook", "name_style": None}
        self.fname = {"src_file": "entry_1.txt", "tr_file": "entry_1_汉化.txt",
                      "src_path": os.path.join(self.aa_dir, "entry_1.txt"),
                      "tr_path": os.path.join(self.out_dir, "entry_1_汉化.txt"),
                      "has_tr": True, "title": "标题测试",
                      "src_url": "https://example.com/blog-entry-1.html"}
        reader_win.invalidate()

    def tearDown(self):
        reader_win.invalidate()
        super().tearDown()

    def write_json(self, d, name, obj):
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False)
        return p

    def ctx(self, **over):
        fname = dict(self.fname)
        fname.update(over)
        return reader_win.load_ctx(self.cfg, "test_work", fname)

    # ---- 几何与行映射 ----
    def test_vis_skips_and_geometry(self):
        c = self.ctx()
        self.assertEqual(c["skips"], [0, 1, 8], "只跳过 # 与 ===== 行，且保留原始 idx")
        self.assertEqual(c["n"], len(self.SRC) - 3)
        self.assertEqual(c["k_idx"], [2, 3, 4, 5, 6, 7, 9, 10], "k → txt idx 映射")
        self.assertEqual(c["k_mt"], [0, 0, 0, 0, 16, 0, 32, 0], "楼间间距：帖头前补足 2 空行")

    def test_line_k_roundtrip(self):
        c = self.ctx()
        self.assertEqual(reader_win.line_of_k(c, 0), 3, "k=0 → txt 第 3 行")
        self.assertEqual(reader_win.k_of_line(c, 3), 0)
        self.assertEqual(reader_win.k_of_line(c, 9), 6, "跳过行(=====) → 其后第一个可渲染行")
        self.assertEqual(reader_win.k_of_line(c, 1), 0, "首行 # 标题 → 第一个可渲染行")
        self.assertEqual(reader_win.k_of_line(c, 99), c["n"] - 1, "越界夹到末行")

    # ---- 区间渲染 ----
    def test_rows_full_and_block_cap(self):
        c = self.ctx()
        m, rows = reader_win.rows(c, "tr", 0, c["n"] - 1)
        self.assertEqual(m, "tr")
        self.assertEqual([r["k"] for r in rows], list(range(c["n"])))
        self.assertEqual([r["idx"] for r in rows], c["k_idx"])
        self.assertEqual([r["n"] for r in rows], [i + 1 for i in c["k_idx"]])
        self.assertTrue(all(r["html"] for r in rows), "空渲染落成空格，不留空串")
        self.assertEqual(rows[0]["html"], reader_win.line_html(c, 0, "tr"))
        self.assertIn("这是台词", rows[5]["html"])
        _m, capped = reader_win.rows(c, "tr", 0, 10 ** 6)
        self.assertEqual(len(capped), min(c["n"], reader_win.BLOCK_MAX))
        _m, clamped = reader_win.rows(c, "tr", -5, -1)
        self.assertEqual(clamped[0]["k"], 0, "负起点夹到 0")

    def test_block_cap_on_long_chapter(self):
        """600 行章节：单次区间请求被 BLOCK_MAX 截断，末尾块不越界。"""
        lines = ["%d ：author ：2020-01-01 ID:x%d" % (i + 1, i) for i in range(600)]
        self.write(self.aa_dir, "entry_600.txt", ["# 长章"] + lines)
        self.write(self.out_dir, "entry_600_汉化.txt", ["# 长章"] + lines)
        reader_win.invalidate()
        c = reader_win.load_ctx(self.cfg, "test_work",
                                {"src_file": "entry_600.txt", "tr_file": "entry_600_汉化.txt",
                                 "src_path": os.path.join(self.aa_dir, "entry_600.txt"),
                                 "tr_path": os.path.join(self.out_dir, "entry_600_汉化.txt"),
                                 "has_tr": True, "title": "长章", "src_url": ""})
        self.assertEqual(c["n"], 600)
        _m, b0 = reader_win.rows(c, "tr", 0, 10 ** 6)
        self.assertEqual(len(b0), reader_win.BLOCK_MAX, "单次上限 BLOCK_MAX 行")
        _m, b1 = reader_win.rows(c, "tr", 550, 10 ** 6)
        self.assertEqual([r["k"] for r in b1], list(range(550, 600)), "尾块夹到 n-1")
        _m, b2 = reader_win.rows(c, "tr", 700, 800)
        self.assertEqual(len(b2), 1, "起点越界夹到末行")

    def test_rows_meta_fields(self):
        c = self.ctx()
        _m, rows = reader_win.rows(c, "tr", 0, c["n"] - 1)
        self.assertEqual(rows[0]["type"], "header", "帖头行类型")
        self.assertEqual(rows[0]["auto"], "header")
        self.assertFalse(rows[0]["dlg"], "帖头不算台词（与旧实现同口径：只有 text/mixed 算）")
        self.assertEqual(rows[5]["type"], "aa", "line_meta_v2 kind=art → aa")
        self.assertIn("置信 0.9", rows[5]["tip"])
        self.assertIn("图形", rows[5]["tip"])
        self.assertFalse(rows[5]["dlg"], "art 行不是台词")
        self.assertEqual(rows[4]["mtk"], 1, "第 2 帖头：前置 1 空行 → 补 1 行楼距")
        self.assertEqual(rows[6]["mtk"], 2, "第 3 帖头：紧邻 → 补 2 行楼距")

    def test_override_and_gold(self):
        self.write_json(os.path.join(self.wd, "line_meta_overrides"), "entry_1.json",
                        {"11": "text"})
        self.write_json(os.path.join(self.wd, "gold_dialogue"), "entry_1.json", {"8": False})
        c = self.ctx()
        _m, rows = reader_win.rows(c, "tr", 0, c["n"] - 1)
        self.assertEqual(rows[-1]["type"], "text", "override 覆盖自动类型")
        self.assertIn("人工: text", rows[-1]["tip"])
        self.assertFalse(rows[5]["dlg"], "gold 标注 False 覆盖自动台词判定")
        self.assertEqual(c["gold"], {"8": False}, "gold 原样带出给模板")

    # ---- 模式兜底 ----
    def test_mode_fallback(self):
        c = self.ctx()
        self.assertEqual(reader_win.effective_mode(c, "tr"), "tr")
        self.assertEqual(reader_win.effective_mode(c, "refine"), "tr", "无精译 → 退 tr")
        reader_win.invalidate()          # fname 参数不进缓存键 → 改参前先清缓存
        no_tr = self.ctx(has_tr=False, tr_path=None)
        self.assertFalse(no_tr["has_tr"])
        self.assertEqual(reader_win.effective_mode(no_tr, "tr"), "src")
        self.assertEqual(reader_win.effective_mode(no_tr, "refine"), "src")
        _m, rows = reader_win.rows(no_tr, "tr", 0, 2)
        self.assertIn("∧＿∧", rows[2]["html"], "无汉化时 tr 档渲染原文（k2 = AA 图形行）")
        ref_dir = os.path.join(self.tmp, "精译")
        os.makedirs(ref_dir, exist_ok=True)
        self.write(ref_dir, "entry_1_精译.txt", self.TR)
        c2 = self.ctx()
        self.assertTrue(c2["has_refine"])
        self.assertEqual(reader_win.effective_mode(c2, "refine"), "refine")

    # ---- page_data ----
    def test_page_data(self):
        c = self.ctx()
        d = reader_win.page_data(c, "tr")
        self.assertEqual(d["n"], c["n"])
        self.assertEqual(d["skips"], [0, 1, 8])
        self.assertEqual(d["mt"], [[4, 1], [6, 2]], "只发非零楼距（k, mtk）")
        self.assertEqual(d["mode"], "tr")
        self.assertTrue(d["hasTr"])
        self.assertFalse(d["hasRefine"])
        self.assertNotIn("html", json.dumps(d), "页面数据不含任何行 HTML")

    # ---- 区间行等价（S2/S3：客户端只经 /range 取行 → 逐行锁等价） ----
    def test_rows_html_matches_line_html_per_mode(self):
        """区间行 HTML == 单行渲染（三种模式逐行比对，含精译）。"""
        c = self.ctx()
        for mode in ("tr", "src"):
            m, rows = reader_win.rows(c, mode, 0, c["n"] - 1)
            self.assertEqual(m, reader_win.effective_mode(c, mode))
            for r in rows:
                self.assertEqual(r["html"], reader_win.line_html(c, r["k"], mode),
                                 "k=%d mode=%s 区间行 HTML ≠ 单行渲染" % (r["k"], mode))
        ref_dir = os.path.join(self.tmp, "精译")
        os.makedirs(ref_dir, exist_ok=True)
        self.write(ref_dir, "entry_1_精译.txt", self.TR)
        reader_win.invalidate()
        c2 = self.ctx()
        self.assertTrue(c2["has_refine"])
        m, rows = reader_win.rows(c2, "refine", 0, c2["n"] - 1)
        self.assertEqual(m, "refine")
        for r in rows:
            self.assertEqual(r["html"], reader_win.line_html(c2, r["k"], "refine"),
                             "k=%d refine 区间行 HTML ≠ 单行渲染" % r["k"])

    def test_rows_meta_matches_ctx_tables(self):
        """区间行元数据与 ctx 几何表逐项一致（行号列/楼距/台词标记/分类都读这些字段）。"""
        c = self.ctx()
        for mode in ("tr", "src"):
            _m, rows = reader_win.rows(c, mode, 0, c["n"] - 1)
            for r in rows:
                k = r["k"]
                self.assertEqual(r["idx"], c["k_idx"][k])
                self.assertEqual(r["n"], c["k_idx"][k] + 1, "行号 = idx+1（含跳过的 # / ===== 行）")
                self.assertEqual(r["mtk"], c["k_mtk"][k])
                self.assertEqual(r["dlg"], c["k_dlg"][k], "台词标记（◆/◇）")
                self.assertEqual([r["type"], r["auto"], r["ov"]], [c["k_type"][k], c["k_auto"][k], c["k_ov"][k]])
                self.assertEqual(r["tip"], reader_win._tip(c, k))
                self.assertEqual(r["gold"], c["k_gold"][k])

    # ---- 缓存 ----
    def test_ctx_cache_and_fingerprint(self):
        c1 = self.ctx()
        self.assertIs(self.ctx(), c1, "同章节二次取用命中缓存")
        self.write(self.out_dir, "entry_1_汉化.txt", self.TR[:-1] + ["改过的行"])
        c2 = self.ctx()
        self.assertIsNot(c2, c1, "汉化文件变化 → 指纹失效重建")
        self.assertIn("改过的行", reader_win.line_html(c2, c2["n"] - 1, "tr"))
        self.write_json(os.path.join(self.wd, "sfx_marks"), "entry_1.json", {"8": [[0, "哇"]]})
        self.assertIsNot(self.ctx(), c2, "sfx 标记变化 → 指纹失效重建")
        reader_win.invalidate("test_work")
        self.assertIsNot(self.ctx(), c2, "显式 invalidate(work) 清缓存")


class TestReaderSearch(Fixture):
    """阅读器全库搜索（2026-10-07）：只搜 text/mixed 行 + 行类型索引。"""

    def setUp(self):
        super().setUp()
        self.work_dir = os.path.join(self.tmp, "work")
        os.makedirs(os.path.join(self.work_dir, "line_meta_v2"))
        self.cfg = {"aa_dir": self.aa_dir, "out_dir": self.tr_dir,
                    "work_dir": self.work_dir, "name_style": None}
        self.write(self.aa_dir, "entry_1.txt", ["あいう", "シンオウ地方", "ｶﾞｶﾞｶﾞ"])
        self.write(self.tr_dir, "entry_1_汉化.txt", ["啊伊呜", "神奥地方", "神奥"])
        self.write(self.aa_dir, "entry_2.txt", ["シンオウの風"])
        self.write(self.tr_dir, "entry_2_汉化.txt", ["神奥的风"])
        self.meta("entry_1", {"1": "text", "2": "text", "3": "art"})
        self.meta("entry_2", {"1": "mixed"})

    def meta(self, base, kinds, extra=None):
        d = {str(k): {"kind": v, "confidence": 1.0} for k, v in kinds.items()}
        d.update(extra or {})
        p = os.path.join(self.work_dir, "line_meta_v2", base + ".json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        return p

    def test_finds_text_line_with_counterpart(self):
        logic.clear_search_cache()
        r = logic.search_lines(self.cfg, "w", "神奥", scope="both")
        # entry_1 第2行 + entry_2 第1行；entry_1 第3行（art）命中被剔除
        self.assertEqual(r["total"], 2)
        self.assertEqual([(i["base"], i["idx"]) for i in r["items"]],
                         [("entry_1", 2), ("entry_2", 1)])
        it = r["items"][0]
        self.assertEqual((it["idx"], it["kind"], it["hit"]), (2, "text", "zh"))
        self.assertEqual(it["zh"], "神奥地方")
        self.assertEqual(it["jp"], "シンオウ地方")        # 对侧行也带回，供列表显示
        self.assertEqual((it["base"], it["chapter_no"]), ("entry_1", 1))
        self.assertFalse(r["truncated"])
        self.assertEqual(r["scanned"], 2)

    def test_jp_query_hits_both_chapters(self):
        logic.clear_search_cache()
        r = logic.search_lines(self.cfg, "w", "シンオウ", scope="both")
        self.assertEqual(r["total"], 2)
        got = {(i["base"], i["idx"], i["hit"]) for i in r["items"]}
        self.assertEqual(got, {("entry_1", 2, "jp"), ("entry_2", 1, "jp")})
        self.assertEqual(r["items"][1]["kind"], "mixed")
        self.assertEqual(r["items"][1]["zh"], "神奥的风")

    def test_scope_limits_sides(self):
        logic.clear_search_cache()
        self.assertEqual(logic.search_lines(self.cfg, "w", "シンオウ", scope="zh")["total"], 0)
        self.assertEqual(logic.search_lines(self.cfg, "w", "神奥", scope="jp")["total"], 0)
        r = logic.search_lines(self.cfg, "w", "神奥", scope="zh")
        self.assertEqual(r["total"], 2)
        self.assertEqual(r["items"][0]["jp"], "")        # 只搜中文时不读原文侧

    def test_art_line_never_returned(self):
        logic.clear_search_cache()
        r = logic.search_lines(self.cfg, "w", "神奥", scope="both")
        self.assertNotIn(3, [i["idx"] for i in r["items"]])

    def test_limit_truncates(self):
        logic.clear_search_cache()
        r = logic.search_lines(self.cfg, "w", "シンオウ", scope="both", limit=1)
        self.assertEqual((r["total"], len(r["items"]), r["truncated"]), (2, 1, True))

    def test_empty_query(self):
        r = logic.search_lines(self.cfg, "w", "   ")
        self.assertEqual((r["items"], r["total"], r["scanned"]), ([], 0, 0))

    def test_result_cache_and_clear(self):
        logic.clear_search_cache()
        a = logic.search_lines(self.cfg, "w", "神奥", scope="both")
        self.assertFalse(a["cached"])
        self.assertTrue(logic.search_lines(self.cfg, "w", "神奥", scope="both")["cached"])
        logic.clear_search_cache("w")
        self.assertFalse(logic.search_lines(self.cfg, "w", "神奥", scope="both")["cached"])

    def test_line_kinds_index_build_reuse_rebuild(self):
        logic.line_kinds.cache_clear() if hasattr(logic.line_kinds, "cache_clear") else None
        logic._LINE_KINDS_CACHE.clear()
        k = logic.line_kinds(self.work_dir)
        self.assertEqual(k["entry_1"], "tta")            # text/text/art
        self.assertEqual(k["entry_2"], "m")
        p = os.path.join(self.work_dir, "line_kinds.json")
        self.assertTrue(os.path.exists(p))               # 落盘
        self.assertTrue(logic.line_kinds(self.work_dir)["entry_1"] == "tta")
        time.sleep(0.01)
        self.meta("entry_1", {"1": "mixed", "2": "text", "3": "art"})
        os.utime(os.path.join(self.work_dir, "line_meta_v2", "entry_1.json"), None)
        self.assertEqual(logic.line_kinds(self.work_dir)["entry_1"], "mta")   # meta 变 → 重建
        logic._LINE_KINDS_CACHE.clear()

    def test_kind_falls_back_without_meta(self):
        logic._LINE_KINDS_CACHE.clear()
        logic.clear_search_cache()
        self.write(self.aa_dir, "entry_3.txt", ["テスト台詞"])
        self.write(self.tr_dir, "entry_3_汉化.txt", ["测试台词"])
        r = logic.search_lines(self.cfg, "w", "测试", scope="both")
        it = [i for i in r["items"] if i["base"] == "entry_3"]
        self.assertEqual(len(it), 1)
        self.assertEqual(it[0]["kind"], "text")           # 无 meta → 单行启发式兜底

if __name__ == "__main__":
    unittest.main()
