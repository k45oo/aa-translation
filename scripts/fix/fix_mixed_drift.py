# -*- coding: utf-8 -*-
"""fix_mixed_drift.py —— 重建 mixed 行 art 段漂移错位（2026-09-05）。

背景: 部分 mixed 行(左 art + 右台词)曾被整行翻译(早于段级保护或 mixed 失败回退),
模型压缩 art 空格/左移图形, 行尾 U+3000 硬垫凑宽 → 宽度对但 art 列位全错。
此类行用段级重译修不好(汉化行坐标已漂移), 必须整行从 SRC 重建。

重建:
  1. 判定: _verify_art_alignment(SRC, 汉化行, segs) == False  (art 列位与 SRC 不符)
  2. art 段: 从 SRC 原样切片(图形绝不该被翻译改动)
  3. text 段(conf>=0.65 真台词): 译文从汉化行提取——
     汉化行的 CJK/全角标点连续段即译文; 按 SRC text 段顺序逐一放回;
     段宽不足 pad, 超宽压缩(_compress_seg_to_width), 压不动则标记 review。
  4. 重建后整行宽度 == SRC 宽度, 且 _verify_art_alignment 通过才算成功。

用法:
  python scripts/fix/fix_mixed_drift.py <work> [--eids 20992] [--dry-run] [--write]
  --dry-run 默认: 只报告+打印重建对比, 不写盘
  --write   写回汉化 txt(自动备份到 work/backup_fix_mixed_drift_<ts>/)
"""
import sys, os, json, re, time, shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # scripts/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "works"))

import aa_translate as T
import aa_common as C  # noqa
from registry import get as _reg_get

# CJK + 全角标点/括号 + 数字 + 长音符 ー (译文里当破折号用) (排除 SRC art 常用字符)
# 2026-09-06: 允许字间 空格/U+3000 —— 主流程整行翻译产物常把台词写成逐字空格
# ('这 次 听 说 国 王'), 旧正则不含空格 → 逐字空格译文被拆成单字, 重建只回填最后一字。
# 2026-09-20 事故: 字符集缺**全角数字**(０-９) / **ー**(U+30FC) / **全角括号**（）：、
#   ① '６６１信用点' 的 '６６１' 不被当译文, 重建后数字被 U+3000 垫顶掉(数字凭空消失);
#   ② '期待你今后的表现哦ーー！！' 被 ー 切成 '期待你今后的表现哦' + '！！' 两个候选,
#      右起分配时行尾主台词段只拿到 '！！' → 整句译文被吞(框线行只剩 '！！');
#   ③ '（另一个账户）' 的括号被当非译文丢弃。
_CJK_CHAR = (r'[\u4e00-\u9fff\u3001\u3002\u3005\uff01\uff1f\uff0c\uff1b\uff1a\u2014\u2026'
             r'\u2018\u2019\u201c\u201d\u300a\u300b\u300c\u300d\u300e\u300f\u3008\u3009'
             r'\u3010\u3011\u3014\u3015\u3016\u3017\uff08\uff09\uff3b\uff3d\uff5b\uff5d'
             r'\u30fb\u30fc\uff5e\u301c\uff10-\uff19\uff21-\uff3a\uff41-\uff5a0-9a-zA-Z]')
_CJK = re.compile(_CJK_CHAR + r'+(?:[ \u3000]' + _CJK_CHAR + r'+)*')
# art 段判定用: 行内不含任何 CJK 的连续段为候选 art (保守: 用 segments 权威切分, 这里只做译文提取)


def load_chapter(cfg, base):
    aa_dir, out_dir = cfg["aa_dir"], cfg["out_dir"]
    work_dir = cfg["work_dir"]
    src_p = os.path.join(aa_dir, base + ".txt")
    tr_p = os.path.join(out_dir, base + "_汉化.txt")
    meta_dir = os.path.join(work_dir, "line_meta_v2")
    if not (os.path.exists(T.txt_existing(src_p)) and os.path.exists(T.txt_existing(tr_p))
            and T.meta_exists(meta_dir, base)):
        return None
    src = T.txt_read(src_p)
    tr = T.txt_read(tr_p)
    meta = T.meta_load(meta_dir, base, {}) or {}
    segmap = {}
    for k, v in meta.items():
        if not isinstance(v, dict) or not v.get("segments"):
            continue
        try:
            idx = int(k) - 1
        except (ValueError, TypeError):
            continue
        if idx >= 0:
            segmap[idx] = v["segments"]
    return src, tr, segmap


