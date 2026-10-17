# -*- coding: utf-8 -*-
"""反哺③(规则版): 翻译后日文残留扫描 (2026-08-18)
用途: 复核汉化 txt 的台词行是否还有日文残留(未译/漏译)。
【降级说明 2026-08-18】: 视觉复核实测误报高(红框错位+专名误判)且"日文残留"本质是
文本任务(规则可做)→ 放弃视觉, 改纯规则扫描(零成本, 可靠)。

规则:
① 台词行(v2 kind=text/mixed)的 text 段区域(或整行)内, 含非艺术日文假名连续段(≥2)
   → 残留嫌疑
② 排除专有名词/角色名白名单(やる夫/さーきー/ルート等, 保留日文是正常行为)
③ 排除纯装饰假名(ヽノー等艺术字符)

输出: temp/vision_review_report.txt (疑似漏译清单, 供人工 review / 重译)

用法: python scripts/tools/vision_review.py --work saki_fc2 [--chapter entry_8532]
"""
import argparse, json, os, re, sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
sys.path.insert(0, os.path.join(BASE, 'scripts', 'common'))
sys.path.insert(0, os.path.join(BASE, 'scripts', 'works'))

import registry
from sites import fc2 as site
import aa_to_images as A
from aa_common import meta_exists, meta_load, list_txt, txt_read   # meta/正文透明读

# 非艺术日文假名连续段(≥2) = 残留嫌疑; 艺术假名(ヽノー等装饰)不算
ART_KANA = set('ヽノー=―ゝ丶つだりへぢづゑゐいうしふとこっッ・…‥ニミヘトソンイリムハヾレンソノ' 
               'ﾆﾉﾏﾑﾞｧｰﾚﾍｲｽﾛﾖﾈﾇﾕﾀﾋﾄﾒｴｵﾜｶｷｸｹｺｻｼｾﾁﾃﾅﾌﾎﾐﾓﾔﾕﾜﾝ'
               'ｦｨｩｪｫｬｭｮｯんツあアヘフシンシツチヨイロモルグリバ' 
               'ピビパボブベデドゼゾジズザ' )
# 专有名词/角色名(保留日文是正常行为, 非漏译)——内置常用名单 + 本作品角色(2026-08-18 清单甄别)
PROPER_NAMES = ['やる夫', 'やるお', 'さーきー', '宮永', '阿知賀', 'ルート', 'イベント',
                'レジェンゴ', 'エンジェル', 'ワールド', 'デート', 'ラッキー', 'スキル',
                'パターン', 'レベル', 'セリフ', 'ネタ', 'ダメ', 'オワタ', 'ワロタ',
                'らん', 'ちゃん', 'さん', 'くん', 'やる', 'やっ', 'まだ', 'あれ', 'それ',
                'これ', 'そう', 'もう', 'よく', 'はや', 'すご', 'ちょ', 'おは', 'こん',
                # さーきー 作品角色/术语(2026-08-18 清单甄别)
                'すこやん', 'セーラ', 'あらたそ', 'ヤッル', 'シズ', 'クロチャー', 'はたて',
                'はっちゃん', 'やっる', 'すばら', 'しゅがは', 'レジェンド', 'テルー', 'やんねえ',
                'チャンプ', 'アコチャー', 'くっころ', 'モブ', 'ドラ', 'ハギヨシ', 'クロ',
                'っちゃん', 'おもち', 'アノ', 'やん', 'てるー', 'はるる', 'トキ', 'あこ',
                'フナ', 'ころたん', 'りゅーか', 'もっさん', 'モテ', 'やんねえ香', 'あらたそー',
                'テスカトリポカ', 'ギギギー', 'ミズハス', 'はやりん', 'アナスタシア',
                'プロ', 'ホモ', 'ポン', 'ゴッ',
                # touhou_teisou 作品角色/术语(2026-08-19)
                'ちっひ', '入速出', '月村', 'レジェンド', 'エムブレム', 'セックスアンドザシティ',
                'ゴールドエクスペリエンスレクイエム', 'やっくま', 'しほ', 'まほ', 'ほむら',
                'しぽりん', '育代', '育上', 'すずか', '暁美', 'ルオ']


