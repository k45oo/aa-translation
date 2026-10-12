# -*- coding: utf-8 -*-
"""行内换行护栏（2026-09-22 entry_8332 / entry_7578 行号错位事故）回归测试。

事故现场：汉化 txt 行数比原文多 2/4 行（中段多出重复译文/半截译文），
工作台读到末几行时报「行数据加载失败」——因为行号整体右移，末尾 idx 越界。

根因：模型译文里带回车/换行 → refine_aa 段级重建把折行当「一段」拼回「一行」，
写回时 "\n".join(lines) 把这一行又拆成 2 行 → 静默多行。

三层护栏（本文件逐层断言）：
  ① aa_translate.one_line() / txt_write()：任何写盘的行先压平行内换行（全库写盘兜底）
  ② refine_aa swallow/poly 路径：模型输出落地前压平（根因处）
  ③ refine_aa 写回前比对原文行数：不一致即拒写（并跳过 ckpt，下次仍会重校）

运行：cd scripts/tests && python -m unittest test_refine_line_guard -v
"""
import io
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'fix')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'common')))

import aa_translate as T
import refine_aa as R
from _work_guard import repo_only  # noqa: E402 （发行包内跳过生成者专属用例）

NL = chr(10)          # 换行
CR = chr(13)          # 回车
LS = chr(0x2028)      # LINE SEPARATOR
PS = chr(0x2029)      # PARAGRAPH SEPARATOR
VT = chr(11)
FF = chr(12)


def _refine_src():
    with io.open(R.__file__, encoding='utf-8') as f:
        return f.read()


class TestOneLine(unittest.TestCase):
    """① aa_translate.one_line / BREAK_RE：行内换行压平。"""

    def test_collapses_all_break_kinds(self):
        self.assertEqual(T.one_line('a' + NL + 'b'), 'ab')
        self.assertEqual(T.one_line('a' + CR + NL + 'b'), 'ab')
        self.assertEqual(T.one_line('a' + LS + 'b'), 'ab')
        self.assertEqual(T.one_line('a' + PS + 'b'), 'ab')
        self.assertEqual(T.one_line('a' + VT + FF + 'b'), 'ab')

    def test_keeps_normal_text_and_handles_empty(self):
        self.assertEqual(T.one_line('【和真】没问题'), '【和真】没问题')
        self.assertEqual(T.one_line(''), '')
        self.assertEqual(T.one_line(None), '')

    def test_break_re_detects_breaks(self):
        for ch in (NL, CR, LS, PS):
            self.assertTrue(T.BREAK_RE.search('x' + ch + 'y'), repr(ch))
        self.assertFalse(T.BREAK_RE.search('普通一行'))


class TestTxtWriteGuard(unittest.TestCase):
    """① txt_write 写盘兜底：含换行的行被压平 → 行数不增。"""

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix='lineguard_')
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_line_count_preserved_when_line_contains_newline(self):
        p = os.path.join(self.dir, 'entry_1_汉化.txt')
        lines = ['# 标题', ' 【和真】前半' + NL + '后半', '图形行']
        T.txt_write(p, lines)
        with io.open(p, encoding='utf-8', newline='') as f:
            raw = f.read()
        got = [l.rstrip(CR) for l in raw.split(NL)]
        # 写盘后仍是 3 行（旧行为：第 2 行被拆成 2 行 → 4 行 → 全章行号错位）
        self.assertEqual(len(got), 3)
        self.assertEqual(got[1], ' 【和真】前半后半')

    def test_clean_lines_untouched(self):
        p = os.path.join(self.dir, 'entry_2_汉化.txt')
        lines = ['# 标题', ' 【和真】正常译文', '']
        T.txt_write(p, lines)
        with io.open(p, encoding='utf-8', newline='') as f:
            raw = f.read()
        self.assertEqual([l.rstrip(CR) for l in raw.split(NL)], lines)


class TestRefineModelOutputGuard(unittest.TestCase):
    """② refine_aa 模型输出落地前压平（swallow 两处 + poly 一处）。"""

    def test_swallow_outputs_sanitized(self):
        s = _refine_src()
        self.assertIn('zh = T.one_line(zh)', s)        # swallow 主路
        self.assertIn('zh2 = T.one_line(zh2)', s)      # swallow 超宽重试

    def test_poly_output_sanitized(self):
        self.assertIn('zh = T.one_line(zh)', _refine_src())


