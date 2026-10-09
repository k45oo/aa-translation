# -*- coding: utf-8 -*-
"""M4a line_meta 接入单元测试。零依赖 unittest。
运行：cd scripts/tests && python -m unittest test_line_meta -v"""
import json, os, sys, tempfile, shutil, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import aa_translate as T


def _write_meta(meta_dir, base, raw):
    os.makedirs(meta_dir, exist_ok=True)
    with open(os.path.join(meta_dir, base + '.json'), 'w', encoding='utf-8') as f:
        json.dump(raw, f)


class TestLoadMeta0based(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = os.path.join(self.tmp, 'line_meta_v2')
        os.makedirs(self.dir, exist_ok=True)
        self.base = 'entry_3463'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plain_kind_format(self):
        # {1-based行号: "kind"}，前2行#标题/#地址无key
        _write_meta(self.dir, self.base, {"3": "header", "4": "text", "5": "art"})
        m = T.load_meta_0based(self.dir, self.base)
        # 1-based 3 → 0-based 2；load_meta_0based 返回 (kind, reason)（2026-08-18 反哺⑨）
        self.assertEqual(m.get(2), ('header', ''))
        self.assertEqual(m.get(3), ('text', ''))
        self.assertEqual(m.get(4), ('art', ''))
        # idx 0/1 (#标题/#地址) 无映射
        self.assertNotIn(0, m)
        self.assertNotIn(1, m)

    def test_dict_kind_format(self):
        _write_meta(self.dir, self.base, {"3": {"kind": "mixed", "confidence": 0.8}, "4": {"kind": "text"}})
        m = T.load_meta_0based(self.dir, self.base)
        self.assertEqual(m.get(2), ('mixed', ''))
        self.assertEqual(m.get(3), ('text', ''))

    def test_missing_file_returns_none(self):
        self.assertIsNone(T.load_meta_0based(self.dir, 'nope'))

    def test_corrupt_file_returns_none(self):
        with open(os.path.join(self.dir, self.base + '.json'), 'w', encoding='utf-8') as f:
            f.write('{not json')
        self.assertIsNone(T.load_meta_0based(self.dir, self.base))

    def test_bad_keys_skipped(self):
        _write_meta(self.dir, self.base, {"abc": "text", "0": "art", "3": "text"})
        m = T.load_meta_0based(self.dir, self.base)
        self.assertIn(2, m)      # key "3" → 0-based 2
        self.assertNotIn(-1, m)  # key "0" → -1 被丢弃


class TestIsTranslatable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = os.path.join(self.tmp, 'line_meta_v2')
        os.makedirs(self.dir, exist_ok=True)
        _write_meta(self.dir, 'entry_3463', {"3": "header", "4": "text", "5": "art", "6": "mixed"})
        self.meta = T.load_meta_0based(self.dir, 'entry_3463')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_art_never_flagged(self):
        self.assertFalse(T.is_translatable(4, ' 纯艺术行', self.meta))  # idx4=art
    def test_text_always_flagged(self):
        self.assertTrue(T.is_translatable(3, ' 台词行 こんにちは', self.meta))  # idx3=text
    def test_mixed_fallback(self):
        # idx5=mixed，回退 is_text_line
        self.assertEqual(T.is_translatable(5, ' 台词 こんにちは', self.meta),
                         T.is_text_line(' 台词 こんにちは'))
    def test_header_fallback(self):
        self.assertFalse(T.is_translatable(2, '1 ： ◆xxx', self.meta))  # idx2=header
    def test_meta_none_fallback(self):
        # meta=None 时与 is_text_line 完全一致
        line = ' テストです'
        self.assertEqual(T.is_translatable(0, line, None), T.is_text_line(line))
    def test_idx_not_in_meta_fallback(self):
        # idx 7 不在 meta → fallback
        line = ' あいうえお'
        self.assertEqual(T.is_translatable(7, line, self.meta), T.is_text_line(line))


class TestArtDefense(unittest.TestCase):
    """终审#5：kind=art 含连续 3+ 非艺术假名 → 回退 is_text_line。"""
    def setUp(self):
        self.meta = {0: 'art', 1: 'art', 2: 'art'}
    def test_art_with_real_kana_dialogue(self):
        # 真台词 'キタキタ'(4字) → 回退进模型
        self.assertTrue(T.is_translatable(0, ' お、キタキタｗ', self.meta))
    def test_art_pure_texture_kana(self):
        # 艺术纹理假名(被 kana_runs 排除) → 仍跳过
        self.assertFalse(T.is_translatable(1, ' 純アート ﾉﾐﾑﾊﾞｯ', self.meta))
    def test_art_short_kana(self):
        # 短假名(不足3字) → 跳过
        self.assertFalse(T.is_translatable(2, ' はいｗ', self.meta))
    def test_art_pure_ascii(self):
        # 纯 ASCII 艺术 → 跳过
        self.assertFalse(T.is_translatable(0, ' ／＼│／', self.meta))


class TestIsCopyFailure(unittest.TestCase):
    def test_exact_copy_with_kana(self):
        # 含假名且逐字一致 → 判失败
        self.assertTrue(T.is_copy_failure(' まるで奴隷', ' まるで奴隷'))
    def test_exact_copy_whitespace_insensitive(self):
        # 尾部空白差异不误判
        self.assertTrue(T.is_copy_failure('まるで奴隷', 'まるで奴隷  '))
    def test_translated_no_fail(self):
        # 已翻译 → 不判失败
        self.assertFalse(T.is_copy_failure(' まるで奴隷', ' 简直像奴隶'))
    def test_partial_keep_name_no_fail(self):
        # 角色名保留但整体已译 → 不判失败（非整行照抄）
        self.assertFalse(T.is_copy_failure(' キングは勝った', ' キング赢了'))
    def test_no_kana_src_no_fail(self):
        # 原文无假名（纯艺术/标点）→ 不判失败
        self.assertFalse(T.is_copy_failure(' ﾉﾐﾑﾊﾞｯ', ' ﾉﾐﾑﾊﾞｯ'))


class TestGlossaryHint(unittest.TestCase):
    def tearDown(self):
        os.environ.pop('AA_USE_GLOSSARY', None)
        T._glossary_pairs_cache = None  # 重置缓存

    def test_default_on_returns_terms(self):
        # 翻转后默认开启：未设 env 时返回术语说明
        os.environ.pop('AA_USE_GLOSSARY', None)
        h = T.glossary_prompt_hint()
        self.assertIn('【术语表】', h)
        self.assertIn('乙でした', h)      # 常用-梗 全量注入
        self.assertNotIn('染灰魔女', h)   # 角色对已迁出 roles.json, 不在全局注入

    def test_flag_off_returns_empty(self):
        # 显式 AA_USE_GLOSSARY=0 关闭 → 空串
        os.environ['AA_USE_GLOSSARY'] = '0'
        self.assertEqual(T.glossary_prompt_hint(), '')

    def test_flag_on_returns_terms(self):
        os.environ['AA_USE_GLOSSARY'] = '1'
        h = T.glossary_prompt_hint()
        self.assertIn('【术语表】', h)
        self.assertIn('→', h)
        self.assertIn('乙でした', h)      # 全局条目(草生える已于2026-09人工删除)
        self.assertNotIn('染灰魔女', h)   # 角色对迁出

    def test_flag_on_missing_glossary_empty(self):
        os.environ['AA_USE_GLOSSARY'] = '1'
        orig = T._GLOSSARY_JSON
        T._GLOSSARY_JSON = r'F:\nonexistent_glossary.json'
        try:
            self.assertEqual(T.glossary_prompt_hint(), '')
        finally:
            T._GLOSSARY_JSON = orig
            T._glossary_pairs_cache = None


class _FakeSite:
    """classify_raw_v2 最小假站点：正文即行列表。"""

    def __init__(self, posts):
        self._posts = posts

    def extract_article(self, raw):
        return raw

    def parse_posts(self, seg):
        return self._posts


class TestClassifyRowExpansion(unittest.TestCase):
    """行内裸换行（HTML <br> 之间夹字面 \\n）必须展开成独立行 —— 落盘 txt 会展开，
    解析端不展开则行数与 txt 不齐 → 整章 meta 被跳过（2026-09-13 entry_16812 事故）。"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
        global LC
        import line_classify_v2 as LC

    HEADER = '1461 ： ほびー ◆hpknKoAjeI ： 2021/03/06(土) 16:02:39 ID:34DDZTR6'
    POSTS = [(HEADER, ['こんにちは\nさようなら', '｜ア｜　｜イ｜'])]

    def test_rows_with_embedded_newline_expanded(self):
        # 落盘 txt 口径：1 行含 2 个裸换行 → 展开成 2 行，总行数 6
        txt = ['# 标题', '# 原帖地址: https://x', self.HEADER, 'こんにちは', 'さようなら', '｜ア｜　｜イ｜']
        meta = LC.classify_raw_v2(_FakeSite(self.POSTS), 'raw', txt)
        self.assertIsNotNone(meta, '行内换行未展开 → 整章 meta 被跳过')
        self.assertEqual(max(int(k) for k in meta), len(txt))
        self.assertEqual(meta[4]['kind'], 'text')     # 展开后的第 1 子行独立判定
        self.assertEqual(meta[5]['kind'], 'text')
        self.assertEqual(meta[3]['kind'], 'header')

    def test_unexpanded_txt_still_rejected(self):
        # 未展开的 txt（5 行）与解析口径不符 → 仍返回 None（保持"不静默产出错位 meta"）
        txt = ['# 标题', '# 原帖地址: https://x', self.HEADER, 'こんにちは\nさようなら', '｜ア｜　｜イ｜']
        self.assertIsNone(LC.classify_raw_v2(_FakeSite(self.POSTS), 'raw', txt))

    def test_blank_subline_kind_blank(self):
        posts = [(self.HEADER, ['Ａ\n'])]
        txt = ['# 标题', '# 原帖地址: https://x', self.HEADER, 'Ａ', '']
        meta = LC.classify_raw_v2(_FakeSite(posts), 'raw', txt)
        self.assertEqual(meta[5]['kind'], 'blank')

    def test_site_extract_article_needs_eid(self):
        """aamatome_2nt/blog_2nt/matomemo3/mukankei_fc2 的 extract_article 需要 eid
        —— 旧实现无条件单参调用 → TypeError → 那 5 个作品 meta 永远为空。"""
        seen = {}
        header = self.HEADER

        class _SiteNeedsEid:
            def extract_article(self, raw, eid):
                seen['eid'] = eid
                return raw

            def parse_posts(self, seg):
                return [(header, ['｜ア｜'])]

        site = _SiteNeedsEid()
        txt = ['# 标题', '# 原帖地址: https://x', self.HEADER, '｜ア｜']
        meta = LC.classify_raw_v2(site, 'raw', txt, eid='16812')
        self.assertIsNotNone(meta)
        self.assertEqual(seen.get('eid'), '16812')


class TestGlossaryWorkMode(unittest.TestCase):
    """WORK_ID 模式: 作品术语表注入 + 按章过滤 (2026-09-01 三级重构)。"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "works"))
        from _work_guard import work_cfg
        cls.cfg = work_cfg("kikkoman", ("aa_dir", "work_dir"), "作品术语表注入用例")
        # 备份并设置 WORK_ID
        cls._orig_work_id = T.WORK_ID
        cls._orig_aa_dir = T.AA_DIR
        T.WORK_ID = "kikkoman"
        T.AA_DIR = cls.cfg["aa_dir"]

    @classmethod
    def tearDownClass(cls):
        T.WORK_ID = cls._orig_work_id
        T.AA_DIR = cls._orig_aa_dir
        T._glossary_pairs_cache = None

    def tearDown(self):
        os.environ.pop('AA_USE_GLOSSARY', None)
        T._glossary_pairs_cache = None

    def test_work_mode_injects_manual_and_common(self):
        h = T.glossary_prompt_hint()
        self.assertIn('不做夫', h)        # manual 作品术语(やらない夫→不做夫)
        self.assertIn('乙でした', h)      # 该词在 kikkoman 作品表内(2026-09-07 起只读作品表, 全局不再注入)
        self.assertIn('【术语表】', h)

    def test_work_mode_role_injected(self):
        # kikkoman 语料含やる夫 → 已确认角色对注入(亚鲁欧, manual 确认态)
        h = T.glossary_prompt_hint()
        self.assertIn('亚鲁欧', h)

    def test_work_mode_chapter_filter(self):
        # 选一章不含 やる夫 的章节 → 角色对按章滤掉(全量常用仍注入)
        import os
        aa = self.cfg["aa_dir"]
        base = None
        for fn in os.listdir(aa):
            if fn.endswith(".txt") and "_汉化" not in fn:
                txt = open(os.path.join(aa, fn), encoding="utf-8", errors="replace").read()
                if "やる夫" not in txt:
                    base = fn[:-4]
                    break
        if base is None:
            self.skipTest("无不含やる夫的章节")
        h = T.glossary_prompt_hint(base=base)
        self.assertNotIn('亚鲁欧', h)     # 角色对按章滤掉
        self.assertIn('乙でした', h)      # 常用对仍全量注入


class TestSegmentLineKanjiEdge(unittest.TestCase):
    """**首文字/尾文字漏译**：ART_KANJI 汉字（一/二/王/人/十/七…）被 is_art_char 判 art，
    落在行首（或行尾）时旧规则（要求 art 段两侧都是 text 段）永远合不回去 → 该汉字**不进
    模型、原样保留**，译文里就剩日文汉字（当事人报的「一/二/王/人 开头漏译」）。
    新增判据 = 汉字与相邻 text 段**紧贴**（无空白相隔）且该 text 段含真台词 → 只搬汉字
    连串（宽 ≤6 格），边框/空白留在 art 段。反例（画出来的汉字）必须不并。
    修复位置: tools/line_classify_v2.py `_merge_narrow_kanji_art`（segments 生成端，
    翻译/校对/台词提取全部消费它）。"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
        global LC
        import line_classify_v2 as LC

    def segs(self, row):
        return LC.segment_line(row)

    def texts(self, row):
        s = self.segs(row)
        return [row[x['start']:x['end']] for x in s if x['kind'] == 'text']

    def arts(self, row):
        s = self.segs(row)
        return [row[x['start']:x['end']] for x in s if x['kind'] == 'art']

    def assert_cover(self, row):
        """段必须完整覆盖整行、无缝无叠，且 src_width 之和 == 整行显示宽度
        （rebuild_mixed_line 按段拼回，缺列会丢字符）。"""
        s = self.segs(row)
        self.assertEqual(s[0]['start'], 0)
        self.assertEqual(s[-1]['end'], len(row))
        for a, b in zip(s, s[1:]):
            self.assertEqual(a['end'], b['start'])
        self.assertEqual(sum(x['src_width'] for x in s), LC.disp_width(row))

    def test_lead_kanji_merged_into_text(self):
        # 一昨日: 旧版 art『一』+text『昨日の話だ』（首字漏译）
        self.assertEqual(self.texts('一昨日の話だ'), ['一昨日の話だ'])
        self.assert_cover('一昨日の話だ')

    def test_lead_kanji_after_indent(self):
        # 行首缩进（U+3000）留在 art 段, 汉字进 text 段
        self.assertEqual(self.texts('　　王様だぞ'), ['王様だぞ'])
        self.assertEqual(self.arts('　　王様だぞ'), ['　　'])

    def test_lead_kanji_after_frame(self):
        row = '┤一つの嘘だ├'
        self.assertEqual(self.texts(row), ['一つの嘘だ'])
        self.assertEqual(self.arts(row), ['┤', '├'])
        self.assert_cover(row)

    def test_lead_kanji_after_frame_and_inner_kanji_art(self):
        # 一昨日: 原段序列 art『一』+text『昨』+art『日』+text『の話だ』（日 也在 ART_KANJI）
        # → ① 先把句中碎段合成『昨日の話だ』, ② 才有真台词邻居可判（顺序不能颠倒）
        row = '　　一昨日の話だ'
        self.assertEqual(self.texts(row), ['一昨日の話だ'])

    def test_fragment_neighbour_not_merged(self):
        # 邻居是绘图碎片（单字/无假名无汉字）→ 不并, 防把画里的汉字拖进模型
        # 『人』紧贴单字段『ｘ』: 段置信度 0.4 / 正文 1 字 → 判非台词 → 不并
        # （2026-10-10 P1 起该碎片段本身也降为 art：全行都是笔画、没有任何台词段）
        row = '人ｘ'
        self.assertIn('人', ''.join(self.arts(row)))
        self.assertEqual(self.texts(row), [])

    def test_short_dialogue_neighbour_merged(self):
        # 邻居是真台词（含假名 → conf 0.92）→ 并（哪怕行很短）
        row = '人明日は晴れる'
        self.assertEqual(self.texts(row), ['人明日は晴れる'])

    def test_lead_kanji_run_after_frame(self):
        row = '＼一人で行く／'
        self.assertEqual(self.texts(row), ['一人で行く'])
        self.assertEqual(self.arts(row), ['＼', '／'])

    def test_lead_kanji_hankaku_frame(self):
        row = '||人斬りだ||'
        self.assertEqual(self.texts(row), ['人斬りだ'])
        self.assertEqual(self.arts(row), ['||', '||'])

    def test_tail_kanji_merged_into_text(self):
        self.assertEqual(self.texts('早く行け一'), ['早く行け一'])

    def test_lead_merged_seg_is_sent_to_model(self):
        # 合并后段置信度按新内容重算（含汉字 → ≥0.65）, 否则等于没修（仍不送模型）
        s = self.segs('　　王様だぞ')
        t = [x for x in s if x['kind'] == 'text'][0]
        self.assertNotIn('confidence', t)

    def test_drawn_kanji_with_spaces_not_merged(self):
        # 画出来的横线『一』两侧是空白 → 不并（防把画面送进模型）
        row = '　一　おはよう'
        self.assertIn('一', ''.join(self.arts(row)))
        self.assertEqual(self.texts(row), ['おはよう'])

    def test_drawn_kanji_between_frames_not_merged(self):
        row = '　｜一｜言いたいことがある｜'
        self.assertIn('一', ''.join(self.arts(row)))
        self.assertEqual(self.texts(row), ['言いたいことがある'])

    def test_wide_kanji_run_not_merged(self):
        # 汉字连串宽 >6 格 → 当画面纹理, 不并（一行 7 个艺术汉字不可能是词头）
        row = '　一丁七丈三上下おはよう'
        self.assertIn('一丁七丈三上下', ''.join(self.arts(row)))

    def test_interior_kanji_still_three_in_one(self):
        # 原行为（2026-08-11）: text-art-text 三合一不回归
        row = '│もう一度言え│'
        self.assertEqual(self.texts(row), ['もう一度言え'])
        self.assertEqual(self.arts(row), ['│', '│'])

    def test_pure_art_line_unchanged(self):
        row = '───────'
        self.assertEqual(self.texts(row), [])
        self.assert_cover(row)

    # ---- 2026-10-10 P1：无决定性台词证据的 text 段降为 art（doc/段级协议漏译根因与优化方案） ----

    def test_decorative_text_seg_demoted_to_art(self):
        # 纯装饰 text 段（∨：无假名无汉字）在旧版是 kind=text（残留检测当台词看）→ 现降为 art
        row = 'おはよう｜∨　｜'
        self.assertIn('∨', ''.join(self.arts(row)))
        self.assertEqual(self.texts(row), ['おはよう'])

    def test_ascii_letter_seg_demoted_to_art(self):
        # ASCII 线条字母作笔画（j/V/r）——旧版 is_art_char 放行 → 降级
        row = '｜jVr｜やる夫'
        self.assertIn('jVr', ''.join(self.arts(row)))
        self.assertEqual(self.texts(row), ['やる夫'])

    def test_real_dialogue_seg_kept_as_text(self):
        # 真台词（含非装饰平假名）不受影响
        row = '　｜また来ちゃいました｜'
        self.assertEqual(self.texts(row), ['また来ちゃいました'])

    def test_single_kana_dialogue_kept_as_text(self):
        # 单字/短台词（え？）由反例保护（_max_real_dialogue_run>=1）保住，不降级
        row = '｜え？｜'
        self.assertEqual(self.texts(row), ['え？'])

    def test_demotion_keeps_segment_coverage(self):
        # 降级只改 kind：段仍覆盖整行、宽度不变（rebuild/art 列位守恒的前提）
        row = '∨∧　　おはようございます　　＞＜｜'
        self.assert_cover(row)
        self.assertEqual(sum(s['src_width'] for s in self.segs(row)), T.disp_width(row))


if __name__ == '__main__':
    unittest.main()
