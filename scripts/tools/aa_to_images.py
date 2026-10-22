"""AA 汉化 txt → 竖屏长图(按画面切块,块内字号自适应)
用法: python scripts/tools/aa_to_images.py --work <id> [--chapter 文件名子串] [--out <dir>] [--width px]
像素烘焙 + MS Gothic 等宽:手机/任何设备看都不破版(无字体依赖)。"""
import sys, os, re, json, argparse, importlib
from html.parser import HTMLParser

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
sys.path.insert(0, os.path.join(BASE, "scripts", "works"))
import registry
from aa_common import (disp_width, is_text_line, ART_HIRA, ART_KATA, ART_KANJI,
                       meta_path, json_read_any, list_txt)

# 与交付 HTML 同款字体语义: 主=MS PGothic(比例,浏览器实测),缺字回退 SimSun
# 布局宽度与字形都按"字符在 PGothic cmap 则用 PGothic,否则 SimSun"逐字符决定
FONT_MAIN = r"C:\Windows\Fonts\msgothic.ttc"        # ttc index 2 = MS PGothic
FONT_FB = r"C:\Windows\Fonts\simsun.ttc"             # 简体回退(HTML 栈同款)
FONT_SIMHEI = r"C:\Windows\Fonts\simhei.ttf"         # 用户楼加大文本行统一字体(消 PGothic/SimSun 粗细混排)
CMAP_JSON = os.path.join(BASE, "fonts", "msgothic_cmap.json")

_sh_cmap_cache = None
def get_simhei_cmap():
    """SimHei cmap(用户楼文本行统一字体用)。逐字符判定 SimHei 是否有字形,
    缺字(如・U+30FB)回退 PGothic 不乱码(M14 00068ff 方案)。缓存避免每字符重复读。"""
    global _sh_cmap_cache
    if _sh_cmap_cache is None:
        try:
            from fontTools.ttLib import TTFont
            _sh_cmap_cache = TTFont(FONT_SIMHEI).getBestCmap()
        except Exception:
            _sh_cmap_cache = {}
    return _sh_cmap_cache

def pick_font(override=None):
    if override:
        if not os.path.exists(override):
            raise SystemExit("字体不存在: " + override)
        return override
    return FONT_MAIN

def load_main_cmap():
    if os.path.exists(CMAP_JSON):
        return set(json.load(open(CMAP_JSON, encoding='utf-8')))
    return None   # 缺 cmap 时全部走主字体

BG = (255, 253, 247)          # #fffdf7 同 HTML pre 背景
FG = (34, 34, 34)             # #222
PAD = 16
# PNG-8 固定调色板(2026-08-14): AA 主色精确保留,抗锯齿过渡色近似
# 顺序: 背景/正文/帖头作者绿/ID蓝/红/深红/灰1/灰2
PALETTE8 = [BG, FG, (0, 136, 0), (0, 0, 255), (255, 0, 0), (128, 0, 0), (162, 161, 157), (67, 66, 65)]
LINE_RATIO = 1.35
MIN_FS, MAX_FS = 16, 40
TEXT_MAX_FS = 64        # 纯文本块字号上限(无 AA 对齐约束,可更大)
TEXT_W = 580            # 纯文本块(无 AA)宽度标杆: 内容区恒 580px(2026-08-14 用户 540→580)
WRAP_W = 500            # 纯文本 text 行宽度 >500px 折行(用户定值,2026-08-14)
AA_RIGHT_PAD = 16       # AA 块(自适应)右侧额外留白(用户方案 2026-08-14)
MAX_BLOCK_ROWS = 300          # 帖头制下仅防超长帖单块过高

def parse_style(s):
    """css 样式串 → ((r,g,b), bold)"""
    color, bold = FG, False
    if not s:
        return color, bold
    if re.search(r'font-weight\s*:\s*bold', s):
        bold = True
    m = re.search(r'color\s*:\s*(#\w{6}|rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\))', s)
    if m:
        c = m.group(1)
        if c.startswith('#'):
            color = tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
        else:
            color = tuple(int(x) for x in re.findall(r'\d+', c))
    return color, bold

class _SegParser(HTMLParser):
    """render_header 的 HTML → [(text, style_str)]"""
    def __init__(self):
        super().__init__()
        self.segs = []
        self.cur = None
    def handle_starttag(self, tag, attrs):
        self.cur = dict(attrs).get('style', '')
    def handle_endtag(self, tag):
        self.cur = None
    def handle_data(self, data):
        self.segs.append((data, self.cur or ''))

def _split_at_width(line, width):
    """从行首累计显示宽度到 width → (前缀, 剩余)。列位对齐用（同 gen_aa_html._width_prefix）"""
    w = 0
    for j, ch in enumerate(line):
        cw = disp_width(ch)
        if w + cw > width:
            return line[:j], line[j:]
        w += cw
    return line, ''


def line_segments(line, cm_style, site):
    """txt 行 → [(text, (color, bold))],复用帖头渲染与 1D10 加粗逻辑"""
    if cm_style == 'font-weight:bold':
        out, pos = [], 0
        for m in re.finditer(r'【1D10:[^】]*】', line):
            out.append((line[pos:m.start()], (FG, False)))
            out.append((m.group(0), (FG, True)))
            pos = m.end()
        out.append((line[pos:], (FG, False)))
        return out
    hdr = site.render_header(line)
    if hdr:
        p = _SegParser()
        p.feed(hdr)
        return [(t, parse_style(st)) for t, st in p.segs]
    if isinstance(cm_style, dict):
        # 片段着色格式 {"style":..,"spans":[{"text","cols"}..]}：只给命中的片段上色（同 gen_aa_html）
        c, b = parse_style(cm_style.get('style') or '')
        want = []
        for sp in (cm_style.get('spans') or []):
            if isinstance(sp, dict):
                t, cols = sp.get('text') or '', sp.get('cols')
            else:
                t, cols = (sp or ''), None
            if t:
                k = line.find(t)
                if k != -1:
                    want.append((k, k + len(t)))
                    continue
            if cols and len(cols) == 2:     # 译文改写了片段 → 按原行显示列回填
                pre, rest = _split_at_width(line, cols[0])
                start = len(pre)
                seg, _ = _split_at_width(rest, max(0, cols[1] - cols[0]))
                if seg:
                    lead = len(seg) - len(seg.lstrip(' \u3000'))
                    trail = len(seg) - len(seg.rstrip(' \u3000'))
                    a, e2 = start + lead, start + len(seg) - trail
                    if e2 > a:
                        want.append((a, e2))
        if want:
            out, pos = [], 0
            for a, e in sorted(want):
                if a < pos:
                    continue
                out.append((line[pos:a], (FG, False)))
                out.append((line[a:e], (c, b)))
                pos = e
            out.append((line[pos:], (FG, False)))
            return [seg for seg in out if seg[0]]
        # 片段在译文里找不到：整行原本全红 → 整行上色，否则不上色
        return [(line, (c, b) if cm_style.get('all') else (FG, False))]
    color, bold = parse_style(cm_style)
    return [(line, (color, bold))]

