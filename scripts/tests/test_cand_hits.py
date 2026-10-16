# -*- coding: utf-8 -*-
"""候选出处命中索引（aa_web_app/cand_hits.py）单元测试。

运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_cand_hits -v
口径（方案 doc/方案归档/候选联网核名与出处高亮方案_2026-10-15.md 决策 11/13）：
- 出处 = 全库原文扫一遍得到的命中章；每候选**按命中行数降序取前 3 章**、每章最多记 20 个命中行；
- 缓存 `work/cand_hits.json`，失效键 = 候选文件 mtime + aa 目录 mtime + 词集合 hash；
- 缺缓存时按需重建并落盘；只读模式（allow_build=False）不给缓存就回空表。
零依赖（unittest + 临时目录），不启服务器、不调 API。
"""
import json
import os
import sys
import tempfile
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, _REPO_ROOT)

from aa_web_app import cand_hits as CH


def _write_chapters(aa_dir, chapters):
    """chapters = {"entry_1": [行...], ...} → 写 aa/entry_1.txt（含 _台词/_汉化 旁支）。"""
    os.makedirs(aa_dir, exist_ok=True)
    out = {}
    for base, lines in chapters.items():
        p = os.path.join(aa_dir, base + ".txt")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        out[base] = p
    return out


def _mk_cand_file(work_dir, terms):
    p = os.path.join(work_dir, "glossary_candidates.json")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"ai_候选": [{"term": t, "translation": "", "type": "角色"} for t in terms]},
                  f, ensure_ascii=False)
    return p


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.aa = os.path.join(self.tmp.name, "aa")

    def test_基本命中与章降序(self):
        """命中行多的章排前面（前端按此顺序展示前 3 章）。"""
        _write_chapters(self.aa, {
            "entry_1": ["マヤ 出场", "无关行", "マヤ 又来了", "マヤ 第三次"],
            "entry_2": ["マヤ 一次"],
            "entry_3": ["本章没有任何候选词"],
        })
        h, cnt = CH.build(self.aa, ["マヤ", "ネオユニヴァース"])
        self.assertEqual(list(h["マヤ"].keys()), ["entry_1", "entry_2"])   # 3 处 > 1 处
        self.assertEqual(h["マヤ"]["entry_1"], [0, 2, 3])
        self.assertEqual(h["マヤ"]["entry_2"], [0])
        self.assertEqual(cnt["マヤ"], {"entry_1": 3, "entry_2": 1})
        self.assertNotIn("ネオユニヴァース", h)                          # 零命中不进表

    def test_每候选最多3章(self):
        _write_chapters(self.aa, {f"entry_{i}": ["トップガン"] * (i + 1) for i in range(1, 6)})
        h, cnt = CH.build(self.aa, ["トップガン"])
        self.assertEqual(len(h["トップガン"]), CH.MAX_CH)                 # 5 章里只留 3 章
        self.assertEqual(list(h["トップガン"])[:3], ["entry_5", "entry_4", "entry_3"])
        self.assertEqual(cnt["トップガン"]["entry_5"], 6)      # entry_i 有 i+1 行该词

    def test_每章最多20行_但计数不封顶(self):
        """行列表封顶 20（只用于滚动定位），真命中数不封顶——否则「命中 N 处」恒 20、
        高频词的出处章排序退化（实测 マヤ 747 次曾显示成 20）。"""
        _write_chapters(self.aa, {"entry_1": ["マヤ"] * 30, "entry_2": ["マヤ"] * 25})
        h, cnt = CH.build(self.aa, ["マヤ"])
        self.assertEqual(len(h["マヤ"]["entry_1"]), CH.MAX_LINES)
        self.assertEqual(cnt["マヤ"], {"entry_1": 30, "entry_2": 25})
        self.assertEqual([b for b, _l, _n in CH.sort_pairs(h, cnt, "マヤ")], ["entry_1", "entry_2"])

    def test_跳过台词与汉化旁支(self):
        """`_台词.txt` / `_汉化.txt` 是衍生文件，命中只能算在原文上。"""
        _write_chapters(self.aa, {"entry_1": ["マヤ"]})
        for suffix in ("_台词", "_汉化"):
            with open(os.path.join(self.aa, "entry_1" + suffix + ".txt"), "w",
                      encoding="utf-8", newline="\n") as f:
                f.write("マヤ\nマヤ\nマヤ")
        h, cnt = CH.build(self.aa, ["マヤ"])
        self.assertEqual(list(h["マヤ"].keys()), ["entry_1"])
        self.assertEqual(h["マヤ"]["entry_1"], [0])
        self.assertEqual(cnt["マヤ"], {"entry_1": 1})

    def test_空词与正则词剔除(self):
        _write_chapters(self.aa, {"entry_1": ["マヤ"]})
        h, _c = CH.build(self.aa, ["", "  ", "re:^マヤ", "マヤ"])
        self.assertEqual(list(h), ["マヤ"])

    def test_空terms直接返回(self):
        _write_chapters(self.aa, {"entry_1": ["マヤ"]})
        self.assertEqual(CH.build(self.aa, []), ({}, {}))

    def test_目录不存在不炸(self):
        self.assertEqual(CH.build(os.path.join(self.tmp.name, "nope"), ["マヤ"]), ({}, {}))

    def test_空行不计命中(self):
        """空行不可能含词——避免"空词命中所有行"类退化。"""
        _write_chapters(self.aa, {"entry_1": ["", "", "マヤ"]})
        h, _c = CH.build(self.aa, ["マヤ"])
        self.assertEqual(h["マヤ"]["entry_1"], [2])


