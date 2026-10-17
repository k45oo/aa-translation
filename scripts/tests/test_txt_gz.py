# -*- coding: utf-8 -*-
"""压缩文本包（.txt.gz）透明层单测 —— 2026-10-17 全库压缩后的口径守护。

守的是**三种静默坏法**（压缩前都不出错、压缩后才炸，且都不报错）：
  ① 枚举漏章：`glob('*.txt')` / `listdir + endswith('.txt')` 在磁盘是 `.txt.gz` 时**一章都看不到**
     （症状「0 章可译」「整部当没下载」）→ 必须走 `list_txt`。
  ② 读炸：`open(path)` 读到 gzip 字节 → `UnicodeDecodeError` / 静默空 → 走 `txt_read`/`txt_existing`。
  ③ **指纹口径**：`txt_file_sha1` 只认「已经 .gz 结尾」的路径时，调用方传规范名 `entry_1.txt`
     → 返回 "" → `refine_aa._ckpt_ok` 判「拿不到指纹」→ **每章重校对（静默烧钱）**。
     必须「plain 路径 == .gz 路径 == 原始字节 sha1」，且 ckpt 记录值在压缩后仍命中。

运行：.venv/Scripts/python.exe -m unittest scripts.tests.test_txt_gz -v
"""
import gzip
import io
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, "common"), os.path.join(_SCRIPTS, "fix"),
           os.path.join(_SCRIPTS, "works")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import aa_translate as T          # noqa: E402
from aa_common import list_txt, txt_read, txt_existing, txt_out_path, txt_write_form   # noqa: E402


LINES = ["# 标题", "# 原帖地址: https://example.test/1", "１ ｜ 楼主 ｜ 2026-01-01 ID:abc",
         "　 ／＼／＼　", "　こんにちは、世界！", ""]


class TestTxtReadLayer(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="aa_txtgz_")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _plain(self, name, lines=LINES):
        p = os.path.join(self.d, name)
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        return p

    def _gz(self, name, lines=LINES, drop_plain=True):
        p = os.path.join(self.d, name)
        with gzip.open(p, "wt", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        if drop_plain and os.path.exists(p[:-3]):
            os.remove(p[:-3])
        return p

    # ---- ③ 指纹（最贵的那条）----
    def test_指纹_plain路径与gz路径相等且等于原始字节(self):
        raw = ("\n".join(LINES)).encode("utf-8")
        want = hashlib.sha1(raw).hexdigest()
        plain = self._plain("entry_1.txt")
        self.assertEqual(T.txt_file_sha1(plain), want)
        gz = self._gz("entry_2.txt.gz")          # 只有 .gz
        self.assertEqual(T.txt_file_sha1(gz), want)
        # 关键：**传规范名**（磁盘只有 .gz）也必须拿到同一个指纹
        self.assertEqual(T.txt_file_sha1(os.path.join(self.d, "entry_2.txt")), want)

    def test_指纹_缺失返回空串(self):
        self.assertEqual(T.txt_file_sha1(""), "")
        self.assertEqual(T.txt_file_sha1(os.path.join(self.d, "nope.txt")), "")

    def test_ckpt记录值在压缩后仍命中(self):
        """refine_ckpt 的 src_sha1 是压缩前算的；文件转 .gz 后 _ckpt_ok 必须仍为 True（零调用跳过）。"""
        import refine_aa as R
        aa = os.path.join(self.d, "aa"); out = os.path.join(self.d, "汉化")
        wd = os.path.join(self.d, "work")
        for x in (aa, out, wd):
            os.makedirs(x, exist_ok=True)
        self._plain(os.path.join("aa", "entry_9.txt"))                  # 先明文（模拟压缩前）
        self._plain(os.path.join("汉化", "entry_9_汉化.txt"))
        cfg = {"aa_dir": aa, "out_dir": out, "work_dir": wd}
        R._ckpt_save(cfg, "entry_9", {"partial_kana": 0}, [])
        self.assertTrue(R._ckpt_ok(cfg, "entry_9"), "压缩前应命中")
        for sub, name in (("aa", "entry_9.txt"), ("汉化", "entry_9_汉化.txt")):
            src = os.path.join(self.d, sub, name)
            with open(src, "rb") as f:
                data = f.read()
            with open(src + ".gz", "wb") as f:
                f.write(gzip.compress(data))
            os.remove(src)                                              # 压缩：删原文件
        self.assertTrue(R._ckpt_ok(cfg, "entry_9"), "压缩后必须仍命中（否则全库重校对花钱）")

    # ---- ① 枚举 ----
    def test_枚举_明文与压缩都收且规范名去重(self):
        self._plain("entry_1.txt")
        self._gz("entry_2.txt.gz")
        self._gz("entry_3_汉化.txt.gz")           # 产物，应被 exclude 掉
        self._gz("entry_4_台词.txt.gz")           # 台词，应被 exclude 掉
        # list_txt 返回**规范名**（.gz 也报 .txt，调用方再经 txt_read/txt_existing 落到实际文件）
        names = list_txt(self.d, exclude=("_台词.txt", "_汉化.txt"))
        self.assertEqual(names, ["entry_1.txt", "entry_2.txt"])
        # 双形态并存时也只报一条（去重）
        self._gz("entry_5.txt.gz")
        self._plain("entry_5.txt")
        self.assertEqual([n for n in list_txt(self.d, exclude=("_台词.txt", "_汉化.txt"))
                          if n.startswith("entry_5")], ["entry_5.txt"])
        # 反面教材：按名过滤 .txt 会把压缩章全漏掉（这就是要禁 glob('*.txt') 的原因）
        naive = [n for n in sorted(os.listdir(self.d)) if n.endswith(".txt")
                 and not n.endswith("_台词.txt")]
        self.assertEqual(naive, ["entry_1.txt", "entry_5.txt"])

    # ---- ② 读 ----
    def test_读_明文与压缩同结果(self):
        self._plain("a.txt")
        self._gz("b.txt.gz")
        self.assertEqual(txt_read(os.path.join(self.d, "a.txt")), LINES)
        self.assertEqual(txt_read(os.path.join(self.d, "b.txt")), LINES)          # 规范名透明
        self.assertTrue(txt_existing(os.path.join(self.d, "b.txt")).endswith(".gz"))

    # ---- 写：保持磁盘原有形态 ----
    def test_写_保持磁盘形态(self):
        gz = self._gz("c.txt.gz")
        p = txt_out_path(os.path.join(self.d, "c.txt"))
        self.assertTrue(p.endswith(".gz"))
        txt_write_form(p, ["新内容"])
        with gzip.open(gz, "rt", encoding="utf-8") as f:
            self.assertEqual(f.read(), "新内容")
        # 明文形态：写回明文
        plain = self._plain("d.txt")
        txt_write_form(txt_out_path(plain), ["明文"])
        with io.open(plain, encoding="utf-8") as f:
            self.assertEqual(f.read(), "明文")


if __name__ == "__main__":
    unittest.main()
