# -*- coding: utf-8 -*-
"""漏译校对: 扫描已汉化章节的纯台词整行残留, 用模型重译 (2026-08-25)

判定: 原文行 is_text_line + 无 AA 艺术字符 + 连续假名>=2 + 译文==原文(整行残留)
→ 视为漏译, 收集后批量重译(带上下文)。
用法: python scripts/tools/fix_missed_lines.py --work <id> [--dry-run] [--limit N]
"""
import sys, os, re, json, argparse

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
sys.path.insert(0, os.path.join(BASE, "scripts", "works"))
import registry
from aa_common import is_text_line, disp_width, pad_to_width, list_txt, txt_read, txt_existing   # 压缩感知

# AA 艺术字符(排除后才是纯台词)
ART_CHARS = set('／＼|()（）[]＿_⊂⊃⊆⊇∧∨ミ彡乂爻≧≦ｰ─━┌┐└┘│├┤┬┴┼ーﾆﾄﾘﾉﾊﾞ〃' + '・｡､｢｣')
KANA2 = re.compile(r'[ぁ-んァ-ヶ]{2,}')
KANA_KANJI = re.compile(r'[ぁ-んァ-ヶ一-龠]')
# 艺术假名黑名单(AA 绘画用, 非真台词假名) —— 与 aa_translate 同款
ART_HIRA = set('つだりへぢづゑゐいうしふとこっッ')
ART_KATA = set('ノミニハマレヘヾヿヽヾー')
ART_KANA_ALL = ART_HIRA | ART_KATA


def is_missed_line(src_line, tr_line):
    """判定: 原文是纯台词(假名/汉字占比高), 且译文整行原样残留。

    注意: 假名判定不排除 ART_KANA(艺术假名黑名单)——黑名单含常用假名(いうふしとこ),
    排除后连续假名匹配不到, 会漏掉「食い気が悪いよ」这类送假名多的台词。
    艺术行误报由「假名/汉字占比」阈值过滤。
    """
    s = src_line.strip()
    if not s or s.startswith('#') or s.startswith('='):
        return False
    if re.match(r'^\d+\s*：', s):
        return False
    if not is_text_line(s):
        return False
    # 连续假名>=2(不排除艺术假名)
    if not KANA2.search(s):
        return False
    # 汉字占比>=0.3(真台词含汉字; 纯艺术行ニニニニ/ズズ～几乎无汉字)
    nz = [c for c in s if not c.isspace()]
    if not nz:
        return False
    kanji_n = sum(1 for c in nz if re.match(r'[一-龠]', c))
    if kanji_n / len(nz) < 0.3:
        return False
    return tr_line.strip() == s


def scan_work(work):
    cfg = registry.get(work)
    out, aa = cfg["out_dir"], cfg["aa_dir"]
    if not os.path.isdir(out):
        print(f"{work}: 无汉化目录")
        return []
    missed = []   # (tf, idx, src, tr)
    for tf in list_txt(out, "_汉化.txt"):
        tr = txt_read(os.path.join(out, tf))
        src = txt_read(txt_existing(os.path.join(aa, tf.replace("_汉化.txt", ".txt"))))
        for i, l in enumerate(tr):
            if i >= len(src):
                continue
            if is_missed_line(src[i], l):
                missed.append((tf, i, src[i], l))
    return missed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--fix", action="store_true", help="实写: 重译漏译行并写回")
    ap.add_argument("--ctx-lines", type=int, default=5, help="上下文: 前/后N楼的台词(默认5, 覆盖镜→眼镜类5楼跨度)")
    a = ap.parse_args()

    missed = scan_work(a.work)
    if a.limit:
        missed = missed[:a.limit]
    print(f"[漏译扫描] {a.work}: {len(missed)} 行纯台词残留")
    for tf, i, s, t in missed[:25]:
        print(f"  {tf} L{i+1}: {s.strip()[:60]!r}")
    if not missed:
        return
    p = os.path.join(BASE, "temp", f"missed_{a.work}.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump([{"file": tf, "line": i + 1, "src": s, "tr": t} for tf, i, s, t in missed],
              open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"清单写入 {p}")

    if a.fix:
        _fix_lines(a.work, missed, a.ctx_lines)


def _res_bounds(src):
    """返回帖头行位置列表(楼边界)。"""
    return [i for i, l in enumerate(src) if re.match(r'^\d+\s*：', l)]