# 艺术符号: 文本行加大判定用(含这些符号的行 = AA 艺术行,不加大)
ART_SYMS = set('│｜┃／＼＿￣─━┌┐└┘├┤┬┴┼■□◆◇△▲▽▼○●◎※〒§☆★→←↑↓∴∵♂♀╋╂╇╅╆┏┓┗┛┣┫┳┻|{}（）()!/ヽノ')

def is_big_text_line(t):
    """可加大的文本行 = 管线文本行 + 行宽≤50格 + 艺术字符占比低(≤40%)
    (台词/规则 → True;AA 艺术行(符号/艺术假名/艺术汉字密集 或 宽行)→ False)
    注: 不能用'含 ART_KANJI 即 False'——台词里'实力'的'力'也是艺术汉字;
    行宽上限防 AA 宽行(横幅/人物)误判"""
    if not is_text_line(t):
        return False
    if disp_width(t) > 100:          # >50 格: AA 横幅/宽行,不加大
        return False
    art = sum(1 for c in t if c in ART_SYMS or c in ART_HIRA or c in ART_KATA or c in ART_KANJI)
    total = sum(1 for c in t if not c.isspace())
    return art / max(total, 1) <= 0.4

def load_line_meta(work_dir, base):
    """读 line_meta.json(行号→type);无则 None(渲染回退规则判定)"""
    import json as _json
    p = os.path.join(work_dir, 'line_meta', base + '.json')
    if os.path.exists(p):
        try:
            with open(p, encoding='utf-8') as f:
                return _json.load(f)
        except Exception:
            return None
    return None

def load_line_meta_v2(work_dir, base):
    """读 line_meta_v2.json(行号→{kind, confidence, reason, segments[]})——嵌字段级分类源。
    segments: [{start,end,kind,src_width,confidence}] 字符级 text/art 边界(2026-08-14 嵌字方案)
    .json / .json.gz 透明（压缩后仍可读）。"""
    p = meta_path(os.path.join(work_dir, 'line_meta_v2'), base)
    if not os.path.exists(p):
        return None
    return json_read_any(p, None)


def load_line_meta_v3(work_dir, base):
    """读 line_meta_v3.json(行号→'art'|'text')——视觉模型按块判定结果(2026-08-16)。
    仅含规则粗筛的存疑行;无文件/无此行 = 沿用 v2 规则判定"""
    import json as _json
    p = os.path.join(work_dir, 'line_meta_v3', base + '.json')
    if os.path.exists(p):
        try:
            with open(p, encoding='utf-8') as f:
                return _json.load(f)
        except Exception:
            return None
    return None

# 汉化中文兜底: 短台词被 v2 误判 art(lz_text_lt5/anon_text_lt2)时,汉化后含连续中文 → 仍嵌字
_HAN2_RE = re.compile(r'[\u4e00-\u9fff]{2,}')


def _is_art_kana(c):
    """画面用假名/符号(装饰耳形/轮廓),非台词假名。台词假名(レジェンゴ 的 レ/ジ/ゴ
    外来语表记)不是画面——扩展台词段时跳过这两种画面假名(2026-08-17 39图修复)"""
    return c in 'ヽノー=―ゝ丶　'
# AA 艺术纹理汉字(人物轮廓/阴影常用,非台词): 连续中文若全是这些字 → 纹理,不兜底嵌字
# (2026-08-14 实验发现: art 行 '二二二二' 被 _HAN2_RE 误判台词 → 整行嵌字毁画面)
_ART_TEXTS = set('二八从三爻人丶乂彡一斗父戈廴厶丿乙了又入十卜卞込刈辷厂心')

