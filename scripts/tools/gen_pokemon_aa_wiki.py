# -*- coding: utf-8 -*-
"""宝可梦AA术语本地 wiki 生成器（2026-09-21）

数据源（均需 v2rayN 代理 127.0.0.1:10808）：
  1. ポケマス wiki  https://seesaawiki.jp/pokemasu2/  —— AA 原创术语（オリジナル技/特性/持ち物、
     ポテンシャル体系、四大資質、世界観用語）EUC-JP 编码
  2. 神奇宝贝百科    https://wiki.52poke.com/        —— 官方中文译名对照（招式/特性/道具/宝可梦/属性/**地名**）
     注（2026-10-07）：`action=parse` 与 `index.php` 已被 52poke 的登录墙 / Cloudflare 挡住（返回
     `mustbeloggedin` / Just a moment）；官方译名的四个列表页仍是 9 月缓存，**新增的「地名」改走**
     `action=query`（`list=embeddedin` / `list=categorymembers` / `prop=revisions`）——这条匿名可用。

产出（默认 doc/aa作品相关/ポケモンAA术语wiki/）：
  index.html            本地可浏览 wiki（内嵌数据，搜索+分类过滤，file:// 直接打开）
  术语表.json           机器可读术语表（jp/zh/分类/效果原文/效果中文/出处/是否官方术语）
  官方译名对照.json     52poke 官方 JP↔ZH 对照（招式/特性/道具/宝可梦/属性）
  README.md             来源/方法/口径/复现步骤
  _raw/                 抓取与翻译缓存（内含 .gitignore 自忽略，不进版本库）

分阶段（可单独跑，缓存复用）：
  python scripts/tools/gen_pokemon_aa_wiki.py --crawl       # 抓取两个源
  python scripts/tools/gen_pokemon_aa_wiki.py --extract     # 解析成条目
  python scripts/tools/gen_pokemon_aa_wiki.py --translate   # 模型翻译（术语名+效果+核心系统页）
  python scripts/tools/gen_pokemon_aa_wiki.py --build       # 生成 wiki
  python scripts/tools/gen_pokemon_aa_wiki.py --all         # 全流程

翻译走项目模型链（scripts/config/model_config.toml），用量记入 usage 统计（tag=pokewiki）。
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

OUT_DIR = os.path.join(BASE, 'doc', 'aa作品相关', 'ポケモンAA术语wiki')
RAW_DIR = os.path.join(OUT_DIR, '_raw')
import aa_config   # 代理统一口径（2026-10-08 阶段 4）

PROXY = aa_config.proxy_url()
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')
POKEMASU = 'https://seesaawiki.jp/pokemasu2'
P52 = 'https://wiki.52poke.com'

# ── 52poke 官方译名来源页（JP↔ZH 成对出现）──────────────────────────────
P52_SOURCES = {
    'moves': ('招式列表', 'p52_moves'),
    'abilities': ('特性列表', 'p52_abilities'),
    'items': ('道具列表', 'p52_items'),
    'species': ('宝可梦列表（按全国图鉴编号）/简单版', 'p52_species'),
}
# 18 属性页（标题形如 火（属性）），取 {{N|中文||日文タイプ|英文 type}}
P52_TYPES_ZH = ['一般', '火', '水', '电', '草', '冰', '格斗', '毒', '地面', '飞行',
                '超能力', '虫', '岩石', '幽灵', '龙', '恶', '钢', '妖精']

# ── 52poke 地名来源（2026-10-07 加）：地区 + 城镇/城市 ────────────────────
# 地区页 = {{地区信息框|name=神奥|jname=シンオウ}}（37 页）；城镇页 = {{城镇信息框|name=祝庆市|jpname=コトブキシティ}}
P52_REGION_TPL = 'Template:地区信息框'
P52_PLACE_CAT = 'Category:城镇'
PLACE_JSON = 'places.json'


def p52_api(params):
    """52poke API（curl + 代理 + UA）。只走 action=query——匿名可用；失败返回 None。"""
    q = urllib.parse.urlencode(params)
    raw = fetch(f'{P52}/api.php?{q}')
    if not raw:
        return None
    try:
        return json.loads(raw.decode('utf-8'))
    except Exception:
        return None


def clean_place(s):
    """去 wiki 标记：-{zh-hans:X;zh-hant:Y}- 取 X；剥 {{模板}} / [[链接|文字]]。"""
    s = (s or '').strip()
    i = s.find('-{zh-hans:')
    if i >= 0:
        s = s[i + len('-{zh-hans:'):].split(';')[0]
    while '{{' in s and '}}' in s:
        a = s.find('{{')
        b = s.find('}}', a)
        if b < 0:
            break
        s = s[:a] + s[b + 2:]
    if '[[' in s:
        parts = []
        for chunk in s.split('[[')[1:]:
            inner = chunk.split(']]')[0]
            parts.append(inner.split('|')[-1])
        if parts:
            s = ''.join(parts)
    s = s.replace('&#160;', ' ').replace('&nbsp;', ' ')
    return s.strip().strip('·・')


def crawl_52poke_places():
    """地区 + 城镇/城市 的官方 JP↔ZH（→ _raw/52poke/places.json）。"""
    try:
        import zhconv
    except Exception:
        zhconv = None
    regions = []
    j = p52_api({'action': 'query', 'list': 'embeddedin', 'eititle': P52_REGION_TPL,
                 'einamespace': 0, 'eilimit': 500, 'format': 'json', 'formatversion': 2})
    if j:
        regions = [x['title'] for x in ((j.get('query') or {}).get('embeddedin') or [])]
    towns, cont = [], ''
    for _ in range(10):
        p = {'action': 'query', 'list': 'categorymembers', 'cmtitle': P52_PLACE_CAT,
             'cmlimit': 500, 'format': 'json', 'formatversion': 2}
        if cont:
            p['cmcontinue'] = cont
        j = p52_api(p)
        if not j:
            break
        towns += [x['title'] for x in ((j.get('query') or {}).get('categorymembers') or [])]
        cont = ((j.get('continue') or {}).get('cmcontinue')) or ''
        if not cont:
            break
    out, seen, miss = [], set(), 0
    for kind, key, titles in (('地区', 'jname', regions), ('城镇', 'jpname', towns)):
        for i in range(0, len(titles), 50):
            j = p52_api({'action': 'query', 'prop': 'revisions', 'rvprop': 'content',
                         'rvslots': 'main', 'titles': '|'.join(titles[i:i + 50]),
                         'format': 'json', 'formatversion': 2})
            for pg in ((j or {}).get('query') or {}).get('pages') or []:
                rev = (pg.get('revisions') or [{}])[0]
                wt = (((rev.get('slots') or {}).get('main')) or {}).get('content') or ''
                # 模板参数可能跨行、或以行尾「|」结尾（{{地區信息框 |\n regioncolor=… |\n name=…）、
                # 或带位置参数（{{ranger地点信息框|菲蕾|name=…）→ 先把换行折成「|」再做整体匹配
                flat = ' | '.join(wt.splitlines())

                def _fld(k, _t=flat):
                    m = re.search(r'[|{]\s*' + k + r'\s*=\s*([^|{}]+)', _t)
                    return m.group(1) if m else ''

                zh, jp = clean_place(_fld('name')), clean_place(_fld(key))
                if not zh:                      # 少数页没有 name= → 退回页面标题（去消歧义括号/地区后缀）
                    zh = re.sub('（[^）]*）', '', pg.get('title') or '').strip()
                    if kind == '地区' and zh.endswith('地区'):
                        zh = zh[:-2]
                if not zh or not jp or '{' in zh + jp or '[' in zh + jp:
                    miss += 1
                    continue
                if zhconv:
                    zh = zhconv.convert(zh, 'zh-cn')   # 52poke 简繁混排 → 统一简体
                if jp in seen:
                    continue
                seen.add(jp)
                out.append({'kind': kind, 'jp': jp, 'zh': zh})
        time.sleep(0.3)
    os.makedirs(os.path.join(RAW_DIR, '52poke'), exist_ok=True)
    json.dump(out, open(os.path.join(RAW_DIR, '52poke', PLACE_JSON), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    n_r = sum(1 for x in out if x['kind'] == '地区')
    print(f'  52poke 地名: {len(out)} 条（地区 {n_r} / 城镇 {len(out) - n_r}），跳过 {miss}')

# ── 核心系统页（要整页翻译）────────────────────────────────────────────
PROSE_PAGES = [
    'システム・設定', '世界観・用語（五期）', '四大資質', '資質別',
    '「指示」型', '「育成」型', '「統率」型', '「能力」型',
    'ポケモン育て屋', 'ポケモンジム', 'ダンジョン', 'スタジアム',
    'バトルラリー', '大会ランク/トレーナーランク', '天賦の才/金冠サイズ',
    'ハーフ（五期）', '「艦娘」ポケモン', '古代種（五期）', '吸血種（五期）',
    'アイテム効果', 'フィールド形成', '捕獲', 'リボン屋', 'バケツリレー', 'カントー十八竜',
    'ポケモン図鑑（五期）', '人物情報（五期）', '戦闘メモ帳',
]

# ── 表格型术语页 ──────────────────────────────────────────────────────
TABLE_PAGES = {
    'オリジナル技.html': ('技', 'オリジナル技'),
    'オリジナル特性.html': ('特性', 'オリジナル特性'),
    'オリジナル持ち物.html': ('持ち物', 'オリジナル持ち物'),
}
POTENTIAL_PAGES = [
    '属別ポテンシャル（五期）', '異名ポテンシャル（五期）', '役割ポテンシャル（五期）',
    '種族ポテンシャル（五期）', 'ヌシポテンシャル（五期）', '専用ポテンシャル（五期）',
    '分類ポテンシャル（五期）', '指令ポテンシャル（五期）', '汎用ポテンシャル（五期）',
    '特権ポテンシャル（五期）', '絆系ポテンシャル（五期）', 'ＰＴポテンシャル（五期）',
    '対_ポテンシャル（五期）', '先制ポテンシャル（五期）', 'ポテンシャル一覧（五期）',
    '種族ポテンシャル',
]


# ══════════════════════ 工具 ══════════════════════
def fetch(url, referer=None, data=None):
    cmd = ['curl', '-s', '--max-time', '90', '-x', PROXY, '-A', UA]
    if referer:
        cmd += ['-e', referer]
    if data:
        cmd += ['--data', data]
    cmd.append(url)
    p = subprocess.run(cmd, capture_output=True)
    return p.stdout


def safe_name(name):
    return re.sub(r'[^\w\u3040-\u30ff\u4e00-\u9fff（）・．]+', '_', name)[:120]


def pokemasu_page(name):
    enc = urllib.parse.quote(name, encoding='euc-jp', safe='')
    raw = fetch(f'{POKEMASU}/d/{enc}', referer=f'{POKEMASU}/')
    if not raw:
        return None
    try:
        return raw.decode('euc-jp')
    except UnicodeDecodeError:
        return raw.decode('euc-jp', errors='replace')


def p52_page(title):
    q = urllib.parse.urlencode({'action': 'parse', 'page': title, 'prop': 'wikitext',
                                'format': 'json', 'formatversion': '2'})
    raw = fetch(f'{P52}/api.php?{q}')
    if not raw:
        return None
    try:
        d = json.loads(raw.decode('utf-8'))
        return d['parse']['wikitext']
    except Exception:
        return None


# ══════════════════════ crawl ══════════════════════
def crawl_pokemasu():
    """ポケマス wiki：菜单页种子 + 有限深度展开（跳过读者投稿页）。"""
    os.makedirs(os.path.join(RAW_DIR, 'pokemasu'), exist_ok=True)
    with open(os.path.join(RAW_DIR, '.gitignore'), 'w', encoding='utf-8') as f:
        f.write('*\n')
    seeds = set()
    for url in [f'{POKEMASU}/', f'{POKEMASU}/l/']:
        raw = fetch(url)
        if not raw:
            print('  ! 取不到', url)
            continue
        h = raw.decode('euc-jp', errors='replace')
        for m in re.finditer(r'href="' + re.escape(POKEMASU) + r'/d/([^"#?]+)"', h):
            seeds.add(urllib.parse.unquote(m.group(1), encoding='euc-jp', errors='replace'))
    print(f'  种子页 {len(seeds)} 个')
    skip = ('僕の考えた○○', 'モデル', '古代ログ一覧', '履歴', 'MenuBar')
    seen, queue = set(), sorted(seeds)
    while queue:
        name = queue.pop(0)
        if name in seen:
            continue
        p = os.path.join(RAW_DIR, 'pokemasu', safe_name(name) + '.html')
        if os.path.exists(p):
            h = open(p, encoding='utf-8', errors='replace').read()
        else:
            h = pokemasu_page(name)
            if h is None:
                seen.add(name)
                print('  ! FAIL', name)
                continue
            open(p, 'w', encoding='utf-8').write(h)
            time.sleep(0.25)
        seen.add(name)
        if any(name.startswith(s) for s in skip):
            continue                      # 读者投稿页不展开
        for m in re.finditer(r'href="' + re.escape(POKEMASU) + r'/d/([^"#?]+)"', h):
            n = urllib.parse.unquote(m.group(1), encoding='euc-jp', errors='replace')
            if n not in seen and not any(n.startswith(s) for s in skip):
                queue.append(n)
        if len(seen) % 25 == 0:
            print(f'    {len(seen)} 页 (队列 {len(queue)})', flush=True)
        if len(seen) > 800:
            print('  ! 触顶 800 页')
            break
    print(f'  ポケマス 页面 {len(seen)}')


def crawl_52poke():
    """52poke 官方译名来源页 + 18 属性页。"""
    os.makedirs(os.path.join(RAW_DIR, '52poke'), exist_ok=True)
    with open(os.path.join(RAW_DIR, '.gitignore'), 'w', encoding='utf-8') as f:
        f.write('*\n')
    for key, (title, fname) in P52_SOURCES.items():
        w = p52_page(title)
        if w:
            open(os.path.join(RAW_DIR, '52poke', fname + '.txt'), 'w', encoding='utf-8').write(w)
            print(f'  52poke {key}: {len(w)} chars')
        else:
            print(f'  ! 52poke {key} 失败')
        time.sleep(0.3)
    types = {}
    for zh in P52_TYPES_ZH:
        w = p52_page(f'{zh}（属性）')
        if not w:
            print('  ! 属性页失败', zh)
            continue
        m = re.search(r'\{\{N\|[^|]*\|[^|]*\|([^|}]+)\|', w)
        if m:
            types[m.group(1).strip()] = zh
        time.sleep(0.3)
    json.dump(types, open(os.path.join(RAW_DIR, '52poke', 'types.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print(f'  属性对照 {len(types)}')


def stage_crawl():
    crawl_pokemasu()
    crawl_52poke()
    crawl_52poke_places()


# ══════════════════════ extract ══════════════════════
def body_of(h):
    h = re.sub(r'<script.*?</script>', '', h, flags=re.S)
    h = re.sub(r'<style.*?</style>', '', h, flags=re.S)
    m = re.search(r'<div id="wiki-body"[^>]*>(.*?)(<div id="wiki-footer"|$)', h, flags=re.S)
    seg = m.group(1) if m else h
    return seg.split('このページを編集する')[0]


def read_pokemasu(name):
    p = os.path.join(RAW_DIR, 'pokemasu', safe_name(name) + '.html')
    if not os.path.exists(p):
        return None
    return open(p, encoding='utf-8', errors='replace').read()


def table_rows(seg):
    out = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', seg, flags=re.S):
        cells = []
        for td in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, flags=re.S):
            txt = re.sub(r'<br\s*/?>', ' / ', td)
            txt = re.sub(r'<[^>]+>', '', txt)
            cells.append(html.unescape(txt).strip())
        if cells:
            out.append(cells)
    return out


NAV_LINES = {'トップページ', 'ページ一覧', 'メンバー', '編集', '×', 'MenuBar1'}


def prose_of(seg, page_name=''):
    t = re.sub(r'<br\s*/?>', '\n', seg)
    t = re.sub(r'</(p|div|h\d|li|tr|td)>', '\n', t)
    t = re.sub(r'<[^>]+>', '', t)
    t = html.unescape(t)
    t = re.sub(r'[ \t\u3000]+\n', '\n', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    lines = [l.strip() for l in t.split('\n')]
    out = []
    for l in lines:
        if not l:
            continue
        if l in NAV_LINES or l == page_name:
            continue
        if l.startswith('管理人') and '募集しております' in l:
            continue
        if l.startswith('最終更新：') or l.endswith('wiki'):
            continue
        if l.startswith('このページ') or l.startswith('カテゴリ：') or l.startswith('添付'):
            continue
        out.append(l)
    return out


def page_missing(h):
    return 'ページが見つかりませんでした' in h


def official_maps():
    """52poke 官方 JP->ZH 对照。"""
    d = os.path.join(RAW_DIR, '52poke')
    maps = {}
    p = os.path.join(d, 'p52_moves.txt')
    if os.path.exists(p):
        w = open(p, encoding='utf-8').read()
        maps['moves'] = {m.group(2): m.group(1) for m in
                         re.finditer(r'\{\{Movelist/gen/ex\|\d+\|([^|]+)\|([^|]+)\|', w)}
    p = os.path.join(d, 'p52_abilities.txt')
    if os.path.exists(p):
        w = open(p, encoding='utf-8').read()
        maps['abilities'] = {m.group(2): m.group(1) for m in
                             re.finditer(r'\{\{特性列表\|\d+\|([^|]+)\|([^|]+)\|', w)}
    p = os.path.join(d, 'p52_items.txt')
    if os.path.exists(p):
        w = open(p, encoding='utf-8').read()
        items = {}
        for m in re.finditer(r'\n\| \{\{I\|([^}|]+)\}\}\n\| ([^|\n]+)\n', w):
            items[m.group(2).strip()] = m.group(1).strip()
        maps['items'] = items
    p = os.path.join(d, 'p52_species.txt')
    if os.path.exists(p):
        w = open(p, encoding='utf-8').read()
        maps['species'] = {m.group(2): m.group(1) for m in
                           re.finditer(r'\{\{Rdexe\|\d+\|([^|]+)\|([^|]+)\|', w)}
    p = os.path.join(d, 'types.json')
    if os.path.exists(p):
        maps['types'] = json.load(open(p, encoding='utf-8'))
    p = os.path.join(d, PLACE_JSON)          # 地名（地区 + 城镇/城市），2026-10-07 加
    if os.path.exists(p):
        places = {}
        for it in json.load(open(p, encoding='utf-8')):
            jp, zh = (it.get('jp') or '').strip(), (it.get('zh') or '').strip()
            if jp and zh:
                places.setdefault(jp, zh)
        maps['places'] = places
    return maps


def stage_extract():
    maps = official_maps()
    terms, pages = [], []
    # 1) 表格型：技 / 特性 / 持ち物
    for fname, (cat, src) in TABLE_PAGES.items():
        p = os.path.join(RAW_DIR, 'pokemasu', safe_name(fname.replace('.html', '')) + '.html')
        if not os.path.exists(p):
            print('  ! 缺页', fname)
            continue
        seg = body_of(open(p, encoding='utf-8', errors='replace').read())
        rows = table_rows(seg)
        head = rows[0] if rows else []
        for row in rows[1:]:
            if not row or not row[0] or row[0].startswith('【'):
                continue
            jp = row[0].strip()
            if not jp:
                continue
            e = {'jp': jp, 'cat': cat, 'src': src, 'page': src}
            if cat == '技':
                fields = ['name', 'kind', 'type', 'range', 'power', 'acc', 'contact', 'gen', 'effect']
                for i, k in enumerate(fields):
                    e[k] = row[i].strip() if i < len(row) else ''
                e['effect'] = e.get('effect', '')
            elif cat == '特性':
                e['gen'] = row[1].strip() if len(row) > 1 else ''
                e['effect'] = row[2].strip() if len(row) > 2 else ''
                e['owners'] = row[3].strip() if len(row) > 3 else ''
            else:
                e['effect'] = row[1].strip() if len(row) > 1 else ''
            terms.append(e)
        print(f'  {cat}: {len([t for t in terms if t["cat"] == cat])} 条（表头 {head}）')
    # 2) ポテンシャル：『名』… 说明
    # 两遍（全局，跨页）：先收「『X』… 说明」这种带分隔符的正条，再拿没分隔符的行兜名字。
    # 原因（实测 52 条垃圾）：没有 … 的行分三种，都是正文而不是条目 ——
    #   ①『X』付随（役割家族的类型标记，旧实现把「付随」当成了说明 → desc「附带」）
    #   ②『エンペラー』は「Ｓ」ランク…（正文句子，旧实现收成「は「Ｓ」…」）
    #   ③『巨人殺し』や『斬山剣』等が…、／『神槍』と呼ばれた…（正文提及、半句）
    # 兜名字是为了不丢『アシスト』『キラー』『野生』这种家族名（只有标记行没有说明）。
    # 必须全局两遍：某个名字的正文提及可能在 A 页、真说明在 B 页，按页两遍会让空条目抢先。
    real, fallback = {}, {}
    for page in POTENTIAL_PAGES:
        h = read_pokemasu(page)
        if not h:
            print('  ! 缺页', page)
            continue
        if page_missing(h):
            print('  - 无此页', page)
            continue
        lines = prose_of(body_of(h), page)
        cat = page.replace('（五期）', '')
        for l in lines:
            m = re.match(r'^『([^』]{1,40})』\s*(?:…|\.\.\.|：|:)\s*(.*)$', l)
            if m:
                name, eff = m.group(1).strip(), (m.group(2) or '').strip()
                if len(eff) >= 2 and name not in ('異名', 'ポテンシャル', 'ＰＴ', '専用', '共通'):
                    real.setdefault(name, (eff, page, cat))
                continue
            m2 = re.match(r'^『([^』]{1,40})』\s*(\S.*)?$', l)
            if not m2:
                continue
            name = m2.group(1).strip()
            if name in ('異名', 'ポテンシャル', 'ＰＴ', '専用', '共通') or len(name) > 20:
                continue
            fallback.setdefault(name, (page, cat))
    for name, (eff, page, cat) in real.items():
        terms.append({'jp': name, 'cat': 'ポテンシャル', 'sub': cat, 'page': page,
                      'src': 'ポテンシャル一覧（五期）', 'effect': eff})
    n_marker = 0
    for name, (page, cat) in fallback.items():
        if name in real:
            continue
        terms.append({'jp': name, 'cat': 'ポテンシャル', 'sub': cat, 'page': page,
                      'src': 'ポテンシャル一覧（五期）（无说明行，待补）', 'effect': '',
                      'marker': True})
        n_marker += 1
    print(f'  ポテンシャル: {len(real)} 条（另有 {n_marker} 条只有名字、无说明行）')
    # 3) 官方对照匹配（术语名与官方名一致时锁定官方中文）
    off_all = {}
    for k in ('moves', 'abilities', 'items', 'species'):
        for jp, zh in maps.get(k, {}).items():
            off_all.setdefault(jp, (zh, k))
    for t in terms:
        hit = off_all.get(t['jp'])
        if hit:
            t['official'] = True
            t['official_zh'] = hit[0]
            t['official_kind'] = hit[1]
        else:
            t['official'] = False
    # 4) 核心系统页
    for page in PROSE_PAGES:
        h = read_pokemasu(page)
        if not h:
            print('  ! 缺页(prose)', page)
            continue
        if page_missing(h):
            print('  - 无此页(prose)', page)
            continue
        lines = prose_of(body_of(h), page)
        pages.append({'title': page, 'lines': lines})
        print(f'  prose {page}: {len(lines)} 行')
    os.makedirs(RAW_DIR, exist_ok=True)
    json.dump({'terms': terms, 'pages': pages},
              open(os.path.join(RAW_DIR, 'terms_raw.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    json.dump({k: v for k, v in maps.items()},
              open(os.path.join(RAW_DIR, 'official.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print(f'  条目合计 {len(terms)}（官方术语命中 {sum(1 for t in terms if t["official"])}），'
          f'系统页 {len(pages)}')


# ══════════════════════ translate ══════════════════════
SYS_TERM = """你是日式AA漫画（やる夫スレ）术语翻译专家，正在为宝可梦题材AA作品建立中文术语表。
规则：
1. 术语名译成简洁中文（2~10字），自然、有命名感；片假名外来语取通行译名或意译，不要生硬音译堆砌。
2. 若条目带 official_zh 字段，术语名必须原样使用该官方中文名，不得改写。
3. 效果/说明逐句译成简体中文，保留数字、百分比、属性/招式/特性名（用官方中文）。
4. 只输出 JSON，不要任何解释、注释或 markdown 代码块。
输出格式：[{"i":序号,"zh":"术语名","desc":"效果中文"}]
"""

SYS_LINES = """你在翻译日式AA漫画（やる夫スレ）宝可梦作品的设定 wiki 页面。
把给定日文行逐行译成简体中文：
1. 输出与输入行数、顺序完全一致的 JSON 数组，每行一个字符串；空行输出空字符串。
2. 保留数字、罗马字代号（Ａ/Ｂ/Ｓ/ＥＸ 等全角等级符号保持全角）、招式/特性/道具/宝可梦名用宝可梦官方中文名。
3. 行内的『』引号术语照译，术语名保持与术语表一致（给定时参考对照）。
4. 只输出 JSON 数组，不要解释、不要代码块。
"""


def call_model(system, body, tag, max_tokens=8192, timeout=300):
    import aa_backend
    os.environ.setdefault('AA_USAGE_TAG', tag)
    txt, backend = aa_backend.call_with_fallback(False, system, body,
                                                 max_tokens=max_tokens, timeout=timeout)
    return txt


def parse_json_array(txt):
    t = txt.strip()
    t = re.sub(r'^```(?:json)?|```$', '', t, flags=re.M).strip()
    i, j = t.find('['), t.rfind(']')
    if i >= 0 and j > i:
        t = t[i:j + 1]
    return json.loads(t)


def translate_batch(entries, hint_pairs):
    """entries: [{'i':k,'jp':..,'effect':..,'official_zh':..}]"""
    payload = json.dumps(entries, ensure_ascii=False)
    hint = ''
    if hint_pairs:
        hint = '\n已知官方译名参考：' + json.dumps(hint_pairs, ensure_ascii=False) + '\n'
    txt = call_model(SYS_TERM, hint + payload, 'pokewiki')
    arr = parse_json_array(txt)
    out = {}
    for it in arr:
        if isinstance(it, dict) and 'i' in it:
            out[int(it['i'])] = (str(it.get('zh', '')).strip(), str(it.get('desc', '')).strip())
    return out


def translate_lines(lines, hint_pairs, batch=25):
    res = {}
    chunks = [(i, lines[i:i + batch]) for i in range(0, len(lines), batch)]

    def work(ch, depth=0):
        start, chunk = ch
        body = json.dumps(chunk, ensure_ascii=False)
        try:
            txt = call_model(SYS_LINES, body, 'pokewiki')
            arr = parse_json_array(txt)
            if len(arr) != len(chunk):
                raise ValueError(f'行数不符 {len(arr)}/{len(chunk)}')
            return start, [str(x) for x in arr]
        except Exception as e:
            if depth >= 2 or len(chunk) <= 4:
                print(f'    ! 段落 {start} 放弃: {e}')
                return start, chunk
            mid = len(chunk) // 2
            a = work((start, chunk[:mid]), depth + 1)
            b = work((start + mid, chunk[mid:]), depth + 1)
            return start, a[1] + b[1]

    with ThreadPoolExecutor(max_workers=4) as ex:
        for start, arr in ex.map(work, chunks):
            for k, v in enumerate(arr):
                if start + k < len(lines):
                    res[start + k] = v
    return [res.get(i, '') for i in range(len(lines))]


def stage_translate(limit=None):
    data = json.load(open(os.path.join(RAW_DIR, 'terms_raw.json'), encoding='utf-8'))
    terms, pages = data['terms'], data['pages']
    maps = json.load(open(os.path.join(RAW_DIR, 'official.json'), encoding='utf-8'))
    off_pairs = {}
    for k in ('moves', 'abilities', 'items', 'species'):
        off_pairs.update(maps.get(k, {}))
    cache_p = os.path.join(RAW_DIR, 'terms_zh.json')
    cache = json.load(open(cache_p, encoding='utf-8')) if os.path.exists(cache_p) else {}
    todo = [t for t in terms if t['jp'] not in cache]
    if limit:
        todo = todo[:limit]
    print(f'  待译 {len(todo)} / 共 {len(terms)}（已缓存 {len(cache)}）')
    batches = []
    for i in range(0, len(todo), 12):
        chunk = todo[i:i + 12]
        items = [{'i': k, 'jp': t['jp'],
                  'kind': t['cat'] + ('/' + t['sub'] if t.get('sub') else ''),
                  'effect': t.get('effect', '')[:400],
                  **({'official_zh': t['official_zh']} if t.get('official') else {})}
                 for k, t in enumerate(chunk)]
        hints = {t['jp']: off_pairs[t['jp']] for t in chunk if t['jp'] in off_pairs}
        batches.append((chunk, items, hints))

    def work(b):
        chunk, items, hints = b
        try:
            got = translate_batch(items, hints)
        except Exception as e:
            print('    ! batch 失败:', e)
            return []
        return [(t['jp'], got.get(k, ('', ''))) for k, t in enumerate(chunk)]

    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(work, batches):
            for jp, (zh, desc) in res:
                if zh:
                    cache[jp] = {'zh': zh, 'desc': desc}
                    done += 1
            if done and done % 120 < 12:
                json.dump(cache, open(cache_p, 'w', encoding='utf-8'), ensure_ascii=False)
                print(f'    已译 {done}/{len(todo)}', flush=True)
    json.dump(cache, open(cache_p, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'  术语翻译完成 {len(cache)}/{len(terms)}')
    # 核心系统页逐行翻译
    page_p = os.path.join(RAW_DIR, 'pages_zh.json')
    pcache = json.load(open(page_p, encoding='utf-8')) if os.path.exists(page_p) else {}
    for pg in pages:
        if pg['title'] in pcache:
            continue
        try:
            pcache[pg['title']] = translate_lines(pg['lines'], {})
            print(f'    页面已译 {pg["title"]}', flush=True)
        except Exception as e:
            print('    ! 页面翻译失败', pg['title'], e)
        json.dump(pcache, open(page_p, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'  系统页翻译完成 {len(pcache)}/{len(pages)}')


# ══════════════════════ build ══════════════════════
HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>宝可梦AA术语wiki（ポケマス / 52poke）</title>
<style>
:root{--bg:#14161a;--panel:#1c1f26;--line:#2b303b;--fg:#e6e8ee;--dim:#98a0b3;--acc:#ff7a45;--acc2:#4ea1ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.65 "Microsoft YaHei",system-ui,sans-serif}
a{color:var(--acc2)}
header{padding:14px 18px;background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
h1{margin:0 0 6px;font-size:17px}
.sub{color:var(--dim);font-size:12px}
#q{width:100%;margin-top:10px;padding:9px 12px;background:#0f1115;color:var(--fg);border:1px solid var(--line);border-radius:6px;font-size:14px}
.wrap{display:flex;align-items:flex-start}
nav{width:230px;flex:0 0 230px;padding:12px;border-right:1px solid var(--line);min-height:100vh}
nav button{display:block;width:100%;text-align:left;margin:2px 0;padding:6px 9px;background:none;border:0;color:var(--fg);border-radius:6px;cursor:pointer;font-size:13px}
nav button:hover{background:#232733}
nav button.on{background:var(--acc);color:#14161a;font-weight:700}
nav .grp{color:var(--dim);font-size:11px;margin:10px 0 4px;letter-spacing:.06em}
main{flex:1;padding:14px 18px;min-width:0}
table{width:100%;border-collapse:collapse}
th,td{border-bottom:1px solid var(--line);padding:7px 8px;vertical-align:top;text-align:left}
th{color:var(--dim);font-size:12px;position:sticky;top:0;background:var(--bg)}
.jp{color:var(--dim);font-size:12px}
.zh{font-weight:600}
.tag{display:inline-block;padding:1px 6px;border:1px solid var(--line);border-radius:10px;font-size:11px;color:var(--dim);margin-right:4px}
.off{color:#ffd166;border-color:#5a4a1a}
.pg{max-width:900px}
.pg h3{color:var(--acc);margin:18px 0 6px}
.zl{padding:1px 0}
.jl{color:var(--dim);font-size:12px;padding:0 0 2px 12px;border-left:2px solid var(--line)}
.count{color:var(--dim);font-size:12px;margin:6px 0 10px}
mark{background:#5a4a1a;color:#ffe9a8}
</style></head><body>
<header>
<h1>宝可梦AA术语wiki <span class="sub">ポケマス wiki（AA原创术语） + 神奇宝贝百科（官方译名）</span></h1>
<div class="sub" id="stat"></div>
<input id="q" placeholder="搜索：日文 / 中文 / 效果关键词（如 ポテンシャル、回避、龙）">
</header>
<div class="wrap"><nav id="nav"></nav><main id="main"></main></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
let cur='all', kw='';
const cats=[['all','全部术语'],['技','原创技'],['特性','原创特性'],['持ち物','原创道具'],
 ['ポテンシャル','潜力（ポテンシャル）'],['系统页','核心系统页中文'],['官方','官方译名对照'],
 ['补充','补充术语（说明对齐）'],['作品专属','作品专属（场地/队伍）'],['修正','人工修正']];
const nav=document.getElementById('nav');
const grp1=document.createElement('div');grp1.className='grp';grp1.textContent='分类';nav.appendChild(grp1);
cats.forEach(([k,label])=>{const b=document.createElement('button');b.textContent=label+(D.counts[k]?` (${D.counts[k]})`:'');
 b.onclick=()=>{cur=k;render();};b.dataset.k=k;nav.appendChild(b);});
const grp2=document.createElement('div');grp2.className='grp';grp2.textContent='潜力细分类';nav.appendChild(grp2);
Object.entries(D.subcounts).forEach(([k,v])=>{const b=document.createElement('button');b.textContent=`『${k}』 (${v})`;
 b.onclick=()=>{cur='sub:'+k;render();};nav.appendChild(b);});
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function hl(s){if(!kw)return esc(s);const re=new RegExp('('+kw.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&')+')','gi');
 return esc(s).replace(re,'<mark>$1</mark>');}
function match(t){if(!kw)return true;const s=(t.jp+' '+(t.zh||'')+' '+(t.desc||'')+' '+(t.effect||'')+' '+(t.kind||'')).toLowerCase();
 return s.includes(kw.toLowerCase());}
function render(){
 document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('on',b.dataset.k===cur));
 const m=document.getElementById('main');m.innerHTML='';
 let rows=[];
 if(cur==='系统页'){ const list=D.pages.filter(p=>!kw||(p.title+p.zh.join(' ')+p.jp.join(' ')).toLowerCase().includes(kw.toLowerCase()));
   const bar=document.createElement('div');bar.className='count';
   bar.innerHTML='系统页 '+list.length+' 篇 <label style="margin-left:10px"><input type="checkbox" id="showjp"> 显示日文原文</label>';
   m.appendChild(bar);
   const chk=bar.querySelector('#showjp');chk.checked=!!D._showjp;chk.onchange=()=>{D._showjp=chk.checked;render();};
   list.forEach(p=>{const d=document.createElement('div');d.className='pg';
    let h='<h3>'+esc(p.title)+'</h3>';
    p.zh.forEach((l,i)=>{const jp=p.jp[i]||'';
      h+='<div class="zl">'+hl(l)+'</div>';
      if(D._showjp&&jp&&jp!==l) h+='<div class="jl">'+esc(jp)+'</div>';});
    d.innerHTML=h;m.appendChild(d);});
   document.getElementById('stat').textContent='系统页 '+list.length+' 篇';return;}
 if(cur==='官方'){ rows=D.official.filter(o=>!kw||(o.jp+o.zh).toLowerCase().includes(kw.toLowerCase())); }
 else if(cur==='补充'){ rows=D.terms.filter(t=>t._src==='补充'&&match(t)); }
 else if(cur==='修正'){ rows=D.terms.filter(t=>t.fixed&&match(t)); }
 else if(cur.startsWith('sub:')){ const s=cur.slice(4); rows=D.terms.filter(t=>t.sub===s&&match(t)); }
 else if(cur==='all'){ rows=D.terms.filter(match); }
 else { rows=D.terms.filter(t=>t.cat===cur&&match(t)); }
 const tb=document.createElement('table');
 tb.innerHTML='<tr><th style="width:190px">日文</th><th style="width:180px">中文</th><th>说明 / 效果</th><th style="width:110px">出处</th></tr>';
 rows.slice(0,800).forEach(t=>{const tr=document.createElement('tr');
  tr.innerHTML=`<td>${hl(t.jp)}${t.official?' <span class="tag off">官方术语</span>':''}${t.fixed?' <span class="tag off" style="background:#7c3aed;color:#fff">已修正</span>':''}</td>
  <td class="zh">${hl(t.zh||'')}</td>
  <td>${hl(t.desc||'')}<div class="jp">${hl((t.effect||'').slice(0,400))}</div></td>
  <td class="jp">${esc(t.page||'')}</td>`;tb.appendChild(tr);});
 m.appendChild(tb);
 document.getElementById('stat').textContent=`共 ${rows.length} 条`+(rows.length>800?'（显示前 800）':'');
}
document.getElementById('q').oninput=e=>{kw=e.target.value.trim();render();};
document.getElementById('stat').textContent=`术语 ${D.terms.length} 条 · 官方对照 ${D.official.length} 条 · 系统页 ${D.pages.length} 篇`;
render();
</script></body></html>
"""


