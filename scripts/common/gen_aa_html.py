# -*- coding: utf-8 -*-
"""通用合集 HTML 生成器：**交付文件夹**（目录 + 单章切换 + 帖头原站颜色 + colormap + 原帖链接）。

用法：python common/gen_aa_html.py --work <id>（或裸参数 <id>）[--one <base>] [--no-source]

2026-10-06 起交付物从「单个 HTML 文件」改为**文件夹**（`cfg["html_out"]` 指向文件夹）；
2026-10-08 起按数据体量自动选**两种形态**（可用 `--inline` / `--external` / `--inline-max <MB>` 覆盖）：

    A) 单档（数据 ≤ INLINE_MAX_BYTES=20MB）：data/ 不存在，全部章节数据内嵌在 index.html
       aaManga/交付HTML/<标题>_汉化合集/
         index.html           目录 + 章节骨架 + 译/原切换 + 阅读进度 + <script>内嵌全部数据</script>
         aa/*.txt.gz 汉化/*.txt.gz   源留档
       → 任意方式打开都能看（双击 file://、发出去、手机文件管理器/QQ 打开），零服务依赖。

    B) 外置（数据 > 阈值）：data/ 一章一文件（懒加载，见下）
         index.html 只放骨架（<pre> 空），章节正文在 data/NNNNN.js
         data/00000.js …      每章一个：{tr, src} = gzip+base64 的**已渲染 HTML**
                              （浏览器 DecompressionStream 解压后填进 <pre>）

两种形态的页面 JS 完全相同（`load()` 先查 `window.AA_DATA`，命中就不去取 data/*.js）；
唯一分支在 `drop()`：单档形态设 `window.AA_INLINE=1`，此时回收远处章节**不丢 b64**
（没有可重读的 data/*.js，丢了就再也拿不回来）。

**为什么外置**：加「译/原切换」要同时装两份文本，内嵌会让单文件翻倍（キッコーマン
14.5MB → ~29MB）；gzip+base64 后两份合计约 9MB。**为什么用 .js 而不是 fetch 读 .txt/.gz**：
双击 `file://` 打开时 fetch/XHR 全被 CORS 拦死，只有 `<script src>` 能离线加载。

**懒加载（2026-10-06 二次优化）**：index.html **不预载**任何章节数据，页面 JS 在切章时
才注入 `<script src="data/NNNNN.js">`（首章 + 恢复章随启动加载，之后预取下一章）。
原因：章节多的作品（この素晴らしいシンオウに祝福を = 810 章）全量预载要读 ~80MB 脚本 +
解压 1620 段，首屏几十秒；而且 810 章全填进 DOM 会到百万级节点，滚动/切换越来越卡。
配套：① 切换只重填当前章（不再遍历全部 `<pre>`）② DOM/解压结果只保留当前章 ±2 章
（`KEEP`，越界即 `drop()`：清 `<pre>` + 释放 DATA/TXT/LOADED，回访时从浏览器缓存秒回）。

源文本经 `_read_lines` **透明读 .txt / .txt.gz**（语义同 aa_translate.txt_read，本地 stdlib
实现以免把生成器拉进翻译链依赖）。

三套显式模板（html_css 字段选择，逐字节复刻历史交付样式）：
  saki   = 现代样式 + 「第N话」标签（さーきー bookshelf 版）
  fc2    = 现代样式 + 紧凑版 + 原帖：链接 + 章指示（さーきー FC2 版）
  legacy = 旧样式（不幸の催眠 版）
模板差异只在 head/章节骨架/CSS；tail JS（切换/进度/翻页）三套统一。

帖头渲染交给 sites/<site>.py 的 render_header()（按原站还原：作者绿粗、ID 红/蓝粗）。
colormap 'font-weight:bold' 只加粗【1D10:X】括号片段本身（整行加粗会撑变形）。
坑：先 enumerate 再过滤 # 行（反了行号整体偏移）；html.escape 防 <> 艺术符；
    gzip 必须带 mtime=0（否则每次生成字节不同）。
"""
import base64
import gzip
import html as H
import json, os, re, shutil, sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.dirname(_SCRIPT_DIR))
sys.path.insert(0, os.path.join(os.path.dirname(_SCRIPT_DIR), "works"))

import registry
import importlib
import aa_font as _AF
from aa_common import HEADER_RE   # 帖头行识别（楼间间距用）

# 交付页 AA 等宽字体文件（外置形态写进交付夹；详见 common/aa_font.py）
FONT_NAME = "aa_font.woff2"

CSS_MODERN = """body { background:#f5f2ea; color:#222; margin:0; }
header { background:#3a3a4a; color:#eee; padding:18px 24px; font-family:"Microsoft YaHei",sans-serif;
         margin-left:240px; }
header h1 { margin:0 0 6px; font-size:22px; }
header p { margin:2px 0; font-size:13px; color:#bbb; }
header p.work-desc { color:#ddd; font-size:13px; line-height:1.7; margin:10px 0 2px; max-width:900px; }
nav { position:fixed; left:0; top:0; bottom:0; width:240px; background:#2c2c3a; z-index:10;
      overflow-y:auto; padding:14px 12px; box-sizing:border-box; }
nav span.toc-label { display:block; color:#888; font-size:12px; font-family:"Microsoft YaHei",sans-serif; margin:0 0 8px 2px; }
nav button { display:block; width:100%; text-align:left; background:#3f3f55; color:#cdd6ff; border:1px solid #55557a; border-radius:4px;
             padding:5px 9px; font-size:12.5px; font-family:"Microsoft YaHei",sans-serif; cursor:pointer; margin:3px 0; }
nav button:hover { background:#4f4f6a; }
nav button.active { background:#8a5a3a; color:#fff; border-color:#a87a52; font-weight:bold; }
main { margin-left:240px; padding:18px 24px 60px; max-width:1150px; }
.chapter { margin:26px 0 40px; }
.chapter h2 { font-family:"Microsoft YaHei",sans-serif; font-size:17px; color:#7a3b2e; border-bottom:2px solid #d8c8a8; padding-bottom:6px; }
.src-link { font-size:12px; color:#888; font-family:"Microsoft YaHei",sans-serif; margin:4px 0 8px; }
.src-link a { color:#5a7ab8; text-decoration:none; }
.pager { display:flex; justify-content:space-between; margin:14px 0 6px; }
.pager button { background:#7a5a3a; color:#fff; border:none; border-radius:4px; padding:8px 18px;
                font-size:14px; font-family:"Microsoft YaHei",sans-serif; cursor:pointer; }
.pager button:hover { background:#8a6a4a; }
.pager button:disabled { background:#c8b8a0; cursor:default; }
.chapter-ind { font-size:12px; color:#999; font-family:"Microsoft YaHei",sans-serif; text-align:center; margin:6px 0; }
pre.aa { font-family: "ＭＳ Ｐゴシック", "MS PGothic", "Mona", "MS Gothic", "SimSun", monospace;
         font-size:15px; line-height:17px; white-space:pre; overflow-x:auto;
         background:#fffdf7; border:1px solid #d8c8a8; border-radius:4px; padding:10px 14px; }
pre.aa span.sfx { font-size:0.95em; font-style:italic; color:#333;
                  font-family:"楷体","KaiTi","STKaiti",serif; }
@media (max-width:640px){
header{margin-left:0;padding:12px 14px}
nav{position:static;width:auto;height:auto;max-height:34vh;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:8px 10px;border-bottom:1px solid #3f3f55}
nav button{font-size:13px;padding:7px 10px}
main{margin-left:0;padding:10px 10px 48px;max-width:none}
pre.aa{font-size:11px;line-height:14px;padding:6px 8px}
.chapter h2{font-size:15px}
.pager button{padding:7px 12px;font-size:13px}
}
"""

CSS_LEGACY = """body{background:#f5f2ea;color:#222;margin:0;font-family:"Microsoft YaHei",sans-serif}
header{background:#2c2c3a;color:#eee;padding:14px 24px;margin-left:240px}
header h1{margin:0 0 6px;font-size:20px}
header p{margin:2px 0;font-size:13px;color:#bbb}
header p.work-desc{color:#ddd;font-size:13px;line-height:1.7;margin:10px 0 2px;max-width:900px}
nav{position:fixed;left:0;top:0;bottom:0;width:240px;background:#2c2c3a;z-index:10;
    overflow-y:auto;padding:16px 10px;box-sizing:border-box}
nav span.toc-label{display:block;color:#888;font-size:12px;font-family:"Microsoft YaHei",sans-serif;margin:0 0 8px 2px}
nav button{display:block;width:100%;text-align:left;background:#3f3f55;color:#cdd6ff;border:1px solid #55557a;border-radius:4px;
    padding:6px 10px;cursor:pointer;font-size:13px;margin-bottom:4px;font-family:"Microsoft YaHei",sans-serif}
nav button:hover{background:#4f4f6a}
nav button.active{background:#8a5a3a;color:#fff;border-color:#a87a52;font-weight:bold}
main{margin-left:240px;padding:18px 24px 60px;max-width:1150px}
pre.aa{font-family:"ＭＳ Ｐゴシック","MS PGothic","Mona","MS Gothic","SimSun",monospace;font-size:15px;line-height:17px;
white-space:pre;overflow-x:auto;background:#fffdf7;border:1px solid #d8c8a8;border-radius:4px;padding:10px 14px}
pre.aa span.sfx{font-size:0.95em;font-style:italic;color:#333;font-family:"楷体","KaiTi","STKaiti",serif}
.pager{display:flex;justify-content:space-between;margin:10px 0}
.pager button{background:#3a3a4a;color:#eee;border:none;border-radius:4px;padding:6px 14px;cursor:pointer}
.chapter-ind{text-align:center;color:#999;font-size:13px;margin:8px 0}
@media (max-width:640px){
header{margin-left:0;padding:12px 14px}
nav{position:static;width:auto;height:auto;max-height:34vh;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:8px 10px;border-bottom:1px solid #3f3f55}
nav button{font-size:13px;padding:7px 10px}
main{margin-left:0;padding:10px 10px 48px;max-width:none}
pre.aa{font-size:11px;line-height:14px;padding:6px 8px}
.pager button{padding:7px 12px;font-size:13px}
}
"""