def v2_text_spans(v2_line, txt, v3_verdict=None):
    """嵌字段判定 → [(start,end)] 字符区间(整行 txt 的切片)。
    主源1: v2 segments 中 kind=text 且 conf≥0.7(mixed 行的 text 段);
    主源2: v2 整行 kind=text(纯文本行,无 segments)→ 整行嵌字;
    兜底: v2 判 art 但 reason 为文本过短规则(lz_text_lt5/anon_text_lt2——短台词
    '３．奈良/草/人名' 误判)且汉化后含连续中文 → 整行嵌字。
    no_dialogue 等 art 纹理行(二八从爻轮廓)绝不兜底——2026-08-14 实验发现
    纯中文正则会把 '二二二二'/'八芹坏' 纹理误判台词致整行嵌字毁画面。
    v3_verdict(2026-08-16 视觉辅助): 视觉模型按块判定 'art'/'text'——覆盖规则:
      art → 返回空(该行不嵌字,修误嵌); text → 强制嵌字(修漏嵌)。
    返回空列表 = 该行不嵌字。"""
    spans = []
    if v3_verdict == 'art':
        return spans
    if v3_verdict == 'text':
        # 视觉确认台词: 沿用规则判定(v2_text_spans 已给出台词段);仅当规则为空
        # (漏嵌)时才整行/子串兜底——视觉确认 text 但规则没找到,按 art 占比分流
        # (2026-08-16: 171 行规则已给 (63,72),视觉确认后直接沿用,防子串收缩丢字)
        pass
    if not v2_line or not isinstance(v2_line, dict):
        if v3_verdict == 'text':
            return [(0, len(txt))]
        return spans
    segs = v2_line.get('segments')
    if segs:
        # 收集 text 段(conf≥0.5 或无 conf)——含中文段必选;无中文段(画面误标 ＜/ニ/∧)
        # 仅当其紧邻已选中文段时才纳入(台词尾缀 '-saki-』』',2026-08-14 第四轮)
        cand = []
        for s in segs:
            if s.get('kind') == 'text':
                conf = s.get('confidence')
                if conf is not None and conf < 0.5:
                    continue
                cand.append((s['start'], s['end'], bool(_HAN2_RE.search(txt[s['start']:s['end']]))))
        for s0, s1, has_han in cand:
            if has_han:
                spans.append((s0, s1))
        if spans:
            # 尾缀并入: 无中文段若与前/后中文段相邻(间隔≤2字符,容 '-saki' 前的 ' -' art 段)
            # 则加入(台词尾缀 '-saki-』』',2026-08-14 第四轮)
            for s0, s1, has_han in cand:
                if has_han:
                    continue
                if any(abs(s0 - es1) <= 2 or abs(es0 - s1) <= 2 for es0, es1 in spans):
                    spans.append((s0, s1))
            return spans
        # segments 无有效台词段 → 落兜底2(0019 真台词段 kind=None,segs 分支找不到)
    if v2_line.get('kind') == 'text':
        # 纯文本行(无 segments): 整行嵌字。但 lz_dotted 等规则会把
        # '画面+台词'行(人物脸+来哦)整行判 text——行内 art 字符占比高 或
        # 行首非空字符是艺术字符(画面在前台词在后)时,只嵌台词子串,
        # 防整行嵌字毁画面(2026-08-14 第三轮: 发誓行 art=21%<25% 漏网)
        non_space = [c for c in txt if not c.isspace()]
        first_ns = non_space[0] if non_space else ''
        art = sum(1 for c in non_space
                  if c in ART_SYMS or c in ART_KATA or c in ART_HIRA or c in ART_KANJI
                  or c in 'ヽ_ノー=―')
        if non_space and (art / len(non_space) > 0.15
                          or first_ns in ART_SYMS or first_ns in ART_KATA or first_ns in 'ヽ_ノ'):
            # 只嵌非艺术连续段(台词)——用汉化中文定位,防画面字符(0026 |ヽ_ _ノ)
            # 台词段('(笑)レジェンゴ' 外来语/括号)并入: 中文段向两侧扩,允许
            # 括号对+片假名(非画面),遇空格/画面符号停(2026-08-17 39图漏嵌修复)
            artset = ''.join(ART_SYMS) + ''.join(ART_KATA) + ''.join(ART_HIRA) + ''.join(ART_KANJI) + 'ヽ_ノー=―'
            # 台词允许的非中文字符(括号/片假名外来语),排除画面假名(ヽノー=― 等)
            for m in re.finditer(r'[^\s' + re.escape(artset) + r']{2,}', txt):
                # 段必须含中文(台词);'lliilil'/'.i' 等画面字母纹理无中文 → 跳过(第三轮)
                if not _HAN2_RE.search(m.group(0)):
                    continue
                s0, s1 = m.start(), m.end()
                # 向两侧扩展: 允许括号/片假名台词成分('(笑)レジェンゴ' 外来语),
                # 遇空格/画面符号/画面假名停(2026-08-17 39图漏嵌修复)
                # 画面符号(非括号): 行首画面 | / ヽ u 等
                _art_punct = set('│｜┃／＼＿￣─━┌┐└┘├┤┬┴┼■□◆◇△▲▽▼○●◎※〒§☆★→←↑↓∴∵♂♀╋╂╇╅╆┏┓┗┛┣┫┳┻|')
                while s0 > 0 and txt[s0-1] not in ' \u3000' and txt[s0-1] not in _art_punct and not _is_art_kana(txt[s0-1]):
                    s0 -= 1
                while s1 < len(txt) and txt[s1] not in ' \u3000' and txt[s1] not in _art_punct and not _is_art_kana(txt[s1]):
                    s1 += 1
                spans.append((s0, s1))
            # 独立片假名词(外来语,如 'レジェンゴ'): 连续≥2 片假名且非画面 → 台词
            # (39图网友命名列表漏嵌修复,2026-08-17)
            for m in re.finditer(r'[\u30a0-\u30ff]{2,}', txt):
                seg = m.group(0)
                if any(_is_art_kana(c) for c in seg):
                    continue
                # 只跳过与既有段重叠的部分;紧邻(中间有空格)的不同台词段都要嵌
                if any(s0 <= m.start() < s1 for s0, s1 in spans):
                    continue
                spans.append((m.start(), m.end()))
            return spans
        return [(0, len(txt))]
    if v2_line.get('kind') in ('art', 'mixed'):
        # 兜底1: v2 文本过短规则(lt)误判 art 的短台词——但 lt 也误伤纹理行
        # (⌒ｉ::/=ﾆ二二/八_人 纯轮廓也被判 lt),须再验汉化后含语义中文
        # (连续中文段含非纹理字;'二二二二'/'八'单字纹理不算)
        reasons = ' '.join(v2_line.get('reason') or [])
        if 'lt' in reasons:
            # 兜底1: lt 短台词——用中文段定位,不整行嵌(64图 {　...　}【现状】:
            # 行首 { } 是 AA 装饰括号,整行嵌会放大括号+压缩其内部空格,2026-08-14 第六轮)
            for m in _HAN2_RE.finditer(txt):
                seg = m.group(0)
                if any(c not in _ART_TEXTS for c in seg):
                    s0 = m.start()
                    while s0 > 0 and txt[s0-1] not in ' \u3000' and txt[s0-1] not in ART_SYMS:
                        s0 -= 1
                    s1 = m.end()
                    while s1 < len(txt) and txt[s1] not in ' \u3000' and txt[s1] not in ART_SYMS:
                        s1 += 1
                    spans.append((s0, s1))
                    break
        if not spans:
            # 兜底2: mixed/art 行的真台词段 v2 可能标 kind=None(0019【阿知贺三年级】在
            # 68:77 段)——用汉化中文定位台词段。仅对 v2 承认"有文本"的 reason 生效
            # (lt=文本过短, dotted=点线文本);no_dialogue=纯画面绝不兜底(第三轮修复:
            # 从劜/八芹坏/爻乂爻 等 AA 轮廓汉字全是 no_dialogue,纹理字表枚举不完)
            reasons = ' '.join(v2_line.get('reason') or [])
            if 'lt' not in reasons and 'dotted' not in reasons:
                if v3_verdict == 'text':
                    # v3 视觉确认台词(规则 no_dialogue 漏嵌): 跳过规则限制,整行嵌
                    return [(0, len(txt))]
                return spans
            for m in _HAN2_RE.finditer(txt):
                seg = m.group(0)
                if any(c not in _ART_TEXTS for c in seg):
                    # 向前扩到台词起点(非空白、非艺术符号)
                    s0 = m.start()
                    while s0 > 0 and txt[s0-1] not in ' \u3000' and txt[s0-1] not in ART_SYMS:
                        s0 -= 1
                    s1 = m.end()
                    while s1 < len(txt) and txt[s1] not in ' \u3000' and txt[s1] not in ART_SYMS:
                        s1 += 1
                    spans.append((s0, s1))
                    break
    if not spans and v3_verdict == 'text':
        # v3 视觉确认台词但规则漏嵌(no_dialogue 误判纹理行为台词): 整行嵌字
        # (2026-08-16 视觉辅助——视觉看到了规则看不到的台词上下文)
        return [(0, len(txt))]
    return spans

