# -*- coding: utf-8 -*-
"""首页快照缓存（2026-10-13 方案 A）：`aa_web_app/index_cache.py` + main 的构建/渲染接线。

不装 httpx、不起 uvicorn —— 直接调 `main.index(request)` 等函数，全部落盘隔离在临时目录。
覆盖：
  - IndexSnapshot：存取/版本/损坏/脏标记/限流单飞/年龄文案
  - main._build_index_data(only=...)：只重算脏作品，其余条目原样复用
  - main.index()：首次同步建、少量脏同步局部重算、大批脏转后台、过期转后台
  - main._render_index()：阅读进度/阅读状态实时覆盖、已删除作品过滤
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from aa_web_app import index_cache as IC        # noqa: E402
from aa_web_app.routers import site_url as M    # noqa: E402  首页/快照域（拆分后归属）
from starlette.requests import Request          # noqa: E402


def _req(path="/"):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [],
                    "query_string": b"", "scheme": "http", "server": ("127.0.0.1", 8765)})


def _fake_work(wid, **kw):
    """一份字段齐全的首页作品条目（字段集合照真实快照）。"""
    w = {"id": wid, "title": "T-" + wid, "cn_name": "", "jp_title": "T-" + wid,
         "author": "◆A", "r18": False, "r18_level": "", "r18_summary": {},
         "site": "yaruobook", "stats": {"work": wid, "error": None, "agg": {"chapters": 2},
                                        "html_out": "", "html_exists": False, "html_mtime": None},
         "fnames": ["entry_1.txt", "entry_2.txt"], "read": 0, "total": 2, "src_total": 2,
         "deficit": 0, "translated": True, "tstate": "done", "status": "连载中",
         "r401_cid": "", "site_url": "", "tags": ["tag1"], "tags_key": "tag1", "mats_key": "",
         "materials": [], "mats_display": [], "mats_more": 0, "unsynced": 0, "rating": None,
         "review": "", "last_update": 1000.0, "read_state": ""}
    w.update(kw)
    return w


class TestSnapshotStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aa_idx_")
        self.path = os.path.join(self.tmp, "sub", "index_cache.json")
        self.store = IC.IndexSnapshot(self.path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_存取往返_含自动建目录(self):
        snap = self.store.set(IC.wrap({"works": [_fake_work("a")]}))
        self.assertEqual(snap["v"], IC.SNAPSHOT_VERSION)
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual([w["id"] for w in self.store.get()["works"]], ["a"])
        # 新实例（= 进程重启）从磁盘读回
        other = IC.IndexSnapshot(self.path)
        self.assertEqual([w["id"] for w in other.get()["works"]], ["a"])

    def test_版本不符当无快照(self):
        self.store.set(IC.wrap({"works": [_fake_work("a")]}))
        with open(self.path, encoding="utf-8") as f:
            d = json.load(f)
        d["v"] = IC.SNAPSHOT_VERSION + 1
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(d, f)
        self.assertIsNone(IC.IndexSnapshot(self.path).get())

    def test_损坏或缺失json当无快照(self):
        self.assertIsNone(IC.IndexSnapshot(self.path).get())
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIsNone(IC.IndexSnapshot(self.path).get())

    def test_works不是list也当无快照(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"v": IC.SNAPSHOT_VERSION, "works": {"a": 1}}, f)
        self.assertIsNone(IC.IndexSnapshot(self.path).get())

    def test_原子写不留tmp(self):
        self.store.set(IC.wrap({"works": []}))
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_脏标记_局部与全量(self):
        self.store.set(IC.wrap({"works": [_fake_work("a")]}))
        self.assertFalse(self.store.dirty())
        self.store.invalidate("a")
        self.assertEqual(self.store.dirty(), {"a"})
        self.assertFalse(self.store.needs_full_rebuild())
        self.store.invalidate(None)
        self.assertTrue(self.store.needs_full_rebuild())
        self.store.clear_dirty({"a"})
        self.assertEqual(self.store.dirty(), {"*"})
        self.store.clear_dirty()
        self.assertFalse(self.store.dirty())

    def test_脏标记随快照落盘_重启后仍在(self):
        self.store.set(IC.wrap({"works": [_fake_work("a")]}))
        self.store.invalidate("a")
        self.store.set(IC.wrap({"works": [_fake_work("a")]}))
        self.assertEqual(IC.IndexSnapshot(self.path).dirty(), {"a"})

    def test_年龄与文案(self):
        self.assertEqual(IC.age_text(None), "未知")
        self.assertEqual(IC.age_text(5), "刚刚")
        self.assertEqual(IC.age_text(120), "2 分钟前")
        self.assertEqual(IC.age_text(3 * 3600), "3 小时前")
        self.assertEqual(IC.age_text(2 * 86400), "2 天前")
        self.assertIsNone(IC.IndexSnapshot(self.path).age())
        self.store.set(IC.wrap({"works": []}))
        self.assertLess(self.store.age(), 5)

    def test_should_refresh_三种触发(self):
        self.assertTrue(self.store.should_refresh())          # 无快照
        self.store.set(IC.wrap({"works": []}))
        self.assertFalse(self.store.should_refresh())          # 新鲜
        self.assertTrue(self.store.should_refresh(now=time.time() + self.store.max_age + 1))
        self.store.invalidate("a")                             # 有脏
        self.assertTrue(self.store.should_refresh())

    def test_begin_refresh_单飞与限流(self):
        t = 1_000_000.0
        self.assertTrue(self.store.begin_refresh(now=t))
        self.assertFalse(self.store.begin_refresh(now=t + 999))    # 单飞
        self.store.end_refresh()
        self.assertFalse(self.store.begin_refresh(now=t + 1))     # 限流（距上次太近）
        self.assertTrue(self.store.begin_refresh(now=t + self.store.min_interval + 1))

    def test_force忽略限流但仍有单飞(self):
        t = 1_000_000.0
        self.assertTrue(self.store.begin_refresh(force=True, now=t))
        self.assertFalse(self.store.begin_refresh(force=True, now=t + 0.1))   # 单飞仍生效
        self.store.end_refresh()
        self.assertTrue(self.store.begin_refresh(force=True, now=t + 0.2))    # 忽略限流

    def test_end_refresh记录错误(self):
        self.store.begin_refresh(now=1.0)
        self.store.end_refresh("boom")
        self.assertEqual(self.store.last_error, "boom")
        self.assertFalse(self.store.refreshing)


class _MainCase(unittest.TestCase):
    """main 接线用例：假 registry + 假 work_stats，快照落临时目录。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aa_idx_main_")
        self.store = IC.IndexSnapshot(os.path.join(self.tmp, "index_cache.json"))
        self.calls = []
        self.works = {"a": {"aa_dir": "", "out_dir": "", "work_dir": "", "title": "A"},
                      "b": {"aa_dir": "", "out_dir": "", "work_dir": "", "title": "B"}}

        def fake_stats(wid):
            self.calls.append(wid)
            return {"work": wid, "error": None, "agg": {"chapters": 2},
                    "chapters": [{"file": "entry_1.txt"}, {"file": "entry_2.txt"}],
                    "html_out": "", "html_exists": False, "html_mtime": None}

        self._patches = [
            mock.patch.object(M, "_INDEX_SNAP", self.store),
            mock.patch.object(M.registry, "WORKS", self.works),
            mock.patch.object(M, "work_stats", fake_stats),
            mock.patch.object(M, "_load_progress", lambda: {}),
            mock.patch.object(M, "_load_read_state", lambda: {}),
            mock.patch.object(M.logic, "load_work_meta", lambda *a, **kw: {}),
            mock.patch.object(M, "r18_summary_cached", lambda *a: {}),
            mock.patch.object(M, "_site_url_for", lambda wid, cfg: ""),
            mock.patch.object(M, "_last_update_refresh", lambda wid, cfg: 0.0),
            mock.patch.object(M.logic, "author_display", lambda *a, **kw: ("◆A", False)),
            mock.patch.object(M.logic, "material_frequency_cached", lambda *a: {}),
            mock.patch.object(M.logic, "count_unsynced_edits", lambda *a: 0),
            mock.patch.object(M, "_site_url_backfill_async", lambda: False),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_only只重算指定作品_其余原样复用(self):
        base = {"works": [_fake_work("a"), _fake_work("b")]}
        out = M._build_index_data(only={"a"}, base=base)
        self.assertEqual(self.calls, ["a"])
        by = {w["id"]: w for w in out}
        self.assertIs(by["b"], base["works"][1])          # 复用同一对象
        self.assertIsNot(by["a"], base["works"][0])

    def test_全量时忽略base(self):
        base = {"works": [_fake_work("a")]}
        out = M._build_index_data(only=None, base=base)
        self.assertEqual(sorted(self.calls), ["a", "b"])
        self.assertEqual(len(out), 2)

    def test_base里的已删作品不进列表(self):
        base = {"works": [_fake_work("zzz")]}
        out = M._build_index_data(only=None, base=base)
        self.assertEqual(sorted(w["id"] for w in out), ["a", "b"])

    def test_首次请求同步建快照并落盘(self):
        self.assertEqual(self.calls, [])
        resp = M.index(_req())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(sorted(self.calls), ["a", "b"])
        self.assertTrue(os.path.exists(self.store.path))
        self.assertEqual(sorted(w["id"] for w in self.store.get()["works"]), ["a", "b"])

    def test_命中快照时不再扫描(self):
        M.index(_req())
        self.calls.clear()
        M.index(_req())
        self.assertEqual(self.calls, [])

    def test_少量脏_同步局部重算(self):
        M.index(_req())
        self.calls.clear()
        self.store.invalidate("b")
        M.index(_req())
        self.assertEqual(self.calls, ["b"])
        self.assertFalse(self.store.dirty())

    def test_大批脏_转后台且保留脏标记(self):
        M.index(_req())
        self.calls.clear()
        self.store.invalidate(None)
        with mock.patch.object(M, "_index_refresh_async") as m:
            M.index(_req())
        self.assertTrue(m.called)                     # 交给后台
        self.assertEqual(self.calls, [])              # 本次不扫
        self.assertTrue(self.store.needs_full_rebuild())

    def test_过期_转后台不改动数据(self):
        M.index(_req())
        self.calls.clear()
        self.store._snap["built_at"] = time.time() - self.store.max_age - 10
        with mock.patch.object(M, "_index_refresh_async") as m:
            M.index(_req())
        self.assertTrue(m.called)
        self.assertEqual(self.calls, [])

    def test_render实时覆盖进度与阅读状态(self):
        snap = {"works": [_fake_work("a"), _fake_work("b")], "built_at": time.time()}
        with mock.patch.object(M, "_load_progress",
                               lambda: {"aa": {"a": {"fname": "entry_2.txt"}}}), \
                mock.patch.object(M, "_load_read_state", lambda: {"b": {"state": "done"}}):
            resp = M._render_index(_req(), snap)
        body = resp.body.decode("utf-8")
        # 进度：entry_2.txt 是第 2 章 → 2/2
        i = body.index('data-work="a"')
        self.assertIn("<div>2/2", body[i:i + 6000])
        j = body.index('data-work="b"')
        self.assertIn('data-rs="done"', body[j:j + 6000])

    def test_render过滤已删除作品(self):
        snap = {"works": [_fake_work("a"), _fake_work("zzz")], "built_at": time.time()}
        resp = M._render_index(_req(), snap)
        body = resp.body.decode("utf-8")
        self.assertIn('data-work="a"', body)
        self.assertNotIn('data-work="zzz"', body)

    def test_状态接口字段(self):
        M.index(_req())
        j = M.api_index_status()
        self.assertTrue(j["ok"])
        self.assertGreater(j["built_at"], 0)
        self.assertIn("age_text", j)
        self.assertFalse(j["refreshing"])

    def test_刷新接口走后台重建(self):
        M.index(_req())
        self.calls.clear()
        with mock.patch.object(M, "_rebuild_index_snapshot") as rb:
            j = M.api_index_refresh()
        self.assertTrue(j["ok"])
        self.assertTrue(j["started"])
        self.assertTrue(rb.called)


if __name__ == "__main__":
    unittest.main()
