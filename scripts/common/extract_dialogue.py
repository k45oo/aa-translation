# -*- coding: utf-8 -*-
"""台词提取公共模块（2026-08-29 建立，收编三套口径）。

统一台词提取到 v3 meta（line_classify_v2）单一口径：
- kind=text → 整行为一段台词
- kind=mixed → 按 segments 取全部 text 段（不按 conf 砍，低置信段标记 confidence）
- kind=art/header/blank → 跳过
- 输出 `L<行号>[_<段号>] [主/用] <段文本>`，段文本不 strip（保留行首 U+3000），
  并记录 start/end 偏移供按行号/段字节级回填（精译/漏译扫描用）。

与 gen_chapter_summaries / gen_glossary_ai 的旧占比启发式
（对「左 art + 右台词」mixed 行失效，漏识 66.6%，见 references/dialogue-extraction-benchmark-2026-08-29.md）
不同——本模块以 line_classify_v2 的 segments 为权威，段边界现成，剥离与回填都精确。

用法: from extract_dialogue import extract_dialogue_from_meta
"""

import json
import os
import re
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_SCRIPT_DIR)
if _SCRIPTS not in sys.path:            # 仓库铁律: 向上两级到 scripts/ 根
    sys.path.insert(0, _SCRIPTS)

# 艺术假名（与 aa_common / line_classify_v2 同源；剥离装饰碎片用）
# ART_HIRA/ART_KATA 转发自 aa_common（实为 aa_translate.py:233-234 定义）
try:
    from aa_common import ART_HIRA, ART_KATA, ART_KANJI, disp_width
except ImportError:  # 独立运行兜底：公式**必须与主实现同口径**
                     # （半角片假名 U+FF61-FF9F = 1 格；旧版 `ord(c) < 0x100` 在这段虚高 6 格/行）
    ART_HIRA = set('つだりへぢづゑゐいうしふとこっッ')
    ART_KATA = set('ノミニハマレヘヾヿヽヾー')
    ART_KANJI = set()

    def disp_width(s):
        return sum(1 if (ord(c) < 0x100 or 0xFF61 <= ord(c) <= 0xFF9F) else 2 for c in s)
# .txt/.txt.gz 与 line_meta_v2 的 .json/.json.gz 一律走透明层（2026-10-17 全库压缩）
from aa_common import (KANA_RE, txt_read, txt_existing, meta_load,   # [ぁ-んァ-ヺー] + 压缩感知
                       meta_exists, meta_path, meta_dump, list_txt)
FACE_KANA = set('ィヲァフテソヅメトイえごドぅぃヌタカリツムちン')  # 脸部/花纹散落艺术假名


# 楼主识别的帖头行（parse_op_header 口径）
# 格式 A: NNN ：作者 ：日期 (yaruobook/本棚系)
# 格式 B: NNN 名前：作者 [] 投稿日：日期 (yarumo_fc2/FC2系)
# 格式 C: NNN 名前：作者 E-mail:xxx ：日期 (yaruocheck 系)
# 格式 A: NNN ：作者 ：日期 (yaruobook/本棚系)
# 2026-09-07: 日期支持 4位/2位年(YY/MM/DD)——iitokolo_fc2 旧格式 2012-2013
# `NNN ：作者 ：YY/MM/DD HH:MM:SS ID:xxx` 用 2 位年, 原 \d{4} 匹配失败致楼主全 [?]
_HEADER_A = re.compile(r'^(\d+)\s*：\s*(.+?)\s*：\s*(?:\d{4}|\d{2})/\d{2}/\d{2}')
_HEADER_B = re.compile(r'^(\d+)\s*名前：\s*(.+?)\s*(?:\[\])?\s*投稿日：\s*\d{4}/\d{2}/\d{2}')
_HEADER_C = re.compile(r'^(\d+)\s*名前：\s*(.+?)\s*(?:E-mail:\S+\s*)?：\s*\d{4}/\d{2}/\d{2}')
_MAIN_FLAG = re.compile(r'[◆★]')  # 楼主标记


