# -*- coding: utf-8 -*-
"""行分类器 v2（作者先验加权）— 生产版（2026-08-11 从 temp/layered 提升，sys.path 改指生产 common/works）。
与 v1.5 的差异：
  1. 作者感知：楼主帖(◆) vs 名無し帖，用不同阈值（先验加权，非硬规则）
     - 名無し帖(88% 纯文本)：text 放宽（≥2字；半角片假名按文本；混合阈值提高）
     - 楼主帖(86% 是AA)：text 收紧（≥7字且无连续艺术段）
  2. 输出加 confidence + reason，供 P1 接入分级采用
  3. 修 v1.5 三类漏标：短台词<5字、半角片假名台词、（）/… 触发混合

用法: python scripts/tools/line_classify_v2.py --work <id> [--chapter 子串] [--missing]
输出: <work_dir>/line_meta_v2/<base>.json  {行号: {kind, confidence, reason}}
评测: python tools/eval_classify_v2.py --work saki_fc2 --chapter 8532
"""
import importlib, inspect, json, os, re, sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
sys.path.insert(0, os.path.join(BASE, "scripts", "works"))

import registry
from aa_common import ART_HIRA, ART_KATA, ART_KANJI, disp_width

EID_FROM_FNAME = re.compile(r'(?:entry_)?(\d+)')
HEADER_AUTHOR = re.compile(r'◆([^ ：\s]+)')

# 与 v1.5 同源的艺术符号
ART_SYMS = set('│｜┃／＼＿￣─━┌┐└┘├┤┬┴┼■□◆◇△▲▽▼○●◎※〒§☆★→←↑↓∴∵♂♀╋╂╇╅╆┏┓┗┛┣┫┳┻|＝≠≡≒∞〓´¨¯°')
# 2026-09-01: AA 纯装饰字符集（绘画字符/嘴/弧线/睫毛, 几乎不可能是台词）。
# 实测这些字符被误判为 text 段送进模型 → 译文落错列(宽对框歪)/AA行误翻。
# 仅收「台词中几乎不出现」的: 弧线(｀´⌒⌢⌣〜)、拉丁线条字母(lIiUu)、装饰点(¨˙)。
# 谨慎排除: ノ/：/（）/・——台词里常见(ノ→手但也是拟声, ：→台词冒号, （）→旁白括号, ・→分隔)。
DECOR_SYMS = set('｀´⌒⌢⌣〜¨˙lIiUu')
ASCII_PUNCT = set('!@#$%^&*()-_=+[]{};:\'" ,.<>/?\\`~')
KANA_RE = re.compile(r'[ぁ-んァ-ヺー]')
# 全角标点（日文台词常用，不应触发 art 判定）
FULLW_PUNCT = set('。、，．…！？「」『』（）()・：；〔〕〈〉《》―')


def is_art_char(c):
    o = ord(c)
    if 0xFF61 <= o <= 0xFF9F:      # 半角片假名——v1.5 一律算艺术，v2 在名無し帖放宽
        return True
    if c in ASCII_PUNCT:
        return True
    # 2026-09-01: 纯装饰字符(弧线/线条字母)判 art——防送进模型改坏/落错列
    if c in DECOR_SYMS:
        return True
    # 2026-09-01: CJK 兼容假名/符号/扩展区字符是 art 纹理(㍉㌘㏍/U+3400+ 生僻字), 非台词
    if c in CJK_COMPAT_KANA or c in CJK_SYMBOLS or c in CJK_EXT_A:
        return True
    # 2026-08-29 台词门禁调优(A): 装饰字符黑名单——这些假名/汉字夹在艺术里作纹理/嘴/花纹,
    # 几乎不可能是台词词。判 art → txt 计数不虚高, 艺术行不再被误标 text/mixed。
    if c in DECOR_KANA or c in DECOR_HANZI:
        return True
    if KANA_RE.match(c):
        return False
    if c in FULLW_PUNCT:
        return False                  # v2: 全角标点不触发 art/mixed
    return c in ART_SYMS or c in ART_KANJI


def _seg_counts(row, treat_hankaku_as_text=False):
    """字符段分析。返回 (art_total, text_total, art_max, text_max, n_text_segs)
    treat_hankaku_as_text=True 时半角片假名计入文本（名無し帖短台词）"""
    segs = []
    for ch in row:
        if ch.isspace():
            continue
        if treat_hankaku_as_text and 0xFF61 <= ord(ch) <= 0xFF9F:
            cls = 'text'
        else:
            cls = 'art' if is_art_char(ch) else 'text'
        if segs and segs[-1][0] == cls:
            segs[-1][1] += 1
        else:
            segs.append([cls, 1])
    art = sum(n for c, n in segs if c == 'art')
    txt = sum(n for c, n in segs if c == 'text')
    art_max = max((n for c, n in segs if c == 'art'), default=0)
    txt_max = max((n for c, n in segs if c == 'text'), default=0)
    return art, txt, art_max, txt_max, len([1 for c, _ in segs if c == 'text'])


def _has_dialogue(row, hankaku=False):
    """行内是否有真实日文对话字符（假名/非艺术汉字，半角片假名可选）。
    纯装饰字符(。o〇ﾟ●○ 全角标点/ASCII字母/圆符)不算——与生产 is_text_line 语义对齐，
    防止装饰艺术行被送进翻译模型而改坏。"""
    for c in row:
        if c.isspace():
            continue
        if KANA_RE.match(c):
            return True
        if hankaku and 0xFF61 <= ord(c) <= 0xFF9F:
            return True
        if '一' <= c <= '鿿' and c not in ART_KANJI:
            # 2026-09-01: 装饰汉字(衛/滯/灘等纹理花边)不算对话——防艺术行判 text 送模型
            if c in DECOR_HANZI:
                continue
            return True
    return False


