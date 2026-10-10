# -*- coding: utf-8 -*-
"""每部 AA 作品的官方设定 wiki 本地化 + 汉化生成器（每部作品独立；2026-10-06 泛化自 gen_konosin_wiki.py）

配置：`scripts/config/work_wikis.json` —— 一个作品一段（站点/目录/分类表/借名表/prompt 作品名/说明文案）。
用法：
  python scripts/tools/gen_work_wiki.py --work ポケの子 --all        # 首次本地化全流程
  python scripts/tools/gen_work_wiki.py --work ポケの子 --update     # 连载更新同步（重抓+增量翻译+重建）
  python scripts/tools/gen_work_wiki.py --list                       # 列出已登记作品

数据源（需 v2rayN 代理 127.0.0.1:10808）：
  https://seesaawiki.jp/<site>/   —— 狐板 AA 作品官方 wiki（seesaa 引擎）
  EUC-JP 编码；页面一览走 /l/?p=N（每页 100 条）；正文在 <div id="page-body-inner"> 内；
  AA 字符画在 <pre class="AA">（原样保留，绝不翻译）

翻译参考（口径来源，只读，不修改）：
  1. 作品术语表   aaManga/<作品>/work/glossary_<作品>.json
  2. 作品知识库   aaManga/<作品>/work/<作品>_knowledge.json（人物中文译名/术语）
  3. 宝可梦 wiki  doc/aa作品相关/ポケモンAA术语wiki/（ポケマス 原创术语 + 52poke 官方译名对照 + 补充术语.json）

产出（doc/aa作品相关/<wiki_dir>/，默认 <作品>wiki）：
  index.html       单文件本地 wiki（数据内嵌，搜索 + 分类导航 + 中日逐行对照）
  术语表.json      {"pages":[…], "terms":[…]}   ← pre_terms 本作 wiki 词源读它
  官方译名对照.json  52poke 官方 JP↔ZH（从宝可梦 wiki 复用，不重复抓取）
  人工修正.json    唯一手改入口（本地译名/修正/替换/按源替换/保留日文），--build 时优先
  README.md        来源/口径/复现步骤
  _raw/            抓取 + 翻译缓存（自带 .gitignore，不进版本库）

接入翻译链：产出目录即宝可梦专属链 pre_terms 的「本作 wiki」词源
（解析顺序 env AA_WORK_WIKI_DIR > work_meta[work].wiki_dir > 约定 <作品>wiki），零额外接线。

分阶段（可单独跑，缓存复用、幂等）：
  --crawl      抓页面一览 + 全部页面    --extract    解析成结构化行（AA 画 与 文本行分离）
  --translate  行级翻译（术语注入，断点续跑）  --fix-kana   残留假名行逐条重译
  --build      生成 index.html + JSON（零 API）
  --update     连载同步 = 重抓全部页（零模型成本）→ 抽取 → 增量翻译（行级缓存，只译新行）→ 重建
  --all        crawl + extract + translate + build

翻译走项目模型链（scripts/config/model_config.toml），用量记入 usage（tag = 作品配置的 usage_tag）。
"""
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
sys.path.insert(0, os.path.join(BASE, 'scripts', 'common'))

OUT_DIR = ''          # 以下全局由 configure(work) 从配置注入（每部作品独立）
RAW_DIR = ''
PAGES_DIR = ''
import aa_config   # 代理统一口径（2026-10-08 阶段 4）

PROXY = aa_config.proxy_url()
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')
CONFIG_PATH = os.path.join(BASE, 'scripts', 'config', 'work_wikis.json')
POKEWIKI_DIR = os.path.join(BASE, 'doc', 'aa作品相关', 'ポケモンAA术语wiki')

SITE = ''             # 站点根，如 https://seesaawiki.jp/pokenoko
WORK = ''             # 作品 id（= aaManga/<id>/、work_meta key）
WORK_TITLE = ''       # 注入 prompt 的作品名
USAGE_TAG = ''        # usage 统计 tag
GEN_CMD = ''          # 生成物里写的“重跑命令”
HTML_TITLE = ''
HTML_H1 = ''
SKIP_PAGES = set()    # 站点框架页/模板页
CAT_SYSTEM = set()    # 系统设定类页名
CAT_TERM_PAGE = {}    # 表格式术语页 {页名: 分类名}
CAT_OVERRIDE = {}     # 人工指定分类 {页名: 分类名}（标题/首行判不出类型时的兜底）
KNOWN_OFFICIAL = {}   # 借名（外作角色/宝可梦）通行中文名
ABOUT_HTML = ''       # index.html「说明」页正文
MISSING_MARKS = ['ページが見つかりませんでした']
PREV_PAGE_NAMES = set()   # 上次 page_list.json 的页名（更新同步时算「新增页」）


def load_config(path=None):
    with open(path or CONFIG_PATH, encoding='utf-8') as f:
        return json.load(f)


def work_ids(path=None):
    """已登记的作品 id（配置里非 `_` 开头的键）。"""
    return [k for k in load_config(path) if not k.startswith('_')]


def configure(work, cfg=None):
    """把某部作品的配置注入模块全局——每部作品独立：站点/输出目录/分类表/借名表/prompt 作品名。"""
    global SITE, WORK, WORK_TITLE, USAGE_TAG, GEN_CMD, HTML_TITLE, HTML_H1
    global SKIP_PAGES, CAT_SYSTEM, CAT_TERM_PAGE, KNOWN_OFFICIAL, ABOUT_HTML, MISSING_MARKS
    global CAT_OVERRIDE, OUT_DIR, RAW_DIR, PAGES_DIR, WORK_DIR
    data = cfg if cfg is not None else load_config()
    c = data.get(work)
    if not c:
        raise SystemExit(f'配置 {CONFIG_PATH} 里没有作品「{work}」；已登记：{work_ids()}')
    SITE = c['site'].rstrip('/')
    WORK = c.get('work_id') or work
    WORK_TITLE = c.get('prompt_title') or WORK
    USAGE_TAG = c.get('usage_tag') or ''
    GEN_CMD = c.get('gen_cmd') or f'scripts/tools/gen_work_wiki.py --work {WORK}'
    HTML_TITLE = c.get('html_title') or f'{WORK} wiki（中日对照）'
    HTML_H1 = c.get('html_h1') or f'『{WORK}』设定 wiki'
    SKIP_PAGES = set(c.get('skip_pages') or [])
    CAT_SYSTEM = set(c.get('cat_system') or [])
    CAT_TERM_PAGE = dict(c.get('cat_term_page') or {})
    CAT_OVERRIDE = dict(c.get('cat_override') or {})
    KNOWN_OFFICIAL = dict(c.get('known_official') or {})
    ABOUT_HTML = c.get('about_html') or ''
    MISSING_MARKS = list(c.get('missing_marks') or ['ページが見つかりませんでした'])
    OUT_DIR = os.path.join(BASE, 'doc', 'aa作品相关', c.get('wiki_dir') or f'{WORK}wiki')
    RAW_DIR = os.path.join(OUT_DIR, '_raw')
    PAGES_DIR = os.path.join(RAW_DIR, 'pages')
    WORK_DIR = os.path.join(BASE, 'aaManga', WORK, 'work')
    return c

# 站点导航/框架文字（不翻译、不进正文）
NAV_LINES = {
    'トップページ', 'ページ一覧', 'メンバー', '×', 'Tweet', '履歴', '編集',
    'このページを編集する', 'このページを元に新規ページを作成', '添付する', '印刷する',
    'コメントをかく', 'ログインする', 'ユーザーIDを使う', 'ユーザIDを使用しないで書き込む',
    '画像コード', '備考', '本文', '名前', 'Menu', 'MenuBar1', 'MenuBar2',
    '最近更新したページ', '添付ファイル一覧（0）', '添付ファイル一覧',
}
NAV_RE = re.compile(
    r'^(最終更新：|カテゴリ：|コメント（\d+）|添付ファイル一覧|このページ|前の\d+件|次の\d+件|'
    r'\d+件〜\d+件を表示|ページ名|最終更新日時|利用規約|\d{4}-\d{2}-\d{2}$|'
    r'&nbsp;|https?://|【メニュー編集】)')