def load_meta(meta_dir, base):
    """读 v3 meta；缺失返回 None（调用方决定是否自动生成/中断）。

    **必须走 meta_load**：line_meta_v2 全库已压成 <base>.json.gz（2026-10-17），
    直接 open(<base>.json) 会把每章都判成「meta 缺失」→ 回退去补 raw HTML
    → raw 早清理 → 整部作品台词全缺、候选池为空（2026-10-20 实测事故）。
    """
    return meta_load(meta_dir, base, None)


def _has_real_dialogue(text):
    """含真实假名门禁(2026-08-29 用户拍板)：段/行内至少 1 个真实假名或非艺术汉字才算台词。

    过滤纯装饰假名碎片（`ミ`/`ヽ`/`メメメ`/`｝メ`——segment_line 的 treat_hankaku_as_text
    把楼主帖半角片假名误判为 text 段，但它们是艺术笔画非台词，_seg_confidence 因此给 0.55）。
    复用 _seg_confidence 的 real 假名语义：KANA_RE 匹配且不在 ART_HIRA/ART_KATA/FACE_KANA。
    """
    for c in text:
        if c.isspace():
            continue
        if '\u4e00' <= c <= '\u9fff' and c not in ART_KANJI:
            return True
        if KANA_RE.match(c) and c not in ART_HIRA and c not in ART_KATA and c not in FACE_KANA:
            return True
    return False


def parse_op_header(line, op_names=None):
    """返回 (is_header, is_op)。is_op=True=楼主楼, False=用户楼。非帖头→(False,None)。"""
    if not line:
        return False, None
    for rx in (_HEADER_A, _HEADER_B, _HEADER_C):
        m = rx.match(line)
        if m:
            author = m.group(2).strip()
            if _MAIN_FLAG.search(author):
                return True, True
            base = re.sub(r'[◆★].*$', '', author).strip()
            if op_names and base in op_names:
                return True, True
            return True, False
    return False, None


def _slice_line_by_widths(line, segs):
    """按段 src_width(显示宽度)序列, 把 line 切成与 segs 一一对应的子串列表。

    源/汉化行通用: meta 段偏移是源行字符坐标, 汉化行因译文字符数不同会漂移,
    段边界以显示宽度为准(汉化行由 pad_to_width 补齐到源宽)。返回 len(segs) 个
    子串, 与 segs[] 顺序对齐; 逐段累积宽度到目标即截断, 允许末段量化微越界。
    """
    out = []
    idx = 0
    n = len(line)
    for sg in segs:
        target = sg['src_width']
        buf = []
        w = 0
        while idx < n and w < target:
            c = line[idx]
            cw = disp_width(c)
            buf.append(c)
            w += cw
            idx += 1
        out.append(''.join(buf))
    return out