def _max_real_dialogue_run(row):
    """最大连续真实对话段长度。

    区分「散落艺术假名/笔画」与「真台词」：楼主帖 art 面板常用单个假名/汉字作装饰
    （ィハノイく赱勹等，属嘴/卷曲/花纹），散落分布，maxrun=1；真台词形成连续段（>=2）。
    边界字符 ー(长音,艺术面板常见 ーー 横线) / 々ヽヾ(迭字符) 作断点不延长 run——
    真台词长音 ー 是尾缀（そーう→ そ う run 已≥2），艺术 ーー 不误触发。
    返回 int（0=无真实对话）。"""
    best = cur = 0
    for c in row:
        if c.isspace() or c in 'ー々ヽヾ':
            cur = 0
            continue
        # 半角浊音/半浊音(ﾞﾟ)/长音(ｰ)/拗音小假名(ｧｨｩｪｫｬｭｮ)/促音(ｯ):
        # 它们都是拟声词的一部分(ｶﾞ/ﾋﾞ/ｰﾝ/ｷｭﾑ/ｷｭｯ), 紧跟前一个假名(cur>0)时继承 run——不中断。
        # 独立出现(前无假名)才是装饰(ｯｯｯ横排/ｰー横线), 此时才作断点。
        if c in 'ﾞﾟｰ':
            continue
        if c in 'ｧｨｩｪｫｬｭｮｯ':
            if cur > 0:
                continue
            cur = 0
            continue
        is_dial = False
        # 全角假名(排除装饰假名——ミヘメソ等是艺术纹理非台词)
        if KANA_RE.match(c):
            is_dial = c not in DECOR_KANA
        # 半角片假名: 拟声词(ｷｭﾑ/ｹﾞｾﾇ/ﾌﾟｼｭｰ)算对话, 纯装饰(ﾉｰ､ﾞﾟ)排除
        elif 0xFF61 <= ord(c) <= 0xFF9F:
            is_dial = c not in HK_DECOR
        # 汉字(排除装饰汉字——穰鬱懶彡等为阴影花边)
        elif '一' <= c <= '鿿' and c not in ART_KANJI:
            is_dial = c not in DECOR_HANZI
        if is_dial:
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return best


# ---------------------------------------------------------------------------
# B1 (M9): mixed 行段边界检测 — segments 字段
# 对 mixed 行输出 [{start,end,kind,src_width}]：
#   - Python 字符索引半开区间 [start,end)，覆盖整行无空隙
#   - kind: text / art；空白(U+3000/空格)并入相邻 art 段（保持字节不变）
#   - src_width = 该段 disp_width（aa_common 正确口径）
#   - 低置信度段(<0.65)附加 confidence 字段，供 B2 回退整行旧逻辑
# ---------------------------------------------------------------------------
# 嘴/眉/肢体等艺术假名（分类器把它们当 text，实际常是画）——text 段只含这些时低置信
ART_KANA_CHARS = ART_HIRA | ART_KATA
# 脸部/花纹散落假名（技能文档记载：ィヲァフテソヅメ等全是面部装饰画，非台词）
FACE_KANA = set('ィヲァフテソヅメトイえごドぅぃヌタカリツムちン')
# 2026-08-29 台词门禁调优(A): 装饰字符黑名单——纯纹理/嘴/花纹/阴影用,几乎不可能成词的假名/汉字。
# 误标根因: 这些字被 is_art_char 放行(判text)导致艺术行 run>=2 误判台词。
# 加入后 _max_real_dialogue_run 排掉它们 → 艺术行 run<2 → 判 art(误标拦截)。
DECOR_KANA = set('ミヘメソ三ニヨミヘケ')            # 全角装饰假名(夹在art里的嘴/纹理)
DECOR_HANZI = set('穰鬱懶彡乂弋辷州屯鬯阝')          # 密集装饰汉字(阴影/花边)
# 2026-09-01: 追加 AA 高频装饰汉字(卫/滯/灘/鑾/眦等作纹理/花边, 非台词)
DECOR_HANZI |= set('衛滯灘鑾眦奝奲娚姶嫐嬢宀丿乀乁乂彡癶麤龘龖爨鬱麐麕')
# CJK 兼容假名/符号(㍉㌘㏍㌢㌦㍻㌻等) 与 CJK 扩展区汉字(U+3400-4DBF) 几乎全是 art 纹理
CJK_COMPAT_KANA = set(chr(c) for c in range(0x31F0, 0x3200))   # ㇰ-ㇿ
CJK_SYMBOLS = set(chr(c) for c in range(0x3300, 0x3400))       # ㌀-㋿
CJK_EXT_A = set(chr(c) for c in range(0x3400, 0x4DC0))         # U+3400-4DBF 扩展A
DECOR_DIALOG = DECOR_KANA | DECOR_HANZI
HK_DECOR = set('ｦｧｨｩｪｫｬｭｮｯｰ､ﾞﾟ')                  # 半角纯装饰(撇/角/长音)不算拟声