# 交付文件夹 UI（三套模板共用）：译/原切换 + 载入提示
CSS_UI = """
.modebar{position:fixed;top:10px;right:14px;z-index:60;display:flex;gap:3px;
  background:rgba(44,44,58,.94);border:1px solid #55557a;border-radius:6px;padding:3px;
  font-family:"Microsoft YaHei",sans-serif;box-shadow:0 1px 6px rgba(0,0,0,.2)}
.modebar button{background:transparent;color:#cdd6ff;border:none;border-radius:4px;
  padding:4px 11px;font-size:12.5px;font-family:inherit;cursor:pointer;line-height:1.4}
.modebar button:hover{background:#4f4f6a}
.modebar button.on{background:#8a5a3a;color:#fff;font-weight:bold}
.modebar button.on:hover{background:#8a5a3a}
#loading{margin:14px 0;padding:10px 14px;border-radius:4px;font-family:"Microsoft YaHei",sans-serif;
  font-size:13px;background:#fff7e0;border:1px solid #e0cf9a;color:#7a5a20}
#loading.err{background:#fdecec;border-color:#e0a0a0;color:#8a2020;white-space:pre-wrap}
#st{position:fixed;left:14px;bottom:14px;z-index:60;display:none;max-width:70vw;
  font-family:"Microsoft YaHei",sans-serif;font-size:12.5px;
  background:rgba(44,44,58,.94);color:#e8e8f0;border:1px solid #55557a;border-radius:6px;
  padding:5px 10px;box-shadow:0 1px 6px rgba(0,0,0,.25)}
#st.on{display:block}
#st.err{background:#8a2020;border-color:#c07070;color:#fff}
/* 手机端目录抽屉（默认隐藏，点「目录」展开；>640px 完全不影响） */
#tocbtn{display:none;position:fixed;left:8px;top:8px;z-index:80;
  background:rgba(44,44,58,.94);color:#cdd6ff;border:1px solid #55557a;border-radius:6px;
  padding:6px 11px;font-size:12.5px;font-family:"Microsoft YaHei",sans-serif;cursor:pointer;
  box-shadow:0 1px 6px rgba(0,0,0,.25)}
#tocmask{display:none;position:fixed;inset:0;z-index:45;background:rgba(0,0,0,.35)}
#tocmask.on{display:block}
/* 交付页编辑（PC） */
.modebar button.editbtn{display:none}
.modebar button.editbtn.on{background:#2a6f9b;color:#fff;font-weight:bold}
.modebar button.editbtn:disabled{opacity:.5;cursor:not-allowed}
.modebar button.editbtn:disabled:hover{background:transparent}
#edithint{display:none;position:fixed;left:50%;transform:translateX(-50%);top:10px;z-index:59;
  font-family:"Microsoft YaHei",sans-serif;font-size:12.5px;background:#fff7e0;border:1px solid #e0cf9a;
  color:#7a5a20;border-radius:6px;padding:5px 12px;box-shadow:0 1px 6px rgba(0,0,0,.18);max-width:70vw}
#edithint.on{display:block}
#edithint.err{background:#fdecec;border-color:#e0a0a0;color:#8a2020}
/* 载入失败面板（content:// 等场景：这是硬错误，手机上也必须看得到 → 见手机段 .loaderr 覆盖） */
#edithint.loaderr{max-width:min(760px,92vw);padding:10px 14px 8px;font-size:13px;line-height:1.7;
  background:#fff;border-color:#c9c9c9;color:#333;box-shadow:0 2px 14px rgba(0,0,0,.28)}
#edithint.loaderr .le-t{font-weight:bold;color:#8a2020;margin-bottom:4px}
#edithint.loaderr .le-b{white-space:normal}
#edithint.loaderr .le-x{margin-top:6px;padding:2px 10px;cursor:pointer}
#edithint.loaderr b{color:#1a4f7a}
/* 行号列 + 行内编辑行（与工作台同款：点行号改译文、编辑行在原行位置、原文行在下） */
.aa-wrap{display:flex;align-items:flex-start;--aa-lh:17px;margin:15px 0}
.aa-wrap>pre.aa{margin:0}                     /* pre 自带 1em 上边距会把正文压低、行号列错位 */
.aa-gut{display:none;flex:0 0 auto;user-select:none;font-family:Consolas,"Courier New",monospace;
  font-size:11.5px;line-height:var(--aa-lh);color:#b0a894;text-align:right;
  padding:11px 4px 0 6px}                      /* 11 = 上边框1 + pre 上内边距10，行行对齐 */
body.editing .aa-gut{display:block}
.aa-gut b{display:block;height:var(--aa-lh);font-weight:normal;cursor:pointer;border-radius:3px;padding:0 4px}
.aa-gut b:hover{color:#7a5a3a;font-weight:bold;background:#f0e6d2}
.aa-gut b.on{color:#fff;background:#8a5a3a;font-weight:bold}
.aa-gut b.none{cursor:default}
pre.aa span.aa-line.hide{display:none}
.aa-edit{display:block;position:relative;z-index:2}
.aa-edit .er-tr{display:block;width:100%;box-sizing:border-box;font:inherit;line-height:var(--aa-lh);
  height:var(--aa-lh);white-space:pre;overflow-x:auto;overflow-y:hidden;background:#fff8e1;
  border:none;border-radius:0;padding:0;margin:0;resize:none;outline:none;color:#222;scrollbar-width:none}
.aa-edit .er-tr::-webkit-scrollbar{height:0;display:none}
.aa-edit .er-tr:focus{background:#ffeec2}
.aa-edit .src-row{display:block;height:var(--aa-lh);line-height:var(--aa-lh);font:inherit;
  white-space:pre;overflow-x:hidden;background:#fff3c4;color:#8a8a8a}
.aa-edit .ae-bar{display:block;padding:3px 0 4px;font-family:"Microsoft YaHei",sans-serif;font-size:12px}
.aa-edit .ae-bar button{background:#7a5a3a;color:#fff;border:none;border-radius:4px;
  padding:4px 12px;font-size:12.5px;font-family:inherit;cursor:pointer;margin-right:6px}
.aa-edit .ae-bar button.ghost{background:#efeade;color:#5a5240;border:1px solid #cfc7b3}
.aa-edit .ae-bar button:hover{filter:brightness(1.08)}
.aa-edit .ae-bar button:disabled{opacity:.55;cursor:default}
.aa-edit .ae-bar .ae-tip{color:#a08a6a;margin-left:2px}
.emsg.ok{color:#2a7a3a}
.emsg.err{color:#a02a2a}
#trm{display:none;position:fixed;inset:0;z-index:80;background:rgba(0,0,0,.35)}
#trm.on{display:block}
#trm .box{position:absolute;left:50%;top:6vh;transform:translateX(-50%);width:min(920px,92vw);max-height:88vh;
  overflow:auto;background:#fffdf7;border-radius:8px;padding:14px 16px;
  font-family:"Microsoft YaHei",sans-serif;font-size:13px;box-shadow:0 8px 30px rgba(0,0,0,.35)}
#trm h3{margin:0 0 10px;font-size:16px;color:#7a3b2e}
#trm .trp{display:grid;grid-template-columns:1fr 1fr auto auto auto;gap:6px;align-items:center;margin-bottom:5px}
#trm .trp input[type=text]{width:100%;box-sizing:border-box;padding:4px 6px;font-size:12.5px;
  border:1px solid #c9c2ae;border-radius:4px;font-family:"MS Gothic",monospace}
#trm .trp label{font-size:12px;color:#666;white-space:nowrap}
#trm .tr-opt{margin:10px 0;display:flex;gap:16px;align-items:center;flex-wrap:wrap;color:#555}
#trm .tr-body{margin-top:10px;border-top:1px solid #e2dccb;padding-top:8px;max-height:46vh;overflow:auto}
#trm .trf{margin-bottom:8px}
#trm .trf b{color:#5a4a30}
#trm ul{margin:4px 0 0 18px;padding:0}
#trm li{font-family:"MS Gothic",monospace;font-size:12px;line-height:1.55}
#trm .warnx{color:#a02a2a}
#trm button{background:#7a5a3a;color:#fff;border:none;border-radius:4px;
  padding:6px 14px;font-size:13px;font-family:inherit;cursor:pointer}
#trm button.ghost{background:#efeade;color:#5a5240;border:1px solid #cfc7b3}
#trm button:hover{filter:brightness(1.08)}
#trm button:disabled{opacity:.55;cursor:default}
#trm .emsg{font-size:12px;color:#888}
/* 分页简版（多个 HTML 文件）：头部范围说明 / 跨文件「上次阅读」提示 / 上一部分·下一部分 */
header p.part-nav a{color:#ffd48a;text-decoration:none;font-weight:bold}
header p.part-nav a:hover{text-decoration:underline}
#xres{padding:6px 12px;background:#fff4d8;border-bottom:1px solid #e0c080;color:#664400;
  font-family:"Microsoft YaHei",sans-serif;font-size:13px}
#xres a{color:#8a5a3a;font-weight:bold}
.pager a.pager-btn{background:#7a5a3a;color:#fff;border-radius:4px;padding:8px 18px;font-size:14px;
  font-family:"Microsoft YaHei",sans-serif;text-decoration:none;display:inline-block}
.pager a.pager-btn:hover{background:#8a6a4a}
@media (max-width:640px){
.modebar{top:6px;right:8px;padding:2px}
.modebar button{padding:4px 9px;font-size:12px}
.modebar button.editbtn{display:none!important}
#edithint,#trm{display:none!important}
/* 例外：章节数据读不到（content:// 打开等）——手机上也要看到该改用哪种方式；
   top 让开顶部工具条（目录/译文按钮在 top:6px 高约 26px），否则面板标题被按钮压住 */
#edithint.loaderr{display:block!important;left:8px;right:8px;top:44px;transform:none;max-width:none;
  max-height:62vh;overflow-y:auto;font-size:12.5px;line-height:1.75}
#edithint.loaderr .le-t{font-size:13px}
#tocbtn{display:block}
nav{position:fixed;left:0;top:0;bottom:0;width:78vw;max-width:300px;height:100%;max-height:none;z-index:50;
  background:#2c2c3a;overflow-y:auto;-webkit-overflow-scrolling:touch;
  padding:50px 10px 14px;box-sizing:border-box;box-shadow:none;visibility:hidden;
  transform:translateX(-100%);transition:transform .18s ease,visibility 0s .18s}
nav.open{transform:none;visibility:visible;box-shadow:2px 0 12px rgba(0,0,0,.35);
  transition:transform .18s ease}
/* note: 关闭时不给阴影 —— 抽屉平移到 -100% 后阴影会露在视口最左边 */
/* 阅读界面左右不留空：正文通栏（模块自带的内边距/边框在这里被覆盖掉） */
header{margin-left:0;padding:12px 12px 12px 66px}
main{margin-left:0;padding:0 0 40px;max-width:none}
pre.aa{font-size:11.5px;line-height:14px;padding:6px 0;border-left:none;border-right:none;border-radius:0}
.aa-wrap{--aa-lh:14px;margin:11.5px 0}
.aa-gut{padding-top:7px}
.chapter{margin:16px 0 26px}
.chapter h2{margin:0 12px 6px;font-size:15px}
.src-link{margin:4px 12px 8px}
.pager{padding:0 12px;margin:12px 0 4px}
.chapter-ind{margin:6px 12px}
}
"""

EID_RE = re.compile(r'(?:entry_)?(\d+)')

LINK_NAME = "index.html"      # 交付文件夹内的入口文件名
META_NAME = "edit_meta.json"  # 编辑服务的坐标文件（作品/章节清单/仓库内相对源路径）
EDIT_EXE = "AA编辑服务.exe"    # 随交付文件夹分发的编辑服务（PyInstaller 单文件）
EDIT_BAT = "编辑服务.bat"
EDIT_BAT_LAN = "手机阅读(局域网).bat"   # 同服务加 --lan：手机连同一 Wi-Fi 也能打开读（远端只读）
EDIT_PY = "edit_server.py"
# ── 单档 / 外置 分界（2026-10-08）──────────────────────────────────────────────
# 章节数据（gzip+b64 的已渲染 HTML）**总量** ≤ 此值 → 全部内嵌进 index.html（单档：
# 双击 file://、发微信/QQ、手机文件管理器打开都能看，不依赖 data/ 目录）；超过 → data/
# 外置 + 懒加载。20MB 的依据：实测 8.8MB 单档在 file:// 下首屏 401ms、DOM 2k 节点，
# 线性外推 20MB ≈ 1s 内；再大则「解析大 JS + 内嵌形态无法释放 b64（见 drop()）」双重
# 吃紧，且 800 章作品单档会到 100MB+（实测 810 章作品 data 168MB）。
# 覆盖：--inline（强制内嵌）/ --external（强制外置）/ --inline-max <MB>
INLINE_MAX_BYTES = 20 * 1024 * 1024
# 编辑服务源码与已打包 exe 的位置（exe 由 scripts/tools/build_edit_server.py 产出）
_EDIT_PY_SRC = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "tools", "edit_server.py"))
_EDIT_EXE_SRC = [
    os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "tools", "dist", EDIT_EXE)),
    os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "tools", EDIT_EXE)),
]
# 编辑服务启动器：优先 exe（别的机器不用装 Python），否则退回 python 跑 edit_server.py
#   extra = "" → 本机改（绑 127.0.0.1）；extra = " --lan" → 手机连同一 Wi-Fi 也能打开读（远端只读）
def _bat_text(extra="", title=""):
    head = ("@echo off\r\n"
            "chcp 936 >nul\r\n"
            "cd /d \"%~dp0\"\r\n")
    if title:
        head += "echo %s\r\necho.\r\n" % title
    return (
        head +
        "if exist \"" + EDIT_EXE + "\" (\r\n"
        "  \"" + EDIT_EXE + "\" \"%~dp0.\"" + extra + " %*\r\n"
        "  goto :eof\r\n"
        ")\r\n"
        "if exist \"" + EDIT_PY + "\" (\r\n"
        "  where python >nul 2>nul || (echo [错误] 本机没有 Python，也缺少 " + EDIT_EXE + " & pause & goto :eof)\r\n"
        "  python \"" + EDIT_PY + "\" \"%~dp0.\"" + extra + " %*\r\n"
        "  goto :eof\r\n"
        ")\r\n"
        "echo [错误] 交付文件夹里缺少 " + EDIT_EXE + " / " + EDIT_PY + "\r\n"
        "pause\r\n")


EDIT_BAT_TEXT = _bat_text()
EDIT_BAT_LAN_TEXT = _bat_text(
    " --lan",
    "手机阅读：手机连同一个 Wi-Fi，用手机浏览器打开下面打印出来的 http://地址")


def deliver_dir(cfg):
    """**gen_aa_html 产出的交付文件夹**（html_out 末尾 .html 也一并去掉）。

    2026-10-06 起 html_out 就是交付文件夹。少数作品生成器仍是 legacy 单文件
    （白头 postprocess / 魔法戦姫 gen_yaruobook_html，registry 里 html_out 仍带 .html），
    这里同样去掉后缀 → 只有在**手动**对它们跑 gen_aa_html 时才会新建文件夹，绝不覆盖旧单文件。
    """
    ho = (cfg.get("html_out") or "").strip()
    return ho[:-5] if ho.endswith(".html") else ho


def deliver_is_folder(cfg):
    """交付物是否为**文件夹**格式（html_out 带 .html = 旧单文件格式）。"""
    return not (cfg.get("html_out") or "").strip().endswith(".html")


def deliver_entry(cfg):
    """交付入口 HTML —— 存在性 / mtime / 双击打开的判据一律用它（别用文件夹 mtime）。

    新式（html_out = 文件夹）→ `<folder>/index.html`；旧式（html_out = .html）→ 该文件本身。
    """
    ho = (cfg.get("html_out") or "").strip()
    if ho.endswith(".html"):
        return ho
    return os.path.join(ho, LINK_NAME) if ho else ""


def _read_lines(path):
    """透明读 .txt / .txt.gz → 行列表（语义与 aa_translate.txt_read 一致，剥 CR）。

    本地 stdlib 实现：gen_aa_html 刻意不 import 翻译链（历史约定=零依赖零 API），
    但必须能读压缩文本包（`compress_text_pkg.py` 产物）。
    """
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return [l.rstrip("\r") for l in f.read().split("\n")]
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return [l.rstrip("\r") for l in f.read().split("\n")]
    gz = path + ".gz"
    if os.path.exists(gz):
        with gzip.open(gz, "rt", encoding="utf-8") as f:
            return [l.rstrip("\r") for l in f.read().split("\n")]
    raise FileNotFoundError(path)


def _src_path(aa_dir, base):
    """"汉化基名 → 原文 txt 路径（含 .gz 透明）"""
    for cand in (os.path.join(aa_dir, base + ".txt"), os.path.join(aa_dir, base + ".txt.gz")):
        if os.path.exists(cand):
            return cand
    return None


def _gz_b64(text):
    """gzip(确定性, mtime=0) + base64 → 塞进 .js 的字符串"""
    return base64.b64encode(gzip.compress(text.encode("utf-8"), 6, mtime=0)).decode("ascii")


# ── 章节数据 payload：两种格式（2026-10-08）──────────────────────────────────
# v1: {"tr":b64,"src":b64}          两份各自 gzip+b64
# v2: {"src":b64,"p":b64,"v":2}     src 全量 + tr 相对 src 的**差异行**
# 为什么 v2 省：AA 里艺术行/帖头/空行/数字行不翻译 → tr 与 src 约 9 成行**逐字节相同**
# （抽 8 章实测 90.0% 行 / 90.8% 字节），v1 等于把同一份内容存两遍。810 章那部
# data 168MB → v2 实测省 ~42%。前端 load 后按 `p` 是否存在分派（见 TAIL_JS 的 unpatch）。
PATCH_SEP = chr(2)          # 差异行「行号 <STX> 内容」的分隔（控制字符，正文不可能出现）


