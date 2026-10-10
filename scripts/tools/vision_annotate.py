# -*- coding: utf-8 -*-
"""视觉辅助分类: 存疑块红框标注渲染 (2026-08-16)
流程:
1. 规则粗筛存疑行(误嵌嫌疑: spans非空且art>35%; 漏嵌嫌疑: spans空且含中文art<25%)
2. 渲染块图时对存疑行画红框+编号(编号=块内行序号)
3. 输出标注图 → 供视觉模型判定"编号N行是台词还是画面"
用法: python scripts/tools/vision_annotate.py --chapter entry_8532 --out temp/vision_ann
"""
import argparse, os, json, sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
sys.path.insert(0, os.path.join(BASE, 'scripts', 'common'))
sys.path.insert(0, os.path.join(BASE, 'scripts', 'works'))
sys.path.insert(0, os.path.join(BASE, 'scripts', 'tools'))

import registry
from sites import fc2 as site
import aa_to_images as A
from PIL import Image, ImageDraw


def suspect_rows(block, lines, v2):
    """块内存疑行 → [(块内行序号, 完整行号, 嫌疑类型)]"""
    out = []
    for i, (ln, t) in enumerate(block):
        v = v2.get(str(ln + 1))
        if not isinstance(v, dict):
            continue
        ns = [c for c in t if not c.isspace()]
        if not ns:
            continue
        art = sum(1 for c in ns if c in A.ART_SYMS or c in A.ART_KATA or c in A.ART_HIRA
                  or c in A.ART_KANJI or c in 'ヽ_ノー=―')
        artpct = art / len(ns)
        sp = A.v2_text_spans(v, t)
        has_han = bool(A._HAN2_RE.search(t))
        # 帖头行跳过(不嵌字,不该送视觉)
        if site.render_header(t):
            continue
        if sp and artpct > 0.35:
            out.append((i, ln + 1, '误嵌?', artpct))
        elif not sp and has_han and artpct < 0.25:
            out.append((i, ln + 1, '漏嵌?', artpct))
    return out


def render_annotated(block, fs, img_w, site, cm, font_path, meta, v2, out_path, suspects):
    """渲染块图(裁剪后) + 存疑行红框编号标注(视觉辅助,2026-08-16)"""
    img = A.render_block(block, site, cm, fs, img_w, font_path,
                         min_width=0, text_bonus=0, meta=meta,
                         freeze_post=A.post_is_op(block, site),
                         embed_v2=v2)
    img = img.convert('RGB')
    d = ImageDraw.Draw(img)
    # 行 y 坐标: 与 render_block 同逻辑(帖头2倍行高)
    line_h = int(fs * A.LINE_RATIO)
    rows_y = []
    y = A.PAD
    for ln, t in block:
        if not t.strip():
            y += line_h
        elif site.render_header(t):
            y += line_h * 3
        else:
            y += line_h
        rows_y.append((ln, y - line_h))
    for i, tln, kind, pct in suspects:
        y0 = rows_y[i][1]
        d.rectangle([A.PAD - 2, y0 - 2, img.size[0] - A.PAD + 2, y0 + line_h + 2],
                    outline=(255, 0, 0), width=2)
        d.text((A.PAD - 2, max(0, y0 - 16)), f'[{kind[0]}]{i}', fill=(255, 0, 0))
    img.save(out_path)
    return suspects


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', default='saki_fc2')
    ap.add_argument('--chapter', default='')
    ap.add_argument('--out', default='temp/vision_ann')
    a = ap.parse_args()

    cfg = registry.get(a.work)
    files = sorted(f for f in os.listdir(cfg['out_dir']) if f.endswith('_汉化.txt'))
    if a.chapter:
        files = [f for f in files if a.chapter in f]

    font_path = A.FONT_MAIN
    os.makedirs(a.out, exist_ok=True)

    for fn in files:
        base = fn.replace('_汉化.txt', '')
        lines = open(os.path.join(cfg['out_dir'], fn), encoding='utf-8').read().split('\n')
        rows = [(i, l) for i, l in enumerate(lines) if not l.startswith('#')]
        meta = json.load(open(os.path.join(cfg['work_dir'], 'line_meta', base + '.json'), encoding='utf-8'))
        v2 = json.load(open(os.path.join(cfg['work_dir'], 'line_meta_v2', base + '.json'), encoding='utf-8'))
        cm = {}
        cmf = os.path.join(cfg['aa_dir'], base + '_colormap.json')
        if os.path.exists(cmf):
            cm = json.load(open(cmf, encoding='utf-8'))

        blocks, cur = [], []
        for ln, txt in rows:
            if site.render_header(txt):
                if cur:
                    blocks.append((A.post_is_op(cur, site), cur))
                cur = [(ln, txt)]
            else:
                cur.append((ln, txt))
        if cur:
            blocks.append((A.post_is_op(cur, site), cur))
        merged = A.merge_posts(A.split_long(blocks), meta=meta, site=site)

        total_suspects = 0
        for bi, b in enumerate(merged, 1):
            fs, img_w = A.block_layout(b, 600, site, False, 0, 0, meta, True, embed_v2=v2)
            suspects = suspect_rows(b, lines, v2)
            if not suspects:
                continue
            total_suspects += len(suspects)
            out_path = os.path.join(a.out, f'{base}_{bi:04d}.png')
            render_annotated(b, fs, img_w, site, cm, font_path, meta, v2, out_path, suspects)
        print(f"{base}: {len(merged)}块, {total_suspects}存疑行 → {a.out}")


if __name__ == '__main__':
    main()