def residual_kana(line):
    """行内非艺术日文假名连续段(≥2) → 段列表(排除专名整段匹配)"""
    segs = re.findall(r'[\u3040-\u30ff\u30fc]{2,}', line)
    real = []
    for s in segs:
        # 段含画面假名占比高 → 画面(非残留)
        if sum(1 for c in s if c in ART_KANA) / len(s) > 0.5:
            continue
        # 专名整段匹配才排除(やる夫 排除; 单独的 やる 不排除——但 やる 常是动词词头,
        # 单独出现多为误报, 也排除常见词缀)
        if s in PROPER_NAMES:
            continue
        real.append(s)
    return real


def scan_chapter(lines, v2):
    """扫描单章 → [(完整行号, 残留段, 行内容)]"""
    out = []
    for idx, t in enumerate(lines):
        if t.startswith('#') or not t.strip():
            continue
        if site.render_header(t):
            continue
        full_ln = idx + 1
        v = v2.get(str(full_ln))
        if not isinstance(v, dict):
            continue
        kind = v.get('kind')
        if kind not in ('text', 'mixed'):
            continue
        # 行内 art 字符占比 >50% → 画面主导行, 残留扫描跳过(v2 误标 mixed 的画面行,
        # 反哺⑤ 未覆盖的; 画面假名误报源头)
        ns = [c for c in t if not c.isspace()]
        if ns:
            art = sum(1 for c in ns if c in A.ART_SYMS or c in A.ART_KATA or c in A.ART_HIRA
                      or c in A.ART_KANJI or c in 'ヽ_ノー=―')
            if art / len(ns) > 0.5:
                continue
        # 有 segments: 只查 text 段区域(mixed 行画面假名不误报)
        if v.get('segments'):
            text_regions = [(s['start'], s['end']) for s in v['segments'] if s.get('kind') == 'text']
            for s0, s1 in text_regions:
                kana = residual_kana(t[s0:s1])
                if kana:
                    out.append((full_ln, kana[0][:8], t.rstrip()[:60]))
                    break
        else:
            kana = residual_kana(t)
            if kana:
                out.append((full_ln, kana[0][:8], t.rstrip()[:60]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', default='saki_fc2')
    ap.add_argument('--chapter', default='')
    a = ap.parse_args()

    cfg = registry.get(a.work)
    files = list_txt(cfg['out_dir'], '_汉化.txt')
    if a.chapter:
        files = [f for f in files if a.chapter in f]

    all_report = []   # (base, 行号, 残留段, 行内容)
    for fn in files:
        base = fn.replace('_汉化.txt', '')
        lines = txt_read(os.path.join(cfg['out_dir'], fn))
        if not meta_exists(os.path.join(cfg['work_dir'], 'line_meta_v2'), base):
            continue
        v2 = meta_load(os.path.join(cfg['work_dir'], 'line_meta_v2'), base, {}) or {}
        hits = scan_chapter(lines, v2)
        if hits:
            print(f"{base}: {len(hits)} 行残留嫌疑", flush=True)
            for full_ln, kana, t in hits:
                all_report.append((base, full_ln, kana, t))

    print(f"\n总计: {len(all_report)} 行疑似残留 / {len(files)} 章")
    # 输出报告
    if all_report:
        os.makedirs(os.path.join(BASE, 'temp'), exist_ok=True)
        out_path = os.path.join(BASE, 'temp', 'vision_review_report.txt')
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write("# 翻译后日文残留扫描报告 (规则方案, 2026-08-18)\n")
            f.write("# 格式: 章节 | 行号 | 残留段 | 行内容\n")
            for base, ln, kana, t in all_report:
                f.write(f"{base} | {ln} | {kana} | {t}\n")
        print(f"报告: {out_path}")
        # 打印前 20 条
        for base, ln, kana, t in all_report[:20]:
            print(f"  {base} 行{ln}: '{t}' [残留:{kana}]")


if __name__ == '__main__':
    main()
