# -*- coding: utf-8 -*-
"""补位位置规范化 + ポケの子 词表守卫（2026-10-11 用户报错回归）。

两类事故：
1. `aa_translate.normalize_pad_placement` —— U+3000 补位落在**文字内部**：
   `>>N` 引用左侧（line_meta 把 `>>` 切成 art 段，段落重建给前面 text 段补位）、
   `（C+1）`/`龙/地面` 这类括号斜杠注记内部；以及模型往 `>>1`/`4-0` 里塞的字。
   口径 = 补位挪到行尾、塞的字删掉，**整行显示宽度不变**（变了会被 scan_width 判
   short 再送 fix_width 重译，白烧 API）。
2. 词表守卫 —— 纯假名术语当子串吃掉日常语法（はどう=は+どう、やけど=关西腔 けど、
   なのは=〜なのは、となり=となる、ほうし=ほうして、あたれ=あたれば、きらい=嫌い）。
"""
import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (SCRIPTS, os.path.join(SCRIPTS, "common"), os.path.join(SCRIPTS, "works")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import aa_translate as A        # noqa: E402
import aa_common as C           # noqa: E402
import pre_terms as PT          # noqa: E402
from _work_guard import repo_only, work_cfg  # noqa: E402


@repo_only("需本机作品数据")
class TestPadPlacement(unittest.TestCase):
    def w(self, s):
        return C.disp_width(s)

    def assertSame(self, src):
        out = A.normalize_pad_placement(src)
        self.assertEqual(self.w(src), self.w(out), "宽度必须不变（否则会被判 short 重译）")
        self.assertEqual(out, A.normalize_pad_placement(out), "必须幂等")
        return out

    def test_ref_pad_moves_to_eol(self):
        """mix 行 `>>N` 引用左侧的补位 → 行尾（全库 142 行的根因）。"""
        out = self.assertSame(" 诶？让他带了什么来着？\u3000\u3000>>德其欧")
        self.assertIn("？>>德其欧", out)
        self.assertTrue(out.endswith("\u3000\u3000"))   # 搬走的 4 格 → 行尾 2 个全角空格

    def test_inner_pad_moves_to_eol(self):
        for src, keep in [(" 属性是「龙\u3000\u3000\u3000\u3000/地面」", "「龙/地面」"),
                          ("我方使出的「冰」招式更容易击中要害。（Ｃ\u3000\u3000\u3000\u3000\u3000+1）", "（Ｃ+1）")]:
            out = self.assertSame(src)
            self.assertIn(keep, out)

    def test_inserted_char_removed(self):
        self.assertIn(">>1", self.assertSame(" 総天然色でお送りします。>>是1のチョイス式"))
        self.assertIn("「3-0」", self.assertSame(" 「3-以0」大勝の航程"))

    def test_art_line_untouched(self):
        """AA 图形行（`二　　/` 后面不是实字）一律不碰。"""
        for src in [" 　/　　/.:.:／.:.:.:＼　　/ /.:.:.:.:∧　　",
                    "　 ⌒辷_（厄）>\u3000\u3000 ::::ノ ／⌒Ｌ（￣二\u3000\u3000/ ――┐"]:
            self.assertEqual(src, A.normalize_pad_placement(src))


@repo_only("需本机作品数据")
class TestPokeGuards(unittest.TestCase):
    """ポケの子 词表守卫（需本机 wiki/作品数据；发行包内自动跳过）。"""
    WORK = "ポケの子"

    @classmethod
    def setUpClass(cls):
        work_cfg(cls.WORK, ("aa_dir", "out_dir"))
        wd = PT.wiki_dir()
        if not os.path.isdir(os.path.join(wd, "")):
            raise unittest.SkipTest("缺 pokemon wiki 目录")
        A.WORK_ID = cls.WORK
        cls.LINES = [
            " というわけで、バトル関連の話は次回に持ち越しとなります",
            " 非常に重要なのはやはり言うまでもないので",
            " 【名前】高町なのは（Lv：95）",
            " 【なのは】ご、ごめんなさ～い！",
            " ・『となり』…　「亜人」ポケモンの「攻」の上昇を無視する。",
            " 狙撃でんじほうしていい？",
            " 撃ってあたれば混乱確定で自滅ワンチャンあるしね",
            " チェイサーだいっきらいそうな男",
            " 流石にそれはどうよって奴かな",
            " まぁタイプ相性はよくないんやけどな",
            " やけどはどうか……？",
            " タスキなら特防か。アブソルタスキじゃないなら",
            " ◎「ちゅうがえり」…　物/飛/単/70/100/○",
            " とくぼう：C　　　「とんぼ」技",
            " よほど「統率」に厳しい－でもなければ“基本誰でも任命できる4種”ですね",
            " 【特性】メガランチャー…　「はどう」等の技の威力を強化",
        ]
        cls.pairs = PT.build_pairs(cls.WORK, corpus="\n".join(cls.LINES))

    def fired(self, line):
        _, hits = PT.apply_hits(line, pairs=self.pairs, work=self.WORK)
        return {(h.get("jp"), h.get("zh")) for h in hits or []}

    def test_grammar_never_eaten(self):
        """语法/日常词不得被当术语吃掉。"""
        for line, bad in [
            (" というわけで、バトル関連の話は次回に持ち越しとなります", "となり"),
            (" 非常に重要なのはやはり言うまでもないので", "なのは"),
            (" 狙撃でんじほうしていい？", "ほうし"),
            (" 撃ってあたれば混乱確定で自滅ワンチャンあるしね", "あたれ"),
            (" チェイサーだいっきらいそうな男", "きらい"),
            (" 流石にそれはどうよって奴かな", "はどう"),
            (" まぁタイプ相性はよくないんやけどな", "やけど"),
            (" よほど「統率」に厳しい－でもなければ“基本誰でも任命できる4種”ですね", "できる"),
        ]:
            self.assertNotIn(bad, {j for j, _ in self.fired(line)}, f"{bad} 不该命中：{line}")

    def test_real_terms_still_fire(self):
        self.assertIn("高町奈叶", {z for _, z in self.fired(" 【名前】高町なのは（Lv：95）")})
        self.assertIn("奈叶", {z for _, z in self.fired(" 【なのは】ご、ごめんなさ～い！")})
        self.assertIn("隔壁", {z for _, z in self.fired(" ・『となり』…　「亜人」")})
        self.assertIn("灼伤", {z for _, z in self.fired(" やけどはどうか……？")})
        self.assertIn("波导", {z for _, z in self.fired(" 【特性】メガランチャー…　「はどう」等の技の威力を強化")})
        self.assertIn("气势披带", {z for _, z in self.fired(" タスキなら特防か")})
        self.assertIn("空翻折返", {z for _, z in self.fired(" ◎「ちゅうがえり」…　物/飛/単/70/100/○")})
        self.assertIn("折返", {z for _, z in self.fired(" とくぼう：C　　　「とんぼ」技")})


if __name__ == "__main__":
    unittest.main()