def _seg_confidence(kind, text):
    """段置信度启发式。text 段含真实假名/汉字 → 高；纯装饰（艺术假名/全角拉丁/标点）→ 低（可能是画）。"""
    if kind == 'text':
        # 2026-09-01: 密集装饰汉字(穰鬱懶彡乂弋辷州屯鬯阝等)不算真实台词汉字——
        # 它们是 art 纹理/花边, 排除后段 conf 降到 <0.65 不送模型(防误翻/落错列)。
        kanji = [c for c in text if '\u4e00' <= c <= '\u9fff' and c not in DECOR_HANZI]
        # 2026-09-01: ー(U+30FC 长音) 排除出"真实假名"——孤立长音是装饰(ーー横线/弧线),
        # 台词长音(いー)的 い 仍算 real 保 text 段。防装饰 ー 段送模型改坏。
        # 拗音小假名(ぁぃぅぇぉゎゃゅょっ)单独/装饰时不算台词(它们是后缀, 独立出现=装饰);
        # 跟在前假名后(きゃ/しょ)时由 き/し 撑起 conf, 不受影响。
        _SMALL_KANA = set('ぁぃぅぇぉゎゃゅょっ')
        real = [c for c in text if KANA_RE.match(c)
                and c not in ART_KANA_CHARS and c not in FACE_KANA
                and c != '\u30fc' and c not in _SMALL_KANA]
        # 2026-09-01: 段以小假名开头且假名<=2 是装饰嘴型(ぅて/ぅてぅ)——真台词不以小假名开头
        _text_nospace = text.strip(' \u3000')
        if _text_nospace and _text_nospace[0] in _SMALL_KANA and len(real) <= 2:
            return 0.4
        # 2026-09-01: 无真实假名/汉字的段(如 ｀⌒⌒、ー、U、l)是纯装饰 → 降置信不送模型。
        # 防装饰字符被当台词翻译 → 译文落错列(宽对框歪)/AA行误翻。
        if not kanji and not real:
            return 0.4
        if kanji or len(real) >= 2 or (real and len(text) >= 3):
            return 0.92
        if real and len(real) >= 1 and len(text) >= 2:
            return 0.7          # 短假名段：可能是嘴/装饰，也可能是一字台词
        return 0.55             # 无真实假名/汉字（纯装饰）→ 低置信，不送模型
    return 0.9               # art 段由 is_art_char 精确判定


_NUM_RUN_RE = re.compile(r'[0-9０-９]+(?:[.．/／\-－～~][0-9０-９]+)*')
_NUM_CHARS = set("0123456789０１２３４５６７８９")
_INNER_SEP = ('/', '／')      # 词内分隔斜杠


def _rescue_inline_numeric(row, kinds):
    """数字记号/词内斜杠**嵌在文本里**时，把其中每个字符改判 text（2026-10-17）。

    `is_art_char` 把 ASCII 数字与 `. / - ~` 判 art 是对的（AA 笔画大量用它们），但它们
    **紧贴非 art 文本字符**时是在写数字/词内分隔。段级协议按字符切段，于是
    `1.5倍` → art `1.` + text `5倍`、`1/3以下` → art `1/` + text `3以下`、
    `3-3だ` → art `3-` + text `3だ`、`本日5/9付け` → text `…5` + art `/` + text `9付け`
    —— 送模型的是**残句**：实测译文把数字翻成「1.变成5倍」「1/都说到3」「3-是3」，
    或整句前半原样留日文（`◎先発で…強化（1.变成了1倍）！`）；art 的 `.` 还要按列位守
    → 补位被塞进数字里（`（全1　　　.33倍）`）。

    判据（保守；两个都不跨空白、都不碰 art 邻居）：
      ① 数字串 `[0-9]+([.／/-～~][0-9]+)*` 前后紧邻**非 art 非空白**字符 → 整串 text
         （`1.5倍`／`（1.1倍）`／`5-3だ`／`1.1～1.5倍`／`1つめ`）；
      ② 斜杠 `／/` 两侧都是**非 art 非空白**字符 → 该斜杠 text（`あく/じめん` 词内分隔）。
    反例保护：`人/王`（人=ART_KANJI）、`／⌒＼`、`l. :. :.`、`ﾆ/ﾆ`、`1人`（人 art）
    两侧是 art/空白 → 不动；个别判 text 的也会被 `_demote_non_dialogue_text_segs`
    （无决定性台词证据降 art）兜回。
    """
    n = len(row)

    def _wordish(c):
        return bool(c) and not c.isspace() and not is_art_char(c)

    for m in _NUM_RUN_RE.finditer(row):
        s, e = m.span()
        if not any(row[j] in _NUM_CHARS for j in range(s, e)):
            continue
        if _wordish(row[s - 1] if s else '') or _wordish(row[e] if e < n else ''):
            for j in range(s, e):
                kinds[j] = 'text'
    for i, ch in enumerate(row):
        if ch in _INNER_SEP and 0 < i < n - 1 \
                and _wordish(row[i - 1]) and _wordish(row[i + 1]):
            kinds[i] = 'text'