def post_is_op(block, site):
    """楼主楼判定: 块内帖头行作者以 ◆ 开头(fc2 系,作者=◆,用户=名無し/其他)。
    块=一帖(帖头起新块),块首非空行即帖头;merged 块可能含多帖头,任一含 ◆ 即楼主楼。"""
    for ln, t in block:
        if t.strip() and site.render_header(t):
            return '◆' in t[:80]
    return False

def big_line(t, ln, meta):
    """行是否加大。meta 键是 1-based 完整行号(文件第 N 行→'N'),
    rows 的 ln 是 0-based → 查 str(ln+1)(交付版错位 bug,2026-08-14 修);
    colormap cm 键是 0-based 与 ln 一致,绝不动。
    2026-08-14 用户方案(用户楼加大优先级最高): meta 是原文(raw)分类,汉化后文本
    可能已变(漏译行 ﾅｧｰﾙｧｰ!!→奈良—！！)——meta 判 text 直接加大;
    meta 判 art/mixed 时用汉化后文本规则(is_big_text_line)交叉验证,真台词仍加大"""
    if meta is not None:
        m = meta.get(str(ln + 1))
        if m == 'text':
            return True
        if m in ('art', 'mixed', 'blank', 'header'):
            return is_big_text_line(t)      # 汉化后文本规则兜底(漏译补译行)
        return False
    return is_big_text_line(t)

def block_is_pure_text(block, site, meta):
    """纯文本块判定(无 AA 的图片): 块内所有非空行(除帖头/空行外)都是 text。
    meta 有则查 meta(原文分类);meta 判 art 时用汉化后文本规则兜底——
    漏译补译行(ﾅｧｰﾙｧｰ!!→奈良—！！)汉化后是台词,应归纯文本块(2026-08-14)"""
    for ln, t in block:
        if not t.strip() or site.render_header(t):
            continue
        if meta is not None:
            m = meta.get(str(ln + 1))
            if m == 'text':
                continue
            if m in ('art', 'mixed'):
                if not is_big_text_line(t):   # 汉化后仍非文本(真 AA)→ 非纯文本块
                    return False
                continue
            return False
        elif not is_big_text_line(t):
            return False
    return True