def stage_build():
    raw = json.load(open(os.path.join(RAW_DIR, 'terms_raw.json'), encoding='utf-8'))
    maps = json.load(open(os.path.join(RAW_DIR, 'official.json'), encoding='utf-8'))
    cache_p = os.path.join(RAW_DIR, 'terms_zh.json')
    zh = json.load(open(cache_p, encoding='utf-8')) if os.path.exists(cache_p) else {}
    pages_zh_p = os.path.join(RAW_DIR, 'pages_zh.json')
    pages_zh = json.load(open(pages_zh_p, encoding='utf-8')) if os.path.exists(pages_zh_p) else {}

    terms = []
    seen = set()
    for t in raw['terms']:
        key = (t['jp'], t['cat'], t.get('sub', ''))
        if key in seen:
            continue
        seen.add(key)
        c = zh.get(t['jp'], {})
        terms.append({
            'jp': t['jp'],
            'zh': c.get('zh') or t.get('official_zh') or '',
            'cat': t['cat'],
            'sub': t.get('sub', ''),
            'kind': t.get('kind', ''),
            'effect': t.get('effect', ''),
            'desc': c.get('desc', ''),
            'page': t.get('page', ''),
            'official': bool(t.get('official')),
        })
    official = []
    for k, label in [('moves', '招式'), ('abilities', '特性'), ('items', '道具'),
                     ('species', '宝可梦'), ('types', '属性'), ('places', '地名')]:
        for jp, z in sorted(maps.get(k, {}).items()):
            official.append({'jp': jp, 'zh': z, 'kind': label})
    pages = [{'title': p['title'], 'jp': p['lines'],
              'zh': pages_zh.get(p['title']) or p['lines']} for p in raw['pages']]

    # --- 人工增量：补充术语.json（通用补充 + 作品专属项）------------------------------
    # 这个文件是手工维护的；本 stage 只负责把它并进来，重建 wiki 不会丢。
    # 同 jp 时人工条目优先（覆盖 zh/page），新 jp 追加；作品专属项统一 cat='作品专属'。
    extra_p = os.path.join(OUT_DIR, '补充术语.json')
    extra = json.load(open(extra_p, encoding='utf-8')) if os.path.exists(extra_p) else {}
    over = {}
    for t in (extra.get('terms') or []):
        jp = (t.get('jp') or '').strip()
        if jp:
            over[jp] = t
    for t in terms:
        o = over.pop(t['jp'], None)
        if o:
            t['zh'] = (o.get('zh') or t['zh']).strip()
            t['page'] = o.get('page') or t['page']
            t['desc'] = o.get('desc') or o.get('note') or t['desc']
            if o.get('effect'):
                t['effect'] = o['effect'].strip()
            t['_src'] = '补充'
    for jp, o in over.items():
        terms.append({'jp': jp, 'zh': (o.get('zh') or '').strip(), 'cat': o.get('cat') or '补充',
                      'sub': o.get('sub', ''), 'kind': o.get('kind', ''),
                      'effect': o.get('effect', ''), 'desc': o.get('note', ''),
                      'page': o.get('page') or '补充（说明对齐）', 'official': False, '_src': '补充'})
    work_terms = []
    for w, sec in (extra.get('works') or {}).items():
        for t in (sec.get('terms') or []):
            if not (t.get('jp') or '').strip():
                continue
            work_terms.append({'jp': t['jp'].strip(), 'zh': (t.get('zh') or '').strip(),
                               'cat': '作品专属', 'sub': '', 'kind': t.get('cat', ''),
                               'effect': '', 'desc': t.get('note', ''),
                               'page': '作品专属 · %s' % w, 'official': False, 'work': w})
        for e in (sec.get('effects') or []):
            if not (e.get('jp') or '').strip():
                continue
            work_terms.append({'jp': e['jp'].strip(), 'zh': (e.get('zh') or '').strip(),
                               'cat': '作品专属', 'sub': e.get('cat', ''), 'kind': '模板',
                               'effect': '', 'desc': e.get('note', ''),
                               'page': '场地模板 · %s' % w, 'official': False, 'work': w})
    terms.extend(work_terms)

    # --- 人工修正：补充术语.json 的「修正」段（覆盖既有条目的 effect/desc/zh）-----------
    # 用途：抽取器的已知坑 ——
    #   ① ポテンシャル 页里「『X』付随」是**类型标记行**，不是说明，抽取器曾把它当
    #      说明 → desc/effect 变成「附带」（已实测 18 条）；
    #   ② オリジナル技 表里 AA 自己就没填（effect=---）；
    #   ③ 长句被按行截断（『エンペラー』は「…」丢了主语）。
    # 手改入口只有补充术语.json（本 stage 只负责合并，重建 wiki 不丢）。
    fixed = miss = 0
    if extra.get('修正'):
        known = {t['jp'] for t in terms}
        for f in extra['修正']:
            jp = (f.get('jp') or '').strip()
            if not jp:
                continue
            if jp not in known:
                print('    [修正] 未匹配（wiki 无此条）: %s' % jp)
                miss += 1
                continue
            for t in terms:
                if t['jp'] != jp:
                    continue
                if f.get('zh'):
                    t['zh'] = f['zh'].strip()
                if 'effect' in f:
                    t['effect'] = (f.get('effect') or '').strip()
                if 'desc' in f:
                    t['desc'] = (f.get('desc') or '').strip()
                if f.get('src'):
                    t['_fix_src'] = f['src']
                t['fixed'] = True
                fixed += 1
                break

    counts = {'all': len(terms)}
    subcounts = {}
    for t in terms:
        counts[t['cat']] = counts.get(t['cat'], 0) + 1
        if t['sub']:
            subcounts[t['sub']] = subcounts.get(t['sub'], 0) + 1
    counts['补充'] = sum(1 for t in terms if t.get('_src') == '补充')   # 人工补充按来源计数（cat 仍是语义类）
    counts['修正'] = fixed
    # --- 构建期体检：可疑 desc → 提示写进「修正」段 -------------------------------
    # 这几种都是实测踩过的：占位串（抓到「付随」列）、比原文短太多、desc 里残留假名、
    # 以及「无说明行」条目却还挂着旧片段译文。清单直接给出 jp，方便粘进 修正。
    BAD_DESC = {'附带', '附带效果', '无', '---', '--', '—'}
    sus = []
    for t in terms:
        if t.get('_src') == '补充' or t.get('_src_fix'):
            continue
        d = (t.get('desc') or '').strip()
        e = (t.get('effect') or '').strip()
        if t.get('marker'):
            if d:
                sus.append((t['jp'], '无说明行却仍有 desc（可能是旧片段译文）'))
            continue
        if not e:
            continue
        if not d or d in BAD_DESC:
            sus.append((t['jp'], '占位/空:' + (d or '空')))
        elif re.search(r'(?<![「『])[ぁ-ゖ]{3,}', re.sub(r'[「『][^」』]{0,20}[」』]', '', d)):
            # 只认平假名连串：片假名在译文里常是专名（『スカーレット姉妹』等），
            # 引号里的专名（「とっかてん」这类）也不算残留
            sus.append((t['jp'], 'desc 残留平假名'))
        elif len(re.sub(r'\s+', '', d)) < \
                len(re.sub(r'\s+', '', re.split(r'←', e)[0])) * 0.35 and \
                len(re.sub(r'\s+', '', re.split(r'←', e)[0])) >= 10:
            # 去空白、去掉原文尾部 ← 吐槽 再比：有些原文靠全角空格撑长
            sus.append((t['jp'], '比原文短太多'))
    if sus:
        print(f'  ⚠ 可疑 desc {len(sus)} 条（在 补充术语.json 的「修正」段按 jp 覆盖）:')
        for jp, why in sus[:40]:
            print(f'      {jp}  —  {why}')
        if len(sus) > 40:
            print(f'      …另 {len(sus) - 40} 条')
    data = {'terms': terms, 'official': official, 'pages': pages, 'fixed': fixed,
            'counts': counts, 'subcounts': dict(sorted(subcounts.items(), key=lambda x: -x[1]))}
    os.makedirs(OUT_DIR, exist_ok=True)
    html_out = HTML_HEAD.replace('__DATA__', json.dumps(data, ensure_ascii=False)
                                 .replace('</', '<\\/'))
    open(os.path.join(OUT_DIR, 'index.html'), 'w', encoding='utf-8').write(html_out)
    json.dump({'terms': terms, 'pages': pages}, open(os.path.join(OUT_DIR, '术语表.json'), 'w',
              encoding='utf-8'), ensure_ascii=False, indent=1)
    json.dump({'official': official}, open(os.path.join(OUT_DIR, '官方译名对照.json'), 'w',
              encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'  index.html {len(html_out)/1024:.0f}KB · 术语 {len(terms)} · 官方 {len(official)} · '
          f'系统页 {len(pages)} · 人工修正 {fixed}' + (f'（未匹配 {miss}）' if miss else ''))


def main():
    args = sys.argv[1:]
    do = set()
    for a in args:
        if a == '--all':
            do |= {'crawl', 'extract', 'translate', 'build'}
        elif a.startswith('--'):
            do.add(a[2:])
    if not do:
        print(__doc__)
        return
    if 'crawl' in do:
        print('[crawl]'); stage_crawl()
    if 'crawl-pk' in do:
        print('[crawl-pokemasu]'); crawl_pokemasu()
    if 'crawl52' in do:
        print('[crawl-52poke]'); crawl_52poke()
    if 'crawl-places' in do:
        print('[crawl-52poke-地名]'); crawl_52poke_places()
    if 'extract' in do:
        print('[extract]'); stage_extract()
    if 'translate' in do:
        print('[translate]'); stage_translate()
    if 'build' in do:
        print('[build]'); stage_build()


if __name__ == '__main__':
    main()