def segment_line(row, treat_hankaku_as_text=False):
    """mixed 行分段 [{start,end,kind,src_width}]，半开区间，覆盖整行无空隙。
    复用 _seg_counts 的逐字符分类口径（含 treat_hankaku_as_text 分支）。

    2026-10-17: 加数字记号/词内斜杠 rescue（见 _rescue_inline_numeric）——`1.5倍`/`1/3以下`
    /`3-3だ` 曾被切在数字中间，残句送模型后数字被翻坏或前半句留日文。

    2026-09-07 修复: DECOR_KANA(ミヘメソニヨケ等) 被静态判 art → 真台词词头被切掉
    (ミルク→ルク、アメリカ→リカ 事故)。分词工具单测正确, 碎片根因在此。
    修法: 词头 rescue——DECOR_KANA 假名若「右侧紧接真实假名/汉字」(形成词/连续对话),
    则改判 text(不再当装饰纹理)。真正装饰(ミ)(ミ)的 ミ 右侧是 ) 或空白, 不受影响。
    """
    n = len(row)
    kinds = [None] * n
    for i, ch in enumerate(row):
        if ch.isspace():
            kinds[i] = 'ws'
        elif treat_hankaku_as_text and 0xFF61 <= ord(ch) <= 0xFF9F:
            kinds[i] = 'text'
        else:
            kinds[i] = 'art' if is_art_char(ch) else 'text'
    # ---- 2026-10-17 数字记号/词内斜杠 rescue（见 _rescue_inline_numeric）----
    _rescue_inline_numeric(row, kinds)
    # ---- 2026-09-07 词头 rescue: 装饰假名集合成员若与相邻假名构成≥2连串 → 整串 text ----
    # DECOR_KANA(ミヘメソニヨケ等)/FACE_KANA 静态判 art 误伤真台词词头/词中字
    # (ミルク→ルク、アメリカ→リカ 事故)。真正装饰(ミ)(ミ)的 ミ 孤立(两侧非假名连串)。
    # 仅救全角假名(半角片假名是拟声/装饰主力, 不 rescue, 防 art 纹理被当台词)。
    i = 0
    while i < n:
        ch = row[i]
        if kinds[i] != 'art' or (ch not in DECOR_KANA and ch not in FACE_KANA):
            i += 1
            continue
        if not ('\u3040' <= ch <= '\u30ff'):
            i += 1
            continue
        # 向左/右扩展: 相邻字符若是假名(非空白) 且非纯符号 → 并入连串
        lo = hi = i
        while lo - 1 >= 0:
            c = row[lo - 1]
            if kinds[lo - 1] == 'ws' or kinds[lo - 1] != 'art' and kinds[lo - 1] != 'text':
                break
            if KANA_RE.match(c) and c not in ART_SYMS:
                lo -= 1
            else:
                break
        while hi + 1 < n:
            c = row[hi + 1]
            if kinds[hi + 1] == 'ws' or (kinds[hi + 1] != 'art' and kinds[hi + 1] != 'text'):
                break
            if KANA_RE.match(c) and c not in ART_SYMS:
                hi += 1
            else:
                break
        run_len = hi - lo + 1
        # 连串≥2 且非「纯单字重复」(ミミミ/ヘヘヘ 是装饰纹理) → 是词, 整串改 text
        run_chars = row[lo:hi + 1]
        if run_len >= 2 and len(set(run_chars)) >= 2:
            for j in range(lo, hi + 1):
                if row[j] in DECOR_KANA or row[j] in FACE_KANA:
                    kinds[j] = 'text'
        i = hi + 1
    runs = []                # [kind, start, end)
    i = 0
    while i < n:
        if kinds[i] == 'ws':
            i += 1
            continue
        k, j = kinds[i], i + 1
        while j < n and kinds[j] == k:
            j += 1
        runs.append([k, i, j])
        i = j
    if not runs:
        return [{'start': 0, 'end': n, 'kind': 'art', 'src_width': disp_width(row)}]
    # 空白归属：段间空白并入相邻 art 段（优先前段）；前后均 text 时并入前段
    out = []
    for k, s, e in runs:
        prev = out[-1] if out else None
        gap_start = prev[2] if prev else 0
        if s > gap_start:
            if prev and prev[0] == 'art':
                prev[2] = s          # 空白并入前一 art 段
            elif k == 'art':
                s = gap_start        # 空白并入本 art 段
            elif prev:
                prev[2] = s          # 前后均 text：并入前段
            else:
                s = gap_start        # 行首空白并入本段
        out.append([k, s, e])
    if out[-1][2] < n:
        out[-1][2] = n               # 行尾空白并入最后一段
    segs = [{'start': s, 'end': e, 'kind': k, 'src_width': disp_width(row[s:e])} for k, s, e in out]
    segs = _merge_narrow_kanji_art(segs, row)
    # 2026-10-10 口径统一（P1）：**无决定性台词证据**的 text 段降为 art。
    # 判据与 aa_translate._rule_dialogue_run_ok / _max_real_dialogue_run 同源：段内没有任何
    # 「非装饰假名、非装饰汉字（_max_real_dialogue_run>0）」的字符 → 是画面笔画，不是台词。
    # 为什么必须降：① 这些段的 conf 本来 <0.65 不会被送模型，但 kind=text 会让**残留检测**
    #   把它们当台词（`_text_segment_part` 只按 kind 取段）；② 段级协议里 kind=art 的段会被
    #   `_verify_art_alignment` 逐列校验，画面笔画因此获得 art 级保护。
    # 实测（やる夫は供給源になるようです 30 章）：82,462 个 text 段里 **52,544（63.7%）** 是
    # 纯装饰（ヽ/‐/∨/＞/∧/、/＜/j/V/r/ｉ/Y/z/ゝ/′/〉/≧…），降级后这些列不再参与台词口径。
    segs = _demote_non_dialogue_text_segs(segs, row)
    for sg in segs:
        conf = _seg_confidence(sg['kind'], row[sg['start']:sg['end']])
        if conf < 0.65:
            sg['confidence'] = round(conf, 2)
    return segs


def _art_kana_seg_is_art(text):
    """2026-10-10 P1-1: 段级艺术判据 —— 与生产链同一个函数（common/aa_art_kana.seg_is_art）。

    表按作品语料标定（`tools/calibrate_art_kana.py`）；本工具 main() 里会显式把当前作品
    的表路径喂给 `aa_art_kana.set_table_path`，否则退回内置基线与写死表。
    """
    try:
        import aa_art_kana as AK
        return AK.seg_is_art(text)
    except Exception:
        return False