def extract_translations(tr_line, segs, src_art_chars):
    """从汉化行提取译文串列表(按出现顺序)。

    策略: 译文(台词)在 mixed 行行尾区 → 去掉尾部 U+3000 垫后, 抓最右连续
    CJK/全角标点段作为主译文; 其余 CJK 段(若有)按从左到右顺序补充。
    排除 SRC art 段里出现过的字符(如 art 用汉字'八'作画)避免误抓:
    候选段若整体由 SRC-art 字符组成 → 丢弃。
    """
    body = tr_line.rstrip(" \u3000")
    cands = [m.group(0) for m in _CJK.finditer(body)]
    # 过滤纯 art 字符候选(整段字符都出现在 src art 里, 如 '八' 'V')
    artset = src_art_chars
    out = []
    for c in cands:
        # 候选内字间空格(主流程逐字空格产物)剥掉再比 art 字符集
        core = c.replace(" ", "").replace("\u3000", "")
        if core and all(ch in artset for ch in core):
            continue
        out.append(c)
    return out


def _looks_graphic_fragment(seg_text, seg_width=None):
    """text 段原文若为图形残片 → 视为 art 原样保留(不翻)。

    图形残片判据(mixed 行左侧小段, meta 常把图形劈进 text):
      - 无假名且无中文常用字 → 图形(卞/7/ヽ/ミ 等作画字符)
      - 或整段都是 AA 作画用汉字/符号且宽度<=4(如 '卞7' 脸的一部分)
      - 或同字重复>=2(傘傘傘 伞图形) / 罕见作画字(圷抃等, 中文几乎不用)
    真台词短段最少含假名(うん/そう)或中文实词。
    """
    kana = [c for c in seg_text if '\u3040' <= c <= '\u30ff']
    if kana:
        # 2026-09-06: 含假名不全等于台词——单字假名残片(ノ/ー/ゝ/､/ﾉ 等 AA 笔段/长音)
        # 不是台词。真台词最少: ≥2 假名 且 非纯重复; 或 假名+中文实词。
        # (うん/え/あ 等语气词是台词, 放行)
        stripped0 = seg_text.strip(' \u3000\u200a\u2002\u2005\u2006\u2009')
        body2 = re.sub(r'[\s\u3000\u200a\u2002\u2005\u2006\u2009]', '', stripped0)
        if body2 in ('うん', 'え', 'あ', 'ん', 'お', 'はい', 'そう', 'うー', 'あー', 'んー'):
            return False  # 语气词 → 台词
        # 纯重复拟声(あぁ/えぇ/うぅ/んん) 或 双假名语气词 → 台词
        if len(body2) == 2 and len(set(body2)) == 1:
            return False
        if len(kana) <= 2 and len(body2) <= 2:
            return True  # 单字/双字残片(ノ/ー/ゝ/､ 等) → 图形
        return False  # 多假名 → 真台词
    # 同字重复(AAA 式伞/装饰字) → 图形
    stripped = seg_text.strip(' \u3000\u2000-\u200b')
    if len(stripped) >= 2 and len(set(stripped)) == 1:
        return True
    # 罕见作画字(AA 常用, 中文不用或极少): 圷抃傘辷辷匕凵爻戈弋圭丿乂卍
    RARE = set('圷抃傘辷匕凵爻戈弋丿乂卍斥圦圸圹圻圼圽圾圿')
    if stripped and all(c in RARE for c in stripped):
        return True
    common_cjk = [c for c in seg_text if '\u4e00' <= c <= '\u9fff']
    if not common_cjk:
        # 2026-09-06: 纯符号/单假名残片: 无中文实词且非语气词(うん/え/あ/ん/お 等台词) → 图形
        # (ノ/ー/ゝ/ﾉ 等 AA 笔段常被 meta 切进 text 且判 False 当台词送模型)
        body = stripped
        if body and all(('\u3040' <= c <= '\u30ff') or c in ' \u3000\u200a\u2002\u2005\u2006\u2009' for c in body):
            body2 = re.sub(r'[\s\u3000\u200a\u2002\u2005\u2006\u2009]', '', body)
            if len(body2) <= 2 and body2 not in ('うん', 'え', 'あ', 'ん', 'お', 'はい', 'そう'):
                return True
        return True  # 纯符号/数字/半角 → 图形
    w = seg_width if seg_width is not None else None
    if w is not None and w <= 4:
        return True  # 窄段中文(卞等作画字) → 图形
    return False