class TestRefineWriteBackGuard(unittest.TestCase):
    """③ 写回兜底：赋值前压平 + 行数不齐拒写 + 拒写不落 ckpt。"""

    def test_assignment_sanitized(self):
        self.assertIn('new_tr[i] = T.one_line(new)', _refine_src())

    def test_line_count_compared_with_src(self):
        s = _refine_src()
        self.assertIn('n_src = len(C.txt_read(C.txt_existing(os.path.join(cfg["aa_dir"], fname))))', s)
        self.assertIn('if len(new_tr) != n_src:', s)
        self.assertIn('stats["write_skipped"] = 1', s)

    def test_write_skipped_blocks_ckpt(self):
        # 拒写的章不能落 ckpt，否则下次指纹相同会被当"已校对"跳过，坏文件永远修不回来
        s = _refine_src()
        self.assertIn('and not stats.get("write_skipped")', s)


class TestSpeakerTagGuard(unittest.TestCase):
    """④ 说话人标记【…】结构护栏（2026-10-02 entry_22208 事故）。

    事故现场：主翻译对「名字不在术语表」的【名前】行失败 → 整行留日文落 review_inline；
    refine 的漏译补译链把整行交给模型，模型丢【】或改写成「名字：」都照样落地
    ——本作实测 635 行（1.11%）丢括号，其中 111 行是「名：」式。
    """

    def setUp(self):
        self._orig = R._call_glm

    def tearDown(self):
        R._call_glm = self._orig          # 模块级打桩必须复原（同进程跑全部测试文件）

    def test_speaker_tag_extraction(self):
        self.assertEqual(R.speaker_tag(' 【カズミラクル】ボクだって'), (' ', '【カズミラクル】'))
        self.assertEqual(R.speaker_tag('【白野】すいません'), ('', '【白野】'))
        self.assertIsNone(R.speaker_tag('普通の行'))
        self.assertIsNone(R.speaker_tag('【】空标记'))
        self.assertIsNone(R.speaker_tag(''))
        self.assertIsNone(R.speaker_tag(None))

    def test_strip_lead_colon(self):
        self.assertEqual(R._strip_lead_colon('卡兹米拉库尔：可恶啊！！！'), '可恶啊！！！')
        self.assertEqual(R._strip_lead_colon('和真:好，走吧'), '好，走吧')
        # 无冒号 / 冒号后为空 → 原样返回（不误删正文）
        self.assertEqual(R._strip_lead_colon('可恶啊！！！'), '可恶啊！！！')
        self.assertEqual(R._strip_lead_colon('名字：'), '名字：')

    @repo_only("需要生成者术语表条目")
    def test_tag_zh_uses_glossary_then_falls_back(self):
        # 表里有词条 → 用译名；表里没有 → 原样保留（未确认译名不猜）
        self.assertEqual(R._tag_zh('【カズミラクル】', 'entry_22208',
                                   'この素晴らしいシンオウに祝福を'), '【和真奇迹】')
        self.assertEqual(R._tag_zh('【視聴者ド】', 'entry_22208',
                                   'この素晴らしいシンオウに祝福を'), '【視聴者ド】')

    def test_retry_path_when_model_keeps_tag(self):
        R._call_glm = lambda s, b, **k: '【观众D】冤枉你了真是抱歉啊～'
        tag = R.speaker_tag('【視聴者ド】あらぬ疑いをかけてすまんが')
        zh, sfx = R.guard_speaker_tag('冤枉你了真是抱歉啊～', tag, '原文', R.SYSTEM_NORMAL,
                                      None, 'entry_22208', 'この素晴らしいシンオウに祝福を')
        self.assertEqual(sfx, '+tag_retry')
        self.assertTrue(zh.startswith('【观众D】'))

    @repo_only("需要生成者术语表条目")
    def test_reattach_path_when_model_never_keeps_tag(self):
        R._call_glm = lambda s, b, **k: '卡兹米拉库尔：可恶啊！！！'
        tag = R.speaker_tag(' 【カズミラクル】ちくしょうっ！！！')
        zh, sfx = R.guard_speaker_tag('卡兹米拉库尔：可恶啊！！！', tag, '原文', R.SYSTEM_NORMAL,
                                      None, 'entry_22208', 'この素晴らしいシンオウに祝福を')
        self.assertEqual(sfx, '+tag_reattach')
        self.assertEqual(zh, '【和真奇迹】可恶啊！！！')      # 括号回来了 + 「名：」被剥掉

    def test_compliant_output_skips_model_call(self):
        def _boom(*a, **k):
            raise AssertionError('译文已合规时不该再调模型')
        R._call_glm = _boom
        tag = R.speaker_tag('【白野】すいません')
        zh, sfx = R.guard_speaker_tag('【白野】不好意思', tag, '原文', R.SYSTEM_NORMAL,
                                      None, 'entry_22208', 'この素晴らしいシンオウに祝福を')
        self.assertEqual((zh, sfx), ('【白野】不好意思', ''))

    def test_no_tag_untouched(self):
        R._call_glm = lambda s, b, **k: '随便'
        zh, sfx = R.guard_speaker_tag('普通译文', None, '原文', R.SYSTEM_NORMAL,
                                      None, 'entry_22208', 'この素晴らしいシンオウに祝福を')
        self.assertEqual((zh, sfx), ('普通译文', ''))

    @repo_only("需要生成者术语表条目")
    def test_job_poly_guard_end_to_end(self):
        # 模型丢括号 → 重试仍丢 → 回填；产出行必须以【和真奇迹】开头
        seq = ['可恶啊！！！为什么对探险堂除我之外的所有人都没效果啊！！！！！？']
        R._call_glm = lambda s, b, **k: seq.pop(0) if seq else '可恶啊！！！'
        src = (' 【カズミラクル】ちくしょうっ！！！なんで俺以外の探検堂の人間には'
               '全員効かないんだよっ！！！！！？')
        ed = R._job_poly(1175, src, src, ' ', src.strip(), None,
                         'entry_22208', 'この素晴らしいシンオウに祝福を')
        self.assertEqual(ed[4], 'poly')
        self.assertTrue(ed[5])
        self.assertEqual(ed[6], 'text_normal_ok+tag_reattach')
        self.assertTrue(ed[3].lstrip().startswith('【和真奇迹】可恶啊！！！'))
        self.assertIn('探险堂', ed[3])
        self.assertEqual(len(ed[3]), len(src))       # 回填后仍 pad 到源宽

    def test_job_poly_without_tag_unchanged_reason(self):
        R._call_glm = lambda s, b, **k: '不是不同的世界线吗？'
        src = ' 違う世界線に迷い込んだかな…？'
        ed = R._job_poly(0, src, src, ' ', src.strip(), None,
                         'entry_22208', 'この素晴らしいシンオウに祝福を')
        self.assertEqual(ed[6], 'text_normal_ok')            # 无标记 → 不触发护栏

    def test_source_wiring(self):
        s = _refine_src()
        self.assertIn('def speaker_tag(', s)
        self.assertIn('def guard_speaker_tag(', s)
        self.assertIn('def tag_class(', s)
        self.assertIn('DICE_FRAG_RE', s)
        self.assertIn('text_normal_ok" + _tsfx', s)           # poly 接护栏
        # 2026-10-03 收紧：段级路径不再接护栏（护栏仅文本行）
        # 2026-10-03 P0 拆分后：段级路径 = _swallow_pre/_swallow_apply/_job_swallow（_poly_apply 起是整行路径）
        sw = s.split('def _swallow_pre')[1].split('def _poly_apply')[0]
        self.assertNotIn('guard_speaker_tag', sw)
        self.assertNotIn('speaker_tag(', sw)