def _res_context(src, i, ctx_lines=3, dlg_path=None):
    """取 i 行所在楼 + 前/后 ctx_lines 楼的台词(含用户楼讨论), 作为场景上下文。

    2026-08-25: 「鏡→眼镜」的线索在后 5 楼(用户吐槽), ±2 行不够 → 按楼分块。
    优先用汉化/<章>_台词.txt(纯净纯文本, 带 L行号+[主]/[用])——无 AA 噪音、中文译文直接给场景;
    不存在则从原文提取日文台词。
    """
    dlg = {}
    if dlg_path and os.path.exists(dlg_path):
        for line in open(dlg_path, encoding="utf-8"):
            m = re.match(r"L(\d+) \[(主|用)\] (.*)", line.strip())
            if m:
                dlg[int(m.group(1)) - 1] = (m.group(2), m.group(3))
    bounds = _res_bounds(src)
    # i 所在楼号
    res_idx = 0
    for r, h in enumerate(bounds):
        if i < h:
            res_idx = r
            break
    else:
        res_idx = len(bounds)
    lo = bounds[max(0, res_idx - ctx_lines)] if res_idx - ctx_lines >= 0 else 0
    hi = bounds[res_idx + ctx_lines] if res_idx + ctx_lines < len(bounds) else len(src)
    if dlg:
        parts = []
        for ci in range(lo, hi):
            if ci == i or ci not in dlg:
                continue
            tag, text = dlg[ci]
            parts.append(f"[{tag}]{text}")
        return " ".join(parts)
    # 回退: 从原文提取日文台词
    ctx = ""
    for ci in range(lo, hi):
        if ci == i:
            continue
        cs = src[ci].strip()
        if cs and not cs.startswith("#") and not re.match(r"^\d+\s*：", cs):
            # 跳过艺术行(假名占比高但汉字少, 如 ニ二二二/ズズ～)
            nz = [c for c in cs if not c.isspace()]
            if not nz:
                continue
            kanji_n = sum(1 for c in nz if re.match(r'[一-龠]', c))
            kana_n = sum(1 for c in nz if re.match(r'[ぁ-んァ-ヶ]', c))
            if kanji_n / len(nz) < 0.3 and kana_n / len(nz) < 0.5:
                continue
            segs = re.findall(r"[ぁ-んァ-ヶ一-龠]+[ぁ-んァ-ヶー・一-龠ｗ〜～！？。、]*", cs)
            t_seg = " ".join(x for x in segs if len(x) >= 2)
            if t_seg:
                ctx += (" [前]" if ci < i else " [后]") + t_seg
    return ctx


def _fix_lines(work, missed, ctx_lines=5):
    """重译漏译行(带上下文), 宽度对齐写回。"""
    cfg = registry.get(work)
    out, aa = cfg["out_dir"], cfg["aa_dir"]
    # 按文件分组
    by_file = {}
    for tf, i, s, t in missed:
        by_file.setdefault(tf, []).append(i)
    sys.path.insert(0, os.path.join(BASE, "scripts"))
    import aa_translate as A

    total_fixed = 0
    for tf, idxs in by_file.items():
        tr_path = txt_existing(os.path.join(out, tf))
        src_path = txt_existing(os.path.join(aa, tf.replace("_汉化.txt", ".txt")))
        tr = txt_read(tr_path)
        src = txt_read(src_path)
        for i in sorted(idxs):
            s = src[i].strip()
            # 上下文: 前/后 ctx_lines 楼台词——优先用 汉化/<章>_台词.txt(纯净纯文本)
            dlg_path = os.path.join(out, tf.replace("_汉化.txt", "_台词.txt"))
            ctx = _res_context(src, i, ctx_lines, dlg_path)
            hint = f"【场景上下文】{ctx}\n" if ctx else ""
            body = f"1|{s}"
            print(f"  重译 {tf} L{i+1}: {s[:40]!r}")
            try:
                txt = A.call_api(hint + "把以下日文 AA 漫画台词翻译成简体中文, 格式 1|译文。",
                                 body, max_tokens=300, timeout=60)
                m = re.search(r"1\|(.+)", txt, re.S)
                if not m:
                    print(f"    ✗ 无译文: {txt[:50]!r}")
                    continue
                new_l = m.group(1).strip()
                # 宽度对齐
                sw = disp_width(s)
                nw = disp_width(new_l)
                if nw < sw:
                    new_l = pad_to_width(new_l, sw)
                elif nw > sw:
                    print(f"    ✗ 超宽 {nw}>{sw}, 跳过")
                    continue
                tr[i] = new_l
                total_fixed += 1
                print(f"    ✓ {new_l[:40]!r}")
            except Exception as e:
                print(f"    ✗ 失败: {str(e)[:60]}")
        open(tr_path, "w", encoding="utf-8").write("\n".join(tr))
    print(f"\n重译完成: {total_fixed} 行")


if __name__ == "__main__":
    main()