def _rebuild_row_impl(src_line, tr_line, segs, out):
    """重建单行。返回 (new_line, ok, reason)。"""
    # 1) art 段原样
    # 2) text 段(conf>=0.65)依次填译文; 译文候选 = extract_translations
    arts = [(i, sg) for i, sg in enumerate(segs) if sg["kind"] == "art"]
    texts = [(i, sg) for i, sg in enumerate(segs)
             if sg["kind"] == "text" and sg.get("confidence", 1.0) >= 0.65]
    if not texts:
        return src_line, True, "no_highconf_text"
    # 候选过滤用字符集 = 所有不译段的原文(art + 低置信 text + 图形残片 text),
    # 防 art 图形字符(八/Y/、/卞7 等 meta 误标或作画)被当译文候选抓走。
    kept_idx = {i for i, sg in enumerate(segs)
                if sg["kind"] == "art" or sg.get("confidence", 1.0) < 0.65}
    art_text = "".join(src_line[sg["start"]:sg["end"]]
                       for i, sg in enumerate(segs) if i in kept_idx)
    # 全角→半角等价(art 被模型半角化: ７→7 ｉ→i 等)也入过滤集
    def _half(c):
        o = ord(c)
        if 0xFF01 <= o <= 0xFF5E:
            return chr(o - 0xFEE0)
        return c
    artset = set(art_text) | {_half(c) for c in art_text if ord(c) > 0x2E7F}
    cands = extract_translations(tr_line, segs, artset)
    trans = {}
    # 可译 text 段: 原文含假名/汉字实词(真台词); 纯图形残片原样保留
    real_texts = []
    for si, sg in texts:
        seg_text = src_line[sg["start"]:sg["end"]]
        if _looks_graphic_fragment(seg_text, sg.get("src_width")):
            trans[si] = seg_text  # 图形残片原样(不翻)
        else:
            real_texts.append((si, sg))
    if not real_texts:
        # 全部是图形残片 → 整行 = SRC art + 残片 = SRC 原文
        return src_line, True, "all_graphic"
    # 2026-09-20: 候选多于真 text 段时，先剔掉「art 回显候选」——整段只有 1 个非 art 字符
    #   （典型: SRC art 写 賞 金, 汉化行残留简体 赏 被当候选 → 右起分配把真台词段挤掉,
    #    例 '杀戮飞车党 赏 金 ３０００信用点' 被重建成 '赏 賞 金 ３０００信用点'）。
    if len(cands) > len(real_texts):
        def _nonart(c):
            return sum(1 for ch in c if ch not in artset)
        trimmed = [c for c in cands if _nonart(c) >= 2]
        if len(trimmed) >= len(real_texts):
            cands = trimmed
    # 2026-09-05: 汉化行台词仍是日文(漏译) → 直接还原 SRC 原文(台词留日文待翻
    # 译管线, 至少 art 列位先修对)。判据: 连续平假名串≥2(はいて/です/ます 等
    # 日文助词动词残留, 中文译文不会有) → 未译日文行
    # 2026-09-20 修正: 旧判据只看「≥2 连续平假名」→ **已译好、仅保留日文专名的行也被误判**:
    #   'やる夫——快跑——！！'(やる=2 平假名)、'谢谢啦 猎人やる夫' → 整句译文被还原成
    #   日文原文(用户报「框线行翻译丢失」)。改为**假名须占优**(假名数 ≥ 汉字数)才算未译日文行;
    #   真漏译行(ありがとね ハンターやる夫 / 残金が…)假名远多于汉字, 照旧还原。
    _body2 = tr_line.replace("\u3000", "").strip()
    import re as _re2
    _ka2 = len([c for c in _body2 if "\u3040" <= c <= "\u30ff"])
    _cjk2 = len([c for c in _body2 if "\u4e00" <= c <= "\u9fff"])
    if _re2.search(r"[\u3041-\u3096]{2,}", _body2) and _ka2 >= _cjk2:
        return src_line, True, "restore_src_nozh"
    if len(cands) < len(real_texts):
        out.append(f"    译文候选 {len(cands)} < 真text段 {len(real_texts)} → 待人工")
        # 缺候选: 能给的给(从右往左=主台词优先), 缺的用 SRC 原样(保 art 对齐优先), 标 review
        r_cands = list(reversed(cands))
        for ri in range(len(real_texts) - 1, -1, -1):
            si, sg = real_texts[ri]
            if r_cands:
                trans[si] = r_cands.pop(0)
            else:
                trans[si] = src_line[sg["start"]:sg["end"]]
        # 回退: 直接尝试, 失败则整行保持 SRC 原样待人工复核台词
        # (宁可留日文台词待人工, 不可 art 错位)
        new_line = T.rebuild_mixed_line(src_line, segs, trans)
        if new_line is not None and C.disp_width(new_line) == C.disp_width(src_line) \
                and T._verify_art_alignment(src_line, new_line, segs):
            return new_line, True, "ok_partial_cands"
        # 候选不足(一句台词被 meta 切碎成多 text 段, 译文是整句) → 台词区合并
        zl = _zone_merge_rebuild(src_line, tr_line, segs)
        if zl is not None:
            return zl, True, "ok_zone"
        return None, False, "cand_short"
    if not cands:
        return None, False, "no_cand"
    # 2026-09-05: 候选整体为日文(假名占比高) = 漏译行(提取的是原文碎片, 角色名
    # 保留如やる夫 只是少数) → 不重建(保留旧行落人工), 防把日文碎片当译文填进。
    _joinc = "".join(cands)
    _KA = [c for c in _joinc if "\u3040" <= c <= "\u30ff"]
    if _joinc and len(_KA) / max(1, len(_joinc)) > 0.5:
        return None, False, "untranslated_row"
    # 候选从右往左分配(译文在行尾, 最右候选=主台词; 左候选=行内左侧小段)
    r_cands = list(reversed(cands))
    for ri in range(len(real_texts) - 1, -1, -1):
        si, sg = real_texts[ri]
        trans[si] = r_cands.pop(0)
    new_line = T.rebuild_mixed_line(src_line, segs, trans)
    if new_line is None:
        # 2026-09-05: 逐段重建失败 → 试台词区合并(meta 把一句台词切碎成多段,
        # 中间夹台词字符被误标 art: 幽閉…ボ|ケ|老人 / □…★…)。首真text段起点→
        # 末真text段终点视为整句台词区, 区内一律用译文填充, 区外 art 原样。
        zl = _zone_merge_rebuild(src_line, tr_line, segs)
        if zl is not None:
            return zl, True, "ok_zone"
        return None, False, "rebuild_none"
    if C.disp_width(new_line) != C.disp_width(src_line):
        return None, False, f"width_mismatch {C.disp_width(new_line)}!={C.disp_width(src_line)}"
    if not T._verify_art_alignment(src_line, new_line, segs):
        return None, False, "art_align_fail"
    return new_line, True, "ok"