def extract_dialogue_from_meta(meta, txt_lines, op_names=None, base=None):
    """从 v3 meta + 行列表提取台词段。

    meta: {行号(1-based,str): {kind,confidence,reason,segments}}
    txt_lines: 行列表（源 aa/<章>.txt 或 汉化 out/<章>_汉化.txt 的 0-based list; txt_lines[meta_key-1]=该物理行）
    op_names: 楼主作者名集合（多马甲），用于 op 识别；None 则仅按 ◆/★ 标记

    返回 list[dict]:
      {line_no, seg_idx, kind, text, seg_start, seg_end, conf, op}
      - line_no: 1-based 物理行号（同 meta key）
      - seg_idx: mixed 行内段号（0 起），text 行恒 0
      - text: 台词段文本（不 strip，保留行首 U+3000，段级拼回需要）
      - seg_start/seg_end: 该段在整行的字符半开区间 [start,end)，回填用
      - conf: 段置信度（text 行=None；mixed 段按 meta/segment confidence）
      - op: '主'/'用'/None（楼主/用户/未知）
    """
    out = []
    cur_op = None
    for key, v in meta.items():
        ln = int(key)
        if ln > len(txt_lines):
            continue
        raw = txt_lines[ln - 1]          # 0-based
        kind = v.get('kind')
        # 帖头行：更新楼主归属，且帖头行永远不产出台词（无条件跳过）。
        # 2026-08-29 修复：帖头行可能被分类器误标为 text/mixed（FC2 站 `N 名前： ◆…投稿日`），
        # 原实现只在 kind=='header' 时跳过，导致这类帖头行被当台词提取（entry_2641 实测 31 处）。
        # 改为 is_hdr 即跳过——帖头行（楼主名/日期/ID 行）物理上不是台词。
        is_hdr, is_op = parse_op_header(raw, op_names)
        if is_hdr:
            cur_op = '主' if is_op is True else ('用' if is_op is False else None)
            continue
        if kind in ('art', 'header', 'blank'):
            continue
        if kind == 'text':
            if not raw.strip() or not _has_real_dialogue(raw):
                continue
            out.append({
                'line_no': ln, 'seg_idx': 0, 'kind': 'text',
                'text': raw, 'seg_start': 0, 'seg_end': len(raw),
                'conf': v.get('confidence'), 'op': cur_op,
            })
        elif kind == 'mixed':
            segs = v.get('segments')
            if not segs:
                # meta 无 segments 兜底：整行按 text 处理（极端情况）
                if raw.strip() and _has_real_dialogue(raw):
                    out.append({
                        'line_no': ln, 'seg_idx': 0, 'kind': 'mixed',
                        'text': raw, 'seg_start': 0, 'seg_end': len(raw),
                        'conf': v.get('confidence'), 'op': cur_op,
                    })
                continue
            si = 0
            # 2026-08-30: 段文本按显示宽度定位(而非字符偏移)——段偏移是源行字符坐标,
            # 汉化行因译文字符数不同会漂移; 段边界以 src_width 为准, 源/汉化行通用。
            widths = _slice_line_by_widths(raw, segs)
            for i, sg in enumerate(segs):
                if sg.get('kind') != 'text':
                    continue
                seg_text = widths[i]
                if not (_has_real_dialogue(seg_text)):
                    continue
                out.append({
                    'line_no': ln, 'seg_idx': si, 'kind': 'mixed',
                    'text': seg_text, 'seg_start': sg['start'], 'seg_end': sg['end'],
                    'conf': sg.get('confidence'), 'op': cur_op,
                })
                si += 1
    return out


def format_dialogue_entry(d):
    """把台词段 dict 格式化为 `L<行号>[_<段号>] [主/用] <段文本>`。"""
    if d['kind'] == 'mixed':
        head = f"L{d['line_no']}_{d['seg_idx']}"
    else:
        head = f"L{d['line_no']}"
    tag = f"[{d['op']}]" if d['op'] else "[?]"
    return f"{head} {tag} {d['text']}"


def write_dialogue_file(out_path, entries, overwrite=False):
    """写台词文件（每行一个 entry）。"""
    if os.path.exists(out_path) and not overwrite:
        return False
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(format_dialogue_entry(d) for d in entries))
    return True