def _demote_non_dialogue_text_segs(segs, row):
    """text 段内无决定性台词证据 → 降为 art（2026-10-10 P1；见 segment_line 注释）。

    只改 kind（span/src_width 不动，行覆盖不变），并把相邻的同类段并回去，避免段表碎片化。
    反例保护：单字真台词（`え？`/`あ`）由 `_max_real_dialogue_run>=1` 保住——降级只针对
    **一个字都不算台词**的段（纯装饰/散落笔画）。"""
    out = []
    for sg in segs:
        k = sg['kind']
        if k == 'text':
            _t = row[sg['start']:sg['end']]
            # 2026-10-10 P1-1: 除"无真实台词 run"外，再与生产同一判据(艺术假名表)对齐
            if _max_real_dialogue_run(_t) == 0 or _art_kana_seg_is_art(_t):
                k = 'art'
        sg = dict(sg, kind=k)
        if out and out[-1]['kind'] == k:
            out[-1]['end'] = sg['end']
            out[-1]['src_width'] = disp_width(row[out[-1]['start']:out[-1]['end']])
            continue
        out.append(sg)
    return out



def _kanji_art_core(txt):
    """art 段内容是否只有 ART_KANJI 汉字（允许首尾空白）→ 返回剥空白后的核心串，否则 None。

    ART_KANJI 是「AA 里常被画出来」的汉字黑名单（一/二/人/王/十/上/下/三/口/力…），
    但它们同时也是常用台词汉字（一昨日/一人/二度/王様/十分）——单看字符无法区分，
    只能靠**位置与紧贴关系**判（见 _merge_narrow_kanji_art）。"""
    core = txt.strip(' \u3000')
    if core and all('\u4e00' <= c <= '\u9fff' and c in ART_KANJI for c in core):
        return core
    return None


def _trailing_kanji_run(txt):
    """txt 结尾处的 ART_KANJI 汉字连串（紧贴 txt 末尾，不跨空白）。"""
    k = len(txt)
    while k > 0 and '\u4e00' <= txt[k - 1] <= '\u9fff' and txt[k - 1] in ART_KANJI:
        k -= 1
    return txt[k:]


def _leading_kanji_run(txt):
    """txt 开头的 ART_KANJI 汉字连串（紧贴 txt 开头，不跨空白）。"""
    k = 0
    while k < len(txt) and '\u4e00' <= txt[k] <= '\u9fff' and txt[k] in ART_KANJI:
        k += 1
    return txt[:k]


def _dialogue_neighbour(text):
    """被并入汉字的那一侧 text 段是否"真台词段"（≥3 字正文 且 段置信度 ≥0.65）。

    2026-09-16 实测（全库 mixed 行抽样 336k 行）：紧贴判据若只看"有假名/汉字"
    （has_real），会把绘图碎片段也当邻居——AA 用 森/彡/芹/笊/斗… 画的脸/logo 里，
    单字碎段（'少'/'筏'/'竿'/'x仁'/'ん'）与 ART_KANJI 汉字紧贴，并进来只有噪点
    （'斗斧示'/'彡州く彡介'/'抖竿'）。收紧后全库变更量 −43%，撤销的行几乎全是这类绘图噪点，
    而真台词行（'日本発祥の…' 前接'日'、'…物語る怪'+'人'、'土産話…' 前接'土'）全部保留。"""
    return (len(text.strip(' \u3000')) >= 3
            and _seg_confidence('text', text) >= 0.65)


def _merge_narrow_kanji_art(segs, row):
    """合并被窄汉字 art 段切走的 text 段（两趟：① 句中三合一 → ②③ 行首/行尾紧贴汉字）。

    背景：ART_KANJI 黑名单里的常用汉字（一/二/人/王/十/上/日/入…）在 AA 里既是被画出来的
    笔画、又是台词用字。被 `is_art_char` 判成 art 段的那些字**不进模型**，于是：
      - 句中：台词被切碎成多段独立翻译（语义断裂 + 段间 pad 堆 U+3000）→ ① 三合一（2026-08-11）
      - 行首/行尾：汉字原样留在译文里，看着像"第一个字没译"（首文字漏译，2026-09-16）

    判据（见各子函数）：位置（紧贴哪个 text 段）+ 邻居是不是真台词段（`_dialogue_neighbour`）
    + 汉字连串宽度 ≤6 格 + 装饰汉字两侧有空白/边框则不并。子函数顺序不能颠倒
    （① 先合并句中碎段，②③ 才有"真台词邻居"可判，`一昨日` = art『一』+text『昨』+art『日』+…）。"""
    return _glue_edge_kanji_runs(_merge_interior_kanji_art(segs, row), row)


def _merge_interior_kanji_art(segs, row):
    """① 原规则（2026-08-11，行为逐字节保留）：text-art-text 三合一。

    art 段窄（≤6 格）且内容全是 ART_KANJI 汉字、两侧都是 text 段、至少一侧有真台词
    → 前 text 段 end 扩展到后 text 段 end；连续链（text-art-text-art-text…）全部并入。"""
    out = []
    i = 0
    n = len(segs)
    while i < n:
        sg = segs[i]
        if (sg['kind'] == 'art' and sg['src_width'] <= 6
                and i + 1 < n and segs[i + 1]['kind'] == 'text'
                and out and out[-1]['kind'] == 'text'):
            art_txt = row[sg['start']:sg['end']]
            if art_txt and _kanji_art_core(art_txt) == art_txt:
                prev_txt = row[out[-1]['start']:out[-1]['end']]
                next_txt = row[segs[i + 1]['start']:segs[i + 1]['end']]

                def has_real(t):
                    return any((KANA_RE.match(c) and c not in ART_KANA_CHARS and c not in FACE_KANA)
                               or ('\u4e00' <= c <= '\u9fff') for c in t)

                if has_real(prev_txt) or has_real(next_txt):
                    out[-1]['end'] = segs[i + 1]['end']
                    out[-1]['src_width'] = disp_width(row[out[-1]['start']:out[-1]['end']])
                    i += 2
                    continue
        out.append(sg)
        i += 1
    return out