# ---- 数字保全兜底（2026-09-20 事故后加）--------------------------------------
# 重建是本地字符搬运（不走模型），任何「汉化行里原有数字在重建后消失」都是 bug：
# 曾因 _CJK_CHAR 缺全角数字，'６６１信用点 → １信用点' 被重建成 '信用点 → 信用点'
# （数字被 U+3000 垫顶掉，宽度校验发现不了）。命中即拒绝该行 → 落人工，绝不写坏数据。
_FW2HW = {ord(chr(0xFF10 + i)): chr(48 + i) for i in range(10)}
_NUM_RE = re.compile(r"[0-9]+")


def _nums_kept(before, after):
    """before 里的数字是否都还在 after 里（全角/半角等价）。"""
    a = (after or "").translate(_FW2HW)
    return all(n in a for n in _NUM_RE.findall(before.translate(_FW2HW)))


def rebuild_row(src_line, tr_line, segs, out):
    """rebuild_row 外壳：重建结果必须保住汉化行原有的数字（全角/半角等价）。

    还原 SRC 原文的分支(restore_src_nozh / new_line == SRC)不校验——那本就是把整行换成原文。
    """
    new_line, ok, reason = _rebuild_row_impl(src_line, tr_line, segs, out)
    if ok and new_line and new_line != src_line and reason != "restore_src_nozh" \
            and not _nums_kept(tr_line, new_line):
        return None, False, "digit_loss"
    return new_line, ok, reason