# ---------------------------------------------------------------------------
# CLI（--work 驱动，meta 缺失自动生成、raw 缺失中断报错，符合「禁静默降级」硬约束）
# ---------------------------------------------------------------------------
def _ensure_meta(meta_dir, base, cfg):
    """meta 缺失时自动生成（零 API）。raw 缺失/生成失败 → 抛异常中断（禁静默降级）。

    存在性判定走 meta_exists（.json / .json.gz 都算）——只看 .json 会把压缩后的
    全库判成「meta 缺失」，进而去补早已清理的 raw HTML（2026-10-20 事故根因）。
    """
    if meta_exists(meta_dir, base):
        return meta_path(meta_dir, base)
    import sys as _sys, os as _os
    # 注意: 本文件在 scripts/common/ 下, 兄弟模块 tools/(line_classify_v2) 在 scripts/tools/。
    # 旧实现 join(_this_dir, 'tools') 会得到 scripts/common/tools(不存在)→ 导入必失败;
    # 必须先 dirname 上跳一级到 scripts/ 再拼兄弟目录。
    _this_dir = _os.path.dirname(_os.path.abspath(__file__))   # scripts/common
    _scripts = _os.path.dirname(_this_dir)                     # scripts/
    for p in (_os.path.join(_scripts, 'tools'),
              _os.path.join(_scripts, 'common'),
              _scripts):
        if p not in _sys.path:
            _sys.path.insert(0, p)
    from line_classify_v2 import classify_raw_v2, _serialize
    import importlib
    site = importlib.import_module('sites.' + cfg['site'])
    raw_dir = cfg['raw_dir']
    aa_dir = cfg['aa_dir']
    # 定位 raw HTML（entry_<eid>.html 或 <base>.html；兜底含 eid 的 html）
    import re as _re
    m = _re.search(r'(\d+)', base)
    eid = m.group(1) if m else ''
    cands = [os.path.join(raw_dir, f"entry_{eid}.html"), os.path.join(raw_dir, f"{base}.html")]
    raw_path = next((p for p in cands if os.path.exists(p)), None)
    if raw_path is None and os.path.isdir(raw_dir):
        for cand in sorted(os.listdir(raw_dir)):
            if cand.endswith('.html') and eid and eid in cand:
                raw_path = os.path.join(raw_dir, cand)
                break
    if raw_path is None:
        raise FileNotFoundError(
            f"[extract_dialogue] raw HTML 缺失: {base} (raw_dir={raw_dir})。"
            f"按「禁静默降级」硬约束中断, 需人工介入补 raw 或确认站源。")
    raw = open(raw_path, encoding='utf-8', errors='replace').read()
    # 行口径 = aa_translate（read().split('\n')）。曾在此"去尾空"，但落盘 txt 就是
    # '\n'.join(行列表) 的逆运算——末尾行为空串时文件以 '\n' 收尾，去掉它会让行数
    # 比解析端少 1 → 误判"行数不齐"中断（2026-09-13 与 line_meta 行内换行展开同批修）。
    txt_lines = txt_read(txt_existing(os.path.join(aa_dir, base + '.txt')))
    meta = classify_raw_v2(site, raw, txt_lines, eid=eid)
    if meta is None:
        raise RuntimeError(f"[extract_dialogue] {base}: meta 生成失败（行数不齐/站点不支持），中断。")
    os.makedirs(meta_dir, exist_ok=True)
    out = _serialize(meta)
    path = meta_dump(meta_dir, base, out)   # 已是 .json.gz 形态就续写 .gz，别还原明文
    print(f"    [extract_dialogue] meta 自动生成 ({len(out)} 行): {os.path.basename(path)}", flush=True)
    return path