def render_block(rows, site, cm, fs, width, font_path, min_width=0, text_bonus=0, meta=None, freeze_post=False, embed_v2=None, embed_v3=None):
    """rows: [(txt行, 完整行号)] → PIL 图。
    与交付 HTML 同语义: 字符在 MS PGothic cmap → PGothic 字形
    否则(简体中文等)→ SimSun。空行占行高保留,楼(帖头)间至少 2 空行;
    粗体用位移双印(无描边荧光)。渲染后按实际内容宽裁剪(≥min_width,防 hinting 偏差裁边)
    freeze_post(2026-08-14 用户方案): 楼主楼(帖头含◆)整楼不动——不加大、字体保持
    PGothic→SimSun 双回退;用户楼文本行(加大行)SimHei 统一(缺字回退 PGothic)消粗细混排
    embed_v2(2026-08-14 嵌字方案): 楼主楼的 line_meta_v2 数据——绘制完块后对
    text 段(conf≥0.7 + 汉化中文兜底)做清背景矩形(外扩2px)→ SimHei fs+2 重绘;
    艺术段像素不动;嵌字在 PNG-8 quantize 之前执行"""
    from PIL import Image, ImageDraw, ImageFont
    line_h = int(fs * LINE_RATIO)
    _fonts = {}
    _sh_cmap = get_simhei_cmap()

    def get_font(fs_line):
        """按字号缓存字体 (主/回退/SimHei)"""
        key = fs_line
        if key not in _fonts:
            _fonts[key] = (ImageFont.truetype(FONT_MAIN, fs_line, index=2),
                           ImageFont.truetype(FONT_FB, fs_line),
                           ImageFont.truetype(FONT_SIMHEI, fs_line))
        return _fonts[key]

    def row_fs(ln, txt):
        """行字号: 用户楼文本行加大,其余 = fs(header/art/mixed/blank/楼主楼全部 不加大)"""
        if text_bonus and not freeze_post and txt.strip() and not site.render_header(txt) and big_line(txt, ln, meta):
            return min(fs + text_bonus, TEXT_MAX_FS)
        return fs

    def row_h(ln, txt, blanks):
        """行高(含楼间补空行 + 自身)"""
        if not txt.strip():
            return line_h
        if site.render_header(txt):
            return line_h * (2 - blanks) + line_h   # 补足 2 空行 + 帖头行自身
        return int(row_fs(ln, txt) * LINE_RATIO)

    # 高度预算
    blanks = 0
    total_h = PAD * 2
    for ln, txt in rows:
        total_h += row_h(ln, txt, blanks)
        if not txt.strip():
            blanks += 1
        elif site.render_header(txt):
            blanks = 0
        else:
            blanks = 0
    W = max(width, min_width) + 64          # 冗余画布,渲染后裁剪
    img = Image.new('RGB', (W, total_h), BG)
    d = ImageDraw.Draw(img)
    cmap_main = load_main_cmap()
    _cache = {}

    def pick(ch, fs_line, use_simhei=False):
        """(字体, advance) — 与浏览器字体回退一致;按(字号,字符,模式)缓存。
        use_simhei(用户楼加大行): SimHei 优先,缺字回退 PGothic(不乱码)"""
        key = (fs_line, ch, use_simhei)
        hit = _cache.get(key)
        if hit is None:
            fm, ff, fsh = get_font(fs_line)
            if use_simhei:
                hit = (fsh, fsh.getlength(ch)) if ord(ch) in _sh_cmap else (fm, fm.getlength(ch))
            else:
                o = ord(ch)
                if cmap_main is not None and o not in cmap_main:
                    hit = (ff, ff.getlength(ch))
                else:
                    hit = (fm, fm.getlength(ch))
            _cache[key] = hit
        return hit

    def draw_seg(buf, font, x0, y0, color, bold, fs_line, use_simhei=False):
        if bold:                       # 位移双印加粗,无描边荧光
            d.text((x0 + 1, y0), ''.join(buf), font=font, fill=color)
        d.text((x0, y0), ''.join(buf), font=font, fill=color)
        if use_simhei:
            fm, ff, fsh = get_font(fs_line)
            return sum((fsh if ord(c) in _sh_cmap else fm).getlength(c) for c in buf)
        return sum(_cache[(fs_line, c, False)][1] for c in buf)

    y = PAD
    blanks = 0
    rightmost = 0
    embed_jobs = []              # (txt, x0, x1, y_base, color, bold) 楼主楼嵌字任务(绘制后执行)
    for ln, txt in rows:
        y += row_h(ln, txt, blanks)      # 先推进行高(含补空)
        if not txt.strip():
            blanks += 1
            continue
        if site.render_header(txt):
            blanks = 0
        else:
            blanks = 0
        fs_line = row_fs(ln, txt)
        use_sh = fs_line > fs            # 加大行=用户楼文本行 → SimHei 统一(缺字回退 PGothic)
        x = PAD
        # 楼主楼嵌字: 记录本行 text 段 x 区间(绘制时同步;freeze 楼 fs_line==fs)
        embed_spans = v2_text_spans(embed_v2.get(str(ln + 1)) if embed_v2 else None, txt,
                                    (embed_v3 or {}).get(str(ln + 1))) if (embed_v2 and freeze_post and fs_line == fs and not site.render_header(txt)) else []
        for text, (color, bold) in line_segments(txt, cm.get(str(ln)), site):
            buf, cur_font = [], None
            for ch in text:
                f, adv = pick(ch, fs_line, use_sh)
                if cur_font is not None and f is not cur_font:
                    x += draw_seg(buf, cur_font, x, y - int(fs_line * LINE_RATIO), color, bold, fs_line, use_sh)
                    buf = []
                buf.append(ch)
                cur_font = f
            if buf:
                x += draw_seg(buf, cur_font, x, y - int(fs_line * LINE_RATIO), color, bold, fs_line, use_sh)
        if embed_spans:
            # 记录嵌字段: v2 区间是原行 txt 字符索引 → 逐字符累加 advance 算像素 x 区间
            # 先扩展段尾: 台词后的 '-saki-』』' 等尾缀(v2 切成独立 art/text 段,无中文被跳过
            # → '【咲　' 与 '-saki-】' 重叠)——合并前把紧随的 字母/数字/'-'/'』』]】' 并入
            # (2026-08-14 第四轮: 红豆【咲 -saki-】重叠)
            _ext = []
            for s0, s1 in sorted(embed_spans):
                while s1 < len(txt) and (txt[s1].isalnum() or txt[s1] in '-ー』」]】』)）'):
                    s1 += 1
                _ext.append((s0, s1))
            # 合并相邻 spans(间距≤1字符宽): 防多段各自清背景重绘时 SimHei 字宽膨胀重叠
            # (问题C: 0026'种人'、0001'【红豆】小ー咲ー',2026-08-14 第三轮)
            merged_spans = []
            for s0, s1 in _ext:
                if merged_spans and s0 - merged_spans[-1][1] <= 1:
                    merged_spans[-1] = (merged_spans[-1][0], max(merged_spans[-1][1], s1))
                else:
                    merged_spans.append((s0, s1))
            # 补缺口: span 间若夹着中文(如'个'被 v2 切在段边界外,3020 发誓在这'个'阿知贺)
            # 则并入前段——台词内部不应有中文缺口(2026-08-14 第四轮)
            filled = []
            for s0, s1 in merged_spans:
                if filled and filled[-1][1] < s0:
                    gap = txt[filled[-1][1]:s0]
                    if _HAN2_RE.search(gap):
                        filled[-1] = (filled[-1][0], s1)
                        continue
                filled.append((s0, s1))
            merged_spans = filled
            advs = [(c, pick(c, fs_line, use_sh)[1]) for c in txt]
            pos = [PAD]
            for _, a in advs:
                pos.append(pos[-1] + a)
            y_base = y - int(fs_line * LINE_RATIO)
            # 样式映射: line_segments 把整行切成 (seg_text, style) 段,按偏移定位
            seg_offsets, seg_styles = [], []
            off = 0
            for seg_text, st in line_segments(txt, cm.get(str(ln)), site):
                seg_offsets.append(off)
                seg_styles.append(st)
                off += len(seg_text)
            for s0, s1 in merged_spans:
                s0 = max(0, min(s0, len(txt)))
                s1 = max(s0, min(s1, len(txt)))
                if s1 <= s0:
                    continue
                color, bold = FG, False
                for i, so in enumerate(seg_offsets):
                    if so <= s0 < so + len(line_segments(txt, cm.get(str(ln)), site)[i][0]):
                        color, bold = seg_styles[i]
                        break
                embed_jobs.append((txt[s0:s1], pos[s0], pos[s1], y_base, color, bold))
        rightmost = max(rightmost, int(x))
    # 楼主楼嵌字(2026-08-14 嵌字方案): 清背景矩形(外扩2px) → SimHei fs_embed 重绘。
    # 先画完整块(art 已就位)再嵌字,确保重绘覆盖原文字;压到 art 可接受(用户决策4);
    # 嵌字必须在此处(quantize 之前)执行——特殊色锚点方案已被 PNG-8 量化杀死
    # 2026-08-14 第二轮修复: ①逐字符 pick() 绘制(SimHei 缺字如 U+30FB・ → PGothic,
    #   不再整段 SimHei 画出口字) ②字号按图宽自适应(宽图字大,0001=834宽→19px) ③逐字符
    #   x 定位(不用整段 draw,防 0011 阿知賀右偏) ④跳过纯空白字符(空格保持原样)
    if embed_jobs:
        embed_fonts = {}
        for txt0, x0, x1, y_base, color, bold in embed_jobs:
            # 嵌字字号: fs+2 基础,图宽超阈值再加(宽图字显得小,0001 834px→19)
            fs_embed = min(fs + 2 + (1 if width >= 800 else 0), TEXT_MAX_FS)
            if fs_embed not in embed_fonts:
                fm, ff, fsh = get_font(fs_embed)
                embed_fonts[fs_embed] = (fm, ff, fsh)
            fm, ff, fsh = embed_fonts[fs_embed]
            # 清背景: 外接矩形外扩 2px
            d.rectangle([x0 - 2, y_base - 2, x1 + 2, y_base + int(fs * LINE_RATIO) + 2], fill=BG)
            # 逐字符绘制: SimHei 优先,缺字回退 PGothic;空格跳过(保持原图);
            # 字符级 x 累加(与主渲染 pick advance 一致,防右偏)
            # 空格策略(2026-08-14 第五/六轮):
            # - 行首空格(第一个非空字符前): 每个都推进原文 advance,不压缩
            #   (0001 '・以《' 前导空格,压缩会左移)
            # - 正文中间连续空格: 压缩为 1 个宽(0072 '亚鲁欧　　　　　现在' 6空格膨胀被裁)
            cx = x0
            prev_blank = False
            seen_nonblank = False
            for ch in txt0:
                if ch.isspace():
                    if seen_nonblank:           # 正文中间: 压缩(连续组只推一次)
                        if not prev_blank:
                            cx += pick(ch, fs_embed, True)[1]
                    else:                       # 行首空格: 每个都推进(不压缩)
                        cx += pick(ch, fs_embed, True)[1]
                    prev_blank = True
                    continue
                prev_blank = False
                seen_nonblank = True
                if ord(ch) in _sh_cmap:
                    f, adv = fsh, fsh.getlength(ch)
                else:
                    f, adv = fm, fm.getlength(ch)
                if bold:
                    d.text((cx + 1, y_base), ch, font=f, fill=color)
                d.text((cx, y_base), ch, font=f, fill=color)
                cx += adv
    # 按 ink 像素边界统一裁剪(2026-08-14): 四周留固定 PAD —— 块首/尾空行、
    # 行尾 U+3000 pad 空格(透明)不再撑宽,上下/右侧空白恒定;fixed 模式保底 min_width
    from PIL import ImageChops
    diff = ImageChops.difference(img, Image.new('RGB', img.size, BG))
    bbox = diff.getbbox()
    if bbox:
        l = max(0, bbox[0] - PAD)
        t = max(0, bbox[1] - PAD)
        r = min(img.size[0], bbox[2] + PAD)
        b = min(img.size[1], bbox[3] + PAD)
        if min_width > 0:
            l, r = 0, max(r, min_width)   # fixed 语义: 左起 PAD,右保底 min_width(防 SimHei 1px hinting 漂移)
        else:
            r = min(img.size[0], bbox[2] + PAD + AA_RIGHT_PAD)  # AA 块右侧再加宽(用户方案 2026-08-14)
        img = img.crop((l, t, r, b))
    # PNG-8 固定调色板(2026-08-14 用户: AA 不需要高画质): 背景/正文/帖头绿/ID蓝/红
    # 等主色精确保留(自适应 quantize 会漂移主色,已弃),仅抗锯齿过渡色近似 → 文件 -70%
    _pal_img = Image.new('P', (1, 1))
    _pal_img.putpalette([v for c in PALETTE8 for v in c])
    img = img.quantize(palette=_pal_img, dither=Image.Dither.NONE)
    return img

