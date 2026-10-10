# -*- coding: utf-8 -*-
"""交付 HTML 的 AA 等宽字体（woff2 子集 + @font-face 注入）。权威实现；CLI = tools/make_aa_font.py。

## 为什么（2026-10-08）

交付页字体栈 `"ＭＳ Ｐゴシック","MS PGothic","Mona","MS Gothic","SimSun"` 全是 Windows 字体，
而 **PGothic 本身是比例字体**（本机 Chromium 16px 实测：ASCII `|` 4px、`ﾆ` 10px、`ヽ` 12px、
`漢` 16px、`　` 11px）→ AA 列位在浏览器里本来就不齐；手机/非 Windows 设备没有这些字体，
回退到随机 CJK 字体 → AA 直接散架。

参照 zseineo/infrared-singularity（内嵌 Monapo/Saitamaar/textar，base64 `@font-face`，单档离线可读）
的做法，这里内嵌**更纱黑体 Sarasa Mono SC**（`fonts/SarasaMonoSC-Regular.ttf`，OFL 可随交付外发；
仓库既有实测：简体 0 缺字、半角假名 advance 0.5em）——本机 Chromium 实测内嵌后
`|`=8、`ﾆ`=8、`ヽ`=16、`漢`=16、`　`=16 **恰好 1:2 网格**。

## 形态

- 语料按作品/章节现取字符集（`base_chars()` 的固定块 ∪ 语料实际字符），fontTools 子集化 + woff2。
  实测 baitou（42 话）→ 5540 字符 / **941 KB**；语料里字体真缺的字只有 1 个（৲）。
- **单档内嵌形态**（数据全在 index.html）：字体以 `data:font/woff2;base64,...` 内嵌
  → 单个 index.html 发出去（微信/QQ/手机文件管理器）也能正确显示 AA。
- **外置形态**（data/*.js）：字体写成交付夹里的 `aa_font.woff2` + 相对 URL 引用
  （本机 Chromium `file://` 实测可加载相对路径字体，`document.fonts.check` = true，无需 base64）。

## 默认栈序 = **last**（2026-10-08 用户 A/B 定案，别改回 first）

一开始默认把内嵌字体排在栈首，被用户否掉：**这批作品的 AA 是按 MS PGothic 的不规则字宽画的**
（作者当年在浏览器里对着 PGothic 摆笔画），换成严格 1:2 的等宽字体后整体列位被推离作者摆的位置
→ 看起来「错位」。

本机 Chromium 实测（同一章，`pre.aa` 内 200 个采样字符，15px）：

| 渲染字体 | 偏离 1:2 网格的字数 | 例 |
|---|---|---|
| 内嵌 Sarasa Mono SC | **0**（全部精确 7.5 / 15） | — |
| 现行 PGothic 栈 | **37** | `=`8、`/`8、`,`3、`レ`13、`マ`14、`ニ`13、`Ｎ`12、`ぃ`12 |

即「网格正确」≠「画得对」：**按作者当初的字体渲染才是对的**。故内嵌字体只作**缺字体时的兜底**
（手机/非 Windows 设备没有 PGothic 时才生效），PC 观感逐字符不变。
想要严格网格：`--font-stack first` / `AA_AA_FONT_STACK=first`。

- 开关：`AA_AA_FONT=0` 关闭内嵌；`AA_AA_FONT_FILE=<路径>` 换字体源（默认 Sarasa Mono SC）。

"""
import hashlib
import os

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_FONT = os.path.join(BASE, "fonts", "SarasaMonoSC-Regular.ttf")
OUT_DIR = os.path.join(BASE, "fonts", "subset")
FAMILY = "AA-SarasaMono"

# 固定字符块：ASCII + 拉丁补充 + 通用标点 + 箭头 + 带圈数字 + 制表/方块/几何/杂项符号
#             + CJK 标点 + 平/片假名 + 全角 ASCII + 半角假名（AA 画笔主力）
BASE_RANGES = [
    (0x0020, 0x007E), (0x00A0, 0x00FF), (0x2000, 0x206F), (0x2190, 0x21FF),
    (0x2460, 0x24FF), (0x2500, 0x257F), (0x2580, 0x259F), (0x25A0, 0x25FF),
    (0x2600, 0x26FF), (0x3000, 0x303F), (0x3040, 0x309F), (0x30A0, 0x30FF),
    (0x31F0, 0x31FF), (0xFE30, 0xFE4F), (0xFF01, 0xFF5E), (0xFF61, 0xFF9F),
]