def _glue_edge_kanji_runs(segs, row):
    """②③ 2026-09-16 **首/尾文字漏译**：行首/行尾紧贴真台词的 ART_KANJI 汉字连串并回 text 段。

    为什么必须放在 ① **之后**：`一昨日の話だ` 的原始段序列是
    art『一』+ text『昨』+ art『日』+ text『の話だ』（日 也是 ART_KANJI）——① 先把它并成
    text『昨日の話だ』，『一』的邻居此刻才算真台词（单字碎段『昨』不算：绘图碎片段
    『少』『筏』『ん』会因此把画里的汉字拖进模型，实测收紧后噪点 −43%）。
    **一轮不够**：汉字连串被 art 段夹住时（art『一』+ art『二』+ text）要第二轮才合得上。"""
    for _ in range(2):
        out = []
        changed = False
        i = 0
        n = len(segs)
        while i < n:
            sg = segs[i]
            if sg['kind'] == 'art':
                art_txt = row[sg['start']:sg['end']]
                prev_txt = out[-1] if out and out[-1]['kind'] == 'text' else None
                next_txt = (segs[i + 1] if i + 1 < n and segs[i + 1]['kind'] == 'text' else None)
                # ② 行尾侧：art 段末尾的汉字连串紧贴后 text 段 → 搬进后段
                if art_txt and art_txt[-1] not in ' \u3000' and next_txt is not None:
                    run = _trailing_kanji_run(art_txt)
                    if run and disp_width(run) <= 6 \
                            and _dialogue_neighbour(row[next_txt['start']:next_txt['end']]):
                        cut = len(art_txt) - len(run)
                        if cut > 0:      # 边框/空白留在 art 段（art 列位不变）
                            out.append({'kind': 'art', 'start': sg['start'], 'end': sg['start'] + cut,
                                        'src_width': disp_width(row[sg['start']:sg['start'] + cut])})
                        next_txt['start'] = sg['end'] - len(run)
                        next_txt['src_width'] = disp_width(row[next_txt['start']:next_txt['end']])
                        out.append(next_txt)
                        changed = True
                        i += 2
                        continue
                # ③ 行首侧：art 段开头的汉字连串紧贴前 text 段 → 搬进前段
                if art_txt and art_txt[0] not in ' \u3000' and prev_txt is not None:
                    run = _leading_kanji_run(art_txt)
                    if run and disp_width(run) <= 6 \
                            and _dialogue_neighbour(row[prev_txt['start']:prev_txt['end']]):
                        cut = len(run)
                        prev_txt['end'] = sg['start'] + cut
                        prev_txt['src_width'] = disp_width(row[prev_txt['start']:prev_txt['end']])
                        if cut < len(art_txt):   # 余下的边框/空白留在 art 段
                            out.append({'kind': 'art', 'start': sg['start'] + cut, 'end': sg['end'],
                                        'src_width': disp_width(row[sg['start'] + cut:sg['end']])})
                        changed = True
                        i += 1
                        continue
            out.append(sg)
            i += 1
        segs = out
        if not changed:
            break
    return segs


def classify_content(row, is_lz):
    """作者先验加权分类 v2.2。
    关键修正（对比 v1.5 / v2.1）：
      - 只对 anon(名無し) 放宽漏标；lz(楼主) 保持 v1 逻辑
      - 两分支都要求「含真实日文对话字符」才可判 text/mixed（防装饰艺术行漏入模型）
    is_lz=True → v1.5 逻辑 + 对话门禁
    is_lz=False → 放宽（text≥2；半角片假名按文本；混合需更大艺术段）+ 对话门禁
    返回 (kind, confidence, reason)"""
    if not row.strip():
        return 'blank', 1.0, ['blank']

    if is_lz:
        # === 楼主帖：v1.5 逻辑 + 真实对话门禁（连续段≥2）===
        if _max_real_dialogue_run(row) < 2:
            return 'art', 0.9, ['no_dialogue']
        segs = []
        for ch in row:
            if ch.isspace():
                continue
            cls = 'art' if is_art_char(ch) else 'text'
            if segs and segs[-1][0] == cls:
                segs[-1][1] += 1
            else:
                segs.append([cls, 1])
        art = sum(n for c, n in segs if c == 'art')
        txt = sum(n for c, n in segs if c == 'text')
        if txt == 0:
            return 'art', 0.95, ['no_text']
        if txt < 5:
            # 2026-08-29 台词门禁调优(A): 短台词不再整行降 art。若 _max_real_dialogue_run>=2
            # (已排除装饰字符后仍有连续真对话) 说明是短台词(そう/でも/命令認証/ブウン/ズポッ
            # /ｷｭﾑｷｭﾑ/ｹﾞｾﾇ 等) —— 保留 text; 否则(纯散落装饰)才降 art。
            if _max_real_dialogue_run(row) >= 2:
                return 'text', 0.8, ['lz_short_dialogue']
            return 'art', 0.85, ['lz_text_lt5']
        if art == 0:
            return 'text', 0.92, ['lz_pure_text']
        art_max = max((n for c, n in segs if c == 'art'), default=0)
        txt_max = max((n for c, n in segs if c == 'text'), default=0)
        if art_max >= 3 and txt_max >= 3:
            return 'mixed', 0.8, ['lz_mixed']
        return 'text' if txt >= art else 'art', 0.78, ['lz_dotted']
    else:
        # === 名無し帖：放宽漏标 + 对话门禁 ===
        if not _has_dialogue(row, hankaku=True):
            return 'art', 0.9, ['no_dialogue']
        art, txt, art_max, txt_max, _ = _seg_counts(row, treat_hankaku_as_text=True)
        if txt < 2:
            return 'art', 0.85, [f'anon_text_lt2({txt})']
        if art == 0:
            return 'text', 0.93, ['anon_pure_text']
        if art_max >= 3 and txt_max >= 3:
            return 'mixed', 0.78, ['anon_mixed']
        return 'text', 0.8, ['anon_dotted_art']