def split_blocks(lines, site):
    """块边界 = 帖头行(一帖一块图);空行原样保留(渲染时占行高,楼间补足 2 行)。
    返回 [(is_op, rows)]: 楼主楼标记在切块阶段锁定,防后续 split_long 拆段丢帖头误判"""
    blocks, cur = [], []
    for ln, txt in lines:
        if site.render_header(txt):
            if cur:
                blocks.append((post_is_op(cur, site), cur))
            cur = [(ln, txt)]
        else:
            cur.append((ln, txt))
    if cur:
        blocks.append((post_is_op(cur, site), cur))
    return blocks

def split_long(blocks, max_rows=300):
    """超长帖(300+ 行)按行数硬拆,防空前长图;楼主楼标记随拆段继承"""
    out = []
    for op, b in blocks:
        if len(b) <= max_rows:
            out.append((op, b))
            continue
        for i in range(0, len(b), max_rows):
            out.append((op, b[i:i + max_rows]))
    return out

def split_by_width(blocks, site, ratio=1.7, min_run=3):
    """宽/窄行分段: 连续窄行段(对话文本)独立成块→字号按文本定(手机可读);
    宽行画面(人物图,含零星台词)保持完整;帖头行跟随后续窄段"""
    fm, ff, cm = _measure_fonts()
    out = []
    for block in blocks:
        ws = [line_width100(t, fm, ff, cm) for ln, t in block
              if t.strip() and not site.render_header(t)]
        wmax = max(ws) if ws else 0
        if wmax == 0:
            out.append(block)
            continue
        seg, narrow_run, pending_hdr = [], [], []
        for ln, txt in block:
            if not txt.strip():
                seg.append((ln, txt))
                continue
            if site.render_header(txt):
                pending_hdr.append((ln, txt))
                continue
            w = line_width100(txt, fm, ff, cm)
            if w < wmax / ratio:                    # 窄行
                if not narrow_run and pending_hdr:  # 帖头并入窄段开头
                    narrow_run += pending_hdr
                    pending_hdr = []
                narrow_run.append((ln, txt))
            else:
                seg += pending_hdr
                pending_hdr = []
                if len(narrow_run) >= min_run:      # 窄段切出
                    out.append(seg)
                    seg = []
                    out.append(narrow_run)
                else:
                    seg += narrow_run
                narrow_run = []
                seg.append((ln, txt))
        seg += pending_hdr
        if len(narrow_run) >= min_run:
            out.append(seg)
            out.append(narrow_run)
        else:
            seg += narrow_run
        if seg:
            out.append(seg)
    return out

