# -*- coding: utf-8 -*-
"""揭示板下载/更新 runner（scripts/runner/board_runner.py）+ 任务管理器 board 类型用例。

全部离线：只测纯函数/落盘口径，不联网（HTTP 走 Fetcher，用例不触发）。
"""
import json
import os
import shutil
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
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

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

    def test_meta_schema_normalised(self):
        """三种 index.json schema 的板名/站点根解析（口径 = logic.board_host_board）。"""
        from aa_web_app import logic
        # ① 新版（board_runner 写）
        self.assertEqual(logic.board_host_board({"host": "http://yarufox.sakura.ne.jp", "board_id": "FOX"}),
                         ("http://yarufox.sakura.ne.jp", "FOX"))
        # ② 旧版 board 字符串
        self.assertEqual(logic.board_host_board({"board": "yarufox.sakura.ne.jp/FOX"}),
                         ("http://yarufox.sakura.ne.jp", "FOX"))
        # ③ 最老的掲示板字段（带说明文字与括号）
        self.assertEqual(logic.board_host_board(
            {"掲示板": "やる夫板のシェルター (yaruoshelter.com /yaruo001/)"}),
            ("http://yaruoshelter.com", "yaruo001"))
        # 解析不出 → 空串（页面据此不显示原帖地址）
        self.assertEqual(logic.board_host_board({"作品": "x"}), ("", ""))

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


class TestBoardPagePollNoReloadLoop(unittest.TestCase):
    """作者页「更新揭示板」轮询：只有本次会话里见过任务在跑，才在 done 后刷新一次。

    2026-10-16 报障：更新揭示板后打开该作品页 → 页面反复刷新。
    根因＝打开页面时 `poll()` 看到「上一次的任务早已 done」→ `setTimeout(location.reload)`
    → 重新加载又看到 done → 无限刷新。仓库无 JS 测试运行时，这里锁住接线：
    会话守卫变量、仍在跑时置位、入队成功后置位、只在 done 时机触发一次。
    """

    def _js(self):
        p = os.path.join(ROOT, "aa_web_app", "templates", "board.html")
        with open(p, encoding="utf-8") as f:
            return f.read()

    def test_reload_guarded_by_session_flag(self):
        js = self._js()
        self.assertIn("let sawRun = false;", js)                              # 会话守卫
        self.assertIn("if (!FINAL.includes(t.status)) { sawRun = true; return; }", js)   # 在跑 → 置位
        self.assertIn("if (t.status === 'done' && sawRun)", js)                # 只在见过在跑时才刷
        self.assertIn("sawRun = false;", js)                                  # 刷一次后清位（防连环）

    def test_enqueue_sets_flag(self):
        """入队成功后置位：任务快于第一次轮询也刷一次（否则更新完看不到新贴）"""
        self.assertGreaterEqual(self._js().count("sawRun = true"), 2)


class TestUpdateRefreshesNewestFirst(unittest.TestCase):
    """更新口径（2026-10-19）：① 从**最新贴**开始落盘；② 活跃贴已增长即重抓（不必 --force）。

    报障：ポケの子 最新章 2026/10/18，但揭示板最新只到 2026/10/16 —— 点「↻ 更新揭示板」
    日志 `跳过(已有) 48 / 刷新 0`：48スレ目本地 2720 楼、原站 3542 楼，旧实现「文件在就跳过」
    → 活跃贴的新投稿永远进不来。
    """

    def test_fetch_order_newest_first(self):
        sel = [{"eid": "1746009935", "n": 1},          # 1スレ目（最旧）
               {"eid": "1790345856", "n": 47},
               {"eid": "1791726314", "n": 48},         # 48スレ目（最新，活跃）
               {"eid": "1654611861", "n": 0}]          # 前スレ（最旧，无スレ目号）
        self.assertEqual([x["eid"] for x in BR.fetch_order(sel)],
                         ["1791726314", "1790345856", "1746009935", "1654611861"])

    def test_fetch_order_does_not_mutate(self):
        sel = [{"eid": "100", "n": 1}, {"eid": "200", "n": 2}]
        BR.fetch_order(sel)
        self.assertEqual([x["eid"] for x in sel], ["100", "200"])

    def test_is_stale(self):
        self.assertTrue(BR.is_stale(2720, 3542))       # 活跃贴长了 → 重抓
        self.assertTrue(BR.is_stale(3800, 5001))
        self.assertFalse(BR.is_stale(3542, 3542))      # 一样长 → 跳过
        self.assertFalse(BR.is_stale(5001, 5001))
        self.assertFalse(BR.is_stale(0, 3542))         # 本地没有该文件 → 走「新增」路（不在这判）
        self.assertFalse(BR.is_stale(2720, 0))         # 归档贴（拿不到实时楼数）→ 不白抓
        self.assertFalse(BR.is_stale(0, 0))

    def test_active_thread_carries_live_res(self):
        """subject.txt 的楼数要带进候选（live_res）——否则判不出本地副本过期。"""
        rows = BR.parse_subject("1791726314.dat<>～ポケの子（？）そのよんじゅうはちっ～ (3542)\n")
        self.assertEqual(rows[0]["res"], 3542)