def _expand_row_lines(row):
    """行内裸换行的展开：HTML 里 <br> 之间夹着字面 \\n 时 clean() 会保留它，
    落盘 txt（'\\n'.join(行列表)）会把它展开成多行 → 解析端必须同样展开，
    否则 len(行) < txt 行数，整章 meta 被行数校验拦掉（2026-09-13 实测：
    真・女神転生オタクくんサマナー entry_16812 解析 3862 vs txt 3890，16 行含 28 个裸换行）。"""
    return row.split('\n') if '\n' in row else [row]


def _expand_lines(lines):
    """同 _expand_row_lines，作用于整份行列表（site.extract 返回的行同样含裸换行）。"""
    if not any('\n' in l for l in lines):
        return lines
    return [sub for l in lines for sub in _expand_row_lines(l)]


def _site_parse_posts(site, seg):
    """站点可选钩子 parse_posts_auto(seg)：多形态站点用它保证与 extract() 同口径
    （yaruo_matome 新格式 <dt> / 2021 前旧格式 `.NNN ：` <p> 流式；只调 parse_posts
    会返回 0 帖 → 行数不齐 → 整章 meta 跳过）。无钩子站点仍用 parse_posts。"""
    fn = getattr(site, "parse_posts_auto", None) or site.parse_posts
    return fn(seg)


def _extract_article_seg(site, raw, eid):
    """站点 extract_article 签名不统一：aamatome_2nt / blog_2nt / matomemo3 / mukankei_fc2
    需要 eid 定位正文锚点（<h2 id="e<eid>"> 等）。旧实现无条件 `extract_article(raw)` →
    这五个作品 TypeError → meta 整章跳过/静默 fallback is_text_line（2026-09-13 实测
    fukou / barasui_murabito / kinshijaku_danna / サクラ大戦 / mukankei_2016 的
    line_meta_v2 全空）。按签名传参，别再用「猜」。
    站点无该 API（白头系 yaruonichijou 是 `===== AA块 N =====` 非帖子型语料）→ 返回 None。"""
    fn = getattr(site, "extract_article", None)
    if fn is None or not hasattr(site, "parse_posts"):
        return None
    try:
        need_eid = len(inspect.signature(fn).parameters) > 1
    except (TypeError, ValueError):
        need_eid = False
    return fn(raw, eid or '') if need_eid else fn(raw)


def classify_raw_v2(site, raw, txt_lines, eid=None):
    """返回 {行号: {"kind","confidence","reason","segments"}}；行号与 txt 行序一致。
    mixed 行附 segments（B1: [{start,end,kind,src_width}]），非 mixed 行 segments=None。
    eid：供需要它的站点 extract_article 定位正文（见 _extract_article_seg）。"""
    seg = _extract_article_seg(site, raw, eid)
    if seg is None:
        print(f'  警告: 站点 {getattr(site, "SITE", "?")} 无 extract_article/parse_posts'
              f'（非帖子型语料）, 跳过 meta 生成')
        return None
    posts = _site_parse_posts(site, seg)
    meta = {}
    n = 3                      # 第1行=#标题, 第2行=#原帖地址, 第3行起为帖子内容
    # 无头帖（rusalka_fc2 每篇首帖：原作者排版时把首帖帖头去掉，正文仍是楼主的）→ header 为 None。
    # 与站点 extract() 同口径：不产生帖头行；该帖按**楼主帖**分类（is_lz），否则 AA 纹理行会被
    # anon 分支判 text/mixed 送进模型（实测 16 章 179 行）。站点用 HEADLESS_FIRST_IS_LZ 声明。
    headless_lz = bool(getattr(site, "HEADLESS_FIRST_IS_LZ", False))
    for header, rows in posts:
        header = header or ""
        if header:
            for _h in _expand_row_lines(header):
                meta[n] = {'kind': 'header', 'confidence': 1.0, 'reason': ['header'], 'segments': None}
                n += 1
            is_lz = bool(HEADER_AUTHOR.search(header))
        else:
            # 无头帖不产帖头行（`_expand_row_lines('')` 会返回 [''] 白占一行 → 行数不齐整章跳过）
            is_lz = headless_lz
        # 兼容两种 parse_posts 返回：[(row, color)...] 或 [row...]（yaruobook）
        lz_rows = [item[0] if isinstance(item, (list, tuple)) else item for item in rows]
        # 行内裸换行展开：1 元素 = 1 txt 行（否则行数与 txt 不齐 → 整章 meta 跳过）
        lz_rows = [sub for row in lz_rows for sub in _expand_row_lines(row)]
        # 楼主帖: 单行判定 + 卷积修正(窗口±2 + 长度判断 + 邻行降权, 补漏标)
        results = _classify_lz_conv(lz_rows, is_lz)
        for row, (kind, conf, reason) in zip(lz_rows, results):
            segments = None
            if kind == 'mixed':
                segments = segment_line(row, treat_hankaku_as_text=not is_lz)
            meta[n] = {'kind': kind, 'confidence': conf, 'reason': list(reason), 'segments': segments}
            n += 1
    if n - 1 != len(txt_lines):
        print(f'  警告: 行数不齐 {n-1} vs txt {len(txt_lines)},跳过')
        return None
    return meta