def merge_posts(blocks, max_rows=120, meta=None, site=None):
    """短帖合并成幕(整帖不拆,累计行数上限),块高 ~2000-3000px 接近東方竖条观感。
    blocks: [(is_op, rows)]。三态分类合并(2026-08-14 用户方案):
    - 楼主楼(◆): 独立不合并(整楼冻结,防误伤用户楼)
    - 用户纯文本帖: 相互合并成幕(宽度标杆 540 统一)
    - 用户含AA帖: 独立不合并(内容宽自适应,不能混入纯文本幕导致折行错乱)"""
    out, acc, acc_rows = [], [], 0
    for op, b in blocks:
        kind = 'op' if op else ('aa' if (site and not block_is_pure_text(b, site, meta)) else 'text')
        if kind != 'text':
            if acc:
                out.append(acc)
                acc, acc_rows = [], 0
            out.append(b)
            continue
        if acc and acc_rows + len(b) > max_rows:
            out.append(acc)
            acc, acc_rows = [], 0
        acc += b
        acc_rows += len(b)
    if acc:
        out.append(acc)
    return out

_MEASURE = None
_SH_MEASURE = None

def _measure_fonts():
    """100px 字号测量字体(advance 与字号近似线性,可缩放)"""
    global _MEASURE
    if _MEASURE is None:
        from PIL import ImageFont
        _MEASURE = (ImageFont.truetype(FONT_MAIN, 100, index=2),
                    ImageFont.truetype(FONT_FB, 100),
                    load_main_cmap())
    return _MEASURE

def _measure_sh():
    """SimHei 统一行的折行测量: (SimHei 100px, PGothic 100px 回退, SimHei cmap)"""
    global _SH_MEASURE
    if _SH_MEASURE is None:
        from PIL import ImageFont
        _SH_MEASURE = (ImageFont.truetype(FONT_SIMHEI, 100),
                       ImageFont.truetype(FONT_MAIN, 100, index=2),
                       get_simhei_cmap())
    return _SH_MEASURE

def line_width100_sh(txt):
    """SimHei 语义行宽(100px): 字符在 SimHei cmap → SimHei,否则 PGothic(与渲染 pick 一致)"""
    fsh, fpg, sh_cmap = _measure_sh()
    return sum(fsh.getlength(c) if ord(c) in sh_cmap else fpg.getlength(c) for c in txt)

# 折行断点优先级字符: 空格 > 句逗顿叹问 > 闭括号(断在其后,避免断在开括号后如'（SS井感')
_BREAK_AFTER = set(' 　，。、！？…．,.;;!?')
_BREAK_AFTER |= set('）」』】〉》〕》')

def wrap_line(txt, max_w100):
    """按宽度智能折行: 超 max_w100(100px 口径)时优先在 空格/标点/闭括号 后断,
    开括号后不断(避免'（SS井感'式截断);返回 [段]"""
    if line_width100_sh(txt) <= max_w100:
        return [txt]
    fsh, fpg, sh_cmap = _measure_sh()
    out, seg, seg_w = [], '', 0.0
    last_break = -1            # seg 内最近可断点索引(断点字符保留在段尾)
    for ch in txt:
        cw = fsh.getlength(ch) if ord(ch) in sh_cmap else fpg.getlength(ch)
        if seg and seg_w + cw > max_w100:
            if last_break >= 0:
                cut = last_break + 1
                out.append(seg[:cut])
                seg = seg[cut:]
                seg_w = line_width100_sh(seg)
            else:
                out.append(seg)
                seg, seg_w = '', 0.0
            last_break = -1
            if seg.strip() == '':        # 折余纯空格(行尾 U+3000 pad)丢弃,不占行
                seg, seg_w = '', 0.0
        seg += ch
        seg_w += cw
        if ch.isspace() or ch in _BREAK_AFTER:
            last_break = len(seg) - 1
    if seg:
        out.append(seg)
    return out

def wrap_block_rows(rows, fs, text_bonus, site, meta, wrap_w):
    """纯文本块超长 text 行折行展开: 每行按 fs+bonus 折算 100px 阈值折行,
    折行段共享原行号(样式/meta 查询一致)。空行/帖头/非 text 行原样。"""
    fm, ff, cmap = _measure_fonts()
    out = []
    for ln, txt in rows:
        if not txt.strip() or site.render_header(txt) or not big_line(txt, ln, meta):
            out.append((ln, txt))
            continue
        fs_line = min(fs + text_bonus, TEXT_MAX_FS)
        max_w100 = wrap_w * 100.0 / fs_line     # 实际折行宽 → 100px 测量口径
        for seg in wrap_line(txt, max_w100):
            out.append((ln, seg))
    return out

def line_width100(txt, fm, ff, cm):
    """按浏览器回退语义实测行宽(100px 字号)"""
    w = 0.0
    for ch in txt:
        w += ff.getlength(ch) if (cm is not None and ord(ch) not in cm) else fm.getlength(ch)
    return w

def block_layout(block, width, site, fixed_width=False, fs_bonus=0, text_bonus=0, meta=None, freeze_post=False, fs_fixed=None, embed_v2=None, embed_v3=None):
    """字号: 让块内最宽内容行(实测 advance)铺满 width,再加 fs_bonus px;
    fixed_width=True: 画布=width+PAD*2 固定(内容区恒 width,右侧留白,不裁边不放大)
    text_bonus 为行级加大(render_block 按 line_meta/规则逐行生效),此处预估加大后的最宽行宽;
    freeze_post: 楼主楼整楼不加大,预估与字号判定同步(2026-08-14);
    fs_fixed: 折行纯文本块固定字号(折行按此字号计算,二次预估不得放大,2026-08-14);
    embed_v2: 楼主楼嵌字行按 fs_embed 预估宽度——嵌字文字 SimHei fs+2 比原文 PGothic
    宽,不预留则右缘出画布被裁(0072 么？】看不到根因,2026-08-14 第五轮);
    embed_v3: 视觉判定(2026-08-16)——v3=text 的行同样按嵌字字号预估"""
    fm, ff, cm = _measure_fonts()
    ws = [line_width100(t, fm, ff, cm) for ln, t in block if t.strip()]
    w100 = max(ws) if ws else 100.0
    fs = max(MIN_FS, min(MAX_FS, int(100 * width / w100) + fs_bonus)) if fs_fixed is None else fs_fixed
    # 文本行加大后的最宽行(渲染宽度预估)
    w_rend = 0.0
    for ln, t in block:
        if not t.strip():
            continue
        f2 = min(fs + text_bonus, TEXT_MAX_FS) if (text_bonus and not freeze_post and big_line(t, ln, meta)) else fs
        # 楼主楼嵌字行: 按嵌字字号(fs+2,宽图+1)预估——文字膨胀预留
        if freeze_post and embed_v2:
            v2l = embed_v2.get(str(ln + 1))
            v3v = (embed_v3 or {}).get(str(ln + 1))
            if v2_text_spans(v2l, t, v3v):
                f2 = max(f2, min(fs + 2 + (1 if width >= 800 else 0), TEXT_MAX_FS))
        w_rend = max(w_rend, line_width100(t, fm, ff, cm) * f2 / 100)
    content_w = int(w_rend) + PAD * 2
    # fixed_width: 窄块统一 width+PAD*2(右侧留白不放大);宽块(横幅/大文本)按内容宽,不裁边
    img_w = max(width + PAD * 2, content_w) if fixed_width else max(64, content_w)
    return fs, img_w