def _cli_main():
    import argparse
    import registry
    ap = argparse.ArgumentParser(description='台词提取（v3 meta 驱动，收编三套口径）')
    ap.add_argument('--work', required=True)
    ap.add_argument('--src', action='store_true',
                    help='只生成原文 <aa_dir>/<章>_台词.txt（不依赖汉化）')
    ap.add_argument('--overwrite', action='store_true')
    a = ap.parse_args()
    cfg = registry.get(a.work)
    aa_dir = cfg['aa_dir']
    meta_dir = os.path.join(cfg['work_dir'], 'line_meta_v2')
    os.makedirs(meta_dir, exist_ok=True)
    # 楼主作者名（多马甲）
    wm = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      'aa_web_app', 'data', 'work_meta.json')
    op_names = set()
    if os.path.exists(wm):
        try:
            data = json.load(open(wm, encoding='utf-8'))
            author = data.get(a.work, {}).get('author', '') if isinstance(data, dict) else ''
            op_names = {x.strip() for x in re.split(r'[,，、]', author) if x.strip()}
        except Exception:
            pass
    # 目标文件命名：<章>.txt → <章>_台词.txt（原文放 aa_dir；汉化放 out_dir）
    entries_total = 0
    files_done = 0
    for base in sorted(_iter_bases(cfg)):
        # meta 自动生成（缺失补）
        try:
            _ensure_meta(meta_dir, base, cfg)
        except Exception as e:
            print(f'  {base}: {e}', flush=True)
            continue
        meta = meta_load(meta_dir, base, {}) or {}
        # 智能模式: 默认(非 --src)时, 若该章有 汉化/<base>_汉化.txt 则生成中文台词,
        # 否则降级为原文台词(--src 始终强制原文)。判断汉化文件是否存在, 不静默杜撰中文。
        hanhua_dir = cfg['out_dir']
        hanhua_path = os.path.join(hanhua_dir, base + '_汉化.txt')
        use_hanhua = (not a.src) and os.path.exists(hanhua_path)
        if use_hanhua:
            txt_lines = open(hanhua_path, encoding='utf-8').read().split('\n')
            out_dir = hanhua_dir
            mode = '汉化'
        else:
            txt_lines = txt_read(txt_existing(os.path.join(aa_dir, base + '.txt')))
            out_dir = aa_dir
            mode = '原文' if a.src else '原文(无汉化文件,降级)'
            if not a.src:
                print(f'  {base}: 无汉化文件({os.path.basename(hanhua_path)}), 降级生成原文台词', flush=True)
        entries = extract_dialogue_from_meta(meta, txt_lines, op_names)
        out_path = os.path.join(out_dir, base + '_台词.txt')
        wrote = write_dialogue_file(out_path, entries, a.overwrite)
        # 楼主识别失败防护（统一入口后的硬约束）：[?] 占 >10% → 中断人工介入。
        # 「不存在无楼主作品」——需检查 work_meta.author 或帖头格式。
        if entries:
            q_n = sum(1 for e in entries if e['op'] is None)
            if q_n / len(entries) > 0.1:
                raise RuntimeError(
                    f"[楼主识别失败] {base}_台词.txt: [?] tag 占 {q_n}/{len(entries)} 台词段 (>10%)。"
                    f"不存在无楼主作品——请检查 work_meta.author 或帖头格式。已中断, 需人工介入。")
        entries_total += len(entries)
        if wrote:
            print(f'  {base}: {len(entries)} 段台词({mode}) → {os.path.basename(out_path)}', flush=True)
            files_done += 1
        else:
            print(f'  {base}: 已存在, 跳过(--overwrite 覆盖)', flush=True)
    print(f'DONE: {files_done} 文件更新, 台词段总数 {entries_total}')


def _iter_bases(cfg):
    """按 aa_dir 枚举原文章基础名（去掉 .txt；`.txt.gz` 也算，走 list_txt 透明层）。

    ⚠️ 禁 `os.listdir + endswith('.txt')`：全库 aa/*.txt 已压成 *.txt.gz（2026-10-17），
    那样一章都枚举不到（本作品 310 章只看到 3 章明文）。
    """
    aa_dir = cfg['aa_dir']
    for fn in list_txt(aa_dir, exclude=('_台词.txt', '_汉化.txt')):
        yield fn[:-4]


# ---------------------------------------------------------------------------
# 统一接口（2026-08-30 单一口径）：gen_chapter_summaries / gen_glossary_ai
# 统一读 v3 meta 口径的 _台词.txt。
# ---------------------------------------------------------------------------
_DLGLINE = re.compile(r'^L(\d+)(?:_(\d+))?\s+\[(主|用|\?)\]\s?(.*)$')