def _conv_row_is_dialogue(row, lz_rows, idx_in_lz):
    """卷积修正: 单行被判 art 时, 若邻行构成'台词区'(多个邻行 true_dialogue_run>=2)则提升为台词。
    仅楼主帖(lz)启用; 名無し帖保持单行。窗口±2, ±1权重2, ±2权重1, 需 strength>=3。
    row: 当前行文本; lz_rows: 该楼内全部行文本(未含帖头); idx_in_lz: 当前行在 lz_rows 的索引。
    返回 True=应判台词(借邻行证据), False=维持 art。"""
    # 长度判断: 当前行必须至少有 1 个台词字符(非纯边框/纯空白), 否则不救
    if len(re.findall(r'[ぁ-んァ-ヶ\u4e00-\u9fff]', row)) < 2:
        return False
    strength = 0
    for d in (-2, -1, 1, 2):
        ni = idx_in_lz + d
        if 0 <= ni < len(lz_rows):
            nr = lz_rows[ni]
            if nr.strip() == '':
                continue
            if _max_real_dialogue_run(nr) >= 2:
                strength += (2 if abs(d) == 1 else 1)
    return strength >= 3


def _classify_lz_conv(lz_rows, is_lz):
    """对一栋楼主帖正文做单行判定。返回 [(kind, conf, reason)...] 与 rows 对齐。

    注: 曾实现卷积借邻行(窗口±2 + 长度判断 + 邻行降权)补漏标, 但实测发现:
    - 半角修复后, 真台词(そう/でも/ｷｭﾑ/ｶﾞﾋﾞｰﾝ等) run 已>=2, 单行 classify_content 可直接识别
    - 走到借邻行的 run<2 行几乎都是散落假名装饰, 借邻行只加误标(mix台词行附近 191条)
    - 故移除了借邻行, 保留半角修复 + 单行判定(entry_2687 误标 640->449, 漏标不变)
    仅楼主帖走此路径(名無し帖单行判定已够)。"""
    results = []
    for row in lz_rows:
        kind, conf, reason = classify_content(row, is_lz)
        results.append((kind, conf, reason))
    return results


def _serialize(meta):
    """meta dict → 可写 JSON 的 dict；segments 仅 mixed 行存在时输出。"""
    out = {}
    for k, v in meta.items():
        d = {"kind": v["kind"], "confidence": round(v["confidence"], 2), "reason": list(v["reason"])}
        if v.get("segments"):
            d["segments"] = v["segments"]
        out[str(k)] = d
    return out


def main():
    args = sys.argv[1:]
    work = args[args.index('--work') + 1] if '--work' in args else None
    only = None
    if '--chapter' in args:
        only = args[args.index('--chapter') + 1]
    # --missing: 只补缺失的 meta（已有 json 不动）——批量补刷用，避免重写全量文件
    missing_only = '--missing' in args
    cfg = registry.get(work)
    # 2026-10-10 P1-1: 艺术假名表（按作品语料标定）——生成端与运行时同一张表
    try:
        import aa_art_kana as _AK
        _tbl = os.path.join(cfg['work_dir'], 'art_kana_%s.json' % work)
        if os.path.exists(_tbl):
            _AK.set_table_path(_tbl)
    except Exception:
        pass
    site = importlib.import_module('sites.' + cfg['site'])
    raw_dir, work_dir = cfg['raw_dir'], cfg['work_dir']
    meta_dir = os.path.join(work_dir, 'line_meta_v2')
    os.makedirs(meta_dir, exist_ok=True)
    files = sorted(f for f in os.listdir(raw_dir) if f.endswith('.html'))
    if only:
        files = [f for f in files if only in f]
    n_ok = n_skip = n_bad = 0
    for fname in files:
        raw = open(os.path.join(raw_dir, fname), encoding='utf-8', errors='replace').read()
        m = EID_FROM_FNAME.search(fname)
        eid = m.group(1) if m else ''
        base = f'entry_{eid}' if cfg['name_style'] == 'entry_eid' else os.path.basename(fname).replace('.html', '')
        if missing_only and os.path.exists(os.path.join(meta_dir, base + '.json')):
            n_skip += 1
            continue
        try:
            txt_lines, _, _ = site.extract(raw, eid, fname)
        except Exception as e:
            print(f'{fname}: extract 失败 {e},跳过', flush=True)
            continue
        if not txt_lines:
            continue
        # 行内裸换行展开（与落盘 txt 的行号口径一致；否则 classify_raw_v2 行数校验不齐）
        txt_lines = _expand_lines(txt_lines)
        meta = classify_raw_v2(site, raw, txt_lines, eid)
        if meta is None:
            n_bad += 1
            continue
        # 序列化: {行号: {"kind":..., "confidence":..., "reason":[...]}, mixed 行附 segments}
        out = _serialize(meta)
        with open(os.path.join(meta_dir, base + '.json'), 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False)
        n_ok += 1
        cnt = {}
        for v in meta.values():
            cnt[v["kind"]] = cnt.get(v["kind"], 0) + 1
        print(f'{base}: {cnt}', flush=True)
    print(f'ALL DONE | 生成 {n_ok} | 已有跳过 {n_skip} | 行数不齐 {n_bad}')


if __name__ == '__main__':
    main()