def chapter_files(tr_dir, name_style):
    files = list_txt(tr_dir, '_汉化.txt')
    if name_style == 'eid_title':
        files = [f for f in files if not f.startswith('entry_')]
    return files

def main():
    ap = argparse.ArgumentParser(description='AA 汉化 txt → 竖屏长图')
    ap.add_argument('--work', required=True)
    ap.add_argument('--chapter', default='', help='文件名子串,缺省=全部')
    ap.add_argument('--out', default='')
    ap.add_argument('--width', type=int, default=600, help='图片基准宽度(默认600=手机2x够看清;720偏大文件也大,2026-08-14下调)')
    ap.add_argument('--fixed-width', action='store_true', help='图宽固定=width(窄块不放大,旧版样式)')
    ap.add_argument('--fs-bonus', type=int, default=0, help='字号增量px(整体加大,同块同字号不错位)')
    ap.add_argument('--text-bonus', type=int, default=2, help='用户楼文本行(台词)加大px,SimHei 统一;默认2(2026-08-14 用户要求 4→2)')
    ap.add_argument('--font', default='', help='主字体路径覆盖(缺省=MS PGothic;简体回退恒为 SimSun,与 HTML 同语义)')
    a = ap.parse_args()
    font_path = pick_font(a.font or None)
    print('主字体:', os.path.basename(font_path), '| 回退: simsun.ttc | cmap:', os.path.basename(CMAP_JSON))
    cfg = registry.get(a.work)
    site = importlib.import_module('sites.' + cfg['site'])
    out_root = a.out or os.path.join(registry.BASE, '交付HTML', a.work + '_图片版')
    total = 0
    chapters = [c.strip() for c in a.chapter.split(',') if c.strip()] if a.chapter else []
    for fn in chapter_files(cfg['out_dir'], cfg.get('name_style')):
        if chapters and not any(c in fn for c in chapters):
            continue
        lines = open(os.path.join(cfg['out_dir'], fn), encoding='utf-8').read().split('\n')
        base = fn.replace('_汉化.txt', '')
        cm = {}
        cmf = os.path.join(cfg['aa_dir'], base + '_colormap.json')
        if os.path.exists(cmf):
            cm = json.load(open(cmf, encoding='utf-8'))
        rows = [(i, l) for i, l in enumerate(lines) if not l.startswith('#')]
        # 2026-08-09 回退: split_by_width 把画面切碎导致排版错乱(271页 vs 39页),
        # 文本放大改由纯文本块天然大字实现,不再切块
        # 2026-08-14 用户方案: 楼主楼(帖头含◆)整楼冻结不加大;用户楼文本行 SimHei 统一加大
        meta = load_line_meta(cfg['work_dir'], base)
        embed_v2 = load_line_meta_v2(cfg['work_dir'], base)   # 楼主楼嵌字段级分类(2026-08-14)
        embed_v3 = load_line_meta_v3(cfg['work_dir'], base)   # 视觉辅助判定覆盖(2026-08-16)
        blocks = merge_posts(split_long(split_blocks(rows, site)), meta=meta, site=site)
        outdir = os.path.join(out_root, base)
        os.makedirs(outdir, exist_ok=True)
        if a.text_bonus and meta is None:
            print(f'  (提示: 无 line_meta,回退规则判定——建议先跑 line_classify.py)')
        for bi, block in enumerate(blocks, 1):
            freeze_post = post_is_op(block, site)
            # 2026-08-14 用户方案: 纯文本块(无 AA)宽度标杆 580 内容区(fixed 语义),
            # text 行超 500px 折行;先算 fs → 折行展开 → 固定 fs 二次预估
            pure_text = (not freeze_post) and block_is_pure_text(block, site, meta)
            if pure_text:
                fs0, _ = block_layout(block, TEXT_W, site, True, a.fs_bonus, a.text_bonus, meta, freeze_post, embed_v2=embed_v2, embed_v3=embed_v3)
                block = wrap_block_rows(block, fs0, a.text_bonus, site, meta, WRAP_W)
                fs, img_w = block_layout(block, TEXT_W, site, True, a.fs_bonus, a.text_bonus, meta, freeze_post, fs_fixed=fs0, embed_v2=embed_v2, embed_v3=embed_v3)
                min_w = TEXT_W + PAD * 2
            else:
                fs, img_w = block_layout(block, a.width, site, a.fixed_width, a.fs_bonus, a.text_bonus, meta, freeze_post, embed_v2=embed_v2, embed_v3=embed_v3)
                min_w = a.width + PAD * 2 if a.fixed_width else 0
            img = render_block(block, site, cm, fs, img_w, font_path, min_w, a.text_bonus, meta, freeze_post,
                               embed_v2=embed_v2 if freeze_post else None,
                               embed_v3=embed_v3 if freeze_post else None)
            img.save(os.path.join(outdir, f'{bi:04d}.png'))
        total += len(blocks)
        print(f'{fn}: {len(blocks)} 块 → {outdir}')
    print(f'共 {total} 张图 → {out_root}')

if __name__ == '__main__':
    main()