# 分类表（SKIP_PAGES / CAT_SYSTEM / CAT_TERM_PAGE / KNOWN_OFFICIAL）见
# scripts/config/work_wikis.json —— 每部作品独立，由 configure() 注入

# 术语抽取：表头行（不当作条目）
TABLE_HEADS = {'技名', '特性名', 'ポテンシャル名', '名前', '名称'}


# ══════════════════════ 工具 ══════════════════════
def fetch(url, referer=None):
    cmd = ['curl', '-s', '--max-time', '90', '-x', PROXY, '-A', UA]
    if referer:
        cmd += ['-e', referer]
    cmd.append(url)
    try:
        p = subprocess.run(cmd, capture_output=True)
    except Exception as e:
        print('  ! curl 异常', e)
        return None
    return p.stdout or None


def site_page(name):
    """按原站页面名取一页（EUC-JP 解码）。

    注意用 euc_jisx0213 而不是 euc-jp：seesaawiki 正文里会出现 JIS X 0212 补充区字符
    （① 等圈数字），plain euc-jp 会解成 U+FFFD 丢字（实测 対戦記録 等页）。
    """
    enc = urllib.parse.quote(name, encoding='euc-jp', safe='')
    raw = fetch(f'{SITE}/d/{enc}', referer=f'{SITE}/')
    if not raw:
        return None
    try:
        return raw.decode('euc_jisx0213')
    except UnicodeDecodeError:
        return raw.decode('euc-jisx0213', errors='replace')


def safe_name(name):
    return re.sub(r'[^\w\u3040-\u30ff\u4e00-\u9fff（）・．「」『』]+', '_', name)[:120]


def page_missing(h):
    return any(m in h for m in MISSING_MARKS)


def is_translatable(line):
    """含假名或汉字才需要翻译（纯数字/符号/拉丁 行原样保留）。"""
    return bool(re.search(r'[\u3040-\u30ff\u4e00-\u9fff\uff66-\uff9f]', line))