class TestBoardThreadUrl(unittest.TestCase):
    """原帖地址（thread_url）：活跃贴 → read.cgi 页面；从 kako 取的归档贴 → kako 页。

    2026-10-19：揭示板页此前**没有任何指向原站贴的入口**，本地副本过期/缺贴时无从核对。
    **坑（实测）**：read.cgi **在站点根下**（`{host}/test/read.cgi/{板}/{eid}/`，板首页自己链的就是它）；
    写成 `{host}/{板}/test/read.cgi/{板}/{eid}/` 是 **HTTP 404** —— 这条是回归线，别再加那个 `/{板}`。
    """

    HOST = "http://yarufox.sakura.ne.jp"

    def test_runner_active_uses_readcgi_at_site_root(self):
        for src in ("active", "dat", "known", ""):
            self.assertEqual(BR.thread_url(self.HOST, "FOX", "1791726314", src),
                             "http://yarufox.sakura.ne.jp/test/read.cgi/FOX/1791726314/")

    def test_runner_404_form_not_produced(self):
        """反面样本：`/FOX/test/read.cgi/...` 实测 404，任何 src 都不许产出这种串。"""
        for src in ("active", "dat", "known", "kako-dat", "kako-html"):
            self.assertNotIn("/FOX/test/read.cgi", BR.thread_url(self.HOST, "FOX", "1791726314", src))

    def test_runner_kako_fetched_uses_kako_page(self):
        want = "http://yarufox.sakura.ne.jp/FOX/kako/1746/17460/1746009935.html"
        for src in ("kako-dat", "kako-html"):
            self.assertEqual(BR.thread_url(self.HOST, "FOX", "1746009935", src), want)

    def test_runner_known_thread_archived_flag(self):
        """不在 subject.txt 活跃清单里（src=known）→ 已归档：read.cgi 只回存档跳转页，正文在 kako。"""
        self.assertEqual(BR.thread_url(self.HOST, "FOX", "1746009935", "known", archived=True),
                         "http://yarufox.sakura.ne.jp/FOX/kako/1746/17460/1746009935.html")
        # 活跃清单拿不到时（subject_ok=False）不猜，保 read.cgi 形态
        self.assertEqual(BR.thread_url(self.HOST, "FOX", "1746009935", "known", archived=False),
                         "http://yarufox.sakura.ne.jp/test/read.cgi/FOX/1746009935/")

    def test_logic_fallback_for_old_index(self):
        """旧 index.json 没写 thread_url → 用 read.cgi 兜底（跑一次更新即写入真实地址）。"""
        from aa_web_app import logic
        self.assertEqual(logic.board_thread_url("http://x", "FOX", "1791726314"),
                         "http://x/test/read.cgi/FOX/1791726314/")
        self.assertEqual(logic.board_thread_url("", "FOX", "1791726314"), "")     # 缺 host → 空
        self.assertEqual(logic.board_thread_url("http://x", "", "1791726314"), "")
        self.assertEqual(logic.board_thread_url("http://x", "FOX", "abc"), "")

    def test_logic_board_slaves_passthrough(self):
        """index.json 写了就用它（归档贴 kako 页），没写才兜底。"""
        import json as _json
        import shutil
        import tempfile
        from aa_web_app import logic
        tmp = tempfile.mkdtemp(prefix="aaboardlogic_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        d = os.path.join(tmp, "◆test_d1")
        os.makedirs(d)
        with open(os.path.join(d, "1スレ目_1746009935.dat"), "w", encoding="utf-8") as f:
            f.write("NEO坊 <>◆t<>2025/04/30(水) 19:45:35 ID:x<>本文<>\n")
        with open(os.path.join(d, "2スレ目_1791726314.dat"), "w", encoding="utf-8") as f:
            f.write("NEO坊 <>◆t<>2026/10/11(日) 22:45:14 ID:y<>本文<>\n")
        with open(os.path.join(d, "index.json"), "w", encoding="utf-8") as f:
            _json.dump({"host": "http://yarufox.sakura.ne.jp", "board_id": "FOX", "slaves": [
                {"label": "1スレ目", "eid": "1746009935", "res": 5001,
                 "thread_url": "http://yarufox.sakura.ne.jp/FOX/kako/1746/17460/1746009935.html"},
                {"label": "2スレ目", "eid": "1791726314", "res": 3542}]}, f)
        old = logic.BOARD_ROOT
        logic.BOARD_ROOT = tmp
        self.addCleanup(setattr, logic, "BOARD_ROOT", old)
        by_eid = {s["eid"]: s for s in logic.board_slaves("◆test_d1")}
        self.assertEqual(sorted(by_eid), ["1746009935", "1791726314"])
        self.assertTrue(by_eid["1746009935"]["thread_url"].endswith("1746009935.html"))
        self.assertEqual(by_eid["1791726314"]["thread_url"],
                         "http://yarufox.sakura.ne.jp/test/read.cgi/FOX/1791726314/")


class TestLastPostField(unittest.TestCase):
    """「最后投稿」字段（2026-10-19，用户指定）：一眼看出这贴还活跃到哪天。

    坑：归档 dat 尾部可能带**板级页脚行**（实测 47スレ目 末行 `満腹狐<>ttp://…/FOX/<>max<>…`），
    所以不能直接取最后一行。
    """

    def test_runner_skips_board_footer(self):
        dat = ("名無し<>sage<>2026/10/16(金) 18:16:47 ID:AAA<>\n"
               "満腹狐<>ttp://x/FOX/<>max<>页脚")
        self.assertEqual(BR._last_date(dat), "2026/10/16(金) 18:16:47 ID:AAA")

    def test_runner_empty_or_no_date(self):
        self.assertEqual(BR._last_date(""), "")
        self.assertEqual(BR._last_date("満腹狐<>x<>max<>y"), "")

    def test_ts_short(self):
        from aa_web_app import logic
        self.assertEqual(logic.board_ts_short("2026/10/19(月) 19:04:15 ID:D6oJWvG0"),
                         "2026/10/19 19:04")
        self.assertEqual(logic.board_ts_short(""), "")
        self.assertEqual(logic.board_ts_short("怪值"), "怪值")

    def test_tail_read(self):
        """旧 index.json 没写 last_ts → 只读尾部 64KB 反查（大贴不整读）。"""
        import shutil
        import tempfile
        from aa_web_app import logic
        tmp = tempfile.mkdtemp(prefix="aaboardtail_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        filler = [f"名無し<>sage<>日付なし {i} <>本文" for i in range(4000)]   # ≈ 90KB > 64KB
        p = os.path.join(tmp, "5000スレ目_123.dat")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(filler + ["NEO坊<>◆t<>2026/10/19(月) 19:04:15 ID:ZZZ<>正文",
                                        "満腹狐<>ttp://x/FOX/<>max<>页脚"]))
        self.assertGreater(os.path.getsize(p), 65536)
        self.assertEqual(logic.board_last_post_ts(p), "2026/10/19(月) 19:04:15 ID:ZZZ")
        self.assertEqual(logic.board_last_post_ts(os.path.join(tmp, "nope.dat")), "")

    def test_slaves_and_collections(self):
        """index.json 写了就用它；没写从 dat 尾部兜底；首页「最后投稿」= 各贴取最新。"""
        import json as _json
        import shutil
        import tempfile
        from aa_web_app import logic
        tmp = tempfile.mkdtemp(prefix="aaboardlast_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        d = os.path.join(tmp, "◆test_d2")
        os.makedirs(d)
        with open(os.path.join(d, "1スレ目_111.dat"), "w", encoding="utf-8") as f:
            f.write("NEO坊<>◆t<>2025/04/30(水) 19:45:35 ID:x<>本文<>\n"
                    "名無し<>sage<>2025/05/02(金) 02:03:04 ID:y<>末<>")
        with open(os.path.join(d, "2スレ目_222.dat"), "w", encoding="utf-8") as f:
            f.write("NEO坊<>◆t<>2026/10/11(日) 22:45:14 ID:z<>本文<>\n"
                    "名無し<>sage<>2026/10/19(月) 19:04:15 ID:w<>末<>")
        with open(os.path.join(d, "index.json"), "w", encoding="utf-8") as f:
            _json.dump({"host": "http://yarufox.sakura.ne.jp", "board_id": "FOX", "slaves": [
                {"label": "1スレ目", "eid": "111", "res": 2,
                 "last_ts": "2026/11/11(水) 11:11:11 ID:fromidx"},
                {"label": "2スレ目", "eid": "222", "res": 2}]}, f)
        old = logic.BOARD_ROOT
        logic.BOARD_ROOT = tmp
        self.addCleanup(setattr, logic, "BOARD_ROOT", old)
        by_eid = {s["eid"]: s for s in logic.board_slaves("◆test_d2")}
        self.assertEqual(by_eid["111"]["last_ts"], "2026/11/11(水) 11:11:11 ID:fromidx")   # index 优先
        self.assertEqual(by_eid["222"]["last_ts"], "2026/10/19(月) 19:04:15 ID:w")         # dat 兜底
        cols = [c for c in logic.board_collections() if c["name"] == "◆test_d2"]
        self.assertEqual(len(cols), 1)
        self.assertEqual(cols[0]["last_ts"], "2026/11/11(水) 11:11:11 ID:fromidx")        # 各贴取最新


class TestBoardReadingModes(unittest.TestCase):
    """揭示板阅读页三件事（2026-10-19 用户指定）：裁空行 / 去重折叠 / 仅楼主。

    实测依据：dat 里 **471/471 作者楼**正文开头都有 1~5 行排版空行（名無し 0 行）；本贴连续
    相同正文 41 组 123 楼；楼主楼 471/3542。
    """

    @staticmethod
    def _res(no, name, *rows):
        return {"no": no, "name": name, "date": "", "id": "",
                "body": [r if r is not None else "" for r in rows]}

    # ---------- ① 裁空行 ----------
    def test_parse_trims_leading_and_trailing_blank_rows(self):
        import shutil
        import tempfile
        from aa_web_app import logic
        tmp = tempfile.mkdtemp(prefix="aaboardtrim_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        p = os.path.join(tmp, "1スレ目_111.dat")
        # 一楼：开头 2 空行 + 正文 2 行 + 中间 1 空行 + 结尾 1 空行；二楼：只有正文
        with open(p, "w", encoding="utf-8") as f:
            f.write("NEO坊 ◆T.<><>2026/10/16(金) 18:16:47 ID:AAA<>"
                    "<br>   <br> <br>AAA画<br>BBB画<br>   <br>CCC<br>  <>\n"
                    "名無し<><>2026/10/16(金) 18:20:00 ID:BBB<>立て乙<>\n")
        res = logic.parse_board_dat(p)["res"]
        self.assertEqual(res[0]["body"], ["AAA画", "BBB画", "   ", "CCC"])   # 首尾裁掉、中间保留
        self.assertEqual(res[1]["body"], ["立て乙"])

    def test_parse_keeps_all_blank_body(self):
        """整楼只有空行 → body 变空列表（不再渲染成一堆 &nbsp; 行）。"""
        import shutil
        import tempfile
        from aa_web_app import logic
        tmp = tempfile.mkdtemp(prefix="aaboardtrim2_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        p = os.path.join(tmp, "1スレ目_112.dat")
        with open(p, "w", encoding="utf-8") as f:
            f.write("名無し<><>2026/10/16(金) 18:16:47 ID:AAA<>   <br>   <>\n")
        self.assertEqual(logic.parse_board_dat(p)["res"][0]["body"], [])

    # ---------- ② 去重 ----------
    def test_body_key_ignores_whitespace_and_blank_rows(self):
        from aa_web_app import logic
        a = self._res(1, "名無し", "  図鑑  ", "", "  ")
        b = self._res(2, "名無し", "図鑑")
        self.assertEqual(logic.board_body_key(a), logic.board_body_key(b))

    def test_mark_dedup_groups_consecutive_and_skips_author(self):
        from aa_web_app import logic
        res = [self._res(1, "名無し", "立て乙"),
               self._res(2, "名無し", "立て乙"),
               self._res(3, "名無し", " 立て乙 "),          # 归一化后相同 → 同组
               self._res(4, "NEO坊 ◆T.", "同じ"),            # 作者楼不参与
               self._res(5, "NEO坊 ◆T.", "同じ"),
               self._res(6, "名無し", "別"),                 # 单楼不成组
               ]
        out = logic.board_mark_dedup(res, "◆T.")
        self.assertEqual(out[0]["_dedupe"], {"count": 3, "nos": [1, 2, 3]})
        self.assertEqual(out[1]["_dedupe"], {"of": 1})
        self.assertEqual(out[2]["_dedupe"], {"of": 1})
        self.assertNotIn("_dedupe", out[3])                   # 作者楼
        self.assertNotIn("_dedupe", out[4])
        self.assertNotIn("_dedupe", out[5])                   # 单楼
        self.assertNotIn("_dedupe", res[0])                   # 不改入参（浅拷贝）

    def test_mark_dedup_real_thread_counts(self):
        """真实数据回归：本贴应得 41 组 / 123 楼（Q2 实测值）。"""
        from aa_web_app import logic
        p = os.path.join(logic.BOARD_ROOT, "◆LXwkncCAp_ポケの子", "48スレ目_1791726314.dat")
        if not os.path.isfile(p):
            self.skipTest("缺 ポケの子 揭示板数据")
        rows = logic.board_mark_dedup(logic.parse_board_dat(p)["res"], "◆LXwkncCAp.")
        heads = [r for r in rows if (r.get("_dedupe") or {}).get("count")]
        self.assertEqual(len(heads), 41)
        self.assertEqual(sum(r["_dedupe"]["count"] for r in heads), 123)

    # ---------- ③ 仅楼主 ----------
    def test_author_trip_and_is_op(self):
        from aa_web_app import logic
        # 有 trip：只认该 trip（末尾「.」容忍）
        self.assertTrue(logic.board_is_op("NEO坊 ◆LXwkncCAp.", "◆LXwkncCAp."))
        self.assertTrue(logic.board_is_op("NEO坊 ◆LXwkncCAp.", "◆LXwkncCAp"))
        self.assertFalse(logic.board_is_op("別の人 ◆OTHER.", "◆LXwkncCAp."))
        # 无 trip：退化「含 ◆」
        self.assertTrue(logic.board_is_op("別の人 ◆OTHER.", ""))
        self.assertFalse(logic.board_is_op("名無しさん＠狐板", ""))

    def test_board_page_view_op_mode(self):
        from aa_web_app import logic
        res = [self._res(1, "NEO坊 ◆T.", "A"),
               self._res(2, "名無し", "x"),
               self._res(3, "NEO坊 ◆T.", "B"),
               self._res(4, "名無し", "x"),
               self._res(5, "NEO坊 ◆T.", "C")]
        v = logic.board_page_view(res, "◆T.", op=True, per_page=2, page=1)
        self.assertTrue(v["op"])
        self.assertEqual([r["no"] for r in v["rows"]], [1, 3])        # 只留楼主楼，楼号保持原值
        self.assertEqual(v["total_pages"], 2)                        # 3 个楼主楼 / 每页 2
        self.assertEqual((v["total_res"], v["op_total"]), (5, 3))
        v2 = logic.board_page_view(res, "◆T.", op=True, per_page=2, page=2)
        self.assertEqual([r["no"] for r in v2["rows"]], [5])
        v3 = logic.board_page_view(res, "◆T.", op=False, per_page=2, page=1)
        self.assertEqual([r["no"] for r in v3["rows"]], [1, 2])
        self.assertEqual(v3["total_pages"], 3)
        # 判不出楼主楼 → 忽略模式，不给白页
        v4 = logic.board_page_view([self._res(1, "名無し", "x")], "", op=True, per_page=2)
        self.assertFalse(v4["op"])
        self.assertEqual([r["no"] for r in v4["rows"]], [1])

    # ---------- 模板接线 ----------
    def test_template_wiring(self):
        tpl = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "..", "aa_web_app", "templates", "board.html"), encoding="utf-8").read()
        for needle in ("dedup-on", 'id="dedup-toggle"', 'id="op-toggle"', "data-grp=",
                       "bres-grp", "aa-board-dedup:", "aa-board-op:", "&op=0#r",
                       "ほか {{ r._dedupe.count - 1 }} 楼相同"):
            self.assertIn(needle, tpl)


if __name__ == "__main__":
    unittest.main()
