# -*- coding: utf-8 -*-
"""台词提取的压缩感知（.txt.gz / line_meta_v2/*.json.gz）回归测试（2026-10-20）。

背景：全库正文压成 `aa/*.txt.gz`、meta 压成 `work/line_meta_v2/*.json.gz`（2026-10-17）后，
`extract_dialogue` 仍在用 `endswith('.txt')` 与 `open(<base>.json)` 直接判定——
于是 310 章的 `真女神转生_DDS_201X` 全部被判成「meta 缺失」→ 回退去补早已清理的
raw HTML → 每章 FileNotFoundError → `_台词.txt` 一个都没生成 → 候选池为空 →
「生成术语候选」产出 0 条（用户报障）。

守住三条透明层口径：
1. `load_meta` 读得到 `<base>.json.gz`
2. `_ensure_meta` 只认「.json / .json.gz 都不存在」才算缺失（不回退 raw）
3. `_iter_bases` 枚举得到 `<base>.txt.gz`（`_台词.txt` / `_汉化.txt` 除外）

运行: .venv/Scripts/python.exe -m unittest scripts.tests.test_dialogue_compressed -v
"""
import gzip
import json
import os
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
for _p in (_REPO_ROOT, os.path.join(_REPO_ROOT, "scripts"),
           os.path.join(_REPO_ROOT, "scripts", "common"),
           os.path.join(_REPO_ROOT, "scripts", "works"),
           os.path.join(_REPO_ROOT, "scripts", "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import extract_dialogue as ED          # noqa: E402


def _write_gz_text(path, text):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)


def _write_gz_json(path, obj):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


class TestCompressedDialogueInputs(unittest.TestCase):
    """压缩形态（.txt.gz / .json.gz）必须与明文形态行为一致。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.aa_dir = os.path.join(self.tmp, "aa")
        self.out_dir = os.path.join(self.tmp, "汉化")
        self.work_dir = os.path.join(self.tmp, "work")
        self.raw_dir = os.path.join(self.tmp, "raw")
        self.meta_dir = os.path.join(self.work_dir, "line_meta_v2")
        for d in (self.aa_dir, self.out_dir, self.meta_dir):
            os.makedirs(d, exist_ok=True)
        self.cfg = {"id": "テスト作品", "aa_dir": self.aa_dir, "out_dir": self.out_dir,
                    "work_dir": self.work_dir, "raw_dir": self.raw_dir,
                    "site": "__not_a_real_site__"}   # 站点模块不存在：证明没走补 meta 分支
        # 3 行正文：#标题 / 帖头 / 台词（末尾换行 → read().split('\n') 得 4 项，行口径同 aa_translate）
        self.lines = ["# テスト", "1 ： ◆abc ：2026/10/20(火) 12:00:00 ID:xyz",
                      "　これは台詞です。", ""]
        self.base = "entry_1"
        _write_gz_text(os.path.join(self.aa_dir, self.base + ".txt.gz"),
                       "\n".join(self.lines))
        # meta（1-based 行号）：第 3 行是台词正文
        self.meta = {"1": {"kind": "header"}, "2": {"kind": "header"},
                     "3": {"kind": "text", "confidence": 0.9}}
        _write_gz_json(os.path.join(self.meta_dir, self.base + ".json.gz"), self.meta)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_meta_读得到_gz(self):
        self.assertEqual(ED.load_meta(self.meta_dir, self.base), self.meta)

    def test_load_meta_缺失仍返回_None(self):
        self.assertIsNone(ED.load_meta(self.meta_dir, "entry_999"))

    def test_ensure_meta_只有_gz_时不算缺失(self):
        """只压了 .gz + raw 已清理 → 不得抛 FileNotFoundError，直接返回 gz 路径。"""
        path = ED._ensure_meta(self.meta_dir, self.base, self.cfg)
        self.assertTrue(path.endswith(".json.gz"))
        self.assertTrue(os.path.exists(path))

    def test_iter_bases_枚举_gz_并排除台词与汉化(self):
        _write_gz_text(os.path.join(self.aa_dir, "entry_2.txt.gz"), "x\n")
        for extra in ("entry_1_台词.txt", "entry_1_汉化.txt"):
            with open(os.path.join(self.aa_dir, extra), "w", encoding="utf-8") as f:
                f.write("x")
        bases = list(ED._iter_bases(self.cfg))
        self.assertIn(self.base, bases)          # 压缩形态必须看得到
        self.assertIn("entry_2", bases)
        self.assertNotIn("entry_1_台词", bases)
        self.assertNotIn("entry_1_汉化", bases)

    def test_ensure_dialogue_file_用_gz_meta_生成台词(self):
        target = ED.ensure_dialogue_file(self.cfg, self.base, src_only=True)
        self.assertTrue(os.path.exists(target))
        with open(target, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("これは台詞です。", body)      # 正文台词抓到了
        self.assertNotIn("◆abc", body)              # 帖头不产台词


if __name__ == "__main__":
    unittest.main()