# ══════════════════════ crawl ══════════════════════
def crawl_page_list():
    """页面一览：/l/?p=N&order=lastupdate&on_desc=1，每页 100 条，直到不足 100。

    覆盖 page_list.json 前先记住旧清单（`PREV_PAGE_NAMES`）→ 更新同步时可报「新增页面」。
    """
    global PREV_PAGE_NAMES
    prev_p = os.path.join(RAW_DIR, 'page_list.json')
    if os.path.exists(prev_p):
        try:
            PREV_PAGE_NAMES = set(json.load(open(prev_p, encoding='utf-8')))
        except Exception:
            PREV_PAGE_NAMES = set()
    names, seen = [], set()
    p = 0
    while p < 40:
        raw = fetch(f'{SITE}/l/?p={p}&order=lastupdate&on_desc=1', referer=f'{SITE}/l/')
        if not raw:
            print(f'  ! 一览第 {p} 页取不到')
            break
        h = raw.decode('euc-jp', errors='replace')
        got = []
        for m in re.finditer(r'href="[^"]*/d/([^"#?]+)"', h):
            n = urllib.parse.unquote(m.group(1), encoding='euc-jp', errors='replace')
            if n not in seen:
                seen.add(n)
                got.append(n)
        names += got
        print(f'  一览 p={p}: 新增 {len(got)}（累计 {len(names)}）', flush=True)
        if len(got) < 100:
            break
        p += 1
        time.sleep(0.3)
    json.dump(names, open(os.path.join(RAW_DIR, 'page_list.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print(f'  页面一览共 {len(names)} 页'
          f'{f"（较上次 +{len(set(names) - PREV_PAGE_NAMES)}）" if PREV_PAGE_NAMES else ""}')
    return names


def _content_key(h):
    """比较用指纹（2026-10-06）：只取正文并折叠空白。

    直接逐字节比较整页 HTML 会被站点模板干扰——广告位/面包屑/最終更新/换行风格
    （实测：同一页两次抓取网络内容相同，但缓存文件是文本模式写的，读回时 universal newlines
    把 CRLF 折成 LF → 与网络原文永不相等）→ 每页都判「改动」，同步报告失去意义
    （--update 报 469/469 全改动，其实零内容变化）。只比正文 + 折叠空白才反映真实改动。"""
    return re.sub(r'\s+', ' ', body_of(h or '')).strip()


def crawl_pages(names, force=False):
    """抓页面。force=True（--update）= 重抓全部页并与缓存比对，报出新增/改动页。

    重抓是纯网络开销（零模型成本）；译文按行缓存在 lines_zh.json，只有新行才送模型。
    """
    os.makedirs(PAGES_DIR, exist_ok=True)
    with open(os.path.join(RAW_DIR, '.gitignore'), 'w', encoding='utf-8') as f:
        f.write('*\n')

    def one(name):
        path = os.path.join(PAGES_DIR, safe_name(name) + '.html')
        cached = os.path.exists(path) and os.path.getsize(path) > 2000
        if cached and not force:
            return name, 'cache'
        h = site_page(name)
        if h is None:
            return name, 'FAIL'
        if cached:
            old = open(path, encoding='utf-8', errors='replace').read()
            if _content_key(old) == _content_key(h):
                return name, 'same'      # 内容没变（站点模板噪声不算）
        open(path, 'w', encoding='utf-8').write(h)
        return name, ('MISS' if page_missing(h) else ('changed' if cached else 'ok'))

    fail, miss, ok, changed = [], [], 0, []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, (name, st) in enumerate(ex.map(one, names), 1):
            if st == 'FAIL':
                fail.append(name)
            elif st == 'MISS':
                miss.append(name)
            elif st == 'changed':
                changed.append(name)
            elif st == 'ok':
                ok += 1
            if i % 50 == 0:
                print(f'    {i}/{len(names)}（新抓 {ok} / 改动 {len(changed)}）', flush=True)
    new_pages = [n for n in names if n not in PREV_PAGE_NAMES and n not in miss]
    json.dump({'fail': fail, 'missing': miss, 'new': new_pages, 'changed': changed},
              open(os.path.join(RAW_DIR, 'crawl_report.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print(f'  抓取完成：新抓 {ok}，内容改动 {len(changed)}，新增页 {len(new_pages)}，'
          f'缺页 {len(miss)}，失败 {len(fail)}')
    if new_pages:
        print('  新增：' + ', '.join(new_pages[:20]))
    if changed:
        print('  改动：' + ', '.join(changed[:20]))
    if miss:
        print('  缺页：' + ', '.join(miss[:20]))
    if fail:
        print('  失败：' + ', '.join(fail[:20]))


def stage_crawl(force=False):
    os.makedirs(RAW_DIR, exist_ok=True)
    names = crawl_page_list()
    crawl_pages(names, force=force)


# ══════════════════════ extract ══════════════════════
def body_of(h):
    m = re.search(r'<div id="page-body-inner"[^>]*>', h)
    seg = h[m.end():] if m else h
    for end in ('<!-- google_ad_section_end', '<!-- entry_bottom', '<div id="page-footer"'):
        j = seg.find(end)
        if j > 0:
            seg = seg[:j]
            break
    return seg


def drop_block(seg, marker):
    """按 div 配对删掉含 marker 的第一个块（用于 TOC）。"""
    i = seg.find(marker)
    if i < 0:
        return seg
    i = seg.rfind('<div', 0, i)
    if i < 0:
        return seg
    depth = 0
    for m in re.finditer(r'<div\b|</div>', seg[i:]):
        if m.group(0).startswith('</'):
            depth -= 1
            if depth == 0:
                return seg[:i] + seg[i + m.end():]
        else:
            depth += 1
    return seg


def _cell_text(inner):
    t = re.sub(r'<br\s*/?>', '\n', inner)
    t = re.sub(r'<[^>]+>', '', t)
    return html.unescape(t).strip()


def table_rows(seg):
    """表格 → 行 × 单元。rowspan 会向下填充同一文本（否则后续行整体左移）。"""
    out, spans = [], {}          # spans: 列号 -> (文本, 还需填充的行数)
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', seg, flags=re.S):
        cells = []
        for m in re.finditer(r'<t[dh]([^>]*)>(.*?)</t[dh]>', tr, flags=re.S):
            rs = re.search(r'rowspan\s*=\s*"?(\d+)', m.group(1))
            cells.append((_cell_text(m.group(2)), int(rs.group(1)) if rs else 1))
        row, ci = [], 0
        def flush():
            nonlocal ci
            while ci in spans:
                t, rem = spans[ci]
                row.append(t)
                if rem <= 1:
                    del spans[ci]
                else:
                    spans[ci] = (t, rem - 1)
                ci += 1

        for txt, rs in cells:
            flush()
            row.append(txt)
            ci += 1
            if rs > 1:
                spans[ci - 1] = (txt, rs - 1)
        flush()
        if any(c.strip() for c in row):
            out.append(row)
    return out


def clean_lines(txt):
    t = html.unescape(txt)
    t = t.replace('\u00a0', ' ').replace('\r\n', '\n').replace('\r', '\n')
    out = []
    for l in t.split('\n'):
        l = l.replace('\u3000', ' ').rstrip()
        l = re.sub(r'[ \t]+', ' ', l).strip()
        if not l:
            continue
        if l in NAV_LINES or NAV_RE.match(l):
            continue
        out.append(l)
    return out


def parse_page(h):
    """页面 → 段落列表（AA 画 / 表格 / 文本行）。"""
    seg = body_of(h)
    seg = re.sub(r'<script.*?</script>', '', seg, flags=re.S)
    seg = re.sub(r'<style.*?</style>', '', seg, flags=re.S)
    seg = drop_block(seg, 'class="wiki-catalog"')
    seg = drop_block(seg, 'class="user-area"') if False else seg

    blocks = []
    pos = 0
    pat = re.compile(r'<pre\b[^>]*class="[^"]*AA[^"]*"[^>]*>(.*?)</pre>'
                     r'|<table\b[^>]*>(.*?)</table>'
                     r'|<h([1-4])\b[^>]*>(.*?)</h\3>', flags=re.S)
    for m in pat.finditer(seg):
        chunk = seg[pos:m.start()]
        lines = clean_lines(re.sub(r'<[^>]+>', '\n', re.sub(r'<br\s*/?>', '\n', chunk)))
        if lines:
            blocks.append({'k': 'text', 'jp': lines})
        if m.group(1) is not None:                       # AA 画：原样保留，绝不翻译
            art = html.unescape(m.group(1)).replace('\r\n', '\n').replace('\r', '\n')
            art_lines = [l.rstrip() for l in art.split('\n')]
            while art_lines and not art_lines[0].strip():
                art_lines.pop(0)
            while art_lines and not art_lines[-1].strip():
                art_lines.pop()
            if art_lines:
                blocks.append({'k': 'art', 'jp': art_lines})
        elif m.group(2) is not None:                     # 表格
            rows = table_rows(m.group(2))
            if rows:
                blocks.append({'k': 'table', 'rows': rows})
        else:                                            # 小标题
            ls = clean_lines(re.sub(r'<[^>]+>', '\n', m.group(4)))
            if ls:
                blocks.append({'k': 'h', 'jp': ls})
        pos = m.end()
    chunk = seg[pos:]
    lines = clean_lines(re.sub(r'<[^>]+>', '\n', re.sub(r'<br\s*/?>', '\n', chunk)))
    if lines:
        blocks.append({'k': 'text', 'jp': lines})

    # 合并相邻同类文本块
    merged = []
    for b in blocks:
        if (merged and b['k'] in ('text', 'art') and merged[-1]['k'] == b['k']):
            merged[-1]['jp'] += b['jp']
        else:
            merged.append(b)
    return merged


def stage_extract():
    names = json.load(open(os.path.join(RAW_DIR, 'page_list.json'), encoding='utf-8'))
    pages, stat = [], {'text': 0, 'table_cells': 0, 'art': 0, 'chars': 0, 'pages': 0, 'missing': 0}
    for name in names:
        p = os.path.join(PAGES_DIR, safe_name(name) + '.html')
        if not os.path.exists(p):
            continue
        h = open(p, encoding='utf-8', errors='replace').read()
        if page_missing(h):
            stat['missing'] += 1
            continue
        blocks = parse_page(h)
        if not blocks:
            continue
        pages.append({'name': name, 'blocks': blocks})
        stat['pages'] += 1
        for b in blocks:
            if b['k'] == 'text' or b['k'] == 'h':
                for l in b['jp']:
                    if is_translatable(l):
                        stat['text'] += 1
                        stat['chars'] += len(l)
            elif b['k'] == 'table':
                for row in b['rows']:
                    for c in row:
                        if is_translatable(c):
                            stat['table_cells'] += 1
                            stat['chars'] += len(c)
            elif b['k'] == 'art':
                stat['art'] += len(b['jp'])
    json.dump(pages, open(os.path.join(RAW_DIR, 'pages_raw.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print(f"  页面 {stat['pages']} 篇（缺页 {stat['missing']}）；"
          f"待译文本行 {stat['text']} + 表格单元 {stat['table_cells']}；"
          f"AA 画行 {stat['art']}（不翻译）；原文合计 {stat['chars']} 字")


# ══════════════════════ build ══════════════════════
HTML_TPL = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__HTML_TITLE__</title>
<style>
:root{--bg:#14161a;--panel:#1c1f26;--line:#2b303b;--fg:#e6e8ee;--dim:#98a0b3;--acc:#ff7a45;--acc2:#4ea1ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.7 "Microsoft YaHei",system-ui,sans-serif}
a{color:var(--acc2)}
header{padding:12px 18px;background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:9}
h1{margin:0 0 4px;font-size:16px}
.sub{color:var(--dim);font-size:12px}
#q{width:100%;margin-top:8px;padding:9px 12px;background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:6px;font-size:14px}
.wrap{display:flex;align-items:flex-start}
nav{width:210px;flex:0 0 210px;padding:10px;border-right:1px solid var(--line);min-height:100vh;position:sticky;top:110px}
nav button{display:block;width:100%;text-align:left;margin:2px 0;padding:6px 9px;background:none;border:0;color:var(--fg);border-radius:6px;cursor:pointer;font-size:13px}
nav button:hover{background:#232733}
nav button.on{background:var(--acc);color:#14161a;font-weight:700}
nav .grp{color:var(--dim);font-size:11px;margin:10px 0 4px;letter-spacing:.06em}
main{flex:1;padding:14px 20px;min-width:0;max-width:1100px}
table{width:100%;border-collapse:collapse;margin:8px 0}
th,td{border:1px solid var(--line);padding:6px 8px;vertical-align:top;text-align:left}
th{color:var(--dim);font-size:12px;background:#191c22}
td.th{color:var(--dim);font-size:12px;background:#191c22;font-weight:700}
.jp{color:var(--dim);font-size:12px}
.zh{font-weight:600}
.tag{display:inline-block;padding:0 6px;border:1px solid var(--line);border-radius:10px;font-size:11px;color:var(--dim)}
.count{color:var(--dim);font-size:12px;margin:6px 0 10px}
h2{margin:6px 0 2px;font-size:18px}
h3{color:var(--acc);margin:16px 0 4px;font-size:15px;border-bottom:1px solid var(--line);padding-bottom:3px}
pre.aa{background:#0f1115;border:1px solid var(--line);border-radius:6px;padding:8px;overflow-x:auto;
 font:12px/1.35 "MS PGothic","MS Gothic",monospace;color:#c9d1e2;white-space:pre}
.zl{padding:1px 0}
.jl{color:var(--dim);font-size:12px;padding:0 0 2px 12px;border-left:2px solid var(--line)}
.plist td{cursor:pointer}
.plist tr:hover td{background:#232733}
mark{background:#5a4a1a;color:#ffe9a8}
.bar{display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.btn{background:#232733;color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:4px 10px;cursor:pointer;font-size:13px}
.note{color:var(--dim);font-size:12px;max-width:900px}
</style></head><body>
<header>
<h1>__HTML_H1__</h1>
<div class="sub" id="stat"></div>
<input id="q" placeholder="搜索：中文 / 日文 / 角色 / 招式 / 场地（如 竞技场、潜能、アムロ）">
</header>
<div class="wrap"><nav id="nav"></nav><main id="main"></main></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
let cur='all', kw='', showjp=false, openPage=null;
const CATS=[['all','全部页面'],['cat:宝可梦','宝可梦个体'],['cat:人物','人物'],
 ['cat:图鉴','宝可梦图鉴'],['cat:潜力','潜能（ポテンシャル）'],['cat:系统设定','系统 / 设定'],
 ['cat:原创招式','原创招式'],['cat:原创特性','原创特性'],['terms','术语对照'],['about','说明']];
function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function hl(s){s=esc(s);if(!kw)return s;const re=new RegExp('('+kw.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','gi');
 return s.replace(re,'<mark>$1</mark>');}
function hay(p){ if(p._h) return p._h; let a=[p.name,p.title];
 p.blocks.forEach(b=>{ if(b.k==='art')return; a=a.concat(b.jp||[]); if(b.rows)b.rows.forEach(r=>a=a.concat(r));});
 p._h=a.join(' '); return p._h;}
function render(){
 const m=document.getElementById('main'); m.innerHTML='';
 document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('on',b.dataset.k===cur));
 if(cur==='about'){ m.innerHTML='<h2>说明</h2><div class="note">'+D.about+'</div>'; return; }
 if(cur==='terms'){ renderTerms(); return; }
 if(openPage){ renderPage(D.pages.find(p=>p.name===openPage)); return; }
 let list=D.pages.filter(p=>cur==='all'||('cat:'+p.cat)===cur);
 if(kw) list=list.filter(p=>hay(p).toLowerCase().includes(kw.toLowerCase()));
 m.innerHTML='<div class="count">'+list.length+' 个页面'+(kw?'（搜索：'+esc(kw)+'）':'')+'</div>';
 const tb=document.createElement('table'); tb.className='plist';
 tb.innerHTML='<tr><th style="width:34%">中文</th><th style="width:26%">日文页名</th><th style="width:12%">分类</th><th>摘要</th></tr>';
 list.slice(0,1200).forEach(p=>{
  const tr=document.createElement('tr'); tr.onclick=()=>{openPage=p.name;render();window.scrollTo(0,0);};
  let snip=p.snip||'';
  outer: for(const b of p.blocks){ if(b.k==='art')continue;
    const seq=(b.k==='table')?[].concat(...b.zh):(b.zh||[]);
    for(let i=0;i<seq.length;i++){const z=seq[i]; if(z&&z.length>1){snip=z;break outer;}}}
  tr.innerHTML='<td class="zh">'+hl(p.title)+'</td><td class="jp">'+hl(p.name)+'</td><td class="jp">'+esc(p.cat)+'</td><td class="jp">'+hl(snip.slice(0,60))+'</td>';
  tb.appendChild(tr);});
 m.appendChild(tb);
 document.getElementById('stat').textContent='页面 '+D.pages.length+' 篇 · 术语 '+D.terms.length+' 条 · 官方译名 '+D.official.length+' 条';
}
function renderPage(p){
 if(!p){openPage=null;render();return;}
 let h='<div class="bar"><button class="btn" id="back">← 返回列表</button>'
  +'<label class="jp"><input type="checkbox" id="sj"'+(showjp?' checked':'')+'> 显示日文原文</label>'
  +'<a class="jp" href="'+esc(p.src)+'" target="_blank">原站页面 ↗</a></div>';
 h+='<h2>'+esc(p.title)+' <span class="jp">'+esc(p.name)+'</span> <span class="tag">'+esc(p.cat)+'</span></h2>';
 p.blocks.forEach(b=>{
  if(b.k==='art'){ h+='<pre class="aa">'+esc(b.jp.join('\n'))+'</pre>'; return; }
  if(b.k==='h'){ h+='<h3>'+hl(b.zh[0]||b.jp[0])+(showjp&&b.jp[0]!==b.zh[0]?' <span class="jp">'+esc(b.jp[0])+'</span>':'')+'</h3>'; return; }
  if(b.k==='text'){ b.zh.forEach((z,i)=>{ h+='<div class="zl">'+hl(z)+'</div>';
     if(showjp&&b.jp[i]!==z) h+='<div class="jl">'+esc(b.jp[i])+'</div>';}); return; }
  if(b.k==='table'){ h+='<table>'; b.rows.forEach((row,ri)=>{ h+='<tr>';
     row.forEach((c,ci)=>{ const z=(b.zh[ri]||[])[ci]||c; const head=(ri===0||'技名,特性名,名称,分類'.indexOf(c)>=0&&ri<2);
      h+='<td'+(head?' class="th"':'')+'>'+hl(z)+(showjp&&z!==c?'<div class="jl">'+esc(c)+'</div>':'')+'</td>';});
     h+='</tr>';}); h+='</table>'; return; }
 });
 const m=document.getElementById('main'); m.innerHTML=h;
 document.getElementById('back').onclick=()=>{openPage=null;render();};
 document.getElementById('sj').onchange=e=>{showjp=e.target.checked;renderPage(p);};
 document.getElementById('stat').textContent='页面 '+D.pages.length+' 篇 · 术语 '+D.terms.length+' 条';
}
function renderTerms(){
 let list=D.terms; if(kw) list=list.filter(t=>(t.jp+' '+t.zh+' '+t.cat+' '+(t.desc||'')+' '+(t.page||'')).toLowerCase().includes(kw.toLowerCase()));
 const m=document.getElementById('main');
 let h='<div class="count">术语 '+list.length+' 条（来自本 wiki 的招式/特性/潜能表 + 作品术语表 + 宝可梦wiki 补充术语）</div>';
 h+='<table><tr><th style="width:24%">日文</th><th style="width:22%">中文</th><th>说明</th><th style="width:12%">出处</th></tr>';
 list.slice(0,1000).forEach(t=>{ h+='<tr><td>'+hl(t.jp)+'</td><td class="zh">'+hl(t.zh)+'</td><td>'+hl(t.desc_zh||'')+(t.desc?'<div class="jl">'+hl(t.desc)+'</div>':'')+'</td><td class="jp">'+esc(t.page||'')+'</td></tr>';});
 h+='</table>';
 m.innerHTML=h;
 document.getElementById('stat').textContent='术语 '+D.terms.length+' 条'+(list.length>1000?'（显示前 1000）':'');
}
const nav=document.getElementById('nav');
const g1=document.createElement('div');g1.className='grp';g1.textContent='目录';nav.appendChild(g1);
CATS.forEach(([k,label])=>{const b=document.createElement('button');b.dataset.k=k;
 b.textContent=label+(k.startsWith('cat:')?` (${D.pages.filter(p=>'cat:'+p.cat===k).length})`:(k==='all'?` (${D.pages.length})`:'')); 
 b.onclick=()=>{cur=k;kw='';openPage=null;document.getElementById('q').value='';render();};nav.appendChild(b);});
document.getElementById('q').oninput=e=>{kw=e.target.value.trim();openPage=null;render();};
render();
</script></body></html>
"""


# 已分辨出的「外作官方中文名」表（键=日文名）。命中即作为页面标题与页内【名前】行的统一口径，
# 优先级最高（压过模型缓存译文）；未列出的名字不动，只在 待确认名称.md 里请人工裁定。
KNOWN_OFFICIAL = {
    # 数码兽
    'ウォーグレイモン': '战斗暴龙兽', 'ブラックウォーグレイモン': '黑暗战斗暴龙兽',
    'エンジェウーモン': '神圣天使兽', 'オメガモン': '奥米加兽', 'ベルゼブブ': '别西卜',
    # 怪物猎人
    'ジンオウガ': '雷狼龙', 'ナルガクルガ': '迅龙', 'クシャルダオラ': '钢龙', 'イビルジョー': '恐暴龙',
    'シャガルマガラ': '天廻龙', 'セルレギオス': '千刃龙', 'モノブロス': '一角龙', 'ヤマツカミ': '浮岳龙',
    'ティガレックス': '轰龙', 'リオレウス': '火龙', 'ディノバルド': '斩龙', 'ラギアクルス': '海龙',
    'ディアブロス': '角龙', 'ラージャン': '金狮子', 'イャンクック': '大怪鸟', 'ゴア・マガラ': '黑蚀龙',
    'ウカムルバス': '乌卡姆鲁巴斯', 'ボルジャナフ': '雷颚龙',
    # 少年漫 / JUMP
    '一方通行': '一方通行', 'インデックス': '茵蒂克丝', 'グリムジョー・ジャガージャック': '葛力姆乔·贾卡杰克',
    'スタープラチナ': '白金之星', 'クロコダイン': '鳄鱼王', 'モモ・ベリア・デビルーク': '桃·贝利亚·戴比路克',
    'キルア': '奇犽', 'ザク・レッドスター': '扎克·红星', 'ギガムート': '吉加穆特',
    # 游戏 / 其他
    'ギャラクシーアイズ・フォトン・ドラゴン': '银河眼光子龙', 'セルベリア・ブレス': '塞露贝利亚·布蕾斯',
    'ディルムッド・オディナ': '迪尔姆德·奥迪那', 'キュゥべえ': '丘比', 'ジバニャン': '地缚猫',
    'リンク': '林克', 'ツキヨミ': '月读', 'グレートシング': '大鲸',
    'ヨクバリス（ダイマックスのすがた）': '藏饱栗鼠（极巨化姿态）', 'ヲ級': '航母WO级',
}


def _write_name_review(conflicts, pages):
    """把「页面标题」与页内【名字】行不一致的条目写成人工核对清单（零 API）。"""
    rows = []
    for c in conflicts:
        rows.append((c['jp'], c['old'] if c['old'] != c['new'] else c['name_line'], c['new'],
                     KNOWN_OFFICIAL.get(c['jp'], '')))
    lines = ['# 待确认名称（自动生成，勿手改）',
             '',
             f'共 {len(rows)} 条：同一个个体的「页面标题」与页内【名前】行写法不同（模型两次调用口径不一致）。',
             '标题里若已蹭了别的作品/宝可梦的官方译名（如 インデックス→索引、ウカムルバス→食腐秃鹫）：',
             '把决定好的名字写进 `人工修正.json` 的 `本地译名`（`["日文名","中文名"]`），',
             f'重跑 `{GEN_CMD} --build`（零 API），标题与页内【名前】行会一起改过来。',
             '',
             '（下表「现标题」= 当前页面标题口径；「页内写法」= 该页【名前】行的另一种译法，供比对。）',
             '',
             '| 日文页名 | 现标题 | 页内写法 | 若仍不对，改成 |', '| --- | --- | --- | --- |']
    for jp, old, new, sug in sorted(rows):
        lines.append(f'| {jp} | {new} | {old if old != new else ""} | {sug} |')
    lines += ['', '改法：把决定好的名字写进 `人工修正.json` 的 `本地译名`（`["日文名","中文名"]`），'
                  f'重跑 `{GEN_CMD} --build`（零 API），'
                  '页面标题与页内【名前】行会一起改过来。']
    path = os.path.join(OUT_DIR, '待确认名称.md')
    open(path, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print(f'  名称核对清单 {len(rows)} 条 → {os.path.basename(path)}')


def build_wiki():
    pages_raw = _read_json(os.path.join(RAW_DIR, 'pages_raw.json'), [])
    cache = _read_json(os.path.join(RAW_DIR, 'lines_zh.json'), {})
    official_all = _read_json(os.path.join(POKEWIKI_DIR, '官方译名对照.json'), {}).get('official', [])

    try:                                    # 模型偶尔吐繁体（如 賈修→贾修）→ 统一转简体
        import zhconv
    except ImportError:
        zhconv = None
    conv_n = [0]

    def _simplify(s):
        """繁→简。必须先给日式引号占位：zhconv 会把 「」『』 转成 “”‘’。"""
        if not zhconv or not s:
            return s
        tmp, back = s, {}
        for i, ch in enumerate('「」『』'):
            if ch in tmp:
                ph = chr(0xE000 + i)
                tmp = tmp.replace(ch, ph)
                back[ph] = ch
        out = zhconv.convert(tmp, 'zh-cn')
        for ph, ch in back.items():
            out = out.replace(ph, ch)
        return out
    manual = _read_json(os.path.join(OUT_DIR, '人工修正.json'), {}) or {}
    man_fix = manual.get('修正') or {}
    man_rep = [(a, b) for a, b in (manual.get('替换') or []) if a]
    man_src = [(jp, w, r) for jp, w, r in (manual.get('按源替换') or []) if jp and w and r]
    man_n = [0]

    def zh_of(src):
        _k = (src or '').strip()
        if _name_over.get(_k):
            return _name_over[_k]
        v = man_fix.get(src) or cache.get(src, src)
        if v:
            for a, b in man_rep:
                if a in v:
                    v = v.replace(a, b)
                    man_n[0] += 1
            for jp, w, r in man_src:               # 只有「源行含该日文名 + 译文含该错译」才纠正
                if jp in src and w in v:
                    v = v.replace(w, r)
                    man_n[0] += 1
            v = apply_zh_fix(v)
        c = _simplify(v) if v else v
        if c != v:
            conv_n[0] += 1
        return c

    def cat_of(name, pg):
        if name in CAT_OVERRIDE:                 # 人工指定分类（配置 cat_override，兜底用）
            return CAT_OVERRIDE[name]
        if name in CAT_TERM_PAGE:
            return CAT_TERM_PAGE[name]
        if name.endswith('ポテンシャル'):
            return '潜力'
        if name == 'ポケモン図鑑':
            return '图鉴'
        if name in CAT_SYSTEM:
            return '系统设定'
        # 页面正文首行即页面类型标记：本 wiki 只有两类实体页
        for b in pg['blocks']:                       # 个体页（表格式）
            if b['k'] in ('text', 'h') and any(l.strip().startswith(('【タイプ】', '【名前】'))
                                               for l in (b['jp'] or [])):
                return '宝可梦'
        head = ''
        for b in pg['blocks']:
            if b['k'] in ('text', 'h'):
                head = next((l.strip() for l in (b['jp'] or []) if l.strip()), '')
            if head:
                break
        if head == 'ポケモン図鑑':                    # 个体页（散文式）
            return '宝可梦'
        if head == '登場人物':                        # 角色页
            return '人物'
        return '系统设定'

    _snip_skip = {'宝可梦图鉴', '登场人物', 'ポケモン図鑑', '登場人物'}

    def _snip_of(blocks):
        """列表页摘要：跳过页面类型标记行与纯记号行。"""
        for b in blocks:
            if b['k'] not in ('text', 'h'):
                continue
            for z in (b['zh'] or []):
                z = (z or '').strip()
                if len(z) > 3 and z not in _snip_skip and not re.fullmatch(r'[#=\-—\s]+', z):
                    return z
        return ''


    _manual0 = _read_json(os.path.join(OUT_DIR, '人工修正.json'), {}) or {}
    _per_work = dict(_manual0.get('本地译名') or [])
    _off_zh2jp = {o['zh']: o['jp'] for o in official_all if o.get('zh')}
    _known_zh2jp = {v: k for k, v in KNOWN_OFFICIAL.items()}
    # 「可信译名」= 宝可梦官方译名 ∪ 本作 wiki 人工表 ∪ 已认得的外作官方译名
    _trusted = set(_off_zh2jp) | set(_per_work.values()) | set(KNOWN_OFFICIAL.values())
    # 整行就是一个专名的行（如「ポケモン図鑑」索引行），一律用统一口径
    _name_over = {}
    _name_over.update({jp: zh for jp, zh in _per_work.items()})
    _name_over.update(KNOWN_OFFICIAL)

    def _alt_title(jp, title, nl):
        """决定页面标题口径：本作人工表 > 外作官方名表 > 原标题。

        模型「只给页名、无上下文」时爱蹭发音相近的别人的官方译名（インデックス→索引、
        ウカムルバス→食腐秃鹫、ギガムート→雷狼龙），但也常把正确的名字翻对；
        故只在本作人工表 / 外作官方名表命中时改写，其余一律保留原标题，交人工核对。
        """
        if _per_work.get(jp):                      # 本作人工表最高（可覆盖代码里的 KNOWN_OFFICIAL）
            return _per_work[jp], title if title != _per_work[jp] else ''
        if KNOWN_OFFICIAL.get(jp):
            return KNOWN_OFFICIAL[jp], title if title != KNOWN_OFFICIAL[jp] else ''
        return title, ''

    src_url = lambda n: f'{SITE}/d/' + urllib.parse.quote(n, encoding='euc-jp', safe='')

    out_pages, terms, blob_parts = [], [], []
    name_conflicts = []
    for pg in pages_raw:
        name = pg['name']
        if name in SKIP_PAGES:
            continue
        cat = cat_of(name, pg)
        blocks = []
        for b in pg['blocks']:
            if b['k'] == 'art':
                blocks.append({'k': 'art', 'jp': b['jp']})
                continue
            if b['k'] in ('text', 'h'):
                zh = [zh_of(l) for l in b['jp']]
                blocks.append({'k': b['k'], 'jp': b['jp'], 'zh': zh})
                blob_parts += b['jp']
            else:
                zrows = [[zh_of(c) for c in row] for row in b['rows']]
                blocks.append({'k': 'table', 'rows': b['rows'], 'zh': zrows})
                blob_parts += [c for row in b['rows'] for c in row]
        title = zh_of(name)
        # 先取页内【名前】行的写法，再定标题口径（页内写法常比「只翻页名」更靠谱）
        nl = ''
        for b in blocks:
            seq = (b['zh'] if b['k'] == 'text' else
                   ([c for r in (b['zh'] or []) for c in r] if b['k'] == 'table' else []))
            for z in seq:
                m = re.match(r'^【(名前|名字|名称|姓名)】\s*(.*)$', (z or '').strip())
                if m:
                    nl = re.split(r'[（(]', m.group(2))[0].strip()
                    break
            if nl:
                break
        title, old_title = _alt_title(name, title, nl)
        if old_title:
            name_conflicts.append({'jp': name, 'old': old_title, 'new': title, 'name_line': nl})
        elif (nl and nl != title and nl not in title and title not in nl
              and not (_per_work.get(name) or KNOWN_OFFICIAL.get(name))):
            name_conflicts.append({'jp': name, 'old': title, 'new': title, 'name_line': nl})
        if cat == '宝可梦':                    # 个体页：页内【名字】行与页面标题统一口径
            def _fix(seq):
                for i, z in enumerate(seq):
                    m = re.match(r'^【(名前|名字|名称|姓名)】\s*(.*)$', (z or '').strip())
                    if not m:
                        continue
                    nm = re.split(r'[（(]', m.group(2))[0].strip()
                    if nm and nm != title and nm not in title and title not in nm:
                        seq[i] = z.replace(nm, title, 1)
            for b in blocks:
                if b['k'] == 'text':
                    _fix(b['zh'])
                elif b['k'] == 'table':
                    for zrow in b['zh']:
                        _fix(zrow)
        out_pages.append({'name': name, 'title': title, 'cat': cat,
                          'snip': _snip_of(blocks), 'src': src_url(name), 'blocks': blocks})
        # 术语抽取
        tcat = CAT_TERM_PAGE.get(name)
        if tcat:
            for b in blocks:
                if b['k'] != 'table':
                    continue
                for row, zrow in zip(b['rows'], b['zh']):
                    if not row or row[0].strip() in TABLE_HEADS or not is_translatable(row[0]):
                        continue
                    terms.append({'jp': row[0].strip(), 'zh': zrow[0].strip(), 'cat': tcat,
                                  'page': name,
                                  'desc': ' ／ '.join(x.strip() for x in row[1:] if x.strip()),
                                  'desc_zh': ' ／ '.join(x.strip() for x in zrow[1:] if x.strip())})
        elif cat == '潜力':
            for b in blocks:
                if b['k'] != 'text':
                    continue
                for l, z in zip(b['jp'], b['zh']):
                    m = re.match(r'^『(.+?)』…\s*(.*)$', l)
                    if m:
                        terms.append({'jp': l.split('…')[0].strip(), 'zh': z.split('…')[0].strip(),
                                      'cat': '潜能', 'page': name,
                                      'desc': m.group(2).strip(),
                                      'desc_zh': (z.split('…', 1)[1].strip() if '…' in z else '')})
    uniq, seen = [], set()
    for t in terms:
        k = (t['jp'], t['cat'])
        if k in seen or not t['jp']:
            continue
        seen.add(k)
        uniq.append(t)
    terms = uniq

    blob = '\n'.join(blob_parts)
    official_used = [o for o in official_all if o.get('jp') and o['jp'] in blob]
    data = {'pages': out_pages, 'terms': terms, 'official': official_used,
            'about': ABOUT_HTML}
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    os.makedirs(OUT_DIR, exist_ok=True)
    html_out = (HTML_TPL.replace('__DATA__', payload)
                        .replace('__HTML_TITLE__', HTML_TITLE)
                        .replace('__HTML_H1__', HTML_H1))
    open(os.path.join(OUT_DIR, 'index.html'), 'w', encoding='utf-8').write(html_out)
    json.dump({'works': WORK, 'source': SITE, 'pages': out_pages, 'terms': terms},
              open(os.path.join(OUT_DIR, '术语表.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    json.dump({'_note': '复制自 doc/aa作品相关/ポケモンAA术语wiki/官方译名对照.json（神奇宝贝百科 52poke），'
                        'used=true 表示该译名在本作 wiki 正文里出现过',
               'official': [{**o, 'used': o['jp'] in blob} for o in official_all]},
              open(os.path.join(OUT_DIR, '官方译名对照.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    cnt = {}
    for p in out_pages:
        cnt[p['cat']] = cnt.get(p['cat'], 0) + 1
    if name_conflicts:
        _write_name_review(name_conflicts, out_pages)
    print(f'  index.html {os.path.getsize(os.path.join(OUT_DIR, "index.html")) / 1048576:.1f}MB；'
          f'页面 {len(out_pages)}；术语 {len(terms)}；官方命中 {len(official_used)}/{len(official_all)}')
    print('  分类：' + ' '.join(f'{k}={v}' for k, v in sorted(cnt.items(), key=lambda x: -x[1])))
    if zhconv:
        print(f'  繁体→简体 归一 {conv_n[0]} 处；人工修正 {len(man_fix)} 条 + 替换命中 {man_n[0]} 处')
    miss = [p['name'] for p in out_pages if p['name'] not in cache]
    if miss:
        print(f'  ⚠ 未译页面名 {len(miss)}：' + ', '.join(miss[:10]))


# ABOUT_HTML（index.html「说明」页）来自 scripts/config/work_wikis.json 的 about_html —— 每部作品独立文案


# ══════════════════════ CLI ══════════════════════
def _parse_work(argv):
    """从 argv 里取 --work <id>（缺省 = default_work）。"""
    for i, a in enumerate(argv):
        if a == '--work' and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith('--work='):
            return a.split('=', 1)[1]
    return None


def main(default_work=None):
    argv = sys.argv[1:]
    if '--list' in argv:
        for w in work_ids():
            print(w)
        return
    work = _parse_work(argv) or default_work
    if not work:
        raise SystemExit('用法：python scripts/tools/gen_work_wiki.py --work <id> [--all|--crawl|--extract|'
                         '--translate|--fix-kana|--build|--update]  /  --list 列出已登记作品')
    argv = [a for a in argv if a not in ('--work', work) and not a.startswith('--work=')]
    configure(work)
    print(f'▶ 作品 {WORK}（{SITE}）→ {os.path.relpath(OUT_DIR, BASE)}')
    stages = [a for a in argv if a.startswith('--')]
    if not stages or '--all' in stages:
        stages = ['--crawl', '--extract', '--translate', '--build']
    for s in stages:
        print(f'▶ {s}')
        if s == '--crawl':
            stage_crawl()
        elif s == '--extract':
            stage_extract()
        elif s == '--translate':
            stage_translate()
        elif s == '--fix-kana':
            stage_fix_kana()
        elif s == '--build':
            stage_build()
        elif s == '--update':
            stage_update()
        else:
            print('  ! 未知阶段', s)


# ══════════════════════ 参考术语（只读）══════════════════════
def _read_json(p, default=None):
    if not os.path.exists(p):
        return default
    return json.load(open(p, encoding='utf-8'))


def load_refs():
    """合并四类参考译名 → {jp: (zh, 来源)}。先到先得（顺序 = 优先级）。

    优先级：作品术语表 > 作品知识库 > 宝可梦wiki补充术语(通用/槽位/作品专属)
            > ポケマス AA术语 > 52poke 官方译名
    `补充术语.json` 的 `歧义` 段 `禁` 名单里的词一律不注入（同形常用词误伤防护）。
    """
    blocked = set()
    for e in _read_json(os.path.join(POKEWIKI_DIR, '补充术语.json'), {}).get('歧义') or []:
        if e.get('禁'):
            blocked.add((e.get('jp') or '').strip())

    cand = []          # (prio, jp, zh, 来源)

    def add(prio, jp, zh, src):
        jp = (jp or '').strip()
        zh = (zh or '').strip()
        if not jp or not zh or jp == zh or len(jp) < 2 or '{' in jp or jp in blocked:
            return
        if re.search(r'[（）「」『』]', jp):
            return
        cand.append((prio, jp, zh, src))

    # 0：本作 wiki 人工确认的本作专名（最高优先，压过一切通用表）
    for jp, zh in (_read_json(os.path.join(OUT_DIR, '人工修正.json'), {}).get('本地译名') or []):
        add(0, jp, zh, '本作wiki人工')
    gl = _read_json(os.path.join(WORK_DIR, f'glossary_{WORK}.json'), {})
    for e in gl.get('glossary') or []:
        add(1, e.get('jp'), e.get('zh'), '作品术语表')
    kn = _read_json(os.path.join(WORK_DIR, f'{WORK}_knowledge.json'), {})
    for sec in ('人物', '术语'):
        for jp, v in (kn.get(sec) or {}).items():
            if isinstance(v, str):
                add(2, jp, v, '知识库')
    extra = _read_json(os.path.join(POKEWIKI_DIR, '补充术语.json'), {})
    for e in extra.get('terms') or []:
        add(3, e.get('jp'), e.get('zh'), '补充术语')
    for k, d in (extra.get('槽位') or {}).items():
        if k.startswith('_'):
            continue
        for jp, zh in (d or {}).items():
            add(3, jp, zh, '槽位表')
    w = (extra.get('works') or {}).get(WORK) or {}
    for e in (w.get('terms') or []) + (w.get('effects') or []):
        add(3, e.get('jp'), e.get('zh'), '作品专属')
    for e in _read_json(os.path.join(POKEWIKI_DIR, '术语表.json'), {}).get('terms', []):
        add(4, e.get('jp'), e.get('zh'), 'AA术语')
    for e in _read_json(os.path.join(POKEWIKI_DIR, '官方译名对照.json'), {}).get('official', []):
        add(5, e.get('jp'), e.get('zh'), '官方')

    cand.sort(key=lambda x: x[0])
    pairs = {}
    for _prio, jp, zh, src in cand:
        pairs.setdefault(jp, (zh, src))
    return pairs


def hints_for(text, pairs, cap=70):
    """只把「本章语料里真出现」的术语当提示注入（防跨作品污染 + 省 token）。"""
    hits = [(jp, zh) for jp, (zh, _s) in pairs.items() if jp in text]
    hits.sort(key=lambda x: -len(x[0]))
    return {jp: zh for jp, zh in hits[:cap]}


# ══════════════════════ translate ══════════════════════
SYS_LINE = """你在汉化日本 AA 漫画（やる夫スレ）的设定 wiki——狐板作品『__WORK__』。
输入是 JSON 数组，每项一行日文（可能是正文一行、小节标题、表格单元，也可能只是页面名/角色名/宝可梦名）。
逐行译成简体中文：
1. 输出 JSON 数组，长度与顺序与输入完全一致；空串输出空串。
2. 专有名词必须用「已知译名」给出的中文（角色名、宝可梦名、招式/特性/道具/场地/队伍名）；没给出的招式/特性/道具/宝可梦/属性/地名，用宝可梦官方中文译名（如 ヒコザル=小火焰猴、ストーンエッジ=尖石攻击、特殊=特殊、変化=变化）。
3. 表格口径：「分類」取值 物理/特殊/変化 → 物理/特殊/变化；「タイプ」→ 属性名（ノ→一般、炎→火、闘→格斗、霊→幽灵、妖→妖精…）；「範囲」取值 単体/全体/自分/敵陣/自陣 → 单体/全体/自身/敌方阵营/己方阵营；「接触」○/×/〇 原样保留。
4. 保留行内结构与符号：◆「」『』《》【】、…、：、/、〜、※ 注（※…）、全角等级符号（Ａ/AA+/C-/Ｂ）、数字、百分比、1/8 这样的分数。不要合并或拆分行，不要补解释。
5. 只输出中文，不要残留任何日文假名（例外：片假名外来语若已有通用中文名就用中文名，如 ポテンシャル→潜能、スタジアム→竞技场）。绝不原样抄回日文。
6. 若某行本身只是一个专有名词（页面名、宝可梦名、角色名、招式名），只输出该名的中文，不要加书名号或说明。
7. 只输出 JSON 数组，不要解释、不要 markdown 代码块。"""

SYS_FIX = """下面给你若干行「日文原文 → 上一次的中文译文」，上一次译文里残留了日文假名（不合格）。
请重新译成**纯中文**（不得残留任何假名），并保持与其他行的译名一致：
- 输入是 JSON 数组 [{"jp":…,"prev":…}]，输出同长度的 JSON 数组，每项为修正后的中文字符串。
- 招式/特性/道具/宝可梦/属性名用宝可梦官方中文译名；角色/场地/队伍名用给定译名。
- 只输出 JSON 数组，不要解释、不要代码块。"""


def call_model(system, body, max_tokens=8192, timeout=300):
    import aa_backend
    txt, backend = aa_backend.call_with_fallback(False, system, body,
                                                 max_tokens=max_tokens, timeout=timeout)
    return txt, backend


def parse_json_array(txt):
    t = (txt or '').strip()
    t = re.sub(r'^```(?:json)?|```$', '', t, flags=re.M).strip()
    i, j = t.find('['), t.rfind(']')
    if i >= 0 and j > i:
        t = t[i:j + 1]
    return json.loads(t)


_KANA_RE = re.compile(r'[\u3041-\u3096\u309d-\u309f\u30a1-\u30fa\u30fd-\u30ff\uff66-\uff9f]')


def _has_kana(s):
    """真·假名判定：排除 ・(U+30FB) 与 ー(U+30FC)——它们常出现在合法中文行里。"""
    return bool(_KANA_RE.search(s or ''))


def translate_lines(lines, pairs, batch=20, workers=8):
    """批翻 → {行: 中文}。失败自动二分重试；返回 (结果 dict, 未通过校验的原文集合)。"""
    res, bad = {}, set()
    chunks = [(i, lines[i:i + batch]) for i in range(0, len(lines), batch)]

    def one(ch, depth=0):
        start, chunk = ch
        hint = hints_for('\n'.join(chunk), pairs)
        head = ''
        if hint:
            head = '已知译名（必须使用）：' + json.dumps(hint, ensure_ascii=False) + '\n'
        body = head + json.dumps(chunk, ensure_ascii=False)
        try:
            txt, _b = call_model(_sys(SYS_LINE), body)
            arr = parse_json_array(txt)
            if not isinstance(arr, list) or len(arr) != len(chunk):
                raise ValueError(f'行数不符 {len(arr) if isinstance(arr, list) else "?"}/{len(chunk)}')
            return start, [str(x) for x in arr]
        except Exception as e:
            if depth >= 2 or len(chunk) <= 3:
                print(f'    ! 块 {start} 放弃：{e}', flush=True)
                return start, list(chunk)
            mid = len(chunk) // 2
            a = one((start, chunk[:mid]), depth + 1)
            b = one((start + mid, chunk[mid:]), depth + 1)
            return start, a[1] + b[1]

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for start, arr in ex.map(one, chunks):
            for k, v in enumerate(arr):
                if start + k < len(lines):
                    res[start + k] = v
    ordered = [res.get(i, '') for i in range(len(lines))]
    # 校验：残留假名 / 原样照抄
    retry_idx = [i for i, v in enumerate(ordered)
                 if _has_kana(v) or (v.strip() == lines[i].strip() and _has_kana(lines[i]))]
    if retry_idx:
        print(f'    二次修正 {len(retry_idx)} 行', flush=True)
        items = [{'jp': lines[i], 'prev': ordered[i]} for i in retry_idx]
        for i in range(0, len(items), batch):
            part = items[i:i + batch]
            idxs = retry_idx[i:i + batch]
            try:
                txt, _b = call_model(SYS_FIX, json.dumps(part, ensure_ascii=False))
                arr = parse_json_array(txt)
                if len(arr) != len(part):
                    raise ValueError('行数不符')
                for k, v in enumerate(arr):
                    v = str(v).strip()
                    if v and not _has_kana(v):
                        ordered[idxs[k]] = v
            except Exception as e:
                print(f'    ! 修正块失败：{e}', flush=True)
    out = {}
    for i, src in enumerate(lines):
        v = (ordered[i] or '').strip()
        if not v or _has_kana(v) or v == src.strip():
            bad.add(src)
            v = v or src
        out[src] = v
    return out, bad


def stage_translate():
    pages = _read_json(os.path.join(RAW_DIR, 'pages_raw.json'), [])
    pairs = load_refs()
    print(f'  参考译名 {len(pairs)} 条（作品术语表/知识库/补充术语/AA术语/官方）')
    cache_p = os.path.join(RAW_DIR, 'lines_zh.json')
    cache = _read_json(cache_p, {})
    todo, seen = [], set()
    for pg in pages:
        if pg['name'] in SKIP_PAGES:
            continue
        if pg['name'] not in cache and pg['name'] not in seen:
            seen.add(pg['name'])
            todo.append(pg['name'])
        for b in pg['blocks']:
            if b['k'] in ('text', 'h'):
                seq = b['jp']
            elif b['k'] == 'table':
                seq = [c for row in b['rows'] for c in row]
            else:
                continue
            for l in seq:
                if is_translatable(l) and l not in cache and l not in seen:
                    seen.add(l)
                    todo.append(l)
    print(f'  待译 {len(todo)} 条（已缓存 {len(cache)}）')
    if todo:
        os.environ['AA_USAGE_TAG'] = USAGE_TAG
        for i in range(0, len(todo), 400):
            part = todo[i:i + 400]
            got, bad = translate_lines(part, pairs)
            cache.update(got)
            json.dump(cache, open(cache_p, 'w', encoding='utf-8'), ensure_ascii=False)
            print(f'    已译 {min(i + 400, len(todo))}/{len(todo)}'
                  f'{f"（未通过校验 {len(bad)}）" if bad else ""}', flush=True)
    print(f'  译文缓存 {len(cache)} 条')


SYS_FIX_ONE = """把下面这行日文（狐板 AA 作品『__WORK__』的 wiki 内容）译成简体中文。
要求：
- 纯中文，不得残留任何日文假名；若整体是一个专有名词（角色名/宝可梦名/招式名/作品名/固有名词），用通行中文译名。
- 保留原有符号结构：『』「」《》【】、…、：、/、数字、百分比、全角等级符号（Ａ/AA+/C-）。
- 只输出译文本身，不要引号、不要解释、不要 JSON。"""


def _sys(s):
    """把 prompt 模板里的 __WORK__ 换成当前作品名（每部作品独立）。"""
    return s.replace('__WORK__', WORK_TITLE)

# 汉化口径统一（对齐已交付汉化的多数写法；只改译文，不改日文原文）
ZH_FIX = [
    (re.compile(r'1/(?:场|場|次|局|回合|戦|战)/'), '1/试/'),
    (re.compile(r'1/(?:场|場|次|局|回合|戦|战)(?![0-9/])'), '1/试'),
    (re.compile(r'/先制|/先手'), '/先行'),
    (re.compile(r'済[）)]'), '已）'),
    (re.compile(r'技能机器'), '招式学习器'),          # 宝可梦wiki补充术语：技マシン→招式学习器
    (re.compile(r'(?<!专属)潜力'), '潜能'),           # 交付多数用「潜能」（専用ポテンシャル 另按交付习惯作「专属潜力」）
    (re.compile(r'育成(?!型)'), '培育'),              # 「育成型」保持（交付 育成型 137 : 培育型 17）
    (re.compile(r'类型'), '属性'),
]


def apply_zh_fix(s):
    for pat, rep in ZH_FIX:
        s = pat.sub(rep, s)
    return s


def stage_fix_kana():
    """把译文里仍残留假名的行逐条重译（单发 + 最强提示）。"""
    cache_p = os.path.join(RAW_DIR, 'lines_zh.json')
    cache = _read_json(cache_p, {})
    refs = load_refs()
    allow_re = re.compile(r'^[あかさたなはまやらわ]行$')        # 五十音图小节标题，本就该保持假名
    manual = _read_json(os.path.join(OUT_DIR, '人工修正.json'), {}) or {}
    keep = set(manual.get('保留日文') or []) | set((manual.get('修正') or {}).keys())
    bad = [k for k, v in cache.items()
           if _has_kana(v) and not allow_re.match(k) and k not in keep]
    print(f'  残留假名行 {len(bad)}')
    if not bad:
        return
    os.environ['AA_USAGE_TAG'] = USAGE_TAG
    fixed = 0
    for k in bad:
        hint = hints_for(k, refs, cap=30)
        body = (('已知译名（优先使用）：' + json.dumps(hint, ensure_ascii=False) + '\n')
                if hint else '') + k
        try:
            txt, _b = call_model(_sys(SYS_FIX_ONE), body, max_tokens=2000, timeout=120)
            v = (txt or '').strip().strip('"').strip()
            if v and not _has_kana(v):
                cache[k] = v
                fixed += 1
                print(f'    ✓ {k[:40]} → {v[:40]}')
            else:
                print(f'    - 仍不合格：{k[:40]} → {v[:40]}')
        except Exception as e:
            print(f'    ! 失败 {k[:40]}: {e}')
    json.dump(cache, open(cache_p, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'  修好 {fixed}/{len(bad)}')


def stage_build():
    build_wiki()


def stage_update():
    """连载更新同步（`--update`）：重抓全部页（零模型成本）→ 重新抽取 → 增量翻译 → 重建。

    作品连载中更新时跑这条：新章/设定改动会同步进本地 wiki；
    译文按行缓存（`_raw/lines_zh.json`），只有新行/改动行才送模型，其余零成本复用。
    """
    stage_crawl(force=True)
    stage_extract()
    stage_translate()
    stage_build()


if __name__ == '__main__':
    main()