def base_chars():
    out = set()
    for a, b in BASE_RANGES:
        out.update(chr(c) for c in range(a, b + 1))
    return out


def chars_of_files(paths):
    """一组 txt / txt.gz 文件里出现的字符（交付页真正会渲染的内容）。"""
    import gzip
    chars = set()
    for p in paths:
        try:
            if str(p).endswith(".gz"):
                text = gzip.open(p, "rt", encoding="utf-8").read()
            else:
                text = open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        chars.update(text)
    chars.discard("\n")
    chars.discard("\r")
    return chars


def chars_of_work(work):
    """作品 aa/ + 汉化/ 全部字符。"""
    import sys
    sys.path.insert(0, os.path.join(BASE, "scripts", "works"))
    import registry
    cfg = registry.get(work)
    if not cfg:
        raise SystemExit("未注册作品: " + work)
    paths = []
    for d in (cfg["aa_dir"], cfg["out_dir"]):
        if not os.path.isdir(d):
            continue
        paths += [os.path.join(d, fn) for fn in os.listdir(d)
                  if fn.endswith(".txt") or fn.endswith(".txt.gz")]
    return chars_of_files(paths)


def build_subset(chars, font_path=None, out_dir=None):
    """字符集 → woff2 子集（同字符集复用缓存）。返回 (路径, 全缺字, 语料缺字)。"""
    from fontTools import subset
    from fontTools.ttLib import TTFont

    font_path = font_path or DEFAULT_FONT
    out_dir = out_dir or OUT_DIR
    chars = set(chars)
    cmap = TTFont(font_path, fontNumber=0, lazy=True).getBestCmap()
    missing = [c for c in sorted(chars) if ord(c) not in cmap]

    key = hashlib.sha1(("".join(sorted(chars)) + os.path.basename(font_path)).encode("utf-8")).hexdigest()[:12]
    out = os.path.join(out_dir, "aa_%s.woff2" % key)
    if os.path.exists(out):
        return out, missing, []

    opts = subset.Options()
    opts.flavor = "woff2"
    opts.layout_features = []       # AA 不需要 ligature/kern
    opts.name_IDs = ["*"]
    opts.name_legacy = True
    opts.drop_tables += ["DSIG"]
    opts.desubroutinize = True
    font = subset.load_font(font_path, opts)
    s = subset.Subsetter(options=opts)
    s.populate(text="".join(sorted(chars)))
    s.subset(font)
    os.makedirs(out_dir, exist_ok=True)
    subset.save_font(font, out, opts)
    return out, missing, []


def font_css(ref, family=None, first=None):
    """`@font-face` + `pre.aa` 字体栈覆盖（追加在页面 CSS 之后，同特异性后者胜）。

    `first=None` → 按 `stack_first()`（默认 last：内嵌字体只作缺字体时的兜底）。
    """
    if first is None:
        first = stack_first()
    family = family or FAMILY
    if first:
        stack = ("'%s', \"ＭＳ Ｐゴシック\", \"MS PGothic\", \"Mona\", \"MS Gothic\", "
                 "\"SimSun\", monospace" % family)
    else:
        stack = ("\"ＭＳ Ｐゴシック\", \"MS PGothic\", \"Mona\", \"MS Gothic\", "
                 "'%s', \"SimSun\", monospace" % family)
    return (
        "@font-face{font-family:'%s';font-style:normal;font-weight:normal;"
        "font-display:swap;src:url(%s) format('woff2');}\n"
        "pre.aa{font-family:%s;}\n" % (family, ref, stack)
    )


def font_enabled():
    return os.environ.get("AA_AA_FONT", "1") not in ("0", "false", "False")


def font_source():
    return os.environ.get("AA_AA_FONT_FILE") or DEFAULT_FONT


def stack_first():
    """内嵌字体是否排在 Windows 字体**之前**。
    默认 last（2026-10-08 用户 A/B 定案）——见模块注释「默认栈序」。"""
    return os.environ.get("AA_AA_FONT_STACK", "last").lower() == "first"