def _zone_merge_rebuild(src_line, tr_line, segs):
    """台词区合并重建: 首个真 text 段起点→末个真 text 段终点为整句台词区。

    meta 切碎场景(一句台词被切多段, 中间夹误标 art 的台词字符):
      seg17 '幽閉するぞ！ボ' + seg18(art) 'ケ' + seg19 '老人！'
    台词区 = [seg17.start, seg19.end], 区内全由译文+pad 填充(不保留区内 art,
    因为它们本就是台词的一部分); 区外 art 原样 → art 列位不变。
    仅当整行等宽 + _verify_art_alignment 通过才返回(否则 None → 落人工)。
    """
    texts = [(i, sg) for i, sg in enumerate(segs)
             if sg["kind"] == "text" and sg.get("confidence", 1.0) >= 0.65]
    if not texts:
        return None
    # 守卫: text 段之间间隔若是明显图形笔画段(_/＼/|/()/◇ 等硬笔画, 或宽度>4)
    # → 拒绝 zone(怕吃真 art); 假名/汉字/省略号/ミ 等台词碎屑放行。
    _ARTY = set("_／＼|()（）[]＿⊂⊃彡乂爻≧≦ｰ─━│├┤┬┴┼:;,.。'\"`´~-=<>^vVヽﾉニ圭三┌┐└┘")
    for a, b in zip(texts, texts[1:]):
        gap = src_line[a[1]["end"]:b[1]["start"]]
        if not gap:
            continue
        # 地图/符号台词(□が現在地、★が…)的 ★□ 是语义内容, zone 会吞 → 落人工
        if any(c in "□★☆○●◎◇◆■▲△" for c in gap):
            return None
        gap_w = C.disp_width(gap)
        if gap_w > 4:
            return None  # 大间隔 → 中间是真 art 段
        if any(ch in _ARTY for ch in gap):
            return None  # 间隔含硬图形笔画 → 不 zone
    z_start = texts[0][1]["start"]
    z_end = texts[-1][1]["end"]
    # 台词区外才是不译 art: 过滤候选字符集用区外 art + 低置信段
    kept_idx = {i for i, sg in enumerate(segs)
                if i < texts[0][0] or i > texts[-1][0]
                or sg["kind"] == "art" and sg.get("confidence", 1.0) < 0.65}
    # 简化: 区外段(索引在 text 段范围外) 全部原样保留 → 候选过滤集 = 区外内容
    out_art = src_line[:z_start] + src_line[z_end:]
    def _half(c):
        o = ord(c)
        return chr(o - 0xFEE0) if 0xFF01 <= o <= 0xFF5E else c
    artset = set(out_art) | {_half(c) for c in out_art if ord(c) > 0x2E7F}
    cands = extract_translations(tr_line, segs, artset)
    if not cands:
        return None
    # 2026-09-05: 候选整体为日文 = 漏译行(未翻译), 不 zone(保留旧行落人工)
    _joinc = "".join(cands)
    _KA = [c for c in _joinc if "\u3040" <= c <= "\u30ff"]
    if _joinc and len(_KA) / max(1, len(_joinc)) > 0.5:
        return None
    # 2026-09-05 fix: 汉化整句可能被 □/★ 等符号断成多候选(它们不在 _CJK 类)——
    # zone 是整句台词区, 应合并区内所有候选为整句, 不是只取末尾(曾丢前半句)。
    zh = "".join(cands).strip() or cands[-1]
    zone_w = C.disp_width(src_line[z_start:z_end])
    if C.disp_width(zh) > zone_w:
        zh = T._compress_seg_to_width(zh, zone_w)
        if zh is None:
            return None
    else:
        zh = T.pad_segment(zh, zone_w)
    new_line = src_line[:z_start] + zh + src_line[z_end:]
    if C.disp_width(new_line) != C.disp_width(src_line):
        return None
    # 校验区外 art 列位(区内 art 是 meta 误标, 不适用 _verify_art_alignment)
    for si, sg in enumerate(segs):
        if sg["kind"] != "art":
            continue
        if si < texts[0][0]:
            # 区前 art: 检查 new_line 前缀含原样
            if not new_line.startswith(src_line[:sg["end"]]):
                return None
        elif si > texts[-1][0]:
            # 区后 art: 检查 new_line 后缀含原样
            if not new_line.endswith(src_line[sg["start"]:]):
                return None
    return new_line