class TestSpeakerTagClassGuard(unittest.TestCase):
    """⑥ 标记分类护栏（2026-10-03 收紧：仅文本行 + 骰子/嵌套/安価安全）。

    用户指令：① 护栏改为仅文本行约束；②【…】内也有骰子内容，确保不要改错。
    """

    W = 'この素晴らしいシンオウに祝福を'

    def setUp(self):
        self._orig = R._call_glm

    def tearDown(self):
        R._call_glm = self._orig

    def test_tag_class_five_kinds(self):
        self.assertEqual(R.tag_class('カズミラクル'), 'speaker')
        self.assertEqual(R.tag_class('白野'), 'speaker')
        self.assertEqual(R.tag_class('ダイス'), 'anka')                  # 骰子スレ的参与者标签
        self.assertEqual(R.tag_class('指示安価　下5 or このレスから5分'), 'anka')
        self.assertEqual(R.tag_class('System'), 'latin')
        self.assertEqual(R.tag_class('？？？'), 'latin')                  # 非名非 art → 逐字保留
        self.assertEqual(R.tag_class('1D912:674'), 'dice')
        self.assertEqual(R.tag_class('Part（【1D912:674】）スレ主'), 'dice')
        self.assertEqual(R.tag_class('¨‘　　　　　　　　 　 　 ,‘'), 'art')   # art 碎片

    def test_nested_tag_not_truncated(self):
        # 旧实现 [^】]{1,40} 会截成 '【Part（【1D912:674】' → 回填即写坏
        line = '【Part（【1D912:674】）スレ主】あそこのBDさん引き抜こうとしたけどダメでした'
        tag = R.speaker_tag(line)
        self.assertEqual(tag[1], '【Part（【1D912:674】）スレ主】')
        self.assertEqual(R.tag_inner(tag[1]), 'Part（【1D912:674】）スレ主')
        self.assertEqual(R.speaker_tag('【【ロトムのカタログ】（提升的能力）】　下5')[1],
                         '【【ロトムのカタログ】（提升的能力）】')

    def test_dice_digits_change_rejected(self):
        # 模型把骰子数字改了 → 结构校验不过 → 用源标记回填（骰子逐字恢复）
        src = '【Part（【1D912:674】）スレ主】あそこのBDさん'
        tag = R.speaker_tag(src)
        zh, sfx = R.guard_speaker_tag('【Part（【1D912:999】）楼主】那边的BD桑',
                                      tag, src, R.SYSTEM_NORMAL, None, 'entry_21944', self.W)
        self.assertEqual(sfx, '+tag_fix')
        self.assertIn('【1D912:674】', zh)                 # 骰子回到源值
        self.assertTrue(zh.startswith('【Part（【1D912:674】）'))
        self.assertIn('那边的BD桑', zh)                     # 正文取自模型

    def test_dice_compliant_output_untouched(self):
        src = '【Part（【1D912:674】）スレ主】あそこのBDさん'
        tag = R.speaker_tag(src)
        zh, sfx = R.guard_speaker_tag('【Part（【1D912:674】）楼主】那边的BD桑',
                                      tag, src, R.SYSTEM_NORMAL, None, 'entry_21944', self.W)
        self.assertEqual((zh, sfx), ('【Part（【1D912:674】）楼主】那边的BD桑', ''))

    def test_anka_structure_kept(self):
        src = '【指示安価　下5 or このレスから5分】'
        tag = R.speaker_tag(src)
        # 合规：词译了、数字/括号骨架没动 → 不介入
        ok = '【指示安价　下5 or 从本回复起5分钟】'
        self.assertEqual(R.guard_speaker_tag(ok, tag, src, R.SYSTEM_NORMAL,
                                             None, 'entry_10441', self.W)[1], '')
        # 骨架被改（下5→下6）→ 源标记回填。
        # 回填口径 = 源标记 inner **过一遍术语替换**后再拼回括号（`_tag_zh`：anka 允许术语替换，
        # 但数字/括号/冒号骨架必须一致）——故断言不能用源文逐字，否则术语表一更新（安価→底价、
        # このレスから5分→本楼起5分）就假红。这里断言：用实现同源的期望值 + 骨架数字回源值。
        zh, sfx = R.guard_speaker_tag('【指示安价　下6 or 从本回复起5分钟】',
                                      tag, src, R.SYSTEM_NORMAL, None, 'entry_10441', self.W)
        self.assertEqual(sfx, '+tag_fix')
        self.assertTrue(zh.startswith(R._tag_zh(tag[1], 'entry_10441', self.W)),
                        f'回填口径不符: {zh!r}')
        self.assertIn('下5', zh)          # 骨架（数字）必须回到源值
        self.assertNotIn('下6', zh)       # 模型改的骨架不许留

    def test_latin_tag_verbatim(self):
        src = '【System】賈修等级提升了「1」！（Lv：79⇒80）'
        tag = R.speaker_tag(src)
        zh, sfx = R.guard_speaker_tag('【系统】贾修等级提升了「1」！（Lv：79⇒80）',
                                      tag, src, R.SYSTEM_NORMAL, None, 'entry_10437', self.W)
        self.assertEqual(sfx, '+tag_fix')
        self.assertTrue(zh.startswith('【System】'))

    def test_art_fragment_never_touched(self):
        # art 碎片：即使模型丢了标记也**不介入**（不重试、不回填）
        def _boom(*a, **k):
            raise AssertionError('art 碎片不该调模型')
        R._call_glm = _boom
        src = '【¨‘　　　　　　　　 　 　 ,‘】 ；|::::::{　} .V　　./　　{　 　 　 .l::'
        tag = R.speaker_tag(src)
        self.assertEqual(R.tag_class(R.tag_inner(tag[1])), 'art')
        zh, sfx = R.guard_speaker_tag('丢失了标记的译文', tag, src, R.SYSTEM_NORMAL,
                                      None, 'entry_10473', self.W)
        self.assertEqual((zh, sfx), ('丢失了标记的译文', ''))

    def test_poly_rejects_when_dice_fragment_lost(self):
        R._call_glm = lambda s, b, **k: '战斗准备骰子'          # 骰子括号被模型吞掉
        src = '【戦闘準備ダイス】（【1D3:2】）'
        ed = R._job_poly(200, src, src, '', src.strip(), None, 'entry_10441', self.W)
        self.assertFalse(ed[5])
        self.assertIn('dice_err', ed[6])
        self.assertEqual(ed[3], src)                          # 保留原行

    def test_poly_keeps_dice_line(self):
        R._call_glm = lambda s, b, **k: '【战斗准备骰子】（【1D3:2】）'
        src = '【戦闘準備ダイス】（【1D3:2】）'
        ed = R._job_poly(200, src, src, '', src.strip(), None, 'entry_10441', self.W)
        self.assertTrue(ed[5])
        self.assertIn('【1D3:2】', ed[3])
        self.assertEqual(len(ed[3]), len(src))                # pad 到源宽


