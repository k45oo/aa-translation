# -*- coding: utf-8 -*-
"""章收藏（2026-10-17）：存盘 helper + 收藏/取消收藏路由 + 阅读页接线。

不碰真实数据：临时目录造 aa/entry_N.txt 当作品，patch web_state.cfg_of 与 web_state._FAV_FILE。
运行：python -m unittest scripts.tests.test_favorites
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from fastapi import HTTPException

from aa_web_app import logic, web_state
from aa_web_app.routers import chapters

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
_TPL = os.path.join(_ROOT, "aa_web_app", "templates")
_RT = os.path.join(_ROOT, "aa_web_app", "routers")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestFavStore(unittest.TestCase):
    """web_state 的收藏读写：坏文件当空、原子写、脏项过滤。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aa_fav_")
        self.favf = os.path.join(self.tmp, "favorites.json")
        p = mock.patch.object(web_state, "_FAV_FILE", self.favf)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_缺文件与坏文件都当空(self):
        self.assertEqual(web_state._load_favs(), {})
        self.assertEqual(web_state._fav_bases("w"), [])
        with open(self.favf, "w", encoding="utf-8") as f:
            f.write("{ 不是 json")
        self.assertEqual(web_state._load_favs(), {})
        with open(self.favf, "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")          # 顶层不是 dict
        self.assertEqual(web_state._load_favs(), {})
        self.assertEqual(web_state._fav_bases("w"), [])

    def test_存盘读回且不留_tmp(self):
        web_state._save_favs({"baitou": ["entry_1", "entry_2"]})
        self.assertEqual(web_state._fav_bases("baitou"), ["entry_1", "entry_2"])
        self.assertFalse(os.path.exists(self.favf + ".tmp"))
        with open(self.favf, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"baitou": ["entry_1", "entry_2"]})

    def test_脏项被过滤(self):
        web_state._save_favs({"w": ["a", 1, None, "b"], "x": "不是列表"})
        self.assertEqual(web_state._fav_bases("w"), ["a", "b"])
        self.assertEqual(web_state._fav_bases("x"), [])


class TestFavRoutes(unittest.TestCase):
    """收藏/取消收藏路由：幂等、校验章存在、全取消后不留空壳键。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aa_fav_rt_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.aa = os.path.join(self.tmp, "aa")
        os.makedirs(self.aa)
        for n in (1, 2):
            with open(os.path.join(self.aa, "entry_%d.txt" % n), "w", encoding="utf-8") as f:
                f.write("# 标题%d\n" % n)
        self.cfg = {"aa_dir": self.aa, "out_dir": os.path.join(self.tmp, "汉化"),
                    "work_dir": os.path.join(self.tmp, "work"), "name_style": None}
        os.makedirs(self.cfg["work_dir"], exist_ok=True)
        logic.invalidate_mch_cache()      # 防上轮/其它用例的 merged_chapters 缓存干扰
        self.favf = os.path.join(self.tmp, "favorites.json")
        for p in (mock.patch.object(web_state, "cfg_of", lambda w: self.cfg),
                  mock.patch.object(web_state, "_FAV_FILE", self.favf)):
            p.start()
            self.addCleanup(p.stop)

    def test_收藏取消与幂等(self):
        r = chapters.api_favs_set("w", chapters.FavBody(base="entry_1", fav=True))
        self.assertTrue(r["ok"])
        self.assertEqual((r["base"], r["fav"], r["total"]), ("entry_1", True, 1))
        # 重复收藏不重复入表
        self.assertEqual(chapters.api_favs_set("w", chapters.FavBody(base="entry_1")).__getitem__("total"), 1)
        g = chapters.api_favs_get("w")
        self.assertEqual(g["favs"], ["entry_1"])
        self.assertEqual((g["total"], g["known"]), (1, 2))
        # 再多收一章；取消一章后另一章还在
        chapters.api_favs_set("w", chapters.FavBody(base="entry_2", fav=True))
        self.assertEqual(chapters.api_favs_get("w")["favs"], ["entry_1", "entry_2"])
        r = chapters.api_favs_set("w", chapters.FavBody(base="entry_1", fav=False))
        self.assertEqual((r["total"], r["fav"]), (1, False))
        self.assertEqual(web_state._load_favs(), {"w": ["entry_2"]})
        # 全取消 → 该作品键被摘掉（不留空列表）
        chapters.api_favs_set("w", chapters.FavBody(base="entry_2", fav=False))
        self.assertEqual(web_state._load_favs(), {})

    def test_不存在的章_404_空base_422(self):
        with self.assertRaises(HTTPException) as cm:
            chapters.api_favs_set("w", chapters.FavBody(base="entry_9", fav=True))
        self.assertEqual(cm.exception.status_code, 404)
        with self.assertRaises(HTTPException) as cm:
            chapters.api_favs_set("w", chapters.FavBody(base="   ", fav=True))
        self.assertEqual(cm.exception.status_code, 422)
        self.assertFalse(os.path.exists(self.favf))      # 校验失败一律不落盘

    def test_汉化章名也能定位同一章(self):
        """base 口径 = 原文 txt_key：汉化名带后缀不该被当成另一章（也不该通过校验）。"""
        for bad in ("entry_1.txt", "entry_1_汉化", "entry_1_汉化.txt"):
            with self.assertRaises(HTTPException) as cm:
                chapters.api_favs_set("w", chapters.FavBody(base=bad, fav=True))
            self.assertEqual(cm.exception.status_code, 404)


class TestFavWiring(unittest.TestCase):
    """仓库无 JS 测试运行时 → 只能锁接线（与 test_pager_wiring 同套路）。"""

    def test_目录与标题都有红心_且有筛选按钮(self):
        t = _read(os.path.join(_TPL, "reader.html"))
        self.assertIn('id="fav-filter"', t)
        self.assertIn('id="fav-cnt"', t)
        self.assertIn('id="fav-empty"', t)
        self.assertIn('id="fav-cur"', t)                 # 正文标题旁的红心
        self.assertIn('class="fav-mark{{', t)            # 目录条目红心（服务端按 c.fav 上色）
        self.assertIn('data-base="{{ c.base }}"', t)
        self.assertIn("/favorites", t)                   # POST 收藏接口
        self.assertIn("sessionStorage.getItem(FAV_KEY)", t)   # 筛选中翻章不掉
        self.assertIn("a.style.display = vis.indexOf(a) >= 0 ? '' : 'none'", t)  # 筛掉的行自己隐藏

    def test_阅读页把fav标记喂给模板(self):
        src = _read(os.path.join(_RT, "chapters.py"))
        self.assertIn('web_state._fav_bases(work)', src)
        self.assertIn('"fav": _cb in _favs', src)
        self.assertIn('"fav_total": sum(1 for t in toc if t["fav"])', src)
        self.assertIn('"cur_fav": base in _favs', src)

    def test_两条路由已注册(self):
        paths = {(r.path, tuple(sorted(r.methods)))
                 for r in chapters.router.routes if getattr(r, "methods", None)}
        self.assertIn(("/api/work/{work}/favorites", ("GET",)), paths)
        self.assertIn(("/api/work/{work}/favorites", ("POST",)), paths)


if __name__ == "__main__":
    unittest.main()