def process_work(work, eids=None, do_write=False):
    cfg = _reg_get(work)
    out_dir = cfg["out_dir"]
    if eids:
        files = [f"entry_{e}.txt" for e in eids]
    else:
        files = T.list_txt(cfg["aa_dir"], exclude=("_台词.txt", "_汉化.txt"))
    all_drift = []
    # 2026-10-01: 备份目录按**每次运行**一个（原每章一个），且只保留最近 2 次运行。
    _bak_dir = os.path.join(cfg["work_dir"], "backup_fix_mixed_drift_" + time.strftime("%Y%m%d%H%M%S")) if do_write else ""
    for fname in files:
        base = fname[:-4]
        loaded = load_chapter(cfg, base)
        if loaded is None:
            continue
        src, tr, segmap = loaded
        if len(src) != len(tr):
            print(f"[{base}] 行数不一致 {len(src)} vs {len(tr)}, 跳过")
            continue
        drift_rows = []  # (idx0, src_line, tr_line, segs, new_line)
        for i, (s, h) in enumerate(zip(src, tr)):
            segs = segmap.get(i)
            if not segs:
                continue
            # 只处理有高置信 text 段的 mixed 行
            texts = [sg for sg in segs if sg["kind"] == "text" and sg.get("confidence", 1.0) >= 0.65]
            if not texts:
                continue
            if T._verify_art_alignment(s, h, segs):
                continue  # art 列位正确, 无需重建
            out = []
            new_line, ok, reason = rebuild_row(s, h, segs, out)
            drift_rows.append((i, s, h, segs, new_line, ok, reason, out))
        if not drift_rows:
            print(f"[{base}] 无 drift 行")
            continue
        print(f"[{base}] drift 行 {len(drift_rows)} 个:")
        ok_n = 0
        for i, s, h, segs, nl, ok, reason, notes in drift_rows:
            for n in notes:
                print(f"    {n}")
            if ok and nl is not None:
                ok_n += 1
                print(f"  L{i+1}: 重建OK 宽{C.disp_width(nl)}==SRC{C.disp_width(s)}")
            else:
                print(f"  L{i+1}: 重建失败({reason}) 保留旧行")
            all_drift.append((base, i, s, h, segs, nl, ok, reason))
        print(f"  -> 重建成功 {ok_n}/{len(drift_rows)}")
        if do_write and ok_n:
            # 备份整章（本次运行共用同一个 backup 目录，见 process_work 开头）
            bak = _bak_dir
            os.makedirs(bak, exist_ok=True)
            _tr_actual = T.txt_existing(os.path.join(out_dir, fname.replace(".txt", "_汉化.txt")))
            shutil.copy2(_tr_actual, os.path.join(bak, os.path.basename(_tr_actual)))
            new_tr = list(tr)
            for i, s, h, segs, nl, ok, reason, notes in drift_rows:
                if ok and nl is not None and new_tr[i] == h:
                    new_tr[i] = nl
            T.txt_write_form(T.txt_out_path(os.path.join(out_dir, fname.replace(".txt", "_汉化.txt"))),
                             new_tr)
            print(f"  -> 已写回并备份到 {bak}")
    if do_write and os.path.isdir(_bak_dir):
        # 只保留最近 2 次运行的备份目录（更早的整目录删）
        C.prune_backup_dirs(cfg["work_dir"], "backup_fix_mixed_drift_")
    return all_drift


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    work = args[0] if args else None
    eids = None
    if "--eids" in sys.argv:
        eids = sys.argv[sys.argv.index("--eids") + 1].split(",")
    do_write = "--write" in sys.argv
    if not work:
        print(__doc__)
        sys.exit(1)
    all_drift = process_work(work, eids, do_write)
    if all_drift:
        ok = sum(1 for _, _, _, _, _, _, o, _ in all_drift if o)
        print(f"\n总计: drift {len(all_drift)} 行, 重建成功 {ok}, 失败 {len(all_drift)-ok}")
    sys.exit(0)