def tr_patch(tr_lines, src_lines):
    """tr 相对 src 的差异行文本（`行号 STX 内容` 每行一条）；行数不一致返回 None。

    行数不等 = 这两种渲染不可按行对齐（v2 的前提），调用方回退整份 tr。
    """
    if len(tr_lines) != len(src_lines):
        return None
    return "\n".join("%d%s%s" % (i, PATCH_SEP, t)
                     for i, (t, s) in enumerate(zip(tr_lines, src_lines)) if t != s)


def chapter_payload(idx, tr_lines, src_lines, src_b64=None, tr_only=False):
    """一章的 chapters 数据 payload（自动选 v1/v2，选不出来就 v1）。

    tr_only（「仅译文」简版）→ 只带 tr（省掉近一半数据；页面上不出现「原文」按钮）。
    src_b64 给定时沿用现成 b64（服务写回时不必重压原文）；src_lines 为 None 时
    **只能**出 v1（没有原文行就没法算差异行）。
    """
    tr_b64 = _gz_b64("\n".join(tr_lines))
    if tr_only:
        return ('window.AA_DATA=window.AA_DATA||[];'
                'window.AA_DATA[%d]={"tr":"%s"};\n' % (idx, tr_b64))
    if src_b64 is None:
        src_b64 = _gz_b64("\n".join(src_lines or []))
    patch = tr_patch(tr_lines, src_lines) if src_lines is not None else None
    if patch is not None:
        p_b64 = _gz_b64(patch)
        if len(p_b64) < len(tr_b64):        # 差异行没比整份 tr 小 → 不划算，走 v1
            return ('window.AA_DATA=window.AA_DATA||[];'
                    'window.AA_DATA[%d]={"src":"%s","p":"%s","v":2};\n' % (idx, src_b64, p_b64))
    return ('window.AA_DATA=window.AA_DATA||[];'
            'window.AA_DATA[%d]={"tr":"%s","src":"%s"};\n' % (idx, tr_b64, src_b64))


def _blank_plan(lines):
    """楼间间距计划：{行索引: 该行前要补的空行数}（帖头前不足 2 空行才补，幂等）。

    原文/汉化行数一致 → 两种渲染共用同一份计划，切换时行位置不会漂。
    """
    plan, blank_run, seen = {}, 0, False
    for i, l in enumerate(lines):
        if l.startswith('#'):
            continue
        if HEADER_RE.match(l) is not None:
            if seen and blank_run < 2:
                plan[i] = 2 - blank_run
            seen, blank_run = True, 0
        elif l.strip():
            blank_run = 0
        else:
            blank_run += 1
    return plan


def _render_body(lines, cm, site, plan, src_lines=None, marks=None):
    """按 plan 渲染整章 → HTML 行列表（'#' 行丢弃，其余过 render_line）。

    src_lines / marks 只在**译文**渲染时传：sfx 特效要对照原文行的半角假名段
    （自动）叠加 work/sfx_marks/<base>.json 的人工标记。原文模式不传（无需特效）。
    """
    out = []
    for i, l in enumerate(lines):
        if l.startswith('#'):
            continue
        n = plan.get(i)
        if n:
            out.extend([''] * n)
        out.append(render_line(i, l, cm, site,
                               src_line=(src_lines[i] if src_lines and i < len(src_lines) else None),
                               sfx_marks=marks))
    return out


def _chapter_sections(cfg, site, chapters, tpl, aa_dir, offset=0, total=None,
                      prev_href="", next_href="", tr_only=False, want_src=True):
    """渲染**一批**章节 → (sections, payloads, n_src_missing)。

    两个调用方共用这一段（别再各写一遍、别改出两套口径）：
      · 正式交付文件夹（`main()`）：全章一批，offset=0，无跨文件链接；
      · 「仅 HTML 文件」简版（`_gen_plain()`）：分页，offset = 本文件首章在整部的序号，
        prev_href/next_href = 相邻分页文件名（首/末章的翻页按钮改成跨文件链接）。

    payload 的章序号 = **本批内**序号（0..len-1）——页面 JS 按本文件的章节骨架取数。
    tr_only=True（仅译文）→ payload 只带 tr；want_src=False 时连原文渲染都跳过。
    """
    if total is None:
        total = len(chapters)
    n = len(chapters)
    n_src_missing = 0
    sections, payloads = [], []
    for idx, f in enumerate(chapters):
        m = EID_RE.search(os.path.basename(f))
        eid = m.group(1) if m else ''
        eid = cfg.get("eid_map", {}).get(os.path.basename(f), eid)  # eid_map 覆盖文件名推断(第1话无 eid 前缀)
        src_url = src_url_of(f, cfg, eid) or ''
        lines = _read_lines(f)
        base = _base_of(f)
        cm = {}
        cmf = os.path.join(aa_dir, base + "_colormap.json")
        if os.path.exists(cmf):
            cm = json.load(open(cmf, encoding="utf-8"))
        plan = _blank_plan(lines)           # 楼间间距：帖头前补足 ≥2 空行（幂等）
        # 原文行（sfx 自动对齐 + 译/原切换共用）——**先读原文**：译文行的 sfx 特效要
        # 对照原文行的半角假名段。带 work/sfx_marks/<base>.json 的人工标记一起透传
        sp = _src_path(aa_dir, base)
        if sp:
            src_lines = _read_lines(sp)
        else:
            src_lines, n_src_missing = lines, n_src_missing + 1
        if len(src_lines) < len(lines):
            src_lines = src_lines + [''] * (len(lines) - len(src_lines))
        marks = _sfx_marks_of(cfg.get("work_dir"), base)
        rendered = _render_body(lines, cm, site, plan, src_lines=src_lines, marks=marks)
        # 原文渲染（译/原切换用）：同一份间距计划 → 两种模式行位置完全对齐
        rendered_src = _render_body(src_lines[:len(lines)], cm, site, plan) if want_src else None
        label = chapter_label(f, cfg)
        style = "" if idx == 0 else ' style="display:none"'
        if idx > 0:
            prev_btn = f'<button type="button" onclick="showChapter({idx-1})" id="prev{idx}">&larr; 上一章</button>'
        elif prev_href:
            prev_btn = f'<a class="pager-btn" href="{H.escape(prev_href)}">&larr; 上一部分</a>'
        else:
            prev_btn = '<button type="button" disabled>&larr; 上一章</button>'
        if idx < n - 1:
            next_btn = f'<button type="button" onclick="showChapter({idx+1})" id="next{idx}">下一章 &rarr;</button>'
        elif next_href:
            next_btn = f'<a class="pager-btn" href="{H.escape(next_href)}">下一部分 &rarr;</a>'
        elif tpl == 'fc2':
            next_btn = f'<button type="button" onclick="showChapter({idx+1})" id="next{idx}">下一章 &rarr;</button>'
        else:
            next_btn = '<button type="button" disabled>下一章 &rarr;</button>'
        cur = offset + idx + 1                 # 「第 N / M 章」用**整部**序号（分页时也看得清在哪）
        if tpl == 'legacy':
            sections.append(f'<section class="chapter" id="ch{idx}"{style}>\n'
                            f'<div class="pager">{prev_btn}{next_btn}</div>\n'
                            f'<h2>{H.escape(label)}</h2>\n'
                            f'<div class="src-link">原帖地址：<a href="{H.escape(src_url)}" target="_blank">{H.escape(src_url)}</a></div>\n'
                            f'<div class="aa-wrap"><div class="aa-gut" id="gut{idx}"></div>'
                            f'<pre class="aa" id="pre{idx}"></pre></div>\n'
                            f'<div class="pager">{prev_btn}{next_btn}</div>\n'
                            f'<div class="chapter-ind">{cur} / {total} 章</div>\n'
                            f'</section>')
        elif tpl == 'fc2':
            num = eid or str(idx + 1)
            sections.append(f'<section class="chapter" id="ch{idx}"{style}>\n'
                            f'<h2>{H.escape(label)}（FC2 #{num}）</h2>\n'
                            f'<div class="src-link">原帖：<a href="{H.escape(src_url)}" target="_blank">{H.escape(src_url)}</a></div>\n'
                            f'<div class="pager">{prev_btn} <span class="chapter-ind">第{cur} / {total} 章</span> {next_btn}</div>\n'
                            f'<div class="aa-wrap"><div class="aa-gut" id="gut{idx}"></div>'
                            f'<pre class="aa" id="pre{idx}"></pre></div>\n'
                            f'<div class="pager">{prev_btn} <span class="chapter-ind">第{cur} / {total} 章</span> {next_btn}</div>\n'
                            f'</section>')
        else:  # saki
            m2 = re.search(r'第(\d+)話', os.path.basename(f))
            num = m2.group(1) if m2 else str(idx + 1)
            sections.append(f'<section class="chapter" id="ch{idx}"{style}>\n'
                            f'<div class="pager">{prev_btn}{next_btn}</div>\n'
                            f'<h2>第{num}话</h2>\n'
                            f'<div class="src-link">原帖地址：<a href="{H.escape(src_url)}" target="_blank">{H.escape(src_url)}</a></div>\n'
                            f'<div class="aa-wrap"><div class="aa-gut" id="gut{idx}"></div>'
                            f'<pre class="aa" id="pre{idx}"></pre></div>\n'
                            f'<div class="pager">{prev_btn}{next_btn}</div>\n'
                            f'<div class="chapter-ind">{cur} / {total} 话</div>\n'
                            f'</section>')
        payloads.append(chapter_payload(idx, rendered, rendered_src, tr_only=tr_only))
    return sections, payloads, n_src_missing


