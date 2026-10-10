#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backfill_aa_comments.py — 为 AAFanClub 下载版章节 HTML 补齐 110话起 的漏译(用户楼为主 + 楼主楼)。

子命令:
  collect : 解析 110 起所有章节(跳过 " (1)" 重复档)。对每个漏译用户帖抽取需译的日文片段
            (乙家族 + 真日文句子), 写入 <jobdir>/jobs.json(片段精确原文+位置在 body 内)
  translate: 对 kind='sentence' 的片段批量后端翻译 → 缓存 <jobdir>/zh.json(key=原文)
  apply   : 每文件按 body_start 降序处理, 在 body 作用域精确替换原文片段→译文(兼容两种结构), 回写
  verify  : 重新 collect, 统计残留(应为 0 上下)

用法:
  python scripts/fix/backfill_aa_comments.py collect --dir ... --jobdir ...
  python scripts/fix/backfill_aa_comments.py translate --do
  python scripts/fix/backfill_aa_comments.py apply
  python scripts/fix/backfill_aa_comments.py verify
"""
import re, os, sys, glob, json, argparse, time

# Windows GBK stdout: print 日文原句/✓ 崩 UnicodeEncodeError——强制 utf-8 防崩(2026-09-08)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DIR = os.path.join(ROOT, '交付HTML', '时间停止探索者亚鲁欧')
DEFAULT_JOB_DIR = os.path.join(ROOT, 'temp', 'backfill')

HDR = re.compile(r'(?:^|(?<=>)|(?<=[\r\n]))[ \t\u3000]*\.?(\d{1,5})\s*[：:]\s*([^：:]{1,110}?)\s*[：:]\s*(\d{4}/\d{2}/\d{2})')
ID_RE = re.compile(r'ID:\s*([A-Za-z0-9+/=#]{4,})')
TAG = re.compile(r'<[^>]+>')
ENT = re.compile(r'&[a-z]+;|&#\d+;')
HIRA = re.compile(r'[\u3040-\u309f]')
KATA = re.compile(r'[\u30a0-\u30ff]')
HANR = re.compile(r'[\u4e00-\u9fff]')
ART = re.compile(r'[：;/|｜＼／・:＿_●○▲△■□◆◇★☆【】〓＝]')
TSET = set('っゃゅょぁぃぅぇぉゎゕゖヵヶ')


def strip_tags(t):
    return ENT.sub(' ', TAG.sub('', t))


def find_raw_span(raw, target_clean):
    """在含实体/标签的原始 body 片段里, 定位清洗后文本 target_clean 对应的原始 [lo,hi) 区间。
    通过同步构造清洗串 + 每字符原始区间映射实现。找不到返回 None。"""
    CLEAN = re.compile(r'<[^>]+>|&[a-z#][a-z0-9;#]*;', re.I)
    cleaned, cmap = [], []
    pos = 0
    while pos < len(raw):
        m = CLEAN.match(raw, pos)
        if m:
            s = m.group(0)
            if s.startswith('&'):
                cleaned.append(' ')
                cmap.append((m.start(), m.end()))
            pos = m.end()
        else:
            ch = raw[pos]
            cleaned.append(ch)
            cmap.append((pos, pos + 1))
            pos += 1
    c = ''.join(cleaned)
    k = c.find(target_clean)
    if k < 0:
        return None
    return (cmap[k][0], cmap[k + len(target_clean) - 1][1])


def chap_num(fn):
    m = re.search(r'.?(\d{3,4})话', fn)
    return int(m.group(1)) if m else None


def parse(text):
    ms = list(HDR.finditer(text))
    out = []
    for i, m in enumerate(ms):
        se = m.end()
        seg = text[m.end(): m.end() + 400]
        idm = ID_RE.search(seg)
        if idm:
            se = m.end() + idm.end()
        be = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        out.append((int(m.group(1)), m.group(2).strip(), se, be))
    return out


OTSU_VARIANTS = sorted([
    ('おつかれさまです', '辛苦啦'), ('おつかれさま', '辛苦啦'), ('おつかれ', '辛苦啦'),
    ('おつでしたー', '辛苦啦'), ('おつです', '辛苦啦'), ('おつおつ', '辛苦啦'),
    ('おつでした', '辛苦啦'), ('おつー', '辛苦啦'), ('おつ', '辛苦啦'),
    ('完結乙です', '完结辛苦啦'), ('完結乙でしたー', '完结辛苦啦'), ('完結乙でした', '完结辛苦啦'), ('完結乙', '完结辛苦'),
    ('重新道一句乙でしたー', '重新道一句辛苦啦'), ('道一句乙でしたー', '道一句辛苦啦'), ('道一句乙', '道一句辛苦'),
    ('忘了乙', '忘了说辛苦'), ('暂时乙です', '暂时辛苦啦'), ('暂时乙', '暂时辛苦'),
    ('莫大的乙', '莫大的辛苦'), ('试行乙', '试行辛苦'), ('王族最大战力乙', '王族最大战力辛苦'),
    ('地平线啊乙', '地平线啊辛苦'),
    ('乙でしたー', '辛苦啦'), ('乙でした', '辛苦啦'), ('乙です', '辛苦啦'),
    ('乙でし', '辛苦'), ('乙かれ', '辛苦'), ('乙ー', '辛苦'), ('乙', '辛苦'),
], key=lambda v: -len(v[0]))

SIMPLE_JP = {  # 常见叹词/短日语 确定性
    'ひぇ': '咿', 'わぁ': '哇', 'やった': '太好了', '怖っ': '好可怕', '草ぁ': '草',
    'うお': '呜哦', 'うわぁ': '呜哇', '痛っ': '好痛', 'あぁ': '啊',
    'ゔっ': '呜', 'くっ': '嚏', 'わー': '哇', 'ひえっ': '咿',
}


SHAPE = set('二二三爻八匕厂从')
def find_otsu(body, relax=False):
    """返回 [(start,end,var,zh)] 乙家族片段。
    relax=True(中文语境帖): 允许乙前为汉字(排除形状字/乙十组合), 用于补齐嵌在中文评论里的收尾乙。"""
    res = []
    for var, zh in OTSU_VARIANTS:
        for m in re.finditer(re.escape(var), body):
            s, e = m.start(), m.end()
            prev = body[max(0, s - 1):s]
            if not relax and HANR.search(prev):
                continue
            if relax:
                if prev in SHAPE:
                    continue
                nxt = body[e:e + 1]
                if nxt in '女種甲型級部個':
                    continue
            res.append((m.start(), m.end(), var, zh))
    res.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    dedup = []
    for r in res:
        if dedup and r[0] >= dedup[-1][0] and r[1] <= dedup[-1][1]:
            continue
        dedup.append(r)
    return dedup


def sentence_lines(body_masked):
    """对已抹掉乙家族的 body(原始), 找仍需翻译的日文文本行(返回原文精确片段)。
    先按 <br> 与 \n 切分(保证片段是原文连续子串), 再去标签/实体/前导点空格。"""
    out = []
    for seg in re.split(r'<br\s*/?>|\n', body_masked):
        s = strip_tags(seg).strip(' \t')
        s = s.lstrip(' .　\u3000')
        if len(s) < 2:
            continue
        hira = sum(1 for c in s if '\u3040' <= c <= '\u309f' and c not in TSET)
        sm = sum(1 for c in s if c in TSET)
        kata = len(KATA.findall(s))
        han = len(HANR.findall(s))
        if han >= 2 and hira == 0:
            continue  # 已译中文
        if han >= 6 and hira <= 2:
            continue  # 已译中文句(含站点名之类残留假名)
        if re.search(r'暇な名無し|メンテナンス予定|ID:\S{4,}', s):
            continue  # 帖头残留
        if hira >= 3 or (hira >= 2 and han <= 2) or (hira == 0 and kata >= 4):
            if ART.search(s) and hira + sm < 2:
                continue
            out.append(s)
    return out


def collect_one(text):
    jobs = []
    for num, name, se, be in parse(text):
        if not (('名無し' in name) or ('メンテナンス' in name) or name.startswith('5/')):
            continue
        body = text[se:be]
        han_all = len(HANR.findall(strip_tags(body)))
        relax = han_all >= 4
        otsu = find_otsu(body, relax)
        pieces = [dict(t=body[s:e], z=zh, kind='otsu') for s, e, v, zh in otsu]
        # 抹掉乙家族后检测句子
        masked = body
        for s, e, v, zh in sorted(otsu, key=lambda r: -r[0]):
            masked = masked[:s] + ' ' * (e - s) + masked[e:]
        for ln in sentence_lines(masked):
            if ln not in [p['t'] for p in pieces]:
                pieces.append(dict(t=ln, z='', kind='sentence'))
        if pieces:
            jobs.append(dict(post=num, body_start=se, body_end=be, pieces=pieces))
    return jobs


def cmd_collect(dirpath, jobdir):
    os.makedirs(jobdir, exist_ok=True)
    all_jobs = []
    for fn in sorted(glob.glob(os.path.join(dirpath, '*.html'))):
        b = os.path.basename(fn)
        if ' (1).' in b:
            continue
        n = chap_num(b)
        if n is None or n < 110:
            continue
        text = open(fn, encoding='utf-8', newline='').read()
        for j in collect_one(text):
            j['file'] = b
            all_jobs.append(j)
    with open(os.path.join(jobdir, 'jobs.json'), 'w', encoding='utf-8') as f:
        json.dump(all_jobs, f, ensure_ascii=False)
    otsu = sum(1 for j in all_jobs if all(p['kind'] == 'otsu' for p in j['pieces']))
    sent = set()
    for j in all_jobs:
        for p in j['pieces']:
            if p['kind'] == 'sentence':
                sent.add(p['t'])
    print(f'jobs={len(all_jobs)} (纯乙帖 {otsu}), 唯一句子 {len(sent)}')


def cmd_map(jobdir):
    path = os.path.join(jobdir, 'jobs.json')
    jobs = json.load(open(path, encoding='utf-8'))
    n = 0
    for j in jobs:
        for p in j['pieces']:
            if p['kind'] == 'sentence' and p['t'] in SIMPLE_JP:
                p['kind'], p['z'], n = 'otsu', SIMPLE_JP[p['t']], n + 1
    json.dump(jobs, open(path, 'w', encoding='utf-8'), ensure_ascii=False)
    print('map: 闲词就近', n)


def cmd_translate(jobdir, do):
    jobs_path = os.path.join(jobdir, 'jobs.json')
    zh_path = os.path.join(jobdir, 'zh.json')
    jobs = json.load(open(jobs_path, encoding='utf-8'))
    cache = json.load(open(zh_path, encoding='utf-8')) if os.path.exists(zh_path) else {}
    pending = sorted({p['t'] for j in jobs for p in j['pieces'] if p['kind'] == 'sentence' and p['t'] not in cache})
    print(f'待译唯一句子: {len(pending)}')
    if not do:
        return
    if not pending:
        return
    sys.path.insert(0, os.path.join(ROOT, 'scripts'))
    sys.path.insert(0, os.path.join(ROOT, 'scripts', 'common'))
    from aa_backend import call_commandcode
    SYSTEM = (
        '把下面 2ch 匿名用户的日文帖子逐条翻译成简体中文。要求: 口语化、简洁自然、保留原意与语气; '
        '「ｗ」保留为「ｗ」或「笑」; 专名采用: 亚鲁欧(やる夫)、波卿(ボ卿)、精灵酱、史莱姆、玛露西尔、'
        '立夏、角兔、独眼巨人、特鲁尼科、厄里斯、阿库西斯、黑色安息日。'
        '每行一条「序号|译文」, 只输出结果不要解释。纯拟声/语气词贴切地译为中文拟声或保留。'
    )
    batch, idx2s = [], {}
    def flush():
        body = '\n'.join('%d|%s' % (i, s) for i, s in batch)
        out = None
        for _ in range(3):
            try:
                out = call_commandcode(SYSTEM, body, max_tokens=4000, timeout=150)
                break
            except Exception as e:
                print('  err %r retry' % e)
                time.sleep(4)
        if out is None:
            return
        got = 0
        for ln in out.splitlines():
            m = re.match(r'^(\d{1,5})\|(.*)$', ln.strip())
            if m:
                i = int(m.group(1))
                if i in idx2s and not idx2s[i][1]:
                    cache[idx2s[i][0]] = m.group(2).strip()
                    idx2s[i][1] = True
                    got += 1
        print('  batch ok %d/%d' % (got, len(batch)))
    for i, s in enumerate(pending):
        idx2s[i] = [s, False]
        batch.append((i, s))
        if len(batch) >= 40:
            flush(); batch, idx2s = [], {}
    if batch:
        flush()
    json.dump(cache, open(zh_path, 'w', encoding='utf-8'), ensure_ascii=False)
    print('translated cache size =', len(cache))


def cmd_apply(jobdir):
    jobs = json.load(open(os.path.join(jobdir, 'jobs.json'), encoding='utf-8'))
    cachepath = os.path.join(jobdir, 'zh.json')
    cache = json.load(open(cachepath, encoding='utf-8')) if os.path.exists(cachepath) else {}
    missing = misses = found = 0
    # body_start 降序处理, 避免长度变化导致偏移错位
    groups = {}
    for j in jobs:
        groups.setdefault(j['file'], []).append(j)
    for fn, jl in groups.items():
        path = os.path.join(DEFAULT_DIR, fn)
        text = open(path, encoding='utf-8', newline='').read()
        new = text
        touched = 0
        for j in sorted(jl, key=lambda x: -x['body_start']):
            se, be = j['body_start'], j['body_end']
            seg = new[se:be]
            edited = seg
            for p in j['pieces']:
                zh = p['z']
                if p['kind'] == 'sentence' and not zh:
                    zh = cache.get(p['t'], '')
                if not zh:
                    missing += 1
                    continue
                n_rep = edited.count(p['t'])
                if n_rep:
                    found += n_rep
                    edited = edited.replace(p['t'], zh)
                else:
                    # 实体/标签导致清洗后文本不匹配原文 → 用清洗字符图回映射替换
                    span = find_raw_span(edited, p['t'])
                    if span:
                        lo, hi = span
                        edited = edited[:lo] + zh + edited[hi:]
                        found += 1
                    else:
                        misses += 1
                        if misses <= 300:
                            print('  ! 未命中: %s 帖%d %r' % (fn, j['post'], p['t'][:40]))
            if edited != seg:
                new = new[:se] + edited + new[be:]
                touched += 1
        if new != text:
            open(path, 'w', encoding='utf-8', newline='').write(new)
            print(f'  ✓ {fn}: {touched}帖回写')
    print(f'apply: 命中替换~{found}, 未命中 {misses}, 缺译文 {missing}')


def cmd_verify(dirpath):
    leftover = 0
    for fn in sorted(glob.glob(os.path.join(dirpath, '*.html'))):
        b = os.path.basename(fn)
        if ' (1).' in b:
            continue
        n = chap_num(b)
        if n is None or n < 110:
            continue
        text = open(fn, encoding='utf-8').read()
        for j in collect_one(text):
            for p in j['pieces']:
                t = p['t']
                if HIRA.search(t.replace('っ', '')) or ('乙' in t) or ('おつ' in t):
                    leftover += 1
                    break
    print('verify: 残留漏译用户帖 ≈', leftover)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['collect', 'map', 'translate', 'apply', 'verify'])
    ap.add_argument('--dir', default=DEFAULT_DIR)
    ap.add_argument('--jobdir', default=DEFAULT_JOB_DIR)
    ap.add_argument('--do', action='store_true')
    a = ap.parse_args()
    {'collect': lambda: cmd_collect(a.dir, a.jobdir),
     'map': lambda: cmd_map(a.jobdir),
     'translate': lambda: cmd_translate(a.jobdir, a.do),
     'apply': lambda: cmd_apply(a.jobdir),
     'verify': lambda: cmd_verify(a.dir)}[a.cmd]()


if __name__ == '__main__':
    main()