class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wd = os.path.join(self.tmp.name, "work")
        self.aa = os.path.join(self.tmp.name, "aa")
        os.makedirs(self.wd, exist_ok=True)
        _write_chapters(self.aa, {"entry_1": ["マヤ", "トップガン"], "entry_2": ["マヤ"]})
        self.cand = _mk_cand_file(self.wd, ["マヤ", "トップガン"])

    def _terms(self):
        return ["マヤ", "トップガン"]

    def test_首次构建并落盘_二次命中缓存(self):
        hits, _cnt, info = CH.load_or_build(self.wd, self.cand, self.aa, self._terms())
        self.assertEqual(info["from"], "built")
        self.assertIn("マヤ", hits)
        cache_p = os.path.join(self.wd, CH.CACHE_NAME)
        self.assertTrue(os.path.exists(cache_p))
        self.assertFalse(os.path.exists(cache_p + ".tmp"))       # 原子写不留 tmp
        hits2, _c2, info2 = CH.load_or_build(self.wd, self.cand, self.aa, self._terms())
        self.assertEqual(info2["from"], "cache")
        self.assertEqual(hits2, hits)

    def test_词集合变化即重建(self):
        CH.load_or_build(self.wd, self.cand, self.aa, self._terms())
        hits, _cnt, info = CH.load_or_build(self.wd, self.cand, self.aa, ["マヤ"])
        self.assertEqual(info["from"], "built")
        self.assertNotIn("トップガン", hits)

    def test_候选文件变化即重建(self):
        CH.load_or_build(self.wd, self.cand, self.aa, self._terms())
        time.sleep(0.01)
        _mk_cand_file(self.wd, ["マヤ", "トップガン", "新增词"])     # mtime 变
        hits, _cnt, info = CH.load_or_build(self.wd, self.cand, self.aa, self._terms() + ["新增词"])
        self.assertEqual(info["from"], "built")
        self.assertNotIn("新增词", hits)                            # 语料里没有 → 不进表

    def test_语料目录变化即重建(self):
        CH.load_or_build(self.wd, self.cand, self.aa, self._terms())
        _write_chapters(self.aa, {"entry_3": ["マヤ"]})             # 新增章 → 语料指纹变
        hits, _cnt, info = CH.load_or_build(self.wd, self.cand, self.aa, self._terms())
        self.assertEqual(info["from"], "built")
        self.assertIn("entry_3", hits["マヤ"])

    def test_单章内容改动即重建(self):
        """语料指纹含逐章 mtime/size——只改某章内容（词集合没变）也要重算命中行。"""
        CH.load_or_build(self.wd, self.cand, self.aa, self._terms())
        _write_chapters(self.aa, {"entry_1": ["マヤ", "トップガン", "マヤ 新加的一行"]})
        hits, _cnt, info = CH.load_or_build(self.wd, self.cand, self.aa, self._terms())
        self.assertEqual(info["from"], "built")
        self.assertEqual(hits["マヤ"]["entry_1"], [0, 2])

    def test_只读模式不给缓存则空表(self):
        hits, _cnt, info = CH.load_or_build(self.wd, self.cand, self.aa, self._terms(), allow_build=False)
        self.assertEqual((hits, info["from"]), ({}, "empty"))
        self.assertFalse(os.path.exists(os.path.join(self.wd, CH.CACHE_NAME)))

    def test_缓存损坏则重建(self):
        p = os.path.join(self.wd, CH.CACHE_NAME)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ 坏 json")
        hits, _cnt, info = CH.load_or_build(self.wd, self.cand, self.aa, self._terms())
        self.assertEqual(info["from"], "built")
        self.assertIn("マヤ", hits)

    def test_schema不符则重建(self):
        p = os.path.join(self.wd, CH.CACHE_NAME)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"schema": "老版本 v0", "命中": {"マヤ": {"entry_1": [0]}}}, f)
        hits, _cnt, info = CH.load_or_build(self.wd, self.cand, self.aa, self._terms())
        self.assertEqual(info["from"], "built")

    def test_词序无关_sig只吃集合(self):
        s1 = CH.compute_sig(self.cand, self.aa, ["マヤ", "トップガン"])
        s2 = CH.compute_sig(self.cand, self.aa, ["トップガン", "マヤ", "マヤ"])
        self.assertEqual(s1, s2)

    def test_hits_counts速查(self):
        hits = {"マヤ": {"entry_1": [1, 2], "entry_2": [3]}}
        self.assertEqual(CH.hits_counts(hits), {"マヤ": {"entry_1": 2, "entry_2": 1}})


if __name__ == "__main__":
    unittest.main()