def _sfx_marks_of(work_dir, base):
    """读人工 sfx 标记（`<work_dir>/sfx_marks/<原文基名>.json`）；缺失/损坏 → {}。

    局部实现（不 import logic）：结构同 `logic.load_sfx_marks` =
    {"1-based行号": [[字符偏移, 文本], ...]}；非法项由 `_marks_for_line` / `_mark_sfx_spans` 兜。
    """
    if not work_dir:
        return {}
    p = os.path.join(work_dir, "sfx_marks", base + ".json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def chapter_order(TR_DIR, name_style=None, work_dir=None):
    """汉化文件排序：优先 work/chapter_order.json（原站顺序, 2026-08-24 起支持），
    缺失时按文件名 eid 数字排序。

    chapter_order.json 结构: {"order": ["20400", "36400", ...]}（eid 列表, 原站顺序）。
    某些作品 eid 与话数序不一致(如 akari_koikokoro: 20400=第1话 36400=第2话 20822=第3话),
    文件名排序会错乱 → 必须用缓存序。
    """
    # 汉化文件（.txt 与 .txt.gz 都算；同名并存时取未压缩那份，避免重复成两章）
    _SUF = ("_汉化.txt.gz", "_汉化.txt")
    picked = {}
    for f in os.listdir(TR_DIR):
        for i, suf in enumerate(_SUF):
            if f.endswith(suf):
                stem = f[:-len(suf)]
                if stem not in picked or i == len(_SUF) - 1:
                    picked[stem] = f
                break
    files = list(picked.values())
    if name_style == "eid_title":
        # 早期 FC2 残留(entry_*_汉化.txt)混入汉化/ 时不得当本作章节
        files = [f for f in files if not f.startswith("entry_")]
    order = []
    if work_dir:
        op = os.path.join(work_dir, "chapter_order.json")
        if os.path.exists(op):
            try:
                order = json.load(open(op, encoding="utf-8")).get("order", [])
            except Exception:
                order = []
    if order:
        eid_of = {}
        for f in files:
            m = EID_RE.search(f)
            if m:
                eid_of[f] = m.group(1)
        # 按 order 里的 eid 找对应文件; order 里没有的(新章)按 eid 数字补尾
        ranked = []
        for e in order:
            for f, fe in eid_of.items():
                if fe == e and f not in ranked:
                    ranked.append(f)
        tail = sorted((f for f in files if f not in ranked),
                      key=lambda f: int(EID_RE.search(f).group(1)) if EID_RE.search(f) else 99999)
        files = ranked + tail
    else:
        files = sorted(files, key=lambda f: int(EID_RE.search(f).group(1)) if EID_RE.search(f) else 99999)
    return [os.path.join(TR_DIR, f) for f in files]


def series_label(first, cfg):
    """label_style='series'：按 cfg['series_arcs'] 把长标题压成「弧名 副题」。"""
    t = first[1:].strip() if first.startswith('#') else first.strip()
    if '異聞' in t and '狂った村' in t and '歪んだ街' in t:
        return '街 異聞'
    normt = re.sub(r'[\s\u3000]+', '', t)
    for arc in cfg.get('series_arcs', []):
        pn = re.sub(r'[\s\u3000]+', '', arc['prefix'])
        if not normt.startswith(pn):
            continue
        # 构造前缀正则：弧名前缀里的每个空白(半角/全角) → [\s\u3000]*，替换串为''无转义坑
        pat = ''.join(r'[\s\u3000]*' if (c.isspace() or c == '\u3000') else re.escape(c)
                      for c in arc['prefix'])
        rest = re.sub(pat, '', t, count=1)
        rest = re.sub(r'[『』◆]', '', rest)        # 去引号/◆
        rest = re.sub(r'または.*$', '', rest)      # 取主标题（去掉 または…）
        rest = re.sub(r'[\s\u3000]+', ' ', rest).strip()  # 空白/全角空格 → 单半角空格
        rest = rest.replace('話', '话')                   # 统一简体「话」（同 saki 惯例）
        if not rest:
            return f"{arc['name']} 第1话"
        return f"{arc['name']} {rest}"
    return t


def work_name_prefixes(cfg):
    """作品名候选串（registry `title` / `html_title` 去掉「汉化合集」后缀 / 别名），长串优先。"""
    names = []
    for k in ("title", "html_title"):
        v = re.sub(r'\s*(汉化合集|漢化合集)\s*$', '', (cfg.get(k) or "").strip()).strip()
        if v:
            names.append(v)
    for a in (cfg.get("title_aliases") or []):
        a = (a or "").strip()
        if a:
            names.append(a)
    return sorted(set(names), key=len, reverse=True)


# 作品名与章节标题之间的分隔符（去掉作品名后一并吃掉）
_SEP_CHARS = " \u3000\t-–—:：·・　"
# 原帖标题里作品名常见的包装（『』「」【】／～）——去掉作品名后残留成对括号时一并剥掉
_WRAP_PAIRS = (('『', '』'), ('「', '」'), ('【', '】'), ('（', '）'), ('(', ')'), ('～', '～'), ('~', '~'))


def strip_work_name(label, cfg):
    """目录条目标题去掉**开头重复的作品名**（「不幸の催眠　第1話」→「第1話」）。

    只去开头且去后仍有内容（避免把整条标题去空）；去掉后只剩成对包装符也继续剥。
    """
    t = (label or "").strip()
    for n in work_name_prefixes(cfg):
        if not t.startswith(n):
            continue
        rest = t[len(n):].lstrip(_SEP_CHARS)
        if not rest:
            continue
        for a, b in _WRAP_PAIRS:
            if len(rest) > 2 and rest.startswith(a) and rest.endswith(b):
                rest = rest[1:-1].strip()
                break
        if rest:
            return rest
    return t


def chapter_label(f, cfg):
    """目录/标题文字，按 label_style"""
    style = cfg.get("label_style", "plain")
    try:
        first = _read_lines(f)[0].strip()
    except Exception:
        first = ''
    if style == "series":
        return series_label(first, cfg)
    if style == "saki":
        m = re.search(r'第(\d+)話', os.path.basename(f))
        return f"第{m.group(1)}话" if m else _base_of(f)
    if style == "fc2":
        m3 = re.search(r'第(\d+)话', first)
        if m3:
            return f'第{m3.group(1)}话'
        m2 = re.search(r'[　\s](\d{1,2})\s*$', first)
        if m2 and first.endswith(m2.group(1)):
            return f'第{m2.group(1)}话'
        t = re.sub(r'^#\s*【あんこ】さーきー【咲 -saki-】\s*', '', first)
        m = EID_RE.search(os.path.basename(f))
        eid = m.group(1) if m else ''
        return (strip_work_name(t, cfg) or (f'番外#{eid}' if eid else os.path.basename(f)))
    # plain: 取 txt 第1行标题原样（去掉开头重复的作品名）
    if first.startswith('#'):
        return strip_work_name(first[1:].strip(), cfg)
    return _base_of(f)


def render_line(i, l, cm, site, src_line=None, sfx_marks=None):
    if not l.strip():
        return ''
    hdr = site.render_header(l)
    if hdr:
        return hdr
    color = cm.get(str(i))
    if color == 'font-weight:bold':
        # 只加粗 【1D10:X】 括号片段本身，其余（含AA艺术）保持原样
        out = []
        pos = 0
        for m in re.finditer(r'【1D10:[^】]*】', l):
            out.append(H.escape(l[pos:m.start()]))
            out.append(f'<span style="font-weight:bold">{H.escape(m.group(0))}</span>')
            pos = m.end()
        out.append(H.escape(l[pos:]))
        return ''.join(out)
    # 2026-08-25: 半角假名译文特效(拟声词/效果音)——对照原文行, 找半角假名段位置,
    #   对应汉化行同一宽度位置的译文中文 → 包 sfx span(减小字号+斜体+弱化色)
    # 2026-09-15: 叠加人工 sfx 标记(划词翻译「sfx 替换」写 work/sfx_marks/<base>.json)
    spans = sfx_spans(l, src_line, _marks_for_line(sfx_marks, i))
    if spans:
        return _render_spans(l, spans, color)
    if color:
        return f'<span style="{H.escape(color)}">{H.escape(l)}</span>'
    return H.escape(l)


# 半角假名段(拟声词/效果音) 2026-08-25
HANKAKU_RE = re.compile(r'[\uff61-\uff9f]{2,}')


def _marks_for_line(sfx_marks, i):
    """取第 i 行(0-based)的 sfx 人工标记：整章 dict（键=1-based 行号串）或直接行内列表。"""
    if not sfx_marks:
        return []
    if isinstance(sfx_marks, dict):
        v = sfx_marks.get(str(i + 1))
        return v if isinstance(v, list) else []
    return sfx_marks if isinstance(sfx_marks, list) else []


def sfx_spans(l, src_line=None, marks=None):
    """汉化行上需包 <span class="sfx"> 的字符区间 [(start, end)]（两个来源并集）。

    ① 自动：原文行半角假名段 → 按显示宽度对齐到汉化行同宽度处的中文段；
    ② 人工：marks = [[字符偏移, 文本], ...]（偏移漂移时取最近的同文本出现处）。
    """
    spans = []
    if src_line and HANKAKU_RE.search(src_line):
        for a, b in _auto_sfx_spans(l, src_line):
            if HAN_ZH_RE.search(l[a:b]):     # 段内中文才包 sfx(纯图形跳过)
                spans.append((a, b))
    spans += _mark_sfx_spans(l, marks or [])
    return _merge_spans(spans, len(l))


def _width_prefix(line, width):
    """从行首累计显示宽度到 width, 返回(前缀串, 剩余串)。按 disp_width 对齐。"""
    from aa_common import disp_width
    w = 0
    for j, ch in enumerate(line):
        cw = disp_width(ch)
        if w + cw > width:
            return line[:j], line[j:]
        w += cw
    return line, ''


def _auto_sfx_spans(l, src_line):
    """原文半角假名段起始显示宽度 = 汉化行同宽度位置 → 对应中文段的字符区间。

    对齐方式: 原文行的半角假名段起始宽度(累计显示宽度) = 汉化行对应位置。
    """
    from aa_common import disp_width
    out = []
    pos = 0          # 汉化行处理游标
    src_pos = 0      # 原文行宽度游标
    for m in HANKAKU_RE.finditer(src_line):
        seg_start = m.start()
        # 原文行到段前的宽度
        pre_w = disp_width(src_line[src_pos:seg_start])
        # 汉化行对应宽度处的前缀(未处理部分)
        pre, rest = _width_prefix(l[pos:], pre_w)
        pos += len(pre)
        # 译文段: rest 的前面部分(到段宽度)
        seg_txt, rest = _width_prefix(rest, disp_width(m.group(0)))
        if seg_txt:
            out.append((pos, pos + len(seg_txt)))
        pos += len(seg_txt)
        src_pos = seg_start + len(m.group(0))
        if not rest:
            break
    return out


def _mark_sfx_spans(l, marks):
    """人工 sfx 标记 [[偏移, 文本], ...] → 字符区间（偏移命中优先，漂移时取最近的同文本出现处）。"""
    out = []
    for m in marks:
        try:
            off, txt = max(0, int(m[0])), str(m[1])
        except (TypeError, ValueError, IndexError):
            continue
        if not txt:
            continue
        if l[off:off + len(txt)] == txt:
            out.append((off, off + len(txt)))
            continue
        hit = -1
        for i in range(len(l) - len(txt) + 1):
            if l.startswith(txt, i) and (hit < 0 or abs(i - off) < abs(hit - off)):
                hit = i
        if hit >= 0:
            out.append((hit, hit + len(txt)))
    return out


def _merge_spans(spans, n):
    """区间排序 + 裁剪到 [0,n) + 重叠合并, 返回 [(start, end)]。"""
    out = []
    for a, b in sorted((int(a), int(b)) for a, b in spans):
        a, b = max(0, min(a, n)), max(0, min(b, n))
        if b <= a:
            continue
        if out and a < out[-1][1]:     # 只合并重叠区间（相邻不合并——保持与旧实现逐字节一致）
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _render_spans(l, spans, color=None):
    """按区间包 sfx span 渲染（其余部分原样转义 + 可选整行颜色）。"""
    out, pos = [], 0
    for a, b in spans:
        out.append(H.escape(l[pos:a]))
        out.append(f'<span class="sfx">{H.escape(l[a:b])}</span>')
        pos = b
    out.append(H.escape(l[pos:]))
    res = ''.join(out)
    if color:
        return f'<span style="{H.escape(color)}">{res}</span>'
    return res


def render_sfx_line(l, src_line, color=None):
    """（兼容入口）半角假名译文 sfx 特效：等价于对 sfx_spans 的结果做 _render_spans。"""
    return _render_spans(l, sfx_spans(l, src_line), color)


# 半角假名段内是否含中文(译文)
HAN_ZH_RE = re.compile(r'[\u4e00-\u9fff]')


def src_url_of(f, cfg, eid):
    """txt 里的 # 原帖地址 行优先；缺失时按 url_pattern 构造（saki 第1话用 eid_map）"""
    for l in _read_lines(f):
        if l.startswith('# 原帖地址: '):
            return l.replace('# 原帖地址: ', '').strip()
    if not eid:
        eid = cfg.get("eid_map", {}).get(os.path.basename(f))
    if eid:
        return cfg["url_pattern"].format(eid=eid)
    return None


TAIL_JS = """</main>
<div class="modebar" id="modebar">
  <button type="button" data-mode="tr" class="on">译文</button>
  <button type="button" data-mode="src">原文</button>
  <button type="button" class="editbtn" id="btn-edit" data-tip="编辑模式：点行号即可改译文，右键行号还原原文"
          title="编辑模式：点行号即可改译文，右键行号还原原文">&#9998; 编辑</button>
  <button type="button" class="editbtn" id="btn-tr" data-tip="翻译替换：查找 → 替换为，直接写回源（AA 艺术行整行跳过）"
          title="翻译替换：查找 → 替换为，直接写回源（AA 艺术行整行跳过）">&#8646; 替换</button>
</div>
<button type="button" id="tocbtn" title="展开 / 收起目录">目录</button>
<div id="tocmask"></div>
<div id="edithint"></div>
<div id="trm"><div class="box">
  <h3>&#8646; 翻译替换（直接写回源）</h3>
  <div id="trm-pairs"></div>
  <div class="tr-opt">
    <button type="button" id="trm-add" class="ghost">+ 增加一对</button>
    <span>范围：<label><input type="radio" name="trscope" value="ch" checked> 当前章</label>
      <label><input type="radio" name="trscope" value="all"> 全部章节</label></span>
    <span id="trm-cur"></span>
  </div>
  <div class="tr-opt">
    <button type="button" id="trm-preview">预览</button>
    <button type="button" id="trm-apply">应用</button>
    <button type="button" id="trm-close" class="ghost">关闭</button>
    <span class="emsg" id="trm-msg"></span>
  </div>
  <div class="tr-body" id="trm-body"></div>
</div></div>
<div id="st"></div>
<script>
(function () {
  var WORKKEY = "__KEY__";
  var TOTAL = __TOTAL__;
  var OFF = __OFFSET__;               // 本文件第 1 章在**整部作品**里的序号（分页简版；不分页 = 0）
  var SELF = "__SELF__";              // 本文件名 —— 存档 + 跨文件「上次读到第 N 话」提示用
  var PLAIN = __PLAIN__;              // 「仅 HTML」交付：没有编辑服务 → 不挂编辑/替换
  var TRONLY = __TRONLY__;            // 「仅译文」：没有原文数据，不显示「原文」按钮
  var PKEY = "aa:" + WORKKEY;
  var TXT = {};                       // n -> {tr, src}（解压后的已渲染 HTML）
  var LOADED = {};                    // n -> Promise（脚本只注入一次）
  var FILLED = {};                    // n -> 1（<pre> 里已经填了内容）
  var SHOWN = -1;                     // 当前章
  var mode = TRONLY ? "tr" : (localStorage.getItem(PKEY + ":mode") === "src" ? "src" : "tr");
  // 换行符：本文件是 Python 三引号（非 raw）字符串，JS 里写反斜杠 n 会被 Python 先解码成真换行
  // → JS 字符串断行报 SyntaxError。凡是要用到换行的 JS，统一用 NL。
  var NL = String.fromCharCode(10);
  var KEEP = 2;                       // 当前章前后各保留几章（越界即释放 DOM+解压结果）
  var mute = true, saveT = 0, jumped = false, pendY = null;

  function el(id) { return document.getElementById(id); }
  function pad(n) { return ("00000" + n).slice(-5); }
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  // 注意：data/*.js 是**后注入**的，它们会 `window.AA_DATA = window.AA_DATA || []` 新建数组，
  // 所以每次都要现读 window.AA_DATA，不能像以前那样在启动时捕获数组引用（捕获了就永远是空数组）
  function dget(n) { return window.AA_DATA ? window.AA_DATA[n] : null; }

  function status(msg, isErr) {
    var s = el("st");
    s.textContent = msg || "";
    s.className = msg ? (isErr ? "on err" : "on") : "";
  }

  var toastT = 0;
  function toast(msg, isErr) {
    status(msg, isErr);
    if (toastT) clearTimeout(toastT);
    toastT = setTimeout(function () { toastT = 0; status(""); }, 3200);
  }

  // ── 章节数据读不到：按「打开方式」说清**真正**的原因 ──────────────────────
  // 「本文件夹必须整体拷贝」是错的：手机文件管理器/QQ 传 content:// 给浏览器时，
  // 只授权了 index.html 这一个文件，浏览器读不了同目录的 data/（桌面 Chrome/file:// 没这问题）。
  function loadErr(n) {
    var f = "data/" + pad(n) + ".js", pr = location.protocol, short, how;
    if (pr === "content:") {
      short = "读不到 " + f + "：当前是文件管理器 / QQ 用 content:// 方式打开的，浏览器只被授权了 index.html 这一个文件，读不了同目录的其它文件（不是文件夹没拷全）";
      how = "<b>手机上这样读（任选一条）：</b><br>"
          + "① 电脑上双击交付夹里的「手机阅读(局域网).bat」，手机连同一个 Wi-Fi，用手机浏览器打开窗口里打印的 http://…:8770/index.html ← 最省事<br>"
          + "② 用火狐手机版打开 file:///storage/emulated/0/你放这本书的目录/index.html<br>"
          + "③ 手机装个本地 HTTP 服务器 App，指向本文件夹，再用 http://127.0.0.1:端口/ 打开";
    } else if (pr === "file:") {
      short = "读不到 " + f + "：这个浏览器不许本地页面读同目录文件（安卓 Chrome 的限制，桌面 Chrome 没这问题）";
      how = "<b>换个打开方式：</b><br>① 火狐手机版打开同一个 file:///…/index.html<br>"
          + "② 电脑「手机阅读(局域网).bat」＋手机浏览器打开发出来的 http://…:8770/index.html<br>"
          + "③ 本地 HTTP 服务器 App 指向本文件夹，再用 http://127.0.0.1:端口/ 打开";
    } else {
      short = "读不到 " + f + "：交付文件夹里的 data/ 目录没跟 index.html 在一起（请整个文件夹一起拷贝）";
      how = "index.html 必须和 data/ 在同一个文件夹里（data/ 里是各章正文数据）。";
    }
    return { short: short, how: how };
  }

  var LOADERR_SHOWN = 0;
  function showLoadErr(n) {                    // 指引面板只弹一次；后续章节失败只走状态条
    var e = loadErr(n);
    toast(e.short, true);
    if (LOADERR_SHOWN) return;
    LOADERR_SHOWN = 1;
    var h = el("edithint");
    if (!h) return;
    h.className = "loaderr on";
    h.innerHTML = '<div class="le-t">读不了章节数据</div><div class="le-b">' + e.how + "</div>"
                + '<button type="button" class="le-x" id="le-x">知道了</button>';
    var b = el("le-x");
    if (b) b.onclick = function () { h.className = ""; };
  }

  // ── 数据装载：<script src> 是 file:// 下唯一能离线读文件的方式（fetch 被 CORS 拦死）──
  function load(n) {
    if (LOADED[n]) return LOADED[n];
    LOADED[n] = new Promise(function (res, rej) {
      var d = dget(n);
      if (d) return res(d);
      var s = document.createElement("script");
      s.src = "data/" + pad(n) + ".js";
      s.onload = function () {
        dget(n) ? res(dget(n)) : rej(new Error("data/" + pad(n) + ".js 里没有第 " + (n + 1) + " 章数据"));
      };
      s.onerror = function () { var e = new Error(loadErr(n).short); e.loadErrN = n; rej(e); };
      document.head.appendChild(s);
    });
    return LOADED[n];
  }

  function unz(b64) {
    var bin = atob(b64), n = bin.length, u8 = new Uint8Array(n);
    for (var i = 0; i < n; i++) u8[i] = bin.charCodeAt(i);
    var st = new Blob([u8]).stream().pipeThrough(new DecompressionStream("gzip"));
    return new Response(st).text();
  }

  // 章节数据两种格式：v1 {tr,src} 两份；v2 {src,p} = src 全量 + tr 相对 src 的差异行
  // （AA 里艺术行/帖头/空行不翻译 → 九成行两份一模一样，v2 只存不同行 → data 省 ~42%）
  var STX = String.fromCharCode(2);
  function unpatch(srcHtml, patch) {
    if (!patch) return srcHtml;
    var rows = String(srcHtml).split(NL), ps = String(patch).split(NL);
    for (var i = 0; i < ps.length; i++) {
      var s = ps[i];
      if (!s) continue;
      var j = s.indexOf(STX);
      if (j < 0) continue;
      var k = +s.slice(0, j);
      if (k >= 0 && k < rows.length) rows[k] = s.slice(j + 1);
    }
    return rows.join(NL);
  }

  function decode(d) {                    // payload → [tr, src]（v1/v2/「仅译文」都吃）
    if (!d.src) return unz(d.tr).then(function (tr) { return [tr, tr]; });   // 仅译文：没有原文数据
    return unz(d.src).then(function (src) {
      if (!d.p) return unz(d.tr).then(function (tr) { return [tr, src]; });
      return unz(d.p).then(function (p) { return [unpatch(src, p), src]; });
    });
  }

  function fill(n) {
    var pre = el("pre" + n);
    if (!pre || !TXT[n]) return;
    var body = TXT[n][mode];
    // 编辑模式 + 已知行号映射 → 每行包一层 <span class="aa-line" data-i="txt行号">
    // （换行放在 span 内部：行与行之间没有裸文本节点，才能把编辑行插进正文流而不多出空行）
    if (ES.mode && ES.mapN === n && ES.map) body = spanRows(body, ES.map);
    pre.innerHTML = body;
    FILLED[n] = 1;
    if (ES.mode) paintGut(n);
  }

  function spanRows(body, ln) {
    var rows = String(body).split(NL), out = [];
    for (var k = 0; k < rows.length; k++) {
      out.push('<span class="aa-line" data-i="' + ((ln && ln[k]) || 0) + '">'
               + rows[k] + (k === rows.length - 1 ? "" : NL) + "</span>");
    }
    return out.join("");
  }

  function drop(n) {
    TXT[n] = null; LOADED[n] = null; FILLED[n] = 0;
    // 释放已加载的 b64。外置形态回访时靠浏览器缓存重读 data/*.js 拿回来；
    // **单档形态（window.AA_INLINE）没有可重读的文件**，置空就再也读不回来 → 必须留着。
    if (!window.AA_INLINE && window.AA_DATA) window.AA_DATA[n] = null;
    var pre = el("pre" + n);
    if (pre) pre.innerHTML = "";
  }

  // 章节多时把远处已渲染的章释放掉：DOM 里始终只留当前章附近几章，
  // 否则 800+ 章全填进 DOM 会让滚动/切换越来越卡（1.6M 级节点）
  function evict() {
    for (var k in FILLED) {
      var n = +k;
      if (FILLED[n] && Math.abs(n - SHOWN) > KEEP) drop(n);
    }
  }

  function prefetch(n) {                       // 空闲时预取下一章 → 「下一章」秒开
    if (n >= 0 && n < TOTAL && !dget(n) && !LOADED[n]) load(n).catch(function () {});
  }

  function paint() {
    if (SHOWN >= 0) fill(SHOWN);                // 只重填当前章（原来会重填全部章 → 切换卡）
    var bs = document.querySelectorAll(".modebar button[data-mode]");
    for (var j = 0; j < bs.length; j++) {
      bs[j].classList.toggle("on", bs[j].getAttribute("data-mode") === mode);
    }
  }

  function show(n) {
    status("正在载入第 " + (n + 1) + " 章…");
    load(n).then(function (d) {
      if (SHOWN !== n) return null;             // 载入期间用户又跳走了
      if (!TXT[n]) return decode(d);            // v1/v2 两种 payload 都吃（v2=差异行）
      return null;
    }).then(function (p) {
      if (SHOWN !== n) return;
      if (p) TXT[n] = {tr: p[0], src: p[1]};
      if (!TXT[n]) return;
      status("");
      fill(n);
      evict();
      if (pendY !== null) { window.scrollTo(0, pendY); pendY = null; mute = false; }
      prefetch(n + 1);
    }).catch(function (e) {
      if (e && typeof e.loadErrN === "number") showLoadErr(e.loadErrN);
      status("第 " + (n + 1) + " 章载入失败：" + (e && e.message ? e.message : e), true);
    });
  }

  function hideAll(except) {                 // 骨架里第 0 章默认可见 → 首跳要显式关掉其余章
    var secs = document.querySelectorAll("section.chapter");
    for (var i = 0; i < secs.length; i++) { if (i !== except) secs[i].style.display = "none"; }
  }

  function jump(n, keepScroll) {
    if (n < 0 || n >= TOTAL) return;
    if (n === SHOWN) { if (!keepScroll) window.scrollTo(0, 0); save(); return; }
    if (SHOWN >= 0) {
      var old = el("ch" + SHOWN), ob = el("toc" + SHOWN);
      if (old) old.style.display = "none";
      if (ob) ob.classList.remove("active");
    } else {
      hideAll(n);
    }
    var s = el("ch" + n), b = el("toc" + n);
    if (s) s.style.display = "block";
    if (b) b.classList.add("active");
    SHOWN = n;
    if (!keepScroll) window.scrollTo(0, 0);
    show(n);
    save();
  }
  // 章节骨架/nav 里的 onclick="showChapter(N)" 走这里（用户主动跳章 → 不再套用记忆位置）
  window.showChapter = function (n) { jumped = true; jump(n, false); };

  function save() {
    if (mute || SHOWN < 0) return;
    try {
      // ch = **整部作品**里的章序号（分页简版要加上本文件的偏移 OFF）——
      // 打开别的分页文件时才能提示「上次读到第 N 话（在 xxx.html）」
      localStorage.setItem(PKEY, JSON.stringify({ch: SHOWN + OFF, y: Math.round(window.scrollY), f: SELF}));
    } catch (e) {}
  }

  function fail(msg) {
    var box = el("loading");
    box.className = "err";
    box.textContent = msg;
  }

  // 分页简版：上次读的是**另一个** HTML 文件 → 头部给一条「继续上次阅读」跳转（不硬跳走）
  function xResume(st) {
    var f = st.f || "";
    var box = document.createElement("div");
    box.id = "xres";
    var t = '上次读到第 ' + (st.ch + 1) + ' 话';
    if (f && f !== SELF) t += '（在 <a href="' + encodeURI(f) + '">' + escapeHtml(f) + '</a>）';
    box.innerHTML = t;
    var hd = document.querySelector("header");
    (hd || document.body).appendChild(box);
  }

  function bindModebar() {
    var bs = document.querySelectorAll(".modebar button[data-mode]");
    for (var i = 0; i < bs.length; i++) {
      (function (b) {
        b.addEventListener("click", function () {
          mode = b.getAttribute("data-mode");
          try { localStorage.setItem(PKEY + ":mode", mode); } catch (e) {}
          paint();
        });
      })(bs[i]);
    }
  }

  function boot() {
    if (typeof DecompressionStream === "undefined") {
      fail("此浏览器不支持 DecompressionStream，无法解压章节数据。\\n请用较新的 Chrome / Edge / Firefox / Safari 打开。");
      return;
    }
    bindModebar();
    var st = null;
    try { st = JSON.parse(localStorage.getItem(PKEY) || "null"); } catch (e) {}
    if (jumped) { mute = false; return; }        // 用户已抢先点章 → 不抢回
    var n = 0;
    var abs = (st && typeof st.ch === "number") ? st.ch : -1;   // 存档 = 整部作品的章序号
    var local = abs - OFF;                                      // 换算成本文件的章序号
    if (st && local >= 0 && local < TOTAL) {
      n = local; pendY = (st.y || 0);
    } else {
      if (st && abs >= 0) xResume(st);           // 存档落在别的分页文件 → 提示，不硬跳
      pendY = 0;
    }
    mute = true;
    jump(n, true);
    var ld = el("loading");
    if (ld) ld.style.display = "none";
  }

  window.addEventListener("scroll", function () {
    if (saveT) return;
    saveT = setTimeout(function () { saveT = 0; save(); }, 500);
  }, {passive: true});
  window.addEventListener("beforeunload", save);
  document.addEventListener("visibilitychange", function () { if (document.hidden) save(); });
  document.addEventListener("keydown", function (e) {
    var tg = (document.activeElement || {}).tagName || "";
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(tg)) return;
    if (e.key === "ArrowRight" || e.key === "PageDown") window.showChapter(SHOWN + 1);
    if (e.key === "ArrowLeft" || e.key === "PageUp") window.showChapter(SHOWN - 1);
  });
  // ═══════════ 手机端：目录抽屉（默认隐藏，点「目录」展开；选完章自动收起）═══════════
  (function () {
    var btn = el("tocbtn"), mask = el("tocmask"), nav = document.querySelector("nav");
    if (!btn || !nav) return;
    function setOpen(on) {
      nav.classList.toggle("open", on);
      if (mask) mask.classList.toggle("on", on);
    }
    btn.addEventListener("click", function () { setOpen(!nav.classList.contains("open")); });
    if (mask) mask.addEventListener("click", function () { setOpen(false); });
    nav.addEventListener("click", function (e) {
      if (e.target && e.target.tagName === "BUTTON") setOpen(false);
    });
    window.addEventListener("resize", function () { if (!isMobile()) setOpen(false); });
  })();

  // ═══════════ 交付页编辑（PC）：行修改 + 翻译替换；写回源由本地「编辑服务」完成 ═══════════
  // 页面本身是静态的（file:// 双击可看），改文件必须有个能写盘的本地服务：
  // 交付文件夹里的 AA编辑服务.exe / edit_server.py。它在 127.0.0.1:8770~8779 上同时
  // 提供静态页与 /api/*，并带 CORS（所以 file:// 打开时也能调它）。连不上 → 只读浏览。
  var ES = {url: "", mode: false, idx: 0, src: "", map: null, mapN: -1, er: null, sp: null};
  var ES_PORT0 = 8770, ES_NPORTS = 10, ES_KEY = "aa_edit_url";

  function isMobile() { return window.innerWidth <= 640; }

  function hint(msg, isErr) {
    var h = el("edithint");
    if (!h) return;
    h.textContent = msg || "";
    h.className = msg ? (isErr ? "on err" : "on") : "";
  }

  function esPing(url, cb) {
    var done = false;
    var t = setTimeout(function () { if (!done) { done = true; cb(null); } }, 2500);
    fetch(url + "/api/ping").then(function (r) { return r.json(); }).then(function (j) {
      if (done) return; done = true; clearTimeout(t); cb(j && j.ok ? j : null);
    }).catch(function () { if (done) return; done = true; clearTimeout(t); cb(null); });
  }

  // 编辑/替换按钮：服务没起来时**置灰不可点**，鼠标悬浮给原因（不再弹红色提示条）
  function editBtns(on, tip) {
    var bs = document.querySelectorAll(".modebar button.editbtn"), m;
    for (m = 0; m < bs.length; m++) {
      // 注意必须给具体值：CSS 里 .editbtn{display:none}，设 "" 会退回样式表 → 按钮仍然不见
      bs[m].style.display = "inline-block";
      bs[m].disabled = !on;
      bs[m].title = on ? (bs[m].getAttribute("data-tip") || "") : (tip || "");
    }
  }

  function esLocate() {
    if (isMobile()) return;                       // 手机端只浏览
    var urls = [], saved = "";
    try { saved = localStorage.getItem(ES_KEY) || ""; } catch (e) {}
    if (location.protocol !== "file:") urls.push("");     // 由编辑服务自己起的页面 → 同源
    if (saved) urls.push(saved);
    for (var i = 0; i < ES_NPORTS; i++) urls.push("http://127.0.0.1:" + (ES_PORT0 + i));
    var k = 0;
    (function step() {
      if (k >= urls.length) {                     // 只读浏览：按钮置灰 + 悬浮说明
        editBtns(false, "只读浏览：改行 / 替换需要先运行交付文件夹里的「AA编辑服务.exe」"
                        + "（双击它，或双击 编辑服务.bat），再从它打开的页面里编辑");
        return;
      }
      esPing(urls[k], function (j) {
        if (!j) { k++; return step(); }
        ES.url = urls[k];
        try { localStorage.setItem(ES_KEY, ES.url); } catch (e) {}
        if (j.remote) {                             // 局域网/手机访问：服务端只读
          editBtns(false, "只读浏览：从局域网地址打开时只读（手机等其它设备只能看）；改行 / 替换请在电脑上用 "
                          + "http://127.0.0.1:" + (location.port || 8770) + "/ 打开");
          return;
        }
        if (j.readonly) {
          editBtns(false, "这个交付文件夹是只读的（里面没有可写的汉化 txt），只能浏览");
          return;
        }
        editBtns(true);
        if (j.stale) hint("编辑服务渲染器与本次交付不一致（exe 较旧）→ 建议重新打包；继续用也可能正常", true);
      });
    })();
  }

  function esFetch(path, opts) {
    return fetch(ES.url + path, opts).then(function (r) { return r.json(); });
  }

  function esMap(cb) {                            // 渲染行号 → txt 行号（按章缓存）
    if (ES.mapN === SHOWN && ES.map) return cb(ES.map);
    esFetch("/api/map?ch=" + SHOWN).then(function (j) {
      if (!j || !j.ok) { toast("取行号映射失败：" + ((j && j.error) || "未知错误"), false); return; }
      ES.mapN = SHOWN; ES.map = j.ln;
      cb(j.ln);
    }).catch(function (e) { toast("编辑服务连接失败：" + e, false); });
  }

  // ── 行号列（编辑模式显示，与正文逐行对齐） ──
  function paintGut(n) {
    var pre = el("pre" + n), gut = el("gut" + n);
    if (!pre || !gut) return;
    if (!ES.mode) { gut.innerHTML = ""; return; }
    var kids = pre.children, h = [], i, c;
    for (i = 0; i < kids.length; i++) {
      c = kids[i];
      if (c.tagName !== "SPAN") continue;
      if (c.classList.contains("hide")) continue;      // 正被编辑顶掉的原行：不占行号位
      if (c.classList.contains("aa-line")) {
        var no = +c.getAttribute("data-i") || 0;
        h.push(no
          ? '<b data-line="' + no + '"' + (no === ES.idx ? ' class="on"' : "")
            + ' title="第 ' + no + ' 行：点击修改译文 · 右键还原原文并保存">' + no + "</b>"
          : '<b class="none"></b>');                   // 楼间距补出来的空行：占位不编号
      } else {                                          // 编辑块（译文行 + 原文行 + 按钮条）
        h.push('<b class="none" style="height:' + Math.max(1, Math.round(c.offsetHeight)) + 'px"></b>');
      }
    }
    gut.innerHTML = h.join("");
  }

  // ── 行内编辑（工作台同款）：原译文行隐藏，编辑行占它的位置，原文行紧随其下、列位对齐 ──
  function openEdit(no) {
    if (!no) { toast("这一行是按楼间距补出来的空行，不能编辑（点其它行号）", false); return; }
    var pre = el("pre" + SHOWN);
    var sp = pre && pre.querySelector('span.aa-line[data-i="' + no + '"]');
    if (!sp) { toast("正文里找不到第 " + no + " 行", false); return; }
    closeEdit();
    esFetch("/api/line?ch=" + SHOWN + "&idx=" + no).then(function (j) {
      if (!j || !j.ok) { toast("取行数据失败：" + ((j && j.error) || "未知错误"), false); return; }
      ES.idx = no; ES.src = j.src || ""; ES.sp = sp;
      var er = document.createElement("span");
      er.className = "aa-edit";
      er.innerHTML = '<textarea class="er-tr" spellcheck="false" wrap="off" rows="1"></textarea>'
        + '<span class="src-row">' + escapeHtml(ES.src || "（原文为空）") + "</span>"
        + '<span class="ae-bar"><button type="button" class="ae-save">保存</button>'
        + '<button type="button" class="ae-restore ghost">还原原文</button>'
        + '<button type="button" class="ae-cancel ghost">取消</button>'
        + '<span class="ae-tip">Ctrl+Enter 保存 · Esc 取消 · Shift+R 还原原文并保存</span></span>';
      sp.classList.add("hide");                 // 原译文行让位给编辑行
      sp.insertAdjacentElement("afterend", er);
      ES.er = er;
      var ta = er.querySelector(".er-tr");
      ta.value = j.tr;
      ta.addEventListener("keydown", function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); saveLine(); }
        else if (e.key === "Escape") { e.preventDefault(); closeEdit(); }
        else if (e.shiftKey && (e.key === "R" || e.key === "r")) {
          e.preventDefault();                   // 与工作台同口径：Shift+R = 还原原文并直接保存
          ta.value = ES.src;
          saveLine();
        }
      });
      er.querySelector(".ae-save").addEventListener("click", function () { saveLine(); });
      er.querySelector(".ae-cancel").addEventListener("click", function () { closeEdit(); });
      er.querySelector(".ae-restore").addEventListener("click", function () { ta.value = ES.src; saveLine(); });
      paintGut(SHOWN);
      ta.focus();
      try { ta.setSelectionRange(ta.value.length, ta.value.length); } catch (e2) {}
    }).catch(function (e) { toast("取行数据失败：" + e, false); });
  }

  function closeEdit() {
    if (ES.er && ES.er.parentNode) ES.er.parentNode.removeChild(ES.er);
    if (ES.sp) ES.sp.classList.remove("hide");
    ES.er = null; ES.sp = null; ES.idx = 0; ES.src = "";
    if (SHOWN >= 0) paintGut(SHOWN);
  }

  // 把该行新的渲染结果填回正文（只换那一行，不重建整章）
  function patchLine(n, k, html) {
    if (!TXT[n] || typeof TXT[n].tr !== "string" || k < 0) return;
    var parts = TXT[n].tr.split(NL);
    if (k >= parts.length) return;
    parts[k] = html;
    TXT[n].tr = parts.join(NL);
    if (n === SHOWN && mode === "tr") fill(n);
  }

  function postSave(idx, text, cb) {
    function send(k) {
      esFetch("/api/save", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ch: SHOWN, idx: idx, text: text})
      }).then(function (j) {
        if (!j || !j.ok) { toast((j && j.error) || "保存失败", false); if (cb) cb(null); return; }
        patchLine(SHOWN, k, j.html);
        toast("已保存 ✓ 第 " + idx + " 行" + (j.padded ? "（已自动补齐宽度）" : "")
              + ((j.residual && j.residual.length) ? "；残留日文 " + j.residual.length : ""));
        if (cb) cb(j);
      }).catch(function (e) { toast("保存失败：" + e, false); if (cb) cb(null); });
    }
    var k = (ES.map && ES.mapN === SHOWN) ? ES.map.indexOf(idx) : -1;
    if (k >= 0) send(k);
    else esMap(function (ln) { send(ln.indexOf(idx)); });   // 映射还没拿到 → 现取一次
  }

  function saveLine() {
    if (!ES.idx || !ES.er) return;
    var ta = ES.er.querySelector(".er-tr");
    var btn = ES.er.querySelector(".ae-save");
    btn.disabled = true;
    postSave(ES.idx, ta.value, function (j) {
      if (j) { closeEdit(); return; }
      btn.disabled = false;
    });
  }

  // 右键行号 = 还原原文并直接保存（与工作台同口径；只在编辑模式下生效）
  function restoreSave(no) {
    if (!no) return;
    esFetch("/api/line?ch=" + SHOWN + "&idx=" + no).then(function (j) {
      if (!j || !j.ok) { toast("取行数据失败", false); return; }
      var pre = el("pre" + SHOWN);
      var sp = pre && pre.querySelector('span.aa-line[data-i="' + no + '"]');
      if (sp) sp.innerHTML = j.src ? escapeHtml(j.src) : " ";   // 立刻见效（写盘后 fill 还会重渲染）
      postSave(no, j.src || "", null);
    }).catch(function (e) { toast("取行数据失败：" + e, false); });
  }

  function bindEdit() {
    var be = el("btn-edit");
    if (be) be.addEventListener("click", function () {
      ES.mode = !ES.mode;
      be.classList.toggle("on", ES.mode);
      be.innerHTML = ES.mode ? "&#10003; 编辑中" : "&#9998; 编辑";
      document.body.classList.toggle("editing", ES.mode);
      if (!ES.mode) { closeEdit(); toast("已退出编辑模式"); return; }
      toast("编辑模式：点行号改译文；右键行号 = 还原原文并直接保存");
      esMap(function () { paint(); });     // 拿到「显示行 → txt 行号」后重填（每行带行号锚点）
    });
    // 行号列是动态重建的 → 事件代理到 document
    document.addEventListener("click", function (e) {
      if (!ES.mode || isMobile()) return;
      var b = e.target && e.target.closest ? e.target.closest(".aa-gut b[data-line]") : null;
      if (b) openEdit(+b.getAttribute("data-line"));
    });
    document.addEventListener("contextmenu", function (e) {
      if (!ES.mode || isMobile()) return;
      var b = e.target && e.target.closest ? e.target.closest(".aa-gut b[data-line]") : null;
      if (!b) return;
      e.preventDefault();
      restoreSave(+b.getAttribute("data-line"));
    });

    // ── 翻译替换弹窗 ──
    function trRow(f, r, s) {
      var d = document.createElement("div");
      d.className = "trp";
      d.innerHTML = '<input type="text" class="tr-find" placeholder="查找（可写 re: 正则）">'
        + '<input type="text" class="tr-repl" placeholder="替换为（留空 = 删除）">'
        + '<label><input type="checkbox" class="tr-sfx"> sfx</label><span></span>'
        + '<button type="button" class="ghost tr-del">删</button>';
      d.querySelector(".tr-find").value = f || "";
      d.querySelector(".tr-repl").value = r || "";
      d.querySelector(".tr-sfx").checked = !!s;
      d.querySelector(".tr-del").addEventListener("click", function () { d.remove(); });
      return d;
    }
    function trPairs() {
      var out = [];
      var rows = document.querySelectorAll("#trm-pairs .trp");
      for (var i = 0; i < rows.length; i++) {
        var f = rows[i].querySelector(".tr-find").value;
        if (!f.trim()) continue;
        out.push([f, rows[i].querySelector(".tr-repl").value,
                  rows[i].querySelector(".tr-sfx").checked]);
      }
      return out;
    }
    function trScope() {
      var r = document.querySelector('input[name="trscope"]:checked');
      return r ? r.value : "ch";
    }
    function trMsg(t, cls) {
      var m = el("trm-msg");
      m.textContent = t || "";
      m.className = "emsg" + (cls ? " " + cls : "");
    }
    function reloadCh(n) {                        // 应用后把改动章的正文换成服务端最新渲染
      return esFetch("/api/chapter?ch=" + n).then(function (j) {
        if (!j || !j.ok) return;
        if (!TXT[n]) TXT[n] = {tr: "", src: ""};
        TXT[n].tr = j.tr;
        if (window.AA_DATA) window.AA_DATA[n] = {tr: j.tr, src: TXT[n].src};
        if (n === SHOWN) fill(n);
      }).catch(function () {});
    }
    function trRun(preview) {
      var pairs = trPairs();
      if (!pairs.length) { toast("先填「查找」内容", false); return; }
      trMsg(preview ? "预览中…" : "应用中…", "");
      esFetch("/api/replace", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({pairs: pairs, scope: trScope(), ch: SHOWN, preview: !!preview})
      }).then(function (j) {
        if (!j || !j.ok) { trMsg((j && j.error) || "失败", "err"); return; }
        trMsg((j.preview ? "预览：命中 " : "已应用：改动 ") + j.total + " 行 / " + j.files.length + " 章", "ok");
        el("trm-body").innerHTML = j.files.length ? j.files.map(function (f) {
          return '<div class="trf"><b>' + escapeHtml(f.file) + "</b>（" + f.changed + " 行"
            + (f.art_skipped ? "，艺术行跳过 " + f.art_skipped : "") + "）<ul>"
            + f.sample.map(function (s) {
                return "<li>" + s[0] + ": " + escapeHtml(s[1]) + " → <b>" + escapeHtml(s[2]) + "</b>"
                  + (s[3] ? ' <span class="warnx">超宽</span>' : "") + "</li>";
              }).join("") + "</ul></div>";
        }).join("") : '<p>没有命中</p>';
        if (!j.preview) {
          for (var i = 0; i < j.files.length; i++) reloadCh(j.files[i].n);
          ES.mapN = -1; ES.map = null;
        }
      }).catch(function (e) { trMsg("请求失败：" + e, "err"); });
    }
    var bt = el("btn-tr");
    if (bt) bt.addEventListener("click", function () {
      el("trm").classList.add("on");
      el("trm-cur").textContent = "（当前：第 " + (SHOWN + 1) + " / " + TOTAL + " 章）";
      if (!el("trm-pairs").children.length) el("trm-pairs").appendChild(trRow("", "", false));
    });
    el("trm-close").addEventListener("click", function () { el("trm").classList.remove("on"); });
    el("trm").addEventListener("click", function (e) { if (e.target === el("trm")) el("trm").classList.remove("on"); });
    el("trm-add").addEventListener("click", function () { el("trm-pairs").appendChild(trRow("", "", false)); });
    el("trm-preview").addEventListener("click", function () { trRun(true); });
    el("trm-apply").addEventListener("click", function () {
      var pairs = trPairs();
      if (!pairs.length) { toast("先填「查找」内容", false); return; }
      var sc = trScope();
      if (!window.confirm("将把 " + pairs.length + " 对替换应用到"
          + (sc === "all" ? "全部 " + TOTAL + " 章" : "当前章") + "，并**直接写回源文件**。继续？")) return;
      trRun(false);
    });
  }

  // 「仅 HTML」交付里没有编辑服务 → 连按钮都不生成（不靠 CSS 藏，免得留个能点的灰按钮）；
  // 「仅译文」没有原文数据 → 去掉「原文」按钮。都在骨架已解析之后跑（modebar 在脚本之前）。
  if (PLAIN) {
    var ebs = document.querySelectorAll(".modebar button.editbtn");
    for (var q = ebs.length - 1; q >= 0; q--) ebs[q].parentNode.removeChild(ebs[q]);
  }
  if (TRONLY) {
    var sbtn = document.querySelector('.modebar button[data-mode="src"]');
    if (sbtn) sbtn.parentNode.removeChild(sbtn);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
    if (!PLAIN) document.addEventListener("DOMContentLoaded", function () { bindEdit(); esLocate(); });
  } else {
    boot();
    if (!PLAIN) { bindEdit(); esLocate(); }
  }
})();
</script>
</body></html>"""


def _write_gz(dst, src):
    """把 src（.txt 或已经是 .gz）压缩/原样复制到 dst（gzip mtime=0 → 生成结果可复现）。"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, "rb") as f:
        raw = f.read()
    if not src.endswith(".gz"):
        raw = gzip.compress(raw, 6, mtime=0)
    with open(dst, "wb") as f:
        f.write(raw)


def render_ver():
    """渲染关键文件指纹（gen_aa_html + aa_common + aa_web_app/logic）。

    编辑服务把渲染逻辑**打进 exe**（见 tools/build_edit_server.py），改一次渲染器就得重打包
    一次；生成合集时把指纹写进 edit_meta.json，服务启动自检 → 不一致就提示重新打包，
    免得「页面用的新渲染 / exe 里的旧渲染」悄悄不一致。冻结(frozen)时同一表达式指向
    `_MEIPASS/scripts/...`，无需分支。
    """
    import hashlib
    h = hashlib.sha1()
    for p in (os.path.abspath(__file__),
              os.path.join(_SCRIPT_DIR, "aa_common.py"),
              os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..", "aa_web_app", "logic.py"))):
        try:
            with open(p, "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(p.encode("utf-8"))
    return h.hexdigest()[:12]


def _meta_src_path(p):
    """交付物里**不写本机绝对路径**：仓库内的源目录改存「相对仓库根」（如 `aaManga/<作品>/aa`）。

    编辑服务按交付夹位置反推仓库根（见 tools/edit_server.py 的 repo_root）再拼回来；
    不在仓库内的路径原样保留（罕见；服务侧按绝对路径处理）。交付页常被拷到别的机器/发出去，
    绝对路径既无用又泄露本机目录结构。
    """
    if not p:
        return p
    base = getattr(registry, "BASE", "") or ""
    try:
        rel = os.path.relpath(p, base)
    except ValueError:                          # 不同盘符
        return p
    if rel.startswith(".."):
        return p
    return rel.replace(os.sep, "/")


def install_edit_server(out_dir, wid, cfg, chapters, layout="external"):
    """把「编辑服务」+ 坐标文件放进交付文件夹（交付页 PC 端改行/替换靠它写回源）。

    · `AA编辑服务.exe` —— 预打包的单文件服务（别的机器不用装 Python）；未打包则拷
      `edit_server.py` 兜底（本机有 Python 时可用）。
    · `编辑服务.bat` —— 双击即启动（优先 exe）。
    · `edit_meta.json` —— 作品 id / 章节清单 / site / 源目录（**相对仓库根**，不含本机绝对
      路径）：服务据此定位章节、渲染行 → txt 行号，并把改动同时写回交付副本与（本机存在
      时的）源目录。`layout` 告诉服务改行后往哪写章节数据（`external`=data/NNNNN.js，
      `inline`=index.html 里内嵌的那段 payload）。
    返回实际放入的服务文件名（缺源码时返回提示串）。
    """
    meta = {
        "work": wid,
        "title": cfg.get("title") or "",
        "site": cfg.get("site"),
        "name_style": cfg.get("name_style"),
        "chapters": [os.path.basename(f) for f in chapters],
        "aa_dir": _meta_src_path(cfg.get("aa_dir")),
        "out_dir": _meta_src_path(cfg.get("out_dir")),
        "work_dir": _meta_src_path(cfg.get("work_dir")),
        "port": 8770,
        "layout": layout,                # inline | external（章节数据存放形态）
        "generator": "scripts/common/gen_aa_html.py",
        "render_ver": render_ver(),      # 服务自检用：exe 里的渲染器是不是这一版
    }
    with open(os.path.join(out_dir, META_NAME), "w", encoding="utf-8", newline="") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    exe = next((p for p in _EDIT_EXE_SRC if os.path.exists(p)), None)
    if exe:
        shutil.copy2(exe, os.path.join(out_dir, EDIT_EXE))
        what = EDIT_EXE
    elif os.path.exists(_EDIT_PY_SRC):
        shutil.copy2(_EDIT_PY_SRC, os.path.join(out_dir, EDIT_PY))
        what = EDIT_PY
    else:
        what = "(缺 edit_server.py)"
    # .bat 用 GBK + CRLF 写（cmd 里中文不乱码；文件本身已带 \r\n，故 newline=''）
    with open(os.path.join(out_dir, EDIT_BAT), "w", encoding="gbk", newline="") as f:
        f.write(EDIT_BAT_TEXT)
    with open(os.path.join(out_dir, EDIT_BAT_LAN), "w", encoding="gbk", newline="") as f:
        f.write(EDIT_BAT_LAN_TEXT)
    return what


def _font_parts(cfg, chapters, aa_dir, out_dir, layout, font_mode="auto", font_stack=None):
    """AA 等宽字体内嵌（common/aa_font.py）→ [<style> 片段]。

    layout=inline（单档 / 简版单文件）→ base64 内嵌；external → 交付夹 `aa_font.woff2` + 相对 URL。
    关闭（`AA_AA_FONT=0` / `--font none`）或失败 → []（页面回退系统字体栈）。
    """
    font_parts = []
    if font_mode != "none" and _AF.font_enabled():
        try:
            font_src = _AF.font_source() if font_mode in ("auto", "1", "") else font_mode
            if not os.path.exists(font_src):
                raise SystemExit("字体文件不存在: %s" % font_src)
            paths = [p for p in (_src_path(aa_dir, _base_of(f)) for f in chapters) if p]
            corpus = _AF.chars_of_files(paths + list(chapters))
            sub, missing, _ = _AF.build_subset(_AF.base_chars() | corpus, font_src)
            if layout == "inline":
                with open(sub, "rb") as fp:
                    font_ref = "data:font/woff2;base64," + base64.b64encode(fp.read()).decode("ascii")
            else:
                shutil.copy2(sub, os.path.join(out_dir, FONT_NAME))
                font_ref = FONT_NAME
            first = _AF.stack_first() if font_stack is None else (font_stack.lower() != "last")
            font_parts.append("<style>\n" + _AF.font_css(font_ref, first=first) + "</style>")
            print("[font] AA 等宽字体 %s（%s, %d 字符, 栈序 %s）%s"
                  % (os.path.basename(sub),
                     "内嵌 base64" if layout == "inline" else FONT_NAME,
                     len(corpus), "first" if first else "last",
                     ("，字体缺 %d 字符→回退" % len(missing)) if missing else ""))
        except SystemExit:
            raise
        except Exception as e:
            print("[font] 跳过（%s）；AA 列位在缺字体的设备上不保证" % e)
    return font_parts


# ── 「仅 HTML 文件」简版（2026-10-11）──────────────────────────────────────────
# 与正式交付文件夹（编辑服务 exe + 源留档 + data/ 目录）并列的**轻交付**：
#   文件名 `【机翻】<交付短名>_<起>-<止>.html`（3 位零填充，1-based 章序号）
#   每个文件**自包含**（章节数据内嵌、字体内嵌）→ 双击 file:// / 发出去 / 手机文件管理器都能看
#   不带 exe / 编辑服务 / 手机阅读 bat / edit_meta / 源 txt.gz / data 目录
#   仍带 译·原切换（`--tr-only` 关）、阅读进度、手机抽屉、跨文件「上次阅读」提示、上一/下一部分
PLAIN_PREFIX = "【机翻】"
_PLAIN_SUFFIXES = ("_汉化合集", "_汉化（机翻）合集")


def plain_short_name(cfg):
    """简版文件名里的 <标题>：交付夹基名去掉 `_汉化合集` 类后缀；无则回退 registry title。"""
    d = deliver_dir(cfg) or ""
    n = os.path.basename(d) if d else ""
    for suf in _PLAIN_SUFFIXES:
        if n.endswith(suf):
            n = n[: -len(suf)]
            break
    return n or (cfg.get("title") or "").strip() or "作品"


def plain_plan(cfg, chapters, split):
    """简版分页计划 → [(文件名, 起始章号(1-based), 结束章号)]；split<=0 = 全部章放一个文件。"""
    per = split if (split and split > 0) else len(chapters)
    per = max(1, per)
    short = plain_short_name(cfg)
    out = []
    for i in range(0, len(chapters), per):
        s, e = i + 1, min(i + per, len(chapters))
        out.append(("%s%s_%03d-%03d.html" % (PLAIN_PREFIX, short, s, e), s, e))
    return out


def _gen_plain(cfg, site, chapters, tpl, css, title, source, desc_zh, split, tr_only,
               out_dir_override=None, font_mode="auto", font_stack=None):
    """「仅 HTML 文件」：按分页计划逐个写出自包含 .html，返回写出的路径列表。

    落在**交付 HTML 目录**（`--out-dir` 可改）= 交付夹的父目录，即 `aaManga/交付HTML/`。
    重跑会先删掉本作品上一次的简版文件（`【机翻】<短名>_*.html`）——章数/分页变了不留陈旧件。
    """
    plan = plain_plan(cfg, chapters, split)
    if not plan:
        raise SystemExit("没有章节可生成")
    out_dir = out_dir_override or os.path.dirname(deliver_dir(cfg))
    if not out_dir:
        raise SystemExit("无法确定输出目录（作品未配置 html_out）")
    os.makedirs(out_dir, exist_ok=True)
    short = plain_short_name(cfg)
    pre = PLAIN_PREFIX + short + "_"
    for fn in os.listdir(out_dir):                  # 清掉上一次的简版文件（只删自己前缀的）
        if fn.startswith(pre) and fn.endswith(".html") and os.path.isfile(os.path.join(out_dir, fn)):
            try:
                os.remove(os.path.join(out_dir, fn))
            except OSError:
                pass
    total = len(chapters)
    written, n_src_missing = [], 0
    for pi, (name, s, e) in enumerate(plan):
        prev_name = plan[pi - 1][0] if pi > 0 else ""
        next_name = plan[pi + 1][0] if pi + 1 < len(plan) else ""
        chunk = chapters[s - 1:e]
        sections, payloads, miss = _chapter_sections(
            cfg, site, chunk, tpl, cfg["aa_dir"], offset=s - 1, total=total,
            prev_href=prev_name, next_href=next_name,
            tr_only=tr_only, want_src=not tr_only)
        n_src_missing += miss
        toc = [f'<button type="button" onclick="showChapter({i})" id="toc{i}">{H.escape(chapter_label(f, cfg))}</button>'
               for i, f in enumerate(chunk)]
        toc_html = ''.join(toc) if tpl == 'fc2' else '\n'.join(toc)
        nav = ""
        if prev_name:
            nav += f'<a href="{H.escape(prev_name)}">← 上一部分</a>'
        if next_name:
            nav += ('　' if nav else '') + f'<a href="{H.escape(next_name)}">下一部分 →</a>'
        range_txt = f"本文件：第 {s:03d}-{e:03d} 话 / 共 {total} 话"
        head = (f'<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8">\n'
                f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                f'<title>{H.escape(title)}</title>\n<style>\n{css}</style></head><body>\n'
                f'<header><h1>{H.escape(title)}</h1>\n'
                f'<p>{H.escape(source)}</p>\n'
                + f'<p class="part-nav">{H.escape(range_txt)}' + (("　" + nav) if nav else "") + '</p>\n'
                + (f'<p class="work-desc">{H.escape(desc_zh)}</p>\n' if desc_zh else '')
                + '</header>\n'
                + f'<nav><span class="toc-label">目录：</span>' + toc_html + '</nav><main>')
        font_parts = _font_parts(cfg, chapters, cfg["aa_dir"], out_dir, "inline", font_mode, font_stack)
        inline_script = ("<script>\nwindow.AA_INLINE=1;\n"
                         + "\n".join(p.rstrip("\n") for p in payloads) + "\n</script>")
        tail = (TAIL_JS.replace("__KEY__", _js_str(cfg.get("title") or short))
                       .replace("__TOTAL__", str(len(chunk)))
                       .replace("__OFFSET__", str(s - 1))
                       .replace("__SELF__", _js_str(name))
                       .replace("__PLAIN__", "1")
                       .replace("__TRONLY__", "1" if tr_only else "0"))
        doc = "\n".join([head, '<div id="loading">正在载入章节数据…</div>']
                        + sections + font_parts + [inline_script, tail])
        dst = os.path.join(out_dir, name)
        open(dst, "w", encoding="utf-8").write(doc)     # 不加 newline='' → CRLF（与历史交付一致）
        written.append(dst)
        print("written %s (%d KB) 第 %03d-%03d 话%s"
              % (dst, os.path.getsize(dst) // 1024, s, e, "（仅译文）" if tr_only else ""))
    print("[plain] %d 个自包含 HTML%s，输出 %s" % (len(written), "（仅译文）" if tr_only else "", out_dir))
    if n_src_missing:
        print("[warn] %d 章缺原文（aa/ 无同名 txt）→ 原文模式回退显示译文" % n_src_missing)
    return written


def main(work=None):
    argv = sys.argv[1:]
    one = None                      # --one <base>：单章试读（只渲染这一章，输出到 work/）
    no_source = "--no-source" in argv   # 不把 aa/汉化 的压缩 txt 拷进交付文件夹
    inline_max_val = None           # --inline-max <MB>：覆盖单档/外置的 20MB 分界
    for i, a in enumerate(argv):
        if a == "--one" and i + 1 < len(argv):
            one = argv[i + 1]
        elif a.startswith("--one="):
            one = a.split("=", 1)[1]
        elif a == "--inline-max" and i + 1 < len(argv):
            inline_max_val = argv[i + 1]
        elif a.startswith("--inline-max="):
            inline_max_val = a.split("=", 1)[1]
    # 裸参数 = 作品 id；带值开关要把**值**一起排除（否则 "20" 会被当成作品 id）
    inline_max = INLINE_MAX_BYTES
    if inline_max_val is not None:
        try:
            inline_max = int(float(inline_max_val) * 1024 * 1024)
        except ValueError:
            raise SystemExit("--inline-max 需要 MB 数值，例如 --inline-max 20")
    force_inline = "--inline" in argv        # 强制单档（无视阈值）
    force_external = "--external" in argv    # 强制 data/ 外置（无视阈值）
    # 交付页 AA 等宽字体（common/aa_font.py）：auto=按语料取子集内嵌 | none=不内嵌 | <路径>=用指定 ttf
    font_mode = "auto"
    font_stack = None
    for i, a in enumerate(argv):
        if a == "--font" and i + 1 < len(argv):
            font_mode = argv[i + 1]
        elif a.startswith("--font="):
            font_mode = a.split("=", 1)[1]
        elif a == "--font-stack" and i + 1 < len(argv):
            font_stack = argv[i + 1]
        elif a.startswith("--font-stack="):
            font_stack = a.split("=", 1)[1]
    # 「仅 HTML 文件」简版（2026-10-11）：--plain 只出 .html（不装编辑服务/源留档）；
    # --tr-only 只存译文；--split N 每 N 章一个文件（0/缺省 = 全部放一个）；--out-dir 改输出目录
    plain = "--plain" in argv
    tr_only = "--tr-only" in argv
    split_val, out_dir_val = None, None
    for i, a in enumerate(argv):
        if a == "--split" and i + 1 < len(argv):
            split_val = argv[i + 1]
        elif a.startswith("--split="):
            split_val = a.split("=", 1)[1]
        elif a == "--out-dir" and i + 1 < len(argv):
            out_dir_val = argv[i + 1]
        elif a.startswith("--out-dir="):
            out_dir_val = a.split("=", 1)[1]
    split = 0
    if split_val is not None:
        try:
            split = int(float(split_val))
        except ValueError:
            raise SystemExit("--split 需要章数，例如 --split 100（0 = 不分页）")
    if plain and one:
        raise SystemExit("--plain 与 --one 不能同时用")
    args = [a for a in argv if not a.startswith("-")
            and a not in (one, inline_max_val, font_mode, font_stack, split_val, out_dir_val)]
    wid = work or (args[0] if args else "saki")
    cfg = registry.get(wid)
    site = importlib.import_module("sites." + cfg["site"])
    TR_DIR, AA_DIR = cfg["out_dir"], cfg["aa_dir"]
    chapters = chapter_order(TR_DIR, cfg.get("name_style"), cfg.get("work_dir"))
    if one:
        chapters = [f for f in chapters if os.path.basename(f).startswith(one)]
        if not chapters:
            raise SystemExit("未找到章节: %s" % one)
        print("[one] 单章试读: %s" % os.path.basename(chapters[0]))
    print(f"chapters: {len(chapters)}")
    tpl = cfg.get("html_css", "saki")      # saki | fc2 | legacy（只影响 head/骨架/CSS）
    css = (CSS_MODERN if tpl in ('saki', 'fc2') else CSS_LEGACY) + CSS_UI
    # html_title/source_note 缺省时回退 registry title（部分作品未配齐字段也能出合集）
    title = cfg.get("html_title") or (cfg["title"] + " 汉化合集")
    source = cfg.get("source_note") or "帖头颜色保留原站"
    # 作品简介（中文）: work/description_zh.txt 存在则渲染到 header
    desc_zh = ''
    if cfg.get("work_dir"):
        dp = os.path.join(cfg["work_dir"], "description_zh.txt")
        if os.path.exists(dp):
            desc_zh = open(dp, encoding="utf-8").read().strip()

    # ── 分支：「仅 HTML 文件」简版（自包含 .html，可能分页成多个文件）─────────────
    if plain:
        return _gen_plain(cfg, site, chapters, tpl, css, title, source, desc_zh, split, tr_only,
                          out_dir_override=out_dir_val, font_mode=font_mode, font_stack=font_stack)

    # 交付文件夹（--one 时写 work/单章渲染_<base>/，不覆盖正式合集）
    if one:
        base_one = _base_of(chapters[0])
        out_dir = os.path.join(cfg.get("work_dir") or os.path.dirname(deliver_dir(cfg)),
                               "单章渲染_%s" % base_one)
    else:
        out_dir = deliver_dir(cfg)
    if not out_dir:
        raise SystemExit("作品未配置 html_out（交付文件夹路径）")

    toc = [f'<button type="button" onclick="showChapter({idx})" id="toc{idx}">{H.escape(chapter_label(f, cfg))}</button>'
           for idx, f in enumerate(chapters)]
    toc_html = ''.join(toc) if tpl == 'fc2' else '\n'.join(toc)

    if tpl == 'legacy':
        head = (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">\n'
                f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                f'<title>{H.escape(title)}</title><style>\n{css}</style></head><body>\n'
                f'<header><h1>{H.escape(title)}</h1>\n'
                f'<p>{H.escape(source)}</p>\n'
                + (f'<p class="work-desc">{H.escape(desc_zh)}</p>\n' if desc_zh else '')
                + '</header>\n'
                f'<nav><span class="toc-label">目录：</span>' + toc_html + '</nav><main>')
    else:
        head = (f'<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8">\n'
                f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                f'<title>{H.escape(title)}</title>\n<style>\n{css}</style></head><body>\n'
                f'<header><h1>{H.escape(title)}</h1>\n'
                f'<p>{H.escape(source)}</p>\n'
                + (f'<p class="work-desc">{H.escape(desc_zh)}</p>\n' if desc_zh else '')
                + '</header>\n'
                f'<nav><span class="toc-label">目录：</span>' + toc_html + '</nav><main>')
    parts = [head, '<div id="loading">正在载入章节数据…</div>']

    os.makedirs(out_dir, exist_ok=True)
    data_dir = os.path.join(out_dir, "data")
    shutil.rmtree(data_dir, ignore_errors=True)     # 章节数变少时不留陈旧数据文件
    os.makedirs(data_dir)
    n_src_missing = 0
    nbytes_data = 0

    secs, payloads, n_src_missing = _chapter_sections(cfg, site, chapters, tpl, AA_DIR)
    parts.extend(secs)
    for idx, payload in enumerate(payloads):
        # 章节数据（v1/v2 自动选；见 chapter_payload）——内容不进 index.html
        dpath = os.path.join(data_dir, "%05d.js" % idx)
        with open(dpath, "w", encoding="utf-8", newline="") as fp:
            fp.write(payload)
        nbytes_data += len(payload)
        # 注意：**不**在这里插 <script src>：章节多时（本书 810 章）全量注入 =
        # 首屏要读几百 MB，双击打开要等很久。数据由页面 JS 按需注入（懒加载）。

    # ── 形态判定：数据小 → 内嵌单档（零依赖阅读）；大 → data/ 外置 + 懒加载 ─────────
    # 判定按 payload 总字节（上面逐章累计），不看章数：同样的章数，厚薄差很多。
    layout = "inline" if (force_inline or
                          (not force_external and nbytes_data <= inline_max)) else "external"
    inline_script = ""
    if layout == "inline":
        buf = []
        for i in range(len(chapters)):
            with open(os.path.join(data_dir, "%05d.js" % i), encoding="utf-8") as fp:
                buf.append(fp.read().rstrip("\n"))
        # AA_INLINE：页面据此在回收远处章节时**不**丢掉 b64 —— 单档没有可重读的 data/*.js，
        # 丢了就再也拿不回来（见 TAIL_JS 的 drop()）。
        inline_script = "<script>\nwindow.AA_INLINE=1;\n" + "\n".join(buf) + "\n</script>"
        shutil.rmtree(data_dir, ignore_errors=True)     # 单档不留 data/（整夹拷走也不会带旧数据）
    tail = TAIL_JS.replace("__KEY__", _js_str(cfg.get("title") or (out_dir and os.path.basename(out_dir)))) \
                  .replace("__TOTAL__", str(len(chapters)))

    # ── AA 等宽字体内嵌（common/aa_font.py）─────────────────────────────────
    # 形态定下来才知道怎么引：单档（一个 index.html 要走天下）→ base64 内嵌；
    # 外置（data/ 本来就要整夹带走）→ 交付夹里的 aa_font.woff2 + 相对 URL
    #（本机 Chromium file:// 实测相对路径字体可加载，见 aa_font 模块注释）。
    font_parts = _font_parts(cfg, chapters, AA_DIR, out_dir, layout, font_mode, font_stack)

    html_out = '\n'.join(parts + font_parts + ([inline_script] if inline_script else []) + [tail])
    os.makedirs(out_dir, exist_ok=True)
    # 注意：不加 newline='' —— Windows 下 open().write 会把 \n 转成 \r\n，
    # 与历史交付文件（CRLF）保持逐字节一致
    entry = os.path.join(out_dir, LINK_NAME)
    open(entry, "w", encoding="utf-8").write(html_out)

    # 源压缩 txt（留档：aa/ 原文 + 汉化/ 成品；--no-source 关闭）
    nbytes_src = 0
    if not no_source:
        for sub in ("aa", "汉化"):
            shutil.rmtree(os.path.join(out_dir, sub), ignore_errors=True)
        for f in chapters:
            base = _base_of(f)
            sp = _src_path(AA_DIR, base)
            if sp:
                d = os.path.join(out_dir, "aa", base + ".txt.gz")
                _write_gz(d, sp)
                nbytes_src += os.path.getsize(d)
            d = os.path.join(out_dir, "汉化", base + "_汉化.txt.gz")
            _write_gz(d, f)
            nbytes_src += os.path.getsize(d)
            # colormap（小的稀疏 JSON）——编辑服务重渲染该行要用（自包含，别依赖源目录）
            cmf = os.path.join(AA_DIR, base + "_colormap.json")
            if os.path.exists(cmf):
                shutil.copy2(cmf, os.path.join(out_dir, "aa", base + "_colormap.json"))
            # 人工 sfx 标记 → work/sfx_marks/（与源 work_dir 同布局，编辑后重渲染不掉特效）
            mp = os.path.join(cfg.get("work_dir") or "", "sfx_marks", base + ".json")
            if os.path.exists(mp):
                dst = os.path.join(out_dir, "work", "sfx_marks", base + ".json")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(mp, dst)

    # 编辑服务（PC 端改行 / 翻译替换写回源；手机端只浏览）+ 坐标文件
    if one:
        print("[one] 单章试读不装编辑服务（正式合集才有）")
    else:
        what = install_edit_server(out_dir, wid, cfg, chapters, layout)
        print(f"[edit] 编辑服务 → {what} + {META_NAME} + {EDIT_BAT} + {EDIT_BAT_LAN}")

    if n_src_missing:
        print(f"[warn] {n_src_missing} 章缺原文（aa/ 无同名 txt）→ 原文模式回退显示译文")
    where = (f"单档内嵌 {len(chapters)} 章 ({nbytes_data//1024} KB b64)" if layout == "inline"
             else f"data/ 外置 {len(chapters)} 章 ({nbytes_data//1024} KB b64)")
    print(f"written {entry} ({os.path.getsize(entry)//1024} KB) [{layout}] {where}"
          + (f" + 源 txt.gz ({nbytes_src//1024} KB)" if nbytes_src else ""))


def _base_of(f):
    """汉化文件名 → 基名（去 _汉化.txt / _汉化.txt.gz）"""
    b = os.path.basename(f)
    for suf in ("_汉化.txt.gz", "_汉化.txt"):
        if b.endswith(suf):
            return b[:-len(suf)]
    return b


def _js_str(s):
    """JS 字符串字面量（转义反斜杠/引号；localStorage key 用）"""
    return json.dumps(s or "", ensure_ascii=False)[1:-1]


if __name__ == "__main__":
    main()
