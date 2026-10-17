# -*- coding: utf-8 -*-
"""术语变体替换执行器（精确定位版）。

核心思路（用户需求：查原文替换对应汉化）：
  - variant_<word>.json 记录了 变体 -> [(章节, 行号)] 的命中映射（原文含 word 的行）
  - 只对"原文确含该 word"的汉化行做替换，绝不误伤别处
  - 词边界敏感：中文片段用 前后非中文 边界，拉丁用 \b，防止粘连子串

用法：
  1) python aa_web_app/gen_variant_extract.py --work yukika_robot --word きらきー
  2) 编辑 variant_<word>.json：给每个变体加 "target": "绮晶", "replace": true/false
     （或 --auto 用默认规则勾选高频词根）
  3) python aa_web_app/apply_variant_replace.py --work yukika_robot --word きらきー \
       --target 绮晶 [--dry-run] [--auto]

--auto：自动勾选"明确词根"变体（去前后缀标点后长度≤6 且命中≥2 的纯词根），
        跳过歧义变体（胡/吃/可怜/金丝雀/克莉琪等疑似其他角色）。
"""
import json, os, re, sys

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "works"))
sys.path.insert(0, os.path.join(_ROOT, "scripts", "common"))

from aa_common import txt_existing   # 压缩感知（.txt/.txt.gz）
import registry

# 疑似其他角色/非きらきー的歧义变体（不自动替换，人工确认）
AMBIGUOUS = {"胡", "吃", "可怜", "金丝雀", "克莉琪", "叽叽", "你说", "你",
             "人家", "试试剃剃看吧", "闪耀之战", "吃桑", "喜欢吃", "吃说"}

# 常见敬语/后缀（剥离后才是词根）
SUFFIXES = ["桑", "酱", "小姐", "先生", "宝贝", "大人", "様", "ちゃん",
            "他们", "们", "的", "的菊花", "家", "星人", "们啊"]


def variant_root(v):
    """剥离敬语/标点/粘连，提取词根。"""
    x = v
    # 去标点/数字
    x = re.sub(r"[，。、！？～…【】「」『』（）：：;；" r"''·☆ｗＷ!?\s|/\\.,]", "", x)
    # 去后缀
    changed = True
    while changed:
        changed = False
        for suf in SUFFIXES:
            if x.endswith(suf) and len(x) > len(suf):
                x = x[:-len(suf)]
                changed = True
    return x


def is_name_like(s):
    """词根判定：全中文或拉丁，长度 2-6，像人名/昵称。
    允许拉丁词根带敬语后缀（kiraki桑/kiraki的/kirakira），因为中文人名
    常带汉字后缀，纯词根会漏匹配整句变体。
    """
    if not s or len(s) > 12:
        return False
    # 拉丁开头（词根），后缀可含少量汉字（桑/酱/的/儿）
    m = re.match(r"^[A-Za-z]{4,}([的儿桑酱们/\u3000].*)?$", s)
    if m:
        return True
    if re.fullmatch(r"[A-Za-z]+", s):
        return len(s) >= 4
    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", s):
        return True
    return False


# 强制词根集：拉丁音译变体（大小写不同），以及明确的低频中文昵称。
# 这些是 きらきー 的确定性译法变体，不依赖模型次数阈值。
FORCE_ROOTS = {"kiraki", "kirari", "kirakira", "kirakiri", "kirak", "kirakir",
               "kirekira"}   # kira 系词根（正则用 ^ 前缀，兼容 Kiraki/kira 桑等）


def auto_select(variants):
    """自动勾选明确变体（字面）。返回 [变体字面]。
    与 apply 的无边界长词优先替换配套：长变体先整串替换（吃掉粘连），
    短词根最后替换（此时粘连长词已不存在，无边界也安全）。
    过滤整句噪音（is_name_like）和歧义角色（AMBIGUOUS）。
    拉丁音译词根走 FORCE_ROOTS（低阈值），中文昵称走计数阈值。
    """
    sel = []
    seen = set()
    for v, c in sorted(variants.items(), key=lambda x: (-len(x[0]), -x[1])):
        if c < 2:
            continue
        root = variant_root(v)
        if root in AMBIGUOUS:
            continue
        if root in ("綺晶", "绮晶", "雪华绮晶", "雪华绮", "綺"):
            continue  # 已是正确译名
        if not is_name_like(v) and not any(root.startswith(r) for r in FORCE_ROOTS):
            continue
        if v not in seen:
            seen.add(v)
            sel.append(v)
    # 补充强制拉丁词根（无论模型是否给出，以 ^前缀匹配 Kiraki/Kira/kiraki桑 等）
    for r in FORCE_ROOTS:
        if not any(s.startswith(r) or s.startswith(r.title()) for s in sel) and r != "kirekira":
            sel.append(r)
    return sel


def build_word_boundary_rx(v):
    """构造词边界正则。
    - 全拉丁: \b 前后
    - 含中文: 前后非中文（防"闪闪"匹配"闪闪发光"）
    """
    if re.fullmatch(r"[A-Za-z]+", v):
        return re.compile(r"(?<![A-Za-z0-9])" + re.escape(v) + r"(?![A-Za-z0-9])")
    # 中文: 前后不能是中文（允许标点/空白/拉丁相邻）
    return re.compile(r"(?<![\u4e00-\u9fff])" + re.escape(v) + r"(?![\u4e00-\u9fff])")