class TestRefineFailTrace(unittest.TestCase):
    """⑤ 校对调用失败行不再静默（2026-10-02）。

    实测定时任务钉 `AA_REFINE_BACKEND=mimo`（单端点不降级）时 429 会把章尾几行
    留成日文，原先只在日志打一行 FAIL、产物里看不出（entry_22208 L2863 即此）。
    护栏：调用失败行落 work/review_inline.txt（人工补清单），且重跑不追加重复。
    """

    def setUp(self):
        import tempfile
        self._orig = (R._reg_get, R._set_chain_for_work, R.refine_chapter)
        self.dir = tempfile.mkdtemp(prefix="failtrace_")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for sub in ("aa", "汉化", "work"):
            os.makedirs(os.path.join(self.dir, sub), exist_ok=True)
        lines = ["# 标题", " 【和真】あざーっす、この怒りはやはり"]
        for p in (os.path.join(self.dir, "aa", "entry_900001.txt"),
                  os.path.join(self.dir, "汉化", "entry_900001_汉化.txt")):
            with io.open(p, "w", encoding="utf-8", newline="") as f:
                f.write(NL.join(lines))
        self.cfg = {"aa_dir": os.path.join(self.dir, "aa"),
                    "out_dir": os.path.join(self.dir, "汉化"),
                    "work_dir": os.path.join(self.dir, "work")}

    def tearDown(self):
        (R._reg_get, R._set_chain_for_work, R.refine_chapter) = self._orig

    def _run(self, edits):
        stats = {"restore": 0, "rebuild": 0, "poly": 0, "total": 0}
        R._reg_get = lambda w: dict(self.cfg)
        R._set_chain_for_work = lambda w: "stub"
        R.refine_chapter = lambda c, b, w, r: (b, dict(stats), list(edits))
        R.process_work("dummy", eids=["900001"], do_write=True, force=True)
        p = os.path.join(self.cfg["work_dir"], "review_inline.txt")
        return io.open(p, encoding="utf-8").read() if os.path.exists(p) else ""

    def test_call_failure_is_recorded(self):
        txt = self._run([(1, " 【和真】あざーっす、この怒りはやはり", " 【和真】谢谢，这份怒火果然还是",
                          " 【和真】谢谢，这份怒火果然还是", "rebuild", False,
                          "tail_translate_err:429 后端链全部失败")])
        self.assertIn("entry_900001.txt | line 1 |", txt)
        self.assertIn("[refine-fallback", txt)

    def test_dry_reject_not_recorded(self):
        # 人工清单类（mixed_missed_manual）不是调用失败 → 不写噪音
        txt = self._run([(1, " src", " dst", " dst", "manual_mixed", False, "mixed_missed_manual")])
        self.assertEqual(txt, "")

    def test_rerun_does_not_duplicate(self):
        ed = [(1, " src", " dst", " dst", "rebuild", False, "tail_translate_err:429")]
        self._run(ed)
        first = self._run(ed)
        second = self._run(ed)
        self.assertEqual(first, second)                       # 去重键生效
        self.assertEqual(first.count("entry_900001.txt | line 1 |"), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)

