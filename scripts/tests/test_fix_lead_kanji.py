# -*- coding: utf-8 -*-
"""定向补译工具（fix_lead_kanji_miss）测试：mock call_api，零 API、零产物写入。

覆盖：
- 命中清单行 → 段级协议补译 → 宽度/art 列位/漏字清除 → 通过
- 模型原样照抄 → 判失败不写回
- dry-run 不落盘；--apply 落盘 + 整章备份（写操作在临时目录）
- 漏字仍在 → miss_remains 拒绝
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(TESTS_DIR)
ROOT = os.path.dirname(SCRIPTS)
for sub in ('', 'common', 'works', 'tools', 'fix'):
    sys.path.insert(0, os.path.join(SCRIPTS, sub))

import aa_translate as T          # noqa: E402
import fix_lead_kanji_miss as F   # noqa: E402


class _FakeAPI:
    """替身 call_api：按调用序号返回预设译文（SYSTEM_MIXED 协议格式）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call_api(self, system, body, **kw):
        self.calls.append((system, body))
        if not self.responses:
            return ''
        return self.responses.pop(0)


class TestRepairRow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='hermes-test-leadkanji-')
        self.work = os.path.join(self.tmp, 'aaManga', 'wk')
        for d in ('aa', '汉化', 'work/line_meta_v2'):
            os.makedirs(os.path.join(self.work, d), exist_ok=True)
        self.cfg = {'aa_dir': os.path.join(self.work, 'aa'),
                    'out_dir': os.path.join(self.work, '汉化'),
                    'work_dir': os.path.join(self.work, 'work')}
        # 作品术语表是翻译硬前置（无表即 SystemExit）→ 临时作品也放一份最小表
        with open(os.path.join(self.cfg['work_dir'], 'glossary_wk.json'), 'w',
                  encoding='utf-8') as f:
            json.dump({'schema': 'AA翻译作品术语表 v2',
                       'glossary': [['やる夫', '亚鲁欧']]}, f, ensure_ascii=False)
        self.orig_api = T.call_api
        self.orig_work_id = T.WORK_ID
        self.orig_aa_dir = T.AA_DIR
        self.orig_registry_get = F.registry.get      # 测试里会被替换成临时作品

    def _patch_registry(self):
        F.registry.get = lambda w: self.cfg      # noqa: E731

    def tearDown(self):
        T.call_api = self.orig_api
        T.WORK_ID = self.orig_work_id
        T.AA_DIR = self.orig_aa_dir
        F.registry.get = self.orig_registry_get      # 复原，防污染其它测试（曾致 test_line_meta 3 errors）
        T._glossary_pairs_cache = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, base, src_lines, tr_lines, meta=None):
        with open(os.path.join(self.cfg['aa_dir'], base + '.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(src_lines))
        with open(os.path.join(self.cfg['out_dir'], base + '_汉化.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(tr_lines))
        if meta is not None:
            with open(os.path.join(self.cfg['work_dir'], 'line_meta_v2', base + '.json'),
                      'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False)

    def _rows(self, base):
        return [{'work': 'wk', 'base': base, 'line': 1,
                 'src': '一般的だって話だね。', 'tr': '一也是一般情况吧。',
                 'miss': [{'col': 0, 'jp': '一'}]}]

    def test_repaired_when_model_translates(self):
        src = '一般的だって話だね。'
        tr = '一也是一般情况吧。'
        self._write('entry_1', [src], [tr])
        F.registry.get = lambda w: self.cfg          # 免 registry
        self.fake = _FakeAPI(['00000_0|一般来说是这样的。'])
        T.call_api = self.fake.call_api
        res = F.process(self._rows('entry_1'), do_write=False)
        self.assertEqual(len(res), 1)
        self.assertTrue(res[0]['ok'], res[0])
        self.assertEqual(T.disp_width(res[0]['new']), T.disp_width(src))
        self.assertNotEqual(res[0]['new'], '一也是一般情况吧。')   # 已重译（不是旧译文）
        self.assertFalse(re.search(r'[ぁ-んァ-ヶ]', res[0]['new']))  # 无残留假名
        self.assertIn('一般', res[0]['new'])          # 译文来自模型

    def test_copy_fail_not_accepted(self):
        # 模型原样照抄 → 生产协议判 copy_fail → 不写、不通过
        src = '一般的だって話だね。'
        self._write('entry_1', [src], ['一也是一般情况吧。'])
        self._patch_registry()
        T.call_api = _FakeAPI([f'00000_0|{src}'] * 3).call_api
        res = F.process(self._rows('entry_1'), do_write=False)
        self.assertFalse(res[0]['ok'])
        self.assertEqual(res[0]['reason'], 'model_fail')

    def test_apply_writes_and_backs_up(self):
        src = '一般的だって話だね。'
        self._write('entry_1', [src], ['一也是一般情况吧。'])
        self._patch_registry()
        T.call_api = _FakeAPI(['00000_0|一般来说是这样的。']).call_api
        res = F.process(self._rows('entry_1'), do_write=True)
        self.assertTrue(res[0]['ok'])
        now = T.txt_read(os.path.join(self.cfg['out_dir'], 'entry_1_汉化.txt'))
        self.assertIn('一般', now[0])
        self.assertEqual(T.disp_width(now[0]), T.disp_width(src))
        baks = [d for d in os.listdir(self.cfg['work_dir']) if d.startswith('backup_fix_lead_kanji_')]
        self.assertEqual(len(baks), 1)
        bak_lines = T.txt_read(os.path.join(self.cfg['work_dir'], baks[0], 'entry_1_汉化.txt'))
        self.assertEqual(bak_lines[0], '一也是一般情况吧。')   # 备份是改前内容

    def test_repeated_runs_keep_only_two_backups(self):
        """2026-10-01 策略：同类备份目录只保留最近 2 次运行（更早的整目录自动删）。

        连跑 4 次写入（每次先复原被改坏的汉化）→ 目录数 ≤2；同一秒内重跑共用同一个目录
        （目录名 = 时间戳），所以这里只断言上界，逐次增长由 test_backup_prune 覆盖。
        """
        src = '一般的だって話だね。'
        for _ in range(4):
            self._write('entry_1', [src], ['一也是一般情况吧。'])
            self._patch_registry()
            T.call_api = _FakeAPI(['00000_0|一般来说是这样的。']).call_api
            F.process(self._rows('entry_1'), do_write=True)
        baks = sorted(d for d in os.listdir(self.cfg['work_dir'])
                      if d.startswith('backup_fix_lead_kanji_'))
        self.assertTrue(1 <= len(baks) <= 2, baks)
        bak_lines = T.txt_read(os.path.join(self.cfg['work_dir'], baks[-1], 'entry_1_汉化.txt'))
        self.assertEqual(bak_lines[0], '一也是一般情况吧。')   # 备份仍是改前内容

    def test_miss_seg_unchanged_rejected(self):
        """段级复核：覆盖漏字列的 text 段没变 → 判未修好（守门函数直测）。

        刻意不用「同列是否仍是原汉字」——`一`/`日` 在中文里也是常用字，
        `一般来说是这样的。` 是合法好译文（真机踩过这个假阳性）。
        """
        from line_classify_v2 import segment_line
        src = '一般的だって話だね。'
        segs = segment_line(src, treat_hankaku_as_text=True)
        miss = [{'col': 0, 'jp': '一'}]
        # 段变了（已翻译）→ 通过
        self.assertTrue(F._miss_seg_changed(src, '一般来说是这样的。', segs, miss))
        # 段没变（原样）→ 拒绝
        self.assertFalse(F._miss_seg_changed(src, src, segs, miss))
        # 无 text 段覆盖漏字列（例如漏字列落在 art 段里）→ 拒绝
        self.assertFalse(F._miss_seg_changed('|一|', '|一|', segment_line('|一|'), miss))

    def test_parallel_matches_serial(self):
        """并发行处理（workers=4）与串行结果一致：行级独立，写回按章串行。"""
        src = ['一般的だって話だね。', '人の心はわからない。', '一二三で行こう。']
        tr = ['一也是一般情况吧。', '人的心不明白。', '一二三走吧。']
        self._write('entry_1', src, list(tr))
        self._patch_registry()
        rows = [{'work': 'wk', 'base': 'entry_1', 'line': i + 1, 'src': src[i], 'tr': tr[i],
                 'miss': [{'col': 0, 'jp': src[i][0]}]} for i in range(3)]
        # 假模型：按请求宽度回等宽中文；第 1 行（num 00000）故意不回 → model_fail
        def api(system, body, **kw):
            out = []
            for ln in body.split('\n'):
                head, _sep, orig = ln.partition('|')
                if head.startswith('00000'):
                    continue
                out.append(f'{head}|' + '好' * (T.disp_width(orig) // 2))
            return '\n'.join(out)

        T.call_api = api
        res = F.process(rows, do_write=False, workers=4)
        self.assertEqual(len(res), 3)
        self.assertEqual(sorted(r['reason'] for r in res),
                         ['model_fail', 'repaired', 'repaired'])
        for r in res:
            if r['ok']:
                self.assertIn('好', r['new'])
                self.assertEqual(T.disp_width(r['new']), T.disp_width(r['src']))

    def test_shrink_tolerance_knob(self):
        """段宽判据口径（2026-10-10 改版）：生产默认只否决「灾难性丢内容」（比 < 0.35），
        中文天然比日文短 → 合法的短译文不再被误杀；`AA_MIXED_SHRINK_MIN_RATIO` 可加严。

        实测动机：`一度家を出たら、基本的に戻ることは無いんだ`（42 格）的中文译文
        只有 ~30 格 → 旧 4 列口径每轮判 seg_shrink → 3 轮全拒（49/170 行都是这个）。
        """
        src = '一度家を出たら、基本的に戻ることは無いんだ'
        tr = '一一旦离开家就基本不会回去了'      # 旧译文（首字日文残留）
        self._write('entry_1', [src], [tr])
        self._patch_registry()
        rows = [{'work': 'wk', 'base': 'entry_1', 'line': 1, 'src': src, 'tr': tr,
                 'miss': [{'col': 0, 'jp': '一'}]}]
        zh = '一旦离开这个家就基本不会回来了'     # 宽 30，比原段窄 12（比 0.71）
        self.assertEqual(T.disp_width(src) - T.disp_width(zh), 12)
        # ① 生产默认（否决下限 0.35）→ 接受；旧 4 列口径这里会被误杀
        T.call_api = _FakeAPI([f'00000_0|{zh}']).call_api
        res = F.process(rows, do_write=False)
        self.assertTrue(res[0]['ok'], res[0])
        self.assertEqual(T.disp_width(res[0]['new']), T.disp_width(src))
        # ② 把否决下限加严到 0.9 → 同一译文被判失败（段退回原文 → 目标段没变 = model_fail）
        os.environ['AA_MIXED_SHRINK_MIN_RATIO'] = '0.9'
        try:
            T.call_api = _FakeAPI([f'00000_0|{zh}'] * 3).call_api
            res2 = F.process(rows, do_write=False)
            self.assertFalse(res2[0]['ok'])
            self.assertEqual(res2[0]['reason'], 'model_fail')
        finally:
            os.environ.pop('AA_MIXED_SHRINK_MIN_RATIO', None)

    def test_missing_glossary_skipped_not_abort(self):
        """作品术语表是硬前置（无表不译）→ 该作品逐行标 no_glossary，但不中断整批。"""
        os.remove(os.path.join(self.cfg['work_dir'], 'glossary_wk.json'))
        self._write('entry_1', ['一般的だって話だね。'], ['一也是一般情况吧。'])
        self._patch_registry()
        T.call_api = _FakeAPI(['00000_0|一般来说是这样的。']).call_api
        res = F.process(self._rows('entry_1'), do_write=True)
        self.assertFalse(res[0]['ok'])
        self.assertEqual(res[0]['reason'], 'no_glossary')
        # 未写回、未建备份
        now = T.txt_read(os.path.join(self.cfg['out_dir'], 'entry_1_汉化.txt'))
        self.assertEqual(now[0], '一也是一般情况吧。')
        self.assertEqual([d for d in os.listdir(self.cfg['work_dir'])
                          if d.startswith('backup_fix_lead_kanji_')], [])

    def test_missing_file_skipped(self):
        self._patch_registry()
        res = F.process(self._rows('entry_none'), do_write=False)
        self.assertFalse(res[0]['ok'])
        self.assertEqual(res[0]['reason'], 'file_missing')


if __name__ == '__main__':
    unittest.main()