def apply_by_hitmap(work, word, target, dry_run=True, auto=True):
    """用 hit_map 行号定位替换——只动原文含 word 的行。"""
    from aa_common import disp_width, pad_to_width
    base = registry.get(work)
    aa_dir, out_dir, work_dir = base["aa_dir"], base["out_dir"], base["work_dir"]
    d = json.load(open(os.path.join(work_dir, f"variant_{word}.json"),
                       encoding="utf-8"))
    variants = d["variants"]
    # 选替换项
    if auto:
        sel = auto_select(variants)
    else:
        sel = [p["variant"] for p in d.get("pairs", []) if p.get("replace")]
    print(f"待替换 {len(sel)} 个变体 → {target}", flush=True)
    for v in sorted(sel, key=lambda x: -variants.get(x, 0)):
        print(f"  {variants.get(v, 0):4d}  {v!r}", flush=True)

    # 处理章节: 收集"原文含 word"的章节（hit_map 的 ch），对整章扫描替换。
    # 实测 hit_map 的 (ch, ln) 行号与模型变体不对应（kiraki 46 命中行 0 行真含
    # kiraki），故不按行号定位，改整章无边界长词优先替换——长变体先吃掉粘连，
    # 短词根最后替换（此时粘连已不存在）。单词根(闪闪)不会误伤"发光"因为
    # 长变体"闪闪发光"若在 sel 里会先被吃掉。
    valid_chs = set()
    for v, hs in d["hits"].items():
        for ch, _ in hs:
            valid_chs.add(ch)
    chap_lines = {ch: None for ch in sorted(valid_chs)}  # None = 整章扫描

    # 构造替换正则（无边界整串 + 长词优先；拉丁词根大小写不敏感）
    replace_rx = []
    for v in sel:
        rx = re.compile(re.escape(v), re.IGNORECASE if re.fullmatch(r"[A-Za-z]+", v) else 0)
        replace_rx.append((rx, v, target))
    replace_rx.sort(key=lambda x: -len(x[1]))  # 长种子先替换

    total_changed = total_skip = 0
    touched = []
    with open(work_dir + "/_variant_dry.log", "w", encoding="utf-8") as dlog:
        for ch, lines in sorted(chap_lines.items()):
            tr_p = txt_existing(os.path.join(out_dir, ch + "_汉化.txt"))
            aa_p = txt_existing(os.path.join(aa_dir, ch + ".txt"))
            if not os.path.exists(tr_p) or not os.path.exists(aa_p):
                continue
            src_lines = open(aa_p, encoding="utf-8").read().split("\n")
            tr_lines = open(tr_p, encoding="utf-8").read().split("\n")
            n_changed = 0
            # lines=None → 整章扫描；否则仅命中行
            for idx in (range(len(tr_lines)) if lines is None else [ln - 1 for ln in sorted(lines)]):
                if idx >= len(tr_lines):
                    continue
                b = tr_lines[idx]
                # 核心保险：仅当原文该行含 word（きらきー 等）才替换——
                # 查原文替换对应汉化，杜绝在无 word 的诗行/形容语境误伤"闪闪发光"
                if idx >= len(src_lines) or word not in src_lines[idx]:
                    continue
                nb = b
                for rx, v, t in replace_rx:
                    if rx.search(nb):
                        nb = rx.sub(t, nb)
                if nb == b:
                    continue
                # 宽度校正
                sw = disp_width(src_lines[idx]) if idx < len(src_lines) and src_lines[idx] else disp_width(b)
                nw = disp_width(nb)
                if nw > sw:
                    total_skip += 1
                    dlog.write(f"SKIP {ch} L{idx+1}: {nw}>{sw} | {b[:40]!r}\n")
                    continue
                if nw < sw:
                    nb = pad_to_width(nb, sw)
                total_changed += 1
                n_changed += 1
                dlog.write(f"OK {ch} L{idx+1}: {b[:50]!r} -> {nb[:50]!r}\n")
                if not dry_run:
                    tr_lines[idx] = nb
            if n_changed:
                touched.append((ch, n_changed))
                if not dry_run:
                    # 备份并写回
                    bdir = os.path.join(work_dir, "backup")
                    os.makedirs(bdir, exist_ok=True)
                    with open(tr_p, "w", encoding="utf-8") as f:
                        f.write("\n".join(tr_lines))
    return total_changed, total_skip, touched, sel


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--word", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--auto", action="store_true")
    args = ap.parse_args()
    c, s, touched, sel = apply_by_hitmap(args.work, args.word, args.target,
                                         dry_run=args.dry_run, auto=args.auto)
    flag = "DRY-RUN " if args.dry_run else ""
    print(f"{flag}替换: changed={c}, skipped={s}, 文件={len(touched)}")
    for fn, n in touched[:15]:
        print(f"  {fn}: {n}")
