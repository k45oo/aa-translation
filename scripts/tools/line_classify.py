# -*- coding: utf-8 -*-
"""行分类器: raw HTML → 每行类型(header/text/art/mixed)→ line_meta.json

用法: python scripts/tools/line_classify.py --work <id> [--chapter 子串]
输出: <work_dir>/line_meta/<base>.json   {完整txt行号: "header"|"text"|"art"|"mixed"}

设计: 只在 extract 阶段信息最全时判定一次,渲染/校验全流程复用。
- 帖头行: 来自解析器 <dt>(100% 准确,不做正则猜)——用户行绝不加大
- 内容行: 字符段分析(艺术段 vs 文本段)——混合行(AA+台词同行)标 mixed,不加大
独立于翻译管线: 不修改 scripts/common|sites|works 任何文件,只读 raw。
"""
import importlib, json, os, re, sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
sys.path.insert(0, os.path.join(BASE, "scripts", "works"))

import registry
from aa_common import ART_HIRA, ART_KATA, ART_KANJI

EID_FROM_FNAME = re.compile(r'(?:entry_)?(\d+)')

# 艺术符号(与 aa_to_images 同源,独立维护避免跨文件 import 链)
ART_SYMS = set('│｜┃／＼＿￣─━┌┐└┘├┤┬┴┼■□◆◇△▲▽▼○●◎※〒§☆★→←↑↓∴∵♂♀╋╂╇╅╆┏┓┗┛┣┫┳┻|＝≠≡≒∞〓´¨¯°')
# ASCII 标点在 AA 里是笔画(- . / = _ ~ 等);ASCII 字母数字算文本(台词常见)
ASCII_PUNCT = set('!@#$%^&*()-_=+[]{};:\'" ,.<>/?\\`~')


KANA_RE = re.compile(r'[ぁ-んァ-ヿー]')

def is_art_char(c):
    o = ord(c)
    if 0xFF61 <= o <= 0xFF9F:          # 半角片假名 = AA 艺术主力
        return True
    if c in ASCII_PUNCT:               # ASCII 标点 = AA 笔画
        return True
    if KANA_RE.match(c):               # 全角假名按文本段处理(ノ在シノハユ是词,在AA表情是画)
        return False
    return c in ART_SYMS or c in ART_KANJI


def classify_content(row):
    """内容行分类: 字符段分析(空格不断段)
    text: 纯文本(台词/规则,≥2字即 text——短台词如'奈良！''太好了'必须加大);
    art: 纯艺术/单字装饰; mixed: 艺术段+文本段并存(不加大)
    2026-08-14 用户反馈: 用户楼加大优先级最高,2字短台词(奈良！/太好了)此前被
    text_total<5 误判 art 不加大——下限从 5 降到 2(单字装饰仍是 art)"""
    if not row.strip():
        return 'blank'
    segs = []
    for ch in row:
        if ch.isspace():
            continue
        cls = 'art' if is_art_char(ch) else 'text'
        if segs and segs[-1][0] == cls:
            segs[-1][1] += 1
        else:
            segs.append([cls, 1])
    art_total = sum(n for c, n in segs if c == 'art')
    text_total = sum(n for c, n in segs if c == 'text')
    if text_total == 0:
        return 'art'
    if text_total < 2:                 # 单字装饰(装饰性单字)→ 不加大
        return 'art'
    if art_total == 0:
        return 'text'
    art_max = max((n for c, n in segs if c == 'art'), default=0)
    text_max = max((n for c, n in segs if c == 'text'), default=0)
    if art_max >= 2 and text_max >= 3:       # 真实混排 → mixed(不加大)
        return 'mixed'
    return 'text' if text_total >= art_total else 'art'   # 单字符点缀归主体


def classify_raw(site, raw, txt_lines):
    """对单个 raw: 返回 {行号: type}(行号与 build_txt 行序一致)"""
    seg = site.extract_article(raw)
    posts = site.parse_posts(seg)
    meta = {}
    n = 3                      # 第1行=#标题, 第2行=#原帖地址, 第3行起为帖子内容
    for header, rows in posts:
        meta[n] = 'header'     # <dt> 帖头,100% 准确,绝不加大
        n += 1
        for row, color in rows:
            meta[n] = classify_content(row)
            n += 1
    if n - 1 != len(txt_lines):
        print(f'  警告: 行数不齐 {n-1} vs txt {len(txt_lines)},跳过')
        return None
    return meta


def main():
    args = sys.argv[1:]
    work = args[args.index('--work') + 1] if '--work' in args else None
    only = None
    if '--chapter' in args:
        only = args[args.index('--chapter') + 1]
    # 支持 --chapter 多值(空格分隔多个 eid)
    chapters = []
    if '--chapter' in args:
        idx = args.index('--chapter')
        chapters = [a for a in args[idx+1:] if not a.startswith('--')]
    cfg = registry.get(work)
    site = importlib.import_module('sites.' + cfg['site'])
    raw_dir, work_dir = cfg['raw_dir'], cfg['work_dir']
    meta_dir = os.path.join(work_dir, 'line_meta')
    os.makedirs(meta_dir, exist_ok=True)
    files = sorted(f for f in os.listdir(raw_dir) if f.endswith('.html'))
    if chapters:
        files = [f for f in files if any(c in f for c in chapters)]
    for fname in files:
        raw = open(os.path.join(raw_dir, fname), encoding='utf-8', errors='replace').read()
        m = EID_FROM_FNAME.search(fname)
        eid = m.group(1) if m else ''
        try:
            txt_lines, _, _ = site.extract(raw, eid, fname)
        except Exception as e:
            print(f'{fname}: extract 失败 {e},跳过', flush=True)
            continue
        if not txt_lines:
            continue
        base = f'entry_{eid}' if cfg['name_style'] == 'entry_eid' else os.path.basename(fname).replace('.html', '')
        meta = classify_raw(site, raw, txt_lines)
        if meta is None:
            continue
        with open(os.path.join(meta_dir, base + '.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False)
        cnt = {}
        for v in meta.values():
            cnt[v] = cnt.get(v, 0) + 1
        print(f'{base}: {cnt}', flush=True)
    print('ALL DONE')


if __name__ == '__main__':
    main()
