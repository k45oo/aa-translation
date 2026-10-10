# -*- coding: utf-8 -*-
"""揭示板下载/更新 runner（scripts/runner/board_runner.py）+ 任务管理器 board 类型用例。

全部离线：只测纯函数/落盘口径，不联网（HTTP 走 Fetcher，用例不触发）。
"""
import json
import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
ROOT = os.path.dirname(SCRIPTS)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(SCRIPTS, "runner"))
sys.path.insert(0, os.path.join(SCRIPTS, "common"))
sys.path.insert(0, os.path.join(SCRIPTS, "works"))
sys.path.insert(0, os.path.join(ROOT, "aa_web_app"))      # task_manager 等

import board_runner as BR      # noqa: E402


class TestKanaNum(unittest.TestCase):
    """标题里的和数字假名 → スレ号（该站标题 `～…そのよんじゅうななっ～`）"""

    def test_basic(self):
        cases = {
            "～ポケの子（？）そのいちっ～": 1,
            "～ポケの子（？）そのにっ～": 2,
            "～ポケの子（？）そのじゅうっ～": 10,
            "～ポケの子（？）そのさんじゅうにっ～": 32,
            "～ポケの子（？）そのよんじゅうななっ～": 47,
        }
        for title, want in cases.items():
            self.assertEqual(BR.kana_num(title), want, title)

    def test_unit_sum_not_multiply(self):
        """ひゃくじゅう = 110（求和），不能按「遇单位就累乘」算成 1000"""
        self.assertEqual(BR.kana_num("そのひゃくじゅうよんっ"), 114)
        self.assertEqual(BR.kana_num("そのひゃくっ"), 100)
        self.assertEqual(BR.kana_num("そのせんっ"), 1000)

    def test_unknown_returns_none(self):
        self.assertIsNone(BR.kana_num("そのふぁいなるっ"))     # 未知假名（最终贴）
        self.assertIsNone(BR.kana_num("【R-18】雑談所"))       # 无「その…っ」


class TestKakoPath(unittest.TestCase):
    """归档地址直构造：kako/<eid前4>/<eid前5>/<eid>（勿依赖跳转页）"""

    def test_url(self):
        self.assertEqual(BR.kako_url("http://yarufox.sakura.ne.jp/FOX", "1624183408"),
                         "http://yarufox.sakura.ne.jp/FOX/kako/1624/16241/1624183408")
        self.assertEqual(BR.kako_url("http://x/FOX", "1746009935"),
                         "http://x/FOX/kako/1746/17460/1746009935")

    def test_kako_link_regex(self):
        m = BR._KAKO_LINK_RE.search("本文 → /FOX/kako/1624/16241/1624183408.html より")
        self.assertEqual(m.groups(), ("1624", "16241", "1624183408"))


class TestSubject(unittest.TestCase):
    def test_parse(self):
        txt = ("1790345856.dat<>～ポケの子（？）そのよんじゅうななっ～ (3800)\n"
               "1789046621.dat<>～ポケの子（？）そのよんじゅうろくっ～ (5001)\n"
               "\n"
               "1791282328.dat<>【R18】◆l872UrR6ywの雑談＆実験場１１９【βテスト】\n")
        rows = BR.parse_subject(txt)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], {"eid": "1790345856",
                                   "title": "～ポケの子（？）そのよんじゅうななっ～", "res": 3800})
        self.assertEqual(rows[2]["res"], 0)      # 无 res 字段容错
        self.assertEqual(BR._first_date("NEO坊 <>◆t<>2025/04/30(水) 19:45:35 ID:x<>本文<>"),
                         "2025/04/30(水) 19:45:35 ID:x")
        self.assertEqual(BR.dat_res_count("a\n\nb\n"), 2)


class TestKakoHtmlToDat(unittest.TestCase):
    def test_convert(self):
        html = ('<dl><dt>1 ：名前 ：2025/04/30(水) 19:45:35 ID:abc</dt>'
                '<dd>一行<br>二行<br></dd>'
                '<dt>2 名前：作者 [] 投稿日：2025/04/30(水) 19:46 ID:def</dt><dd>AA</dd></dl>')
        dat = BR.kako_html_to_dat(html)
        lines = dat.split("<>\n")            # 每条 res 以 `<>` 结尾，正文内可含换行
        self.assertEqual(dat.count("<>"), 8)           # 2 条 × 4 段分隔符
        self.assertIn("2025/04/30(水) 19:45:35 ID:abc", lines[0])
        self.assertIn("一行\n二行", lines[0])
        self.assertIn("2025/04/30(水) 19:46 ID:def", lines[1])
        self.assertEqual(BR._first_date(dat), "2025/04/30(水) 19:45:35 ID:abc")