def parse_dialogue_file(path):
    """读 _台词.txt → list[dict]，解析 `L<行号>[_<段号>] [主/用|?] <文本>`。

    返回 [{line_no, seg_idx, op, text, raw}]：
      - line_no: 1-based 行号（int）
      - seg_idx: mixed 行内段号（int，0 起）；text 行恒 0
      - op: '主'/'用'/None（? 转 None）
      - text: 台词文本（已去 L 前缀/tag；不 strip 行首 U+3000，保留行内空格）
      - raw: 原始行（供 kana_runs 等需要原样的调用）
    解析失败的行返回 None（跳过）。"""
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding='utf-8'):
        line = line.rstrip('\n')
        if not line.strip():
            continue
        m = _DLGLINE.match(line)
        if not m:
            continue
        line_no = int(m.group(1))
        seg_idx = int(m.group(2)) if m.group(2) else 0
        op = m.group(3)
        text = m.group(4)
        if text is None:
            text = ''
        out.append({'line_no': line_no, 'seg_idx': seg_idx,
                    'op': '主' if op == '主' else ('用' if op == '用' else None),
                    'text': text, 'raw': line})
    return out


def ensure_dialogue_file(cfg, base, out_dir=None, src_only=False, overwrite=False):
    """确保 <base>_台词.txt 已按 v3 meta 口径生成；缺失则生成（meta 缺失自动生成、raw 缺失中断）。

    src_only=True → 只生成原文 aa/<base>_台词.txt（gen_glossary/gen_summaries 用原文台词）；
    src_only=False + out_dir → 生成汉化 out/<base>_台词.txt（refine_runner 精译/校对用）。
    返回目标文件路径；生成失败抛异常（不静默降级）。"""
    aa_dir = cfg['aa_dir']
    meta_dir = os.path.join(cfg['work_dir'], 'line_meta_v2')
    os.makedirs(meta_dir, exist_ok=True)
    # 楼主作者名（多马甲）
    import re as _re
    wm = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      'aa_web_app', 'data', 'work_meta.json')
    op_names = set()
    if os.path.exists(wm):
        try:
            data = json.load(open(wm, encoding='utf-8'))
            author = data.get(cfg.get('id', ''), {}).get('author', '') if isinstance(data, dict) else ''
            op_names = {x.strip() for x in _re.split(r'[,，、]', author) if x.strip()}
        except Exception:
            pass
    # 目标路径 + 读取源(原文台词读 aa/<base>.txt; 汉化台词读 out/<base>_汉化.txt)
    if src_only:
        target = os.path.join(aa_dir, base + '_台词.txt')
        src_txt = txt_existing(os.path.join(aa_dir, base + '.txt'))
    else:
        _out = out_dir or cfg['out_dir']
        target = os.path.join(_out, base + '_台词.txt')
        src_txt = txt_existing(os.path.join(_out, base + '_汉化.txt'))
    # 已存在且未要求覆盖 → 直接返回（复用现有 v3 meta 口径文件）
    if os.path.exists(target) and not overwrite:
        return target
    # 汉化模式必须存在 <base>_汉化.txt, 缺失按「禁静默降级」硬约束中断(需先翻译)
    if not src_only and not os.path.exists(src_txt):
        raise FileNotFoundError(
            f"[extract_dialogue] 汉化文件缺失: {src_txt}。按「禁静默降级」硬约束中断, 需先翻译该章。")
    _ensure_meta(meta_dir, base, cfg)
    meta = meta_load(meta_dir, base, {}) or {}
    txt_lines = txt_read(src_txt)
    entries = extract_dialogue_from_meta(meta, txt_lines, op_names)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    write_dialogue_file(target, entries, overwrite=True)
    print(f"    [extract_dialogue] 生成台词: {os.path.basename(target)} ({len(entries)} 段)", flush=True)
    return target


if __name__ == '__main__':
    _cli_main()