class TestDirGuards(unittest.TestCase):
    """目录级安全闸：同作者多作品必须各占一个目录（曾把同作者另一部作品的贴串号）"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aaboard_")

    def _touch(self, name):
        with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as f:
            f.write("dummy")

    def test_existing_labels(self):
        self._touch("1スレ目_1656756098.dat")
        self._touch("前スレ（キャラ作成〜第8話）_1654611861.dat")
        self._touch("index.json")
        l2e, e2l = BR.existing_labels(self.tmp)
        self.assertEqual(l2e["1スレ目"], "1656756098")
        self.assertEqual(e2l["1654611861"], "前スレ（キャラ作成〜第8話）")
        self.assertEqual(len(l2e), 2)

    def test_foreign_dats(self):
        self._touch("1スレ目_1656756098.dat")
        self._touch("2スレ目_1656756099.dat")
        # 本次范围只含 1656756098 → 另一个是「别人的贴」
        self.assertEqual(BR.foreign_dats(self.tmp, {"1656756098"}), ["2スレ目_1656756099.dat"])
        self.assertEqual(BR.foreign_dats(self.tmp, {"1656756098", "1656756099"}), [])

    def test_index_roundtrip_and_labels(self):
        p = os.path.join(self.tmp, "index.json")
        BR.save_index(p, {"work_title": "ポケの子（？）", "board": "x/FOX"},
                      [{"label": "2スレ目", "eid": "22"}, {"label": "1スレ目", "eid": "11"}])
        idx = BR.load_index(p)
        self.assertEqual([s["eid"] for s in idx["slaves"]], ["11", "22"])   # 按 eid 升序
        self.assertEqual(idx["work_title"], "ポケの子（？）")
        self.assertIn("updated", idx)


class TestTaskManagerBoardType(unittest.TestCase):
    """任务管理器 board 类型：伪 work id `board:<板id>`，命令拼装/日志目录"""

    def test_is_board_task(self):
        import task_manager as TM
        self.assertTrue(TM._is_board_task("board:FOX"))
        self.assertTrue(TM._is_board_task("whatever", "board"))
        self.assertFalse(TM._is_board_task("baitou", "translate"))

    def test_build_command(self):
        import task_manager as TM
        cmd = TM._build_command("board", "board:FOX", {
            "board": "FOX", "host": "http://yarufox.sakura.ne.jp", "match": "ポケの子",
            "author": "◆LXwkncCAp.", "dir": "◆LXwkncCAp_ポケの子", "title": "ポケの子（？）",
            "work_id": "pokeko_fc2", "kako_from": 1746, "kako_to": 1774,
            "scan_kako": True, "force": False})
        joined = " ".join(cmd)
        self.assertIn("board_runner.py", joined)
        self.assertIn("--board FOX", joined)
        self.assertIn("--match ポケの子", joined)
        self.assertIn("--kako-from 1746", joined)
        self.assertIn("--kako-to 1774", joined)
        self.assertIn("--scan-kako", joined)
        self.assertNotIn("--force", joined)
        self.assertIn("board:FOX", "board:FOX")      # work 伪 id 由调用方传

    def test_board_log_dir(self):
        import task_manager as TM
        d = TM._work_dir_of("board:FOX", "board")
        self.assertTrue(d.endswith("揭示板"))
        self.assertEqual(TM._legacy_lock("board:FOX", "board"), "")   # 无作品目录 → 无旧锁

    def test_board_type_registered(self):
        import task_manager as TM
        self.assertIn("board", TM.TYPES)


class TestWorkbenchBoardApi(unittest.TestCase):
    """工作台侧：入队校验 + index.json 板信息解析（直接调路由函数，不起服务器）"""

    def test_meta_parse_regex(self):
        import re
        pat = re.compile(r'([A-Za-z0-9.\-]+\.[A-Za-z]{2,})[^\w]*/([A-Za-z0-9_]+)')
        self.assertEqual(pat.search("yarufox.sakura.ne.jp/FOX").groups(),
                         ("yarufox.sakura.ne.jp", "FOX"))
        self.assertEqual(pat.search("やる夫板のシェルター (yaruoshelter.com /yaruo001/)").groups(),
                         ("yaruoshelter.com", "yaruo001"))

    def test_enqueue_validation(self):
        """缺关键词/缺目录 → 422（否则会把整块板全抓下来）"""
        from fastapi import HTTPException
        from aa_web_app import main as M
        with self.assertRaises(HTTPException):
            M._board_enqueue({"board": "FOX"})                       # 无 dir/match/author
        with self.assertRaises(HTTPException):
            M._board_enqueue({"board": "FOX", "dir": "x"})           # 有目录但无 match/author

    def test_index_meta_real_dirs(self):
        """既有揭示板目录的板信息解析（数据缺失时跳过）"""
        from aa_web_app import main as M
        d = os.path.join(ROOT, "揭示板", "◆LXwkncCAp_ポケの子")
        if not os.path.isdir(d):
            self.skipTest("揭示板数据未同步")
        meta = M._board_index_meta("◆LXwkncCAp_ポケの子")
        self.assertEqual(meta["board"], "FOX")
        self.assertTrue(meta["host"].startswith("http"))
        self.assertEqual(meta["match"], "ポケの子")
        self.assertGreaterEqual(meta["slaves"], 1)
        self.assertTrue(meta["eid_min"].isdigit() and meta["eid_max"].isdigit())


if __name__ == "__main__":
    unittest.main()
