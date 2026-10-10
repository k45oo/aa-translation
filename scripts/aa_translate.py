# -*- coding: utf-8 -*-
"""
AA 漫画汉化管线 v2
- 预标记文本行：含全角假名 / 非艺术汉字 / 日文标点 的行
- 模型只接收标记行（带全局行号），艺术行不经模型，0 损坏风险
- 提示词列出行号清单，要求全部输出；校验行宽、残留假名，缺行自动补译
"""
import json, os, re, sys, time, hashlib, urllib.request, urllib.error, concurrent.futures, gzip
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根(2026-08-18 Phase 3 动态计算)

# registry（works/）与 sites/ 包：--work 模式需要；旧脚本不受影响
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_THIS_DIR, "works"))
sys.path.insert(0, os.path.join(_THIS_DIR, "common"))
sys.path.insert(0, _THIS_DIR)

import aa_env     # 仓库根 .env 统一读写（scripts/common/aa_env.py, 2026-10-08 阶段 1）
ENV_FILE = aa_env.env_path()   # 仓库根 .env（2026-10-08 起为唯一用户可改层；hermes 两处仅只读兜底）
# 2026-08-19 模型配置统一化: 网关/模型从 scripts/config/model_config.toml 读([default] 段)
# 环境变量 AA_GATEWAY/AA_MODEL 仍可覆盖(最高优先级); 未设回落配置值
import aa_config as _CFG  # scripts/common/aa_config.py(同目录已 sys.path)
import task_report as TR   # 2026-08-29 任务管理器: 心跳/停止(common 已 sys.path, 直跑 no-op)

# Windows: stdout 被任务管理器/守护脚本重定向到文件时默认仍按 GBK 编码,
# print 日文/✓/､(U+FF64) 等非 GBK 字符崩 UnicodeEncodeError(refine_aa 校对 538 行崩溃根因)。
# 本模块被 74+ 个 CLI 脚本 import——顶层 reconfigure 一次覆盖全部直跑场景。
# 工作台主进程(uvicorn) stdout=DEVNULL, reconfigure 无副作用; 控制台直跑 errors=replace 保不崩。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

GATEWAY = _CFG.get("default", "gateway", "https://api.commandcode.ai/provider/v1/chat/completions")
# 默认模型; AA_MODEL 环境变量可切换, 未设置回落配置值
MODEL = _CFG.get("default", "model", "xiaomi/mimo-v2.5")
UA = _CFG.backend("commandcode").get(
    "ua", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# 本地代理（2026-10-08 阶段 4）：统一从 aa_config 读（AA_PROXY > TOML [default].proxy > 默认）；
# 调用时现取，设置页改了 .env 无需重启进程。函数形式保留 `_proxy()`，PROXY 仅作旧引用兼容。
def _proxy():
    try:
        return _CFG.proxy_url()
    except Exception:
        return "http://127.0.0.1:10808"


PROXY = _proxy()
AA_DIR = os.path.join(ROOT, "aaManga", "白头与染灰魔女", "aa")
OUT_DIR = os.path.join(ROOT, "aaManga", "白头与染灰魔女", "汉化")
WORK_DIR = os.path.join(ROOT, "aaManga", "白头与染灰魔女", "work")
WORK_ID = ""   # 2026-08-24: registry 模式下的作品 id（--work 传入），供章节级 R18 判定


FLAG_PER_CHUNK = 50          # flagged lines per API call
MAX_WORKERS = int(os.environ.get("AA_MAX_WORKERS", "8"))   # 8 默认； 等受限后端可降为 2-3
if os.environ.get("AA_FREE_MODE") in ("1", "true", "True"):
    # 2026-08-27: 免费模式多模型并发——AA_FREE_WORKERS 默认=免费模型数(3),
    #   每个 worker 经 _call_free_chain 轮询分散到不同免费模型(GLM/ox/laguna 并行),
    #   各自平台限流独立, 不再强制串行。
    MAX_WORKERS = int(os.environ.get("AA_FREE_WORKERS", "3"))
MAX_OUT_TOKENS = int(_CFG.get("default", "max_output_tokens", "16384"))


def _model():
    """运行时读取 AA_MODEL env，未设置时回落付费版 MODEL（与 common/aa_api.py 同语义）"""
    return os.environ.get("AA_MODEL") or MODEL


def _gateway():
    """运行时读取 AA_GATEWAY env，未设置时回落 GATEWAY（免费版走 zen 端点 chat 格式，见 common/aa_api.py 注释）"""
    return os.environ.get("AA_GATEWAY") or GATEWAY


def _extract_text(resp):
    """兼容 chat/completions（choices[].message.content）与 responses（output[].content[].text）两种响应"""
    if "choices" in resp:
        return resp["choices"][0]["message"].get("content") or ""
    out = []
    for item in resp.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    out.append(c.get("text", ""))
    return "\n".join(out)
TEMPERATURE = 0.4

KANA_RE = re.compile(r'[ぁ-んァ-ヺー\uff61-\uff9f]')  # \uff61-\uff9f = 半角片假名(ｦ-ﾟ) 2026-08-25
JP_PUNCT_RE = re.compile(r'[。、…「」『』！？～×※・—]')
HEADER_RE = re.compile(r'^\d+\s*：|^\d+\s*名前')
MARKER_RE = re.compile(r'^(#|=====)')
BOX_END = set('│｜┃┘└┐┌┓┏┚┙»」』')

# 艺术用汉字（本语料统计：无假名行中计数≥15、且常与绘画字符共现）
ART_KANJI = set('一丁七丈三上下不个丶丿乂乍二云人介从代入八冂冖凵刈匕匚十升厂厶土圦圭块孑宀寸小川州幺廴弋弌彡彳心才抖斗斥日月沁洲淤火炒爪爻王示笊芥芯芹衍辷込金蒜戈力千父毛')

# 角色名/艺术纹理（残留校验白名单；统计自本语料+人工确认）
# 注意：必须用集合字面量！set('字符串') 会拆成单字符导致整个白名单失效
# 主数据源: <repo>\glossary.json（人工维护，gen_glossary.py 生成）
_GLOSSARY_JSON = os.path.join(ROOT, "glossary.json")
_ROLES_JSON = os.path.join(ROOT, "roles.json")

def _load_known_names():
    try:
        with open(_GLOSSARY_JSON, encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("known_names", []))
    except Exception:
        return None

KNOWN_NAMES = _load_known_names()
if KNOWN_NAMES is None:
    # 兜底: JSON 缺失时的内置白名单（防止脚本不可用）
    KNOWN_NAMES = {
    'オリジナル', 'オリジナルちゃん', 'ロベルタ', 'ほむら', 'ほむほむ', 'ほむちゃん', 'キュゥべえ', 'アリス', 'キング',
    'シスターハルヒ', 'イギィア', 'マチルダ', '明美ほむら', 'シロアタマ', 'セカンド', 'クエスト', 'ヒリヒリ', 'もどかの',
    'さん', 'ムツキ', 'クローバー', 'つかさ', 'ファースト', 'でた', 'ムイ', 'です', 'イィ', 'アア', 'ィア', 'ほむ', 'トく',
    'ラヮ', 'ヒァ', 'イン', 'アス', 'ィチ', 'てィ', 'ぅト', 'あけおめ', 'テオ', 'ムィ', 'ンイ', 'くメ',
    'トキ', 'やえ', 'カツ丼', 'さすやる', 'ポンおじさん', 'クソ鳴きキング', 'むっきー', 'はやりん', 'しゅがは', 'すばら',
    'トッププロ', 'もこたん', 'てょ', 'やるお', 'ンゴ', 'トシ', '穏乃', 'レジェ', 'ドラ', '大トロ', '憧', '玄', '宥', '衣',
    '白糸台', '虎姫', '阿知賀', 'ネキ', 'ポン', '姫様', 'ぱねぇ', 'テスカトリポカ', 'やんねえ香',
    '戒能プロ', 'シュガーハート', 'はぁと', '小鍛冶', 'Grandmaster', 'The Spook', 'プロ雀士', '牌のお姉さん',
    '準決勝', '敗者復活', '大将戦', '副将戦', '中堅戦', '先鋒戦', '次鋒戦',
    'よりん', 'すこや', 'レジェンゴ', 'レジェンド', 'チャンプ', 'グランマ', 'もんぶち', 'ステモ', 'かじゅ',
    'クロチャー', 'あこちゃん', 'アコ', 'アコチャー', 'あぐり', 'ともきー', 'はるる', 'タコス', 'シズ',
    'シニア', '松永プロ', 'ハギヨシ', 'エトペン', 'ヤギ', 'コミュ', 'モッピーピポパポ', 'かんなレボリューション',
    '池田ァ', '池田', 'メイ', 'んぽさん', 'ナメ', 'ポム', 'じぇ', 'オイ', 'テルテル', 'リスト',
    'やる', 'やる夫', 'すやん', 'すこやん', 'プロ', 'エエ', 'ジェンゴ', 'ヤル', 'ヤッル',
    'ギヨシ', 'ろたん', 'ころたん', 'クロチャ', 'やん', 'イイ', 'やんねえ', 'くん', 'ちゃん',
    'モピ', 'ろも', 'ころも', 'リく', 'ジェンド', 'ゅがは', 'ィソ', 'クロ', 'くト', 'ワカ',
    'ワハハカー', 'あらたそ', 'オイ', 'ーか', 'か', 'とーからしい', 'から',
    'よりん', 'グランマ', 'シニア', 'クロチャー', 'もち', 'コミュ', 'レジェンゴ',
    'モッピーピポパポ', 'あぐり', 'アコチャー', 'ハギヨシ', 'れちゃん', 'マイト', 'ガガ',
    'メソラシ', 'ギギギ', 'はみれす', 'きゃらおけ', 'レイコ', 'キタ', 'パフェは別腹',
    'なお', 'タスク', '王クリ', 'はたたたさん', 'フェイス', 'ねえ香', 'テメエら', 'ナメ',
    'ヒロ候补', '木山アナ', 'かいのーさん', 'ヤルコロ', 'ユウチャー', 'くろちゃー', 'はたて',
    'そラミ', 'ドドドドドドド', 'ゴゴゴゴゴゴ', 'ンゴェ', 'ツモ', 'そうか', '確か', 'ファッ',
    'こちゃー', 'すこやんちゃん', 'ォァ', 'ルリ', 'フフ', 'ェェェ',
    'ぃぅ', 'ュゃ', 'チテ', 'チぅミ', 'こソム', 'くム', 'ムム', 'ィく', 'クト', 'くく',
    'ィク', 'ンア', 'てソ', 'ちじ', 'くヒ', 'イトミ', 'くツ', 'にフ', 'そラミ', 'やら',
    '渣渣', 'ドヤ顔', 'れちゃんステイ', '右京',
    '讃甘高校', '岡山県', 'ラ温', 'ミソ',
    'イッチ', 'イチ', 'かおりん', 'かおん', 'ボマー', 'シノハユ', 'シユ', 'はっちゃん',
    'はちゃん', 'エトペン', 'ポム', 'じぇ', 'んぽさん', 'タコス', 'シズ', 'ヤギ', 'エエ',
    'イイ', 'くん', 'ちゃん', 'やん', 'やんねえ', 'やんねえ香', 'モピ', 'ろも', 'ころも',
    'リく', 'ジェンド', 'ゅがは', 'ィソ', 'クロ', 'くト', 'ワカ', 'ワハハカー',
    'よりん', 'グランマ', 'シニア', 'クロチャー', 'もち', 'コミュ', 'レジェンゴ',
    'モッピーピポパポ', 'あぐり', 'アコチャー', 'ハギヨシ', 'れちゃん', 'マイト', 'ガガ',
    'メソラシ', 'ギギギ', 'はみれす', 'きゃらおけ', 'レイコ', 'キタ', 'パフェは別腹',
    'タスク', '王クリ', 'はたたたさん', 'フェイス', 'ねえ香', 'ナメ',
    'ヒロ候补', '木山アナ', 'かいのーさん', 'ヤルコロ', 'ユウチャー', 'くろちゃー', 'はたて',
    'おもち', 'アナ', 'ヒロ', '木山', 'なお', 'テルテル', 'かんなレボリューション',
    'ファッ', 'ンゴェ', 'エエ', 'ツモ',
    # 拆分碎片（ART_HIRA 含 り/こ/し/と 导致角色名被拆）
    'よん', 'ジェンゴゴゴゴ', 'グラン', 'シア', 'やかじゅ', 'そか', 'コュ', 'ちゃ',
    'ジェンゴに', 'すやんちゃん', 'ピポパポ', 'ジェンドォォォォ', 'フナ', 'もさん',
    'すや', 'ソム', 'イト', 'そラ', 'アコチャ', 'トバ', 'ング', 'もき', 'かな',
    'ファ', 'てた', 'ィぃ', 'フイ', 'チト', 'てる', '確か', 'トバ', 'ング',
    'ハートバーニング', 'バーニング', 'ともきー', 'しってた', 'ファッ',
    'あぐ', 'ギヨシが',
    'くろ',
    'さす',
    'オリ',
    'カチュ',
    'カン',
    'シャ',
    'ジで',
    'ズス',
    'ダイス',
    'テル',
    'ナギ',
    'ナギト',
    'ヒリ',
    'ホモは',
    'ボゼ',
    'メガテンかな',
    'メゲ',
    'モテ',
    'モブ',
    'ヤる',
    'ユキ',
    'リツ',
    'ロン', 'チぅ', 'そら', 'イコ', 'パフェ', 'クリ', 'ねえ', 'かの',
    'ユウチャ', 'くろちゃ', 'あぐり', 'かいのー', 'ねえ香', '対木', 'はみれす',
    'はやん', 'ホモ', 'シュガ', 'ろたんか', 'オカルト', 'には', 'シュガハ', 'ころたん',
    'おまえらぁ', 'なすやん', 'かも', 'れでシズジェンゴ', 'からなあ', 'すこやん眼',
    'ぃぃ',
    'えば',
    'えろ',
    'えト',
    'くろ',
    'さき',
    'さん',
    'たかみ',
    'たん',
    'てぅ',
    'できる夫',
    'とよね',
    'はーたん',
    'ひト',
    'ぽん',
    'んソ',
    'ィス',
    'ィタ',
    'イエロ',
    'イエロー',
    'イツ',
    'エエエ',
    'エエエエエ',
    'エエエエエエエ',
    'オリ',
    'カン',
    'クソ',
    'ダイス',
    'ヅモ',
    'テル',
    'ナギ',
    'ナギト',
    'ヒリ',
    'ボゼ',
    'メガテン',
    'メゲ',
    'モテ',
    'モブ',
    'ヤる',
    'ユキ',
    'リツ',
    'ロン',
    '無駄ヅモ',
    }
ART_HIRA = set('つだりへぢづゑゐいうしふとこっッ')
ART_KATA = set('ノミニハマレヘヾヿヽヾー')

HIRA = re.compile(r'[ぁ-ん]')
KATA = re.compile(r'[ァ-ヶ]')

# 半角片假名/半角符号(U+FF61-FF9F) 在等宽字体(MS Gothic)中渲染为 1 格
HANKAKU = set(chr(c) for c in range(0xFF61, 0xFFA0))

def kana_runs(line):
    """提取行内连续假名段（排除艺术假名），返回 cleaned 片段列表"""
    out = []
    for m in re.finditer(r'[ぁ-んァ-ヶ]+', line):
        s = ''.join(c for c in m.group() if c not in ART_HIRA and c not in ART_KATA)
        if len(s) >= 2:
            # 纯重复单字符 run（メメメ…/ぃぃぃ…）是艺术纹理/拟声，非台词（2026-08-19 yaruohiroba）
            if len(set(s)) == 1:
                continue
            out.append(s)
    return out

def residual_text(line):
    """pass2 口径：行内非艺术假名计数（平假名≥2 或 片假名≥4 判为有残留）。"""
    nospace = re.sub(r'\s', '', line)
    hira = [c for c in HIRA.findall(nospace) if c not in ART_HIRA]
    kata = [c for c in KATA.findall(nospace) if c not in ART_KATA]
    return len(hira) >= 2 or len(kata) >= 4


def _text_segment_part(line, segs):
    """取 line 中所有 text 段拼接（art 段跳过）——残留检测只认 text 段。"""
    return ''.join(line[s['start']:s['end']] for s in segs if s['kind'] == 'text')


def load_segments_meta(meta_dir, base):
    """加载 v3 line_meta 的 segments：{0-based idx: [seg,...]}（仅 mixed 行有）。
    文件缺失/无 segments 返回 {}（与 load_meta_segments_0based 返回 None 区分，pass3 用空 dict 判断）。"""
    path = os.path.join(meta_dir, base + '.json')
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:
        return {}
    out = {}
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        segs = v.get('segments')
        if not segs:
            continue
        try:
            idx = int(k) - 1
        except (ValueError, TypeError):
            continue
        if idx >= 0:
            out[idx] = segs
    return out


def residual_suspect_segs(line, segs, extra_names=None):
    """segments 感知的残留检测：有 segments → 只检 text 段；无 → 全行（原逻辑）。"""
    if not segs:
        return residual_suspect(line, extra_names)
    return residual_suspect(_text_segment_part(line, segs), extra_names)


def residual_text_segs(line, segs):
    """pass2 口径的段级版本：只对 text 段数假名（art 段装饰假名不报）。"""
    if not segs:
        return residual_text(line)
    part = _text_segment_part(line, segs)
    nospace = re.sub(r'\s', '', part)
    hira = [c for c in HIRA.findall(nospace) if c not in ART_HIRA]
    kata = [c for c in KATA.findall(nospace) if c not in ART_KATA]
    return len(hira) >= 2 or len(kata) >= 4


def residual_bad_segs(line, segs, whitelist):
    """pass3 口径的段级版本：只对 text 段做白名单残留检测。无 segments → 全行（原逻辑）。"""
    if not segs:
        return [s for s in kana_runs(line) if s not in whitelist]
    return [s for s in kana_runs(_text_segment_part(line, segs)) if s not in whitelist]

def residual_suspect(line, extra_names=None):
    """返回行内可疑残留日语片段（长假名段且非角色名/艺术纹理白名单）。

    extra_names：调用方可并入作品专属白名单（作品术语表 known_names），
    不影响全局 KNOWN_NAMES 语义（L0 单测守护）。"""
    names = KNOWN_NAMES
    if extra_names:
        names = KNOWN_NAMES | set(extra_names)
    return [s for s in kana_runs(line) if len(s) >= 3 and s not in names]

# ---------------------------------------------------------------------------
# P0-C 人工修复保护（M0）
# 目的：pass2/pass3/fix_width 等模型修复流程不得覆盖人工修好的行。
# 数据：<work_dir>/manual_fix.json
#   { "<filename>": { "<line_no>": {"text": "...", "hash": "...", "ts": "...", "reason": "..."} } }
# 规则：record_manual_fix 是唯一写入入口；load + is_manual_fix 供各 pass 跳过。
# ---------------------------------------------------------------------------
MANUAL_FIX_FILENAME = "manual_fix.json"

def manual_fix_path(work_dir):
    return os.path.join(work_dir, MANUAL_FIX_FILENAME)

def _manual_sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

def load_manual_fixes(work_dir):
    """返回 {filename: {int_line_no: entry}}；文件缺失/损坏返回 {}"""
    p = manual_fix_path(work_dir)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    out = {}
    for fn, lines in data.items():
        out[fn] = {}
        for k, v in lines.items():
            try:
                out[fn][int(k)] = v
            except (ValueError, TypeError):
                continue
    return out

def record_manual_fix(work_dir, filename, line_no, before, after, reason="人工修复"):
    """追加/更新人工修复记录。before==after 则跳过（无实际改动）。
    所有人工修改入口（pass4/5 回填、review 回填、矫正台）必须走此函数。"""
    if before.strip() == after.strip():
        return False
    data = {}
    if os.path.exists(manual_fix_path(work_dir)):
        try:
            with open(manual_fix_path(work_dir), encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    entry = data.setdefault(filename, {})
    key = str(line_no)
    entry[key] = {
        "text": after,
        "hash": _manual_sha(after),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "reason": reason,
    }
    with open(manual_fix_path(work_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return True

def is_manual_fix(work_dir, filename, line_no, current_text):
    """判断当前行是否是一处人工修复（内容与记录一致）。一致则受保护，模型 pass 应跳过。"""
    fixes = load_manual_fixes(work_dir)
    fn_fixes = fixes.get(filename, {})
    entry = fn_fixes.get(line_no)
    if not entry:
        return False
    return entry.get("text") == current_text or entry.get("hash") == _manual_sha(current_text)

# T4.2(2026-08-18): key 惰性缓存 + AA_KEY_RELOAD=1 强制重读逃生口
# （避免每次 API 调用重读 .env；双档案切换 HERMES_HOME 后设 AA_KEY_RELOAD=1 刷新）
# 2026-08-19: key 名从配置 [default].key_env 读; 找不到时回落 OPENCODE_GO_API_KEY(兼容旧 .env)
_KEY_CACHE = None
def get_key():
    global _KEY_CACHE
    if _KEY_CACHE is None or os.environ.get("AA_KEY_RELOAD") in ("1", "true", "True"):
        key_names = [_CFG.get("default", "key_env", "OPENCODE_GO_API_KEY"), "OPENCODE_GO_API_KEY"]
        for key_name in key_names:
            k = aa_env.read_key(key_name)
            if k:
                _KEY_CACHE = k
                return _KEY_CACHE
        raise SystemExit(f"no {key_names[0]}(或 OPENCODE_GO_API_KEY) in {ENV_FILE}"
                         "（设置页「密钥」区块可写入；或手工加一行 KEY=value）")
    return _KEY_CACHE

# M5 指标采集：每次调用落盘 JSONL（默认开启，usage 字段本就随响应返回零额外开销）
def _usage_file():
    """调用时读取，避免导入时缓存导致动态切换失效；未设 AA_USAGE_FILE 时落配置 [usage].file"""
    return os.environ.get("AA_USAGE_FILE") or _CFG.usage_file()

def _log_usage(prompt_tokens, completion_tokens, model=None, backend=None, u=None):
    """u: 响应 usage 原始 dict —— 2026-09-15 成本口径 P0, 取缓存命中/思考 token（口径同 aa_backend）。"""
    uf = _usage_file()
    if not uf:
        return
    tag = os.environ.get("AA_USAGE_TAG") or _CFG.usage_tag()
    rec = {
        "tag": tag, "ts": time.time(),
        "task": os.environ.get("AA_TASK_ID"),
        "prompt": prompt_tokens, "completion": completion_tokens,
        "total": prompt_tokens + completion_tokens,
        "model": model, "backend": backend,
    }
    # 2026-09-04: 复用 aa_backend 的行号上下文(模型↔行映射)
    try:
        import aa_backend as _ab
        _ln = getattr(_ab._USAGE_CTX, "lines", None)
        if _ln:
            rec["lines"] = _ln
    except Exception:
        pass
    # 2026-09-15 P0: 缓存命中输入 + 思考 token（缺省不写; reasoning=0 不写省体积）
    try:
        import aa_backend as _ab
        if _ab._cost_fields():
            _ch, _rt = _ab._usage_extra(u)
            if _ch is not None:
                rec["cache_hit"] = _ch
            if _rt:
                rec["reasoning"] = _rt
    except Exception:
        pass
    try:
        with open(uf, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _ensure_meta_flat(base, meta_dir, cfg=None):
    """平铺版 meta 缺失自动生成（零 API）：从 WORK_DIR/AA_DIR 反推作品目录调用 line_classify_v2。
    传 cfg（registry 条目）时直接用其 raw_dir/site，免反推。失败返回 None。"""
    try:
        path = os.path.join(meta_dir, base + ".json")
        if os.path.exists(path):
            return meta_dir
        print(f"  [M4a] meta 缺失, 自动生成 {os.path.basename(path)}", flush=True)
        _scripts_dir = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(_scripts_dir, "tools"))
        sys.path.insert(0, os.path.join(_scripts_dir, "common"))
        sys.path.insert(0, _scripts_dir)
        import importlib, re as _re
        import line_classify_v2 as LC
        # 优先 cfg 直传；否则从 AA_DIR 反查 registry
        site_name = None
        raw_dir = None
        if cfg is not None:
            site_name = cfg["site"]
            raw_dir = cfg["raw_dir"]
        if site_name is None:
            work_root = os.path.dirname(os.path.abspath(WORK_DIR))
            raw_dir = os.path.join(work_root, "raw")
            try:
                import registry as _R
                for wid, wc in _R.all_works().items():
                    if os.path.abspath(wc["aa_dir"]) == os.path.abspath(AA_DIR):
                        site_name = wc["site"]
                        raw_dir = wc["raw_dir"]
                        break
            except Exception:
                pass
        if site_name is None:
            print("    [M4a] 无法识别站点, 跳过自动生成", flush=True)
            return None
        site = importlib.import_module("sites." + site_name)
        m = _re.search(r'(\d+)', base)
        eid = m.group(1) if m else ''
        cands = [os.path.join(raw_dir, f"entry_{eid}.html"), os.path.join(raw_dir, f"{base}.html")]
        raw_path = next((p for p in cands if os.path.exists(p)), None)
        if raw_path is None and os.path.isdir(raw_dir):
            for cand in sorted(os.listdir(raw_dir)):
                if cand.endswith(".html") and eid and eid in cand:
                    raw_path = os.path.join(raw_dir, cand)
                    break
        if raw_path is None:
            print(f"    raw 缺失, 跳过生成", flush=True)
            return None
        raw = open(raw_path, encoding="utf-8", errors="replace").read()
        txt_lines = open(os.path.join(AA_DIR, base + ".txt"), encoding="utf-8").read().split("\n")
        meta = LC.classify_raw_v2(site, raw, txt_lines, eid=eid)
        if meta is None:
            return None
        os.makedirs(meta_dir, exist_ok=True)
        out = LC._serialize(meta)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        print(f"    [M4a] meta 生成完成 ({len(out)} 行)", flush=True)
        return meta_dir
    except Exception as e:
        print(f"    [M4a] meta 自动生成失败: {e}, fallback is_text_line", flush=True)
        return None


# 窄字符（ASCII/半角片假名 U+FF61-FF9F）= 1 格；其余 2 格。
# 与 aa_common.disp_width 同实现（半角片假名实测渲染 1 格，旧版 ord(c)<0x100 会虚高 6 格/行）。
_NARROW_RE = re.compile(r'[\x00-\xff\uFF61-\uFF9F]')

def disp_width(s):
    return 2 * len(s) - len(_NARROW_RE.findall(s))

def has_kana(s):
    return bool(KANA_RE.search(s))

def is_text_line(l):
    """预标记：真文本行 = 含假名，或含非艺术汉字（纯标点/装饰不标记，中文标点与日文相同无需翻译）"""
    if HEADER_RE.match(l) or MARKER_RE.match(l):
        return False
    if KANA_RE.search(l):
        # 2026-08-25: 纯半角假名行(无全角假名/汉字)可能是艺术装饰(ｶﾞｶﾞｶﾞ)——
        #   整行只有半角假名且连续重复>=3 → 艺术行不标记
        nz = [c for c in l if not c.isspace()]
        hankaku_only = all('\uff61' <= c <= '\uff9f' for c in nz)
        if hankaku_only:
            # 连续重复字符占比(ｶﾞｶﾞｶﾞ→3连) vs 台词(ｷｭｳｯ→无3连)
            rep3 = sum(1 for c in nz if ''.join(nz).count(c) >= 3)
            if rep3 / max(len(nz), 1) >= 0.6:
                return False
        return True
    for c in l:
        if '\u4e00' <= c <= '\u9fff':
            if c not in ART_KANJI:
                return True
    return False


def line_kind(l):
    """行类型分类（2026-08-25 宽度校验用）。

    返回: skip(#/=====) / header(帖头) / text(整行文本) / mixed(文本+图形, 右侧台词) / art(纯图形)
    """
    if l.startswith(('#', '=====')):
        return 'skip'
    if HEADER_RE.match(l) or MARKER_RE.match(l):
        return 'header'
    if is_text_line(l):
        # 2026-08-25: 半角假名触发的 text 行可能含图形符(右侧台词)——应 mixed 段级处理,
        #   整行送模型会改坏左侧 AA
        nz = [c for c in l if not c.isspace()]
        if nz:
            han_kana = sum(1 for c in nz if '\uff61' <= c <= '\uff9f')
            art_chars = sum(1 for c in nz if c in '／＼|()（）[]＿_⊂⊃ミ彡乂爻≧≦ｰ─━│├┤┬┴┼ﾆﾄﾘ:;,.。')
            if han_kana and art_chars / len(nz) >= 0.2:
                return 'mixed'
        return 'text'
    # 非 text 但有假名/非艺术汉字散落（右侧台词或图形内嵌文字）→ mixed
    if KANA_RE.search(l):
        # 2026-08-25: 纯半角假名重复(ｶﾞｶﾞｶﾞ) → art 装饰, 非台词
        nz = [c for c in l if not c.isspace()]
        if nz and all('\uff61' <= c <= '\uff9f' for c in nz):
            rep3 = sum(1 for c in nz if ''.join(nz).count(c) >= 3)
            if rep3 / len(nz) >= 0.6:
                return 'art'
        return 'mixed'
    for c in l:
        if '\u4e00' <= c <= '\u9fff' and c not in ART_KANJI:
            return 'mixed'
    return 'art'


def allow_overwidth(src_line):
    """该行是否允许超宽（TR 宽 > SRC 宽 不报错）。

    2026-08-25 用户决策：text 行(整行台词)、mixed 行(文本在右侧/右侧仅1-2符号)、
    标题行(#/=====)、帖头行 允许超宽——译文比日文长是正常的，且右侧文本
    超宽不破坏左侧 AA 图形；art 行(纯图形)不允许（超宽会错位破坏图形）。
    """
    return line_kind(src_line) in ('skip', 'header', 'text', 'mixed')


# ---------------------------------------------------------------------------
# 艺术行否决（2026-10-08，参考 zseineo/infrared-singularity 的候选级提取）
#   本仓库行级判定（is_text_line / line_meta_v2）会把「含全角片假名的 AA 图行」当文本行
#   （AA 常用 ミ/ニ/ハ/ノ 与借字 三/二/乂/彡 作笔画）。实测送模型行里这类行占 36%、
#   字符占 38.5%（104 部作品抽样）；模型只会原样复述（白头第4话 ckpt 2933 条里 1972 条
#   是这种行、其中 1950 条译文==原文），另偶发把笔画译成汉字（アニニニ→啊你你你）。
#   判据：整行无平假名、无非艺术汉字、无句末标点/引号 → 无可译内容；再要求命中艺术信号
#   （AA 墙同字≥3 或 艺术字符占比 ≥0.5）才否决，避免误杀纯片假名台词。
#   保护：真片假名词（非艺术假名 run，如 ライナー/ウオッカ）、半角假名台词（ｸﾙｯ/ｲｲ）、
#   笑声（ハハハ）任一命中即不否决。
#   实测（本仓库 51 章 / 9.4 万无假名行，地面真值 = 已交付汉化）：否决 4.9 万行
#   （覆盖 52% 的无假名行），其中 99.7% 汉化==原文（模型原样复述 = 纯浪费 token）；
#   误杀 0.33%，且多为空白差与上述 art 事故行。
#   全库抽样（104 部作品各前 5 章，开关前后对比）：**送模型行 −27.3%、字符 −29.7%**；
#   艺术密集作品单章 −57~63%（如 白头第4话 3357→1360 行 / 字符 −61%）。
#   AA_ART_VETO=0 关闭。
# ---------------------------------------------------------------------------
_ART_VETO_WALL_RE = re.compile(r"([ニ二三ミ彡爻从乂工ﾆﾐ])\1\1")
_ART_VETO_FULL_KATA_RE = re.compile(r"[\u30a0-\u30ff]{2,}")
_ART_VETO_HK_RUN_RE = re.compile(r"[\uff66-\uff9f]{2,}")
_ART_VETO_HK_SET = set(chr(_c) for _c in range(0xFF66, 0xFFA0))   # 单个半角假名（噪声占比用）
_ART_VETO_HK_MOD = set('\uff9e\uff9f\uff70')   # 浊点 ﾞ/半浊点 ﾟ/长音 ｰ：装饰性，不计入"真台词"判定
_ART_VETO_KATA = ART_KATA | {'ヿ'}
_ART_VETO_WALL_CHARS = set("ニ二三ミ彡爻从乂工ﾆﾐ")
_ART_VETO_NOISE = set("＼／｜|＿_∧∨＜＞〈〉《》〔〕⌒ー－-─━=＝+＋*:;,.。、･・゛゜'\"`^~")
_ART_VETO_BRACKETS = "『』【】「」《》〔〕〚〛"


def art_veto_enabled():
    """艺术行否决开关（AA_ART_VETO=0 关闭）。"""
    return os.environ.get("AA_ART_VETO", "1") not in ("0", "false", "False")


def _art_veto_run_is_art(r):
    """片假名 run 是否「像艺术笔画」：全为艺术假名（单字重复时须是 AA 墙用字）。"""
    if len(set(r)) == 1:
        return r[0] in _ART_VETO_WALL_CHARS
    return all(c in _ART_VETO_KATA for c in r)


def _art_veto_has_real_text(l):
    """行内是否存在「像真词/真台词」的假名（任一命中即不否决）。"""
    for r in _ART_VETO_FULL_KATA_RE.findall(l):
        if not _art_veto_run_is_art(r):
            return True
    # 半角假名：只看「连续成串」的半角假名（ｸﾙｯ/ﾍﾞｶﾞ/ｲｲ/ｲﾀﾀﾀﾀ 是真文本；
    # 散落在 AA 笔画里的孤立 ｲ/ﾆ/ﾉ 不算）。浊点/半浊点/长音是装饰，先剔除。
    for r in _ART_VETO_HK_RUN_RE.findall(l):
        r = ''.join(c for c in r if c not in _ART_VETO_HK_MOD)
        if len(r) < 2:
            continue
        if len(set(r)) >= 2:
            return True
        if r[0] not in ('ﾆ', 'ﾐ'):       # ﾆﾆﾆ/ﾐﾐﾐ 是 AA 墙；ｲｲ/ﾊﾊ 是台词笑声
            return True
    return False


def art_line_veto(l):
    """整行纯艺术笔画（无可译内容）→ True：本地照抄、不进模型。"""
    if HEADER_RE.match(l) or MARKER_RE.match(l):
        return False
    nz = [c for c in l if not c.isspace()]
    if len(nz) < 4:
        return False
    if HIRA.search(l):                       # 平假名（助词/送假名）= 台词最强信号
        return False
    for c in l:
        if '\u4e00' <= c <= '\u9fff' and c not in ART_KANJI:
            return False
    for c in "！？。" + _ART_VETO_BRACKETS:
        if c in l:
            return False
    if _art_veto_has_real_text(l):
        return False
    if _ART_VETO_WALL_RE.search(l):
        return True
    n = sum(1 for c in nz if c in _ART_VETO_KATA or c in ART_KANJI
            or c in _ART_VETO_NOISE or c in _ART_VETO_HK_SET)
    return n / len(nz) >= 0.5


# ---------------------------------------------------------------------------
# 主块 art 列锚点校验（2026-09-05）
# 背景: meta 判 kind=text 且无 segments 的「AA 图形+台词」行走主块整行翻译
#   (translate_lines)。弱模型(实测 dashscope qwen3.5-flash)翻译时压缩 art 字符间
#   空格/把图形整段推位 → 行尾 U+3000 硬垫凑宽 → 宽度==SRC(宽度校验抓不到)
#   但 art 列位错(渲染图歪)。translate_lines_mixed 的段级 art 校验对无 segments
#   行不生效 → 主块必须自校验(2026-09-05 entry_17808 14 行错位事故)。
# 原理: 模型只能改「台词区」文字, 不该移动图形笔画。取 SRC 中 art 图形字符
#   (排除假名/汉字/日文标点——那些可能是台词)的 (字符, 显示列) 锚点, 要求候选
#   中同字符出现在相近列(容差 tol, 防半角/全角空格混用的 ±1-2 列历史差异误伤)。
#   保守启用: 仅当 SRC art 锚点数 >= min_art 才校验(纯文本行直接过)。
# ---------------------------------------------------------------------------
# 图形笔画字符(AA 作画用; 台词常用标点 !?() 不列入避免误伤中文台词)
_ART_STROKE_CHARS = set(
    '／＼│｜┃━─┌┐└┘├┤┬┴┼＿￣|()[]（）ｰ'
    '⊂⊃ミ彡乂爻≧≦ﾆﾄﾘ:;,.。ヽﾉゝゞノニマハレヘ'
    '〆卍§※＊☆★○●◎◇◆□■△▲▽▼'
    '=<>^vV~-+xX'
)


def _is_dialogue_char(c):
    """台词字符(假名/汉字/日文标点)——不是 art 图形笔画。"""
    return ('\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9fff'
            or c in '。、…「」『』！？～×※・—')


def _art_anchors(line):
    """返回行内 art 图形字符的 (字符, 显示列) 列表。空白跳过, 台词字符不算。"""
    out = []
    col = 0
    for c in line:
        if not c.isspace() and c in _ART_STROKE_CHARS and not _is_dialogue_char(c):
            out.append((c, col))
        col += 1 if ord(c) < 128 else 2
    return out


def art_stroke_preserved(orig_line, cand_line, tol=4, min_art=3):
    """主块整行翻译后 art 列锚点守恒校验。

    返回 True=通过, False=模型移动/删改了图形笔画(应重试/落人工, 勿写盘)。
    候选 art 锚点按字符分组记录全部列位, SRC 每个锚点在候选找同字符
    列位差 <= tol 的匹配(容差吸收半角/全角空格混用历史差异)。
    保守: SRC art 锚点数 < min_art 视为纯文本/弱图形行, 不校验。
    (2026-09-05 entry_17808 实测: min_art=3 tol=4 抓 11/14 坏行, 修好行 0 误判,
    全章 913 行仅 1 误伤——漏网的均为 art 字符极少/破坏在台词区边缘的轻度行)
    """
    a_src = _art_anchors(orig_line)
    if len(a_src) < min_art:
        return True
    a_cand = {}
    for c, col in _art_anchors(cand_line):
        a_cand.setdefault(c, []).append(col)
    for c, col in a_src:
        cols = a_cand.get(c)
        if not cols:
            return False
        if not any(abs(col - cc) <= tol for cc in cols):
            return False
    return True


# ---------------------------------------------------------------------------
# 压缩文本包：.txt / .txt.gz 透明读写（2026-09-05）
# 阅读界面读压缩文本包省磁盘：读 .txt 时若同基名 .txt.gz 存在则透明读（文本 gzip ~78%）。
# 文件仍按 基名.txt 或 基名.txt.gz 命名，行内容/行号/宽度不变式不受压缩影响。
# 供 logic.py(阅读器/矫正) 与管线共用——统一 import 勿复制。
# ---------------------------------------------------------------------------
def txt_key(fname):
    """entry_1.txt / entry_1.txt.gz / entry_1_汉化.txt.gz -> entry_1 / entry_1_汉化。"""
    for s in (".txt.gz", ".txt"):
        if fname.endswith(s):
            return fname[: -len(s)]
    return fname


def txt_existing(path):
    """给定 .txt 基路径，返回实际存在者：path 或 path+'.gz'；皆无则原 path。"""
    if os.path.exists(path):
        return path
    gz = path + ".gz"
    if os.path.exists(gz):
        return gz
    return path


def txt_read(path):
    """读 .txt 或 .txt.gz 为行列表（剥 CR）。压缩文本包透明读。

    - path 直接以 .gz 结尾：按 .gz 读
    - path 基 .txt 存在：按 .txt 读
    - path 基 .txt 不存在但 path+'.gz' 存在：按 .gz 读（透明）
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


# 行内换行护栏（2026-09-22 entry_8332/entry_7578 行号错位事故）：写盘的「行」若含换行符，
# "\n".join 会把 1 行拆成 2 行 → 全章行号右移（末几行读不到数据，工作台报「行数据加载失败」）。
# 根因：模型译文折行被段级重建拼回一行（refine_aa 的 swallow/poly 路径）。
BREAK_RE = re.compile(r"[\r\n\u2028\u2029\v\f]+")


def one_line(s):
    """压掉行内换行/行分隔符（模型输出折行是行号错位事故的根因）。"""
    return BREAK_RE.sub("", s or "")


# 同类备份目录（backup_<kind>_<时间戳>/）每个作品只保留最近 N 次运行（2026-10-01 用户指定）。
BACKUP_KEEP_PER_KIND = 2


def prune_backup_dirs(work_dir, prefix, keep=None, quiet=False):
    """只保留最近 `keep` 个 `prefix*` 备份目录，更早的整目录删除。返回 (删除数, 释放字节)。

    备份目录名 = prefix + 零填充时间戳（`%Y%m%d%H%M%S` / `%Y%m%d_%H%M%S`）→ 名字降序即时间
    降序；同秒重跑同名目录天然覆盖。**只删目录、只删匹配 prefix 的、绝不碰 keep 之内的**
    （含刚建的那个），所以调用方在自己建完备份目录后立即调用是安全的。
    """
    import glob as _glob
    import shutil as _shutil
    keep = BACKUP_KEEP_PER_KIND if keep is None else int(keep)
    ds = sorted(_glob.glob(os.path.join(work_dir, prefix + "*")), reverse=True)
    n = 0
    freed = 0
    for d in ds[max(0, keep):]:
        if not os.path.isdir(d):
            continue
        sz = 0
        for dp, _dn, fn in os.walk(d):
            for f in fn:
                try:
                    sz += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
        try:
            _shutil.rmtree(d)
        except OSError:
            continue
        n += 1
        freed += sz
    if n and not quiet:
        print("[backup] 清理 %d 个旧备份目录（%s*，保留最近 %d 次，释放 %.1f MB）"
              % (n, prefix, keep, freed / 1048576.0), flush=True)
    return n, freed


def txt_write(path, lines):
    """写 .txt 或 .txt.gz（\n 连接；.gz 走 gzip）。产物同款 CRLF。

    行内换行护栏：含换行的行先压平并告警（否则 1 行写成 2 行 → 全章行号错位）。
    """
    _bad = [i for i, l in enumerate(lines) if BREAK_RE.search(l or "")]
    if _bad:
        print("[txt_write] WARN %d 行含换行符(已压平, 行首 %s), 防行号错位: %s"
              % (len(_bad), ",".join(str(i + 1) for i in _bad[:5]), path), file=sys.stderr)
        lines = [one_line(l) for l in lines]
    data = "\n".join(lines)
    if path.endswith(".gz"):
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(data)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)


# ---------------------------------------------------------------------------
# M4a: line_meta 驱动的翻译范围控制
# 依据审阅#1 行号口径抽查：meta 为 1-based 物理行号(从3起,前2行为#标题/#地址)，
# 生产 enumerate(lines) 为 0-based → load 时统一 int(k)-1 转 0-based。
# feature flag: AA_USE_LINE_META=1 启用；meta 缺失/损坏/idx越界 → fallback is_text_line。
# kind 语义：art 硬不进模型 / text 必进 / mixed 走 is_text_line / header/blank 走旧逻辑。
# ---------------------------------------------------------------------------
def load_meta_0based(meta_dir, base):
    """加载 <meta_dir>/<base>.json → {0-based idx: kind}。
    兼容两种 meta 格式：{行号: "kind"} 或 {行号: {"kind":...,"confidence":...}}。
    文件缺失/损坏返回 None（调用方 fallback is_text_line）。
    2026-08-18 反哺⑨: 额外提取 reason → {0-based idx: (kind, reason_str)}——
    mixed 行用 reason 判断是否含真台词(no_dialogue=纯画面不进模型)。"""
    path = os.path.join(meta_dir, base + '.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:
        return None
    out = {}
    for k, v in raw.items():
        try:
            idx = int(k) - 1
        except (ValueError, TypeError):
            continue
        if isinstance(v, dict):
            kind = v.get('kind')
            reason = ' '.join(v.get('reason') or []) if isinstance(v.get('reason'), list) else str(v.get('reason') or '')
        else:
            kind = v
            reason = ''
        if idx >= 0:
            out[idx] = (kind, reason)
    return out


def is_translatable(idx, line, meta):
    """line_meta 驱动的单行判定。meta 为 None 或 idx 不在映射 → fallback is_text_line。
    meta[idx] 兼容两种: (kind, reason) 元组(2026-08-18 反哺⑨) 或 kind 字符串。

    2026-10-08: 先过 `art_line_veto`（整行纯艺术笔画 → 本地照抄不进模型，
    不论 meta 说是 text 还是 mixed；AA_ART_VETO=0 关闭）。
    """
    if art_veto_enabled() and art_line_veto(line):
        return False
    if meta is not None and idx in meta:
        entry = meta[idx]
        if isinstance(entry, tuple):
            kind, reason = entry
        else:
            kind, reason = entry, ''
        if kind == 'art':
            # 防御（终审#5）：分类器误把含真实台词的 art 行判为艺术——若含连续 3+ 非艺术假名，
            # 强制回退 is_text_line（避免漏译）。纯艺术纹理（kana_runs 已排除艺术假名）不受影响。
            for run in kana_runs(line):
                if len(run) >= 3:
                    return is_text_line(line)
            return False              # 硬不进模型
        if kind == 'text':
            # 2026-09-15 P2-a: "text" 行也可能整行无日语（纯符号/ASCII 艺术被误标）——
            # 实测这类行占**发模型字符 21.3%**，送过去只会让模型原样复述（白烧输出 token）。
            # 本地照抄即可（无假名、无非艺术汉字 = 没有可译内容）。
            if skip_non_jp_enabled() and no_japanese(line):
                return False
            return True               # 必进模型
        if kind == 'mixed':
            # 反哺⑨(2026-08-18): mixed 行用 reason 判断——no_dialogue=纯画面(画面误标)
            # 不进模型; 其余(lz_mixed/lz_dotted/lz_text_lt5等)=有台词,进(按段译)
            if 'no_dialogue' in reason:
                return False
            return is_text_line(line)  # 保守，走旧逻辑
        # header/blank/未知 kind → 走旧逻辑（header/blank is_text_line 本就 False）
    return is_text_line(line)

# ---------------------------------------------------------------------------
# 终审#3：feature flag 翻转语义
# 之前：AA_USE_LINE_META=1 / AA_USE_GLOSSARY=1 显式开启（默认关）。
# 现在：默认开启，显式设 "0" 关闭（回滚开关）。meta/glossary 缺失自动 fallback。
# 统一 helper，避免三处重复判断。
# ---------------------------------------------------------------------------
def _flag_on(name, default_on=True):
    v = os.environ.get(name)
    if v is None:
        return default_on
    return v not in ("0", "false", "False", "")

def line_meta_enabled():
    """line_meta 是否启用（默认开，显式 AA_USE_LINE_META=0 关闭）。"""
    return _flag_on("AA_USE_LINE_META")

def glossary_enabled():
    """glossary 术语前移是否启用（默认开，显式 AA_USE_GLOSSARY=0 关闭）。"""
    return _flag_on("AA_USE_GLOSSARY")


def tm_enabled():
    """TM（翻译记忆）复用是否启用（2026-09-15 P3）。

    默认**关**；`AA_USE_TM=1` 打开（真机 A/B 验收通过后再考虑默认开）。
    见 doc/方案归档/token成本优化方案_2026-09-15.md §9（实测：归一化整行 key 覆盖 21.1% 送模型行、护栏通过 99.2%）。
    """
    try:
        _cd = os.path.join(_THIS_DIR, "common")
        if _cd not in sys.path:
            sys.path.insert(0, _cd)
        import aa_tm as _TM
        return _TM.enabled()
    except Exception:
        return False

SYSTEM = """你是やる夫AA漫画的资深汉化翻译。用户会发来若干行AA漫画文本，每行以「序号|」开头。这些行【全部】包含日语文字，需要你翻译。

要求：
1. 把每一行的日语翻译成简体中文：自然、贴切、符合漫画语气，不增删内容。
2. 行内“绘画”字符必须原样保留：全角空格U+3000、边框（│｜／＼＿￣┌┐└┘など）、半角片假名（ﾉﾐﾑﾊﾞｯｽｽﾄなど）、ASCII艺术字符——都是画面的一部分，不可改动。
3. 翻译后的行必须与原行保持完全相同的显示宽度（ASCII半角占1格，其他占2格）。译文变短时用全角空格U+3000填充原文字区域（气泡/文本框内），保持边框位置。
4. 【关键】译文必须比原文日语文字更短或等长。若译文太长放不进原文字区域，必须精简措辞。禁止译文后面残留任何原文文字——原文文字必须被译文【完整替换】，绝不能把译文追加在原文旁边。
5. 角色名、专有名词保留日文原文（如「白頭」「灰かぶりの魔女」以及 キング、ほむら、オリジナル、キュゥべえ 等角色名）。
6. 行首尾装饰（＊｡･ﾟ･｡ 或 ……… 或 !!!）原样保留；「」『』引号与、。！？…翻译为中文「」与，。！？……
7. 必须翻译【所有】给出的行，一条都不能漏。如果某行其实没有日语文字（纯绘画），就原样照抄输出。
8. 输出格式：每行「序号|翻译后的完整行」，一行一条，不要输出任何其他内容。

示例：
输入:
0017|　　　　｜　こんにちは、お元気ですか？　｜
0019|やっぱり俺は魔女になるしかないのか……
0021|　　　　｜　名前は？　どこからきたの？　｜
输出:
0017|　　　　｜　你好，你还好吗？　　　　　｜
0019|果然我只有成为魔女这一条路可走吗……
0021|　　　　｜　你叫什么名字？从哪里来的？　　　　　　｜
（0021 的错误反例：『｜　你从哪里来的？　名前は？　どこからきたの？　｜』——原文残留必须彻底替换，禁止追加在译文后面。）"""

def call_api(system, body, max_tokens=MAX_OUT_TOKENS, timeout=400, work_r18=None, low_r18=False):
    # 2026-09-15 P2-b: 所有 system 变体(SYSTEM/SYSTEM_MIXED/FIX_MIXED_SYSTEM/补译)最前拼同一段
    # 公共前缀 → 跨类型调用互相吃 DeepSeek 前缀缓存（实测同前缀命中 79~89%，分叉则≈0）。
    # **只在会缓存的端点(官网 deepseek)上拼**——网关不缓存(实测 0/0/0), 拼了只是白加 ~200 token。
    # 单一入口处拼接, 各调用点无需改动; AA_COMMON_PREFIX=0 全局关闭。
    try:
        import aa_prompt_prefix as _PP
        system = _PP.apply(system, _PP.current_cache_backend(work_r18))
    except Exception:
        pass
    # AA_BACKEND 切换后端：siliconflow=限免(默认) / openrouter=免费兜底 / ollama=本地(已停用) / 默认云端
    # 与 common/aa_api.py 对称（AGENTS.md 铁律：改模型须两处对称）
    # 2026-08-19 AA_R18 模式: R18 作品设 AA_R18=1, 当前后端 r18 不安全则直接抛错(防白烧重试)
    # 2026-08-24 work_r18 显式参数: 章节级 R18 判定驱动后端——None=读 AA_R18 env(旧行为),
    #   True/False 显式指定(翻译主流程/清理按每章 prep 判定传入, 多章并行安全)
    if work_r18 is None:
        work_r18 = os.environ.get("AA_R18") in ("1", "true", "True")
    if work_r18:
        try:
            sys.path.insert(0, os.path.join(_THIS_DIR, "common"))
            import aa_backend as _AB
            _b = _AB.backend()
            if not _CFG.r18_safe(_b):
                raise RuntimeError(
                    f"AA_R18=1 但当前后端 {_b} 的 r18=false(有内容审查)——"
                    f"R18 作品请用 commandcode/siliconflow/qwen-vl, 见 model_config.toml [backend.{_b}]")
        except RuntimeError:
            raise
        except Exception:
            pass   # 后端探测失败不阻塞(保守放行)
    try:
        sys.path.insert(0, os.path.join(_THIS_DIR, "common"))
        import aa_backend
        b = aa_backend.backend()
        # 2026-08-26 免费模式: 即使 AA_BACKEND 未设(cloud)也强制走 call_with_fallback
        # (免费链 + 无限重试); 否则 cloud 分支会走付费云端直接模式。
        _free_mode = os.environ.get("AA_FREE_MODE") in ("1", "true", "True")
        if b != "cloud" or _free_mode:
            # 2026-08-22 自动降级：转发给 aa_backend.call_with_fallback——
            # 从配置链(或显式 AA_BACKEND)开始, 某后端 429/截断/R18被拒/空响应 时自动切下一候选。
            # 各候选 model 由 call_with_fallback 内 _model_for_backend 按 AA_*_MODEL env 覆盖。
            # work_r18 已在函数开头解析(参数 > env)。low_r18 表示低含量 R18 yes 章——忽略显式 AA_BACKEND。
            text, _fb = aa_backend.call_with_fallback(
                work_r18, system, body, max_tokens=max_tokens, timeout=timeout, low_r18=low_r18)
            return text
        # b=="cloud"(AA_BACKEND 未设) → 落下方云端直接模式(走 gateway/model/key, 含 proxy 重试)
    except ImportError:
        pass   # 缺 common/aa_backend 时回落云端（老环境兼容）
    gw = _gateway()
    if "chat/completions" in gw:
        # chat 格式(OpenAI 兼容端点): 不带 reasoning_effort——commandcode/mimo 等不接受该参数,
        # 需要关推理的后端(siliconflow 等)显式设 AA_BACKEND 走 aa_backend.call_* 
        payload = {
            "model": _model(), "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": body}],
            "max_tokens": max_tokens, "temperature": TEMPERATURE,
        }
    else:
        payload = {
            "model": _model(), "instructions": system, "input": body,
            "max_output_tokens": max_tokens, "temperature": TEMPERATURE,
            "reasoning": {"effort": "none"},
        }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # T4.1(2026-08-18): 每次 attempt 只试一种网络路径（避免每轮 2 种路径产生 2 倍无效请求）
    # 顺序: 直连→直连(退避)→代理→代理(退避)→直连; AA_USE_PROXY=0/1 强制单路径
    # 2026-10-08 阶段 4: 策略统一走 aa_config.use_proxy_mode()（env AA_USE_PROXY > TOML [default].use_proxy），
    #   使「设置页关掉代理」对 CLI 子进程同样生效。
    last_err = None
    try:
        force = _CFG.use_proxy_mode()
    except Exception:
        force = (os.environ.get("AA_USE_PROXY") or "auto").strip().lower() or "auto"
    proxy_seq = [False, False, True, True, False]
    if force == "0":
        proxy_seq = [False] * 5
    elif force == "1":
        proxy_seq = [True] * 5
    for attempt in range(5):
        use_proxy = proxy_seq[attempt]
        try:
            if use_proxy:
                op = _CFG.proxy_opener(force=True)   # 代理轮次：地址取 AA_PROXY/TOML
                req = urllib.request.Request(_gateway(), data=data, headers={
                    "Authorization": "Bearer " + get_key(), "Content-Type": "application/json", "User-Agent": UA})
                with op.open(req, timeout=timeout) as r:
                    resp = json.load(r)
            else:
                req = urllib.request.Request(_gateway(), data=data, headers={
                    "Authorization": "Bearer " + get_key(), "Content-Type": "application/json", "User-Agent": UA})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    resp = json.load(r)
            if resp.get("status") == "incomplete":
                raise RuntimeError("truncated: " + json.dumps(resp.get("incomplete_details"), ensure_ascii=False))
            out = _extract_text(resp)
            # M5 指标采集：AA_USAGE_FILE 环境变量指向 JSONL 时逐调用落盘
            u = resp.get("usage", {}) or {}
            _log_usage(u.get("input_tokens") or u.get("prompt_tokens") or 0,
                       u.get("output_tokens") or u.get("completion_tokens") or 0,
                       u=u, model=_CFG.get("default", "model"), backend="cloud")
            return out
        except Exception as e:
            last_err = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"API failed after retries: {last_err}")

def parse_result(text):
    res = {}
    for line in text.splitlines():
        m = re.match(r'^(\d{1,5})\|(.*)$', line)
        if m:
            res[int(m.group(1))] = m.group(2)
    return res

def pad_to_width(line, target):
    w = disp_width(line)
    if w == target:
        return line
    if w < target:
        need = target - w
        full, half = divmod(need, 2)
        pad = '\u3000' * full + (' ' if half else '')
        stripped = line.rstrip()
        if stripped and stripped[-1] in BOX_END and line != stripped:
            return stripped[:-1] + pad + stripped[-1] + line[len(stripped):]
        return line + pad
    return line

# M4b: 模型原样照抄日语行检测（覆盖事故根因之一）。
# 限定：原文含可翻译假名 且 译文与原文逐字符一致 → 判失败（模型假装完成）。
# 角色名/纯艺术行本就保留日文，但那些行不经过本校验（art 不进模型；角色名在文本行内是正常保留，
# 此处通过「整行 TR==SRC」才判失败，不会误伤单片段保留）。
def is_copy_failure(src, tr):
    """src 含假名且 tr 与 src 逐字符一致 → 模型原样照抄，判失败。

    2026-08-28: 豁免纯半角片假名(ﾉﾐﾑﾊﾞｯ 等)——半角片假名行是艺术装饰(擬声/效果音特效)，
    原样保留是正确行为, 不算模型照抄失败(has_kana 含 \uff61-\uff9f 会误判)。
    """
    if not has_kana(src):
        return False
    # 纯半角片假名=艺术装饰, 不判 copy 失败
    nz = [c for c in src if not c.isspace()]
    if nz and all('\uff61' <= c <= '\uff9f' for c in nz):
        return False
    return tr.rstrip() == src.rstrip()

# M4b: glossary 术语前移进 pass1 提示词（PRD 4.4 点4）。
# 目的：让模型 pass1 直接输出标准中文译名，避免后处理子串替换 re-pad 改变宽度引入覆盖事故。
# 受 AA_USE_GLOSSARY=1 控制（默认 0 不改变现有行为）。角色名保留日文优先级仍高于术语统一
# （SYSTEM 第5条），故注入时措辞为「若该词在本作是角色名则保留日文，否则用标准译名」。
# M4b: glossary 术语前移进 pass1 提示词（PRD 4.4 点4）。
# 目的：让模型 pass1 直接输出标准中文译名，避免后处理子串替换 re-pad 改变宽度引入覆盖事故。
# 受 AA_USE_GLOSSARY 控制（默认开）。2026-08-21 落地两项（doc/翻译模型对比_2026-08-21.md）：
#   ① 措辞去豁免句：旧措辞「若该词是角色名则保留日文」让 muse/glm-4.5-air 对角色名词汇免疫，
#      实测新措辞（角色名也要用标准译名）使 muse 白頭一致率 0->14/14；
#   ② 按作品过滤：不再一次性注入全部 38 对（带无关词噪音），改按模块级 AA_DIR 语料判定
#      只注入本作出现过的术语对（省 token、零质量损失）。
# _glossary_pairs_cache 显式赋值可整体覆盖（测试/实验用）；None = 自动按作品过滤。
_glossary_pairs_cache = None          # 显式覆盖用；None = 自动按作品过滤
_glossary_work_cache = {}             # {(aa_dir, json_path): 过滤后术语对}
_glossary_raw_cache = {}              # {json_path: 全量术语对}

def _raw_glossary_pairs():
    """glossary.json 全部替换对（不过滤）。以 json 路径为键缓存；损坏返回空列表。

    2026-09-01: 兼容对象格式 [{jp,zh,cat}]（新 schema v2）与旧二元组 [jp,zh]。
    """
    p = _GLOSSARY_JSON
    if p in _glossary_raw_cache:
        return _glossary_raw_cache[p]
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        pairs = []
        for x in data.get("glossary", []):
            if isinstance(x, list) and len(x) == 2:
                pairs.append(tuple(x))
            elif isinstance(x, dict) and x.get("jp") and x.get("zh"):
                pairs.append((x["jp"], x["zh"]))
    except Exception:
        pairs = []
    _glossary_raw_cache[p] = pairs
    return pairs

def _load_role_pairs():
    """roles.json 角色表 → 替换对 (日文→中文)。缺失返回空列表。

    2026-09-01: 角色名对迁出 glossary.json → roles.json。读角色块全部条目。
    别名(别名列表)不生成替换对——替换对只认 日文→中文 主名(防别名误伤)。
    """
    try:
        with open(_ROLES_JSON, encoding="utf-8") as f:
            data = json.load(f)
        pairs = []
        for r in data.get("角色", []):
            jp = r.get("日文")
            zh = r.get("中文")
            if jp and zh:
                pairs.append((jp, zh))
        return pairs
    except Exception:
        return []

def _work_glossary_entries():
    """当前作品术语表条目列表 [{jp,zh,cat,来源}]。

    2026-09-07 用户规则: 翻译/校对只用作品术语表(glossary_<work>.json), 不读全局 glossary;
    表缺失 → raise SystemExit 中断提醒(硬前置, 无表不静默翻译)。缺文件不现场生成
    (术语表只手动触发, 2026-09-06 用户规则)。仅 WORK_ID 为空(legacy 旧模式一次性脚本)
    时保留全局回退(不设作品概念, 不受新规则约束)。
    """
    if not WORK_ID:
        # legacy 旧模式(一次性修复脚本, 无作品概念): 用全局 glossary(旧行为)
        return _raw_glossary_entries()
    try:
        _wg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # scripts/
        _wg_common = os.path.join(_wg_dir, "common")
        if _wg_common not in sys.path:
            sys.path.insert(0, _wg_common)
        import gen_work_glossary as _GWG
        import registry as _R
        cfg = _R.get(WORK_ID)
        if not cfg:
            raise SystemExit(
                f"[glossary] 作品 {WORK_ID} 不在 registry——翻译/校对必须走作品术语表, 无法继续")
        wdir = cfg["work_dir"]
        path = _GWG.work_glossary_path(wdir, WORK_ID)
        if not os.path.exists(path):
            raise SystemExit(
                f"[glossary] 无作品术语表 {path}\n"
                f"  术语表是翻译/校对硬前置(只读作品表, 不读全局)。请先手动生成:\n"
                f"  ① 快速派生(零 token, 从全局角色表派生 auto): python scripts/common/gen_work_glossary.py --work {WORK_ID} --force\n"
                f"  ② 完整流程(工作台 work 页「生成术语候选…」→ 候选确认 → 写入作品表;\n"
                f"     或 CLI: python scripts/common/gen_glossary_ai.py --work {WORK_ID} --full)")
        manual, auto, _, _ = _GWG.read_work_glossary(path)
        return manual + auto
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(
            f"[glossary] 作品术语表读取失败({WORK_ID})——翻译/校对硬前置, 无法继续")


def _raw_glossary_entries():
    """全局 glossary.json 条目 → [{jp,zh,cat,来源}]。"""
    try:
        with open(_GLOSSARY_JSON, encoding="utf-8") as f:
            data = json.load(f)
        out = []
        for x in data.get("glossary", []):
            if isinstance(x, dict):
                out.append({"jp": x.get("jp"), "zh": x.get("zh"),
                            "cat": x.get("cat", ""), "来源": x.get("来源", "manual")})
            elif isinstance(x, list) and len(x) == 2:
                out.append({"jp": x[0], "zh": x[1], "cat": "", "来源": "manual"})
        return [e for e in out if e["jp"] and e["zh"]]
    except Exception:
        return []


def _load_glossary_pairs(base=None):
    """返回要注入的术语对（已按注入语义过滤）。

    注入语义 (2026-09-01 三级重构):
      - manual 对 + 常用对(cat 含「常用」): 全量注入, 不过滤
      - auto 的特殊/角色对: 按 base 语料二次过滤(命中才注入)
    base(2026-08-27): 章节 base(entry_<eid>), 按章语料过滤。
    显式 _glossary_pairs_cache 覆盖优先(测试/实验用)。
    """
    if _glossary_pairs_cache is not None:
        return _glossary_pairs_cache

    entries = _work_glossary_entries()
    if not entries:
        return []
    full, filt = [], []
    for e in entries:
        jp, zh = e.get("jp"), e.get("zh")
        if not jp or not zh:
            continue
        cat = e.get("cat", "")
        src = e.get("来源", "manual")
        # 2026-09-01: auto 角色对 zh==jp(未人工确认译名) 不注入——只展示供确认,
        # 注入「アクア→アクア」= 教模型保持日文, 浪费 token 且非标准译名语义
        if src == "auto" and "角色" in cat and zh == jp:
            continue
        if src == "manual" or "常用" in cat:
            full.append((jp, zh))
        else:
            filt.append((jp, zh, cat))
    if base is None:
        # 无章节: 全部注入(不过滤)
        return full + [(jp, zh) for jp, zh, _ in filt]

    # 按章过滤 auto 特殊/角色对
    corpus = ""
    try:
        fn = os.path.join(AA_DIR, base + ".txt")
        if os.path.exists(fn):
            with open(fn, encoding="utf-8", errors="replace") as _f:
                corpus = _f.read()
    except OSError:
        pass
    if corpus:
        hits = [(jp, zh) for jp, zh, _ in filt if jp in corpus or zh in corpus]
        return full + hits
    return full + [(jp, zh) for jp, zh, _ in filt]

_POKEMON_PIPE_CACHE = {}


def _pokemon_pipeline(work=None):
    """当前作品是否走宝可梦专属链（work_meta.pipeline == "pokemon"）。

    用途：该链的术语一致性由**翻译前强制替换**（pre_terms）保证，故术语对不再注入 prompt。
    缓存按 work id（WORK_ID 可能在 import 之后才被 runner 设上，故不能只缓一个布尔）。
    """
    w = work or WORK_ID
    if not w:
        return False
    if w in _POKEMON_PIPE_CACHE:
        return _POKEMON_PIPE_CACHE[w]
    v = False
    try:
        _cd = os.path.join(os.path.dirname(os.path.abspath(__file__)), "common")
        if _cd not in sys.path:
            sys.path.insert(0, _cd)
        import work_pipeline as _WP
        v = (_WP.pipeline_of(w) == "pokemon")
    except Exception:
        v = False
    _POKEMON_PIPE_CACHE[w] = v
    return v


def glossary_prompt_hint(base=None):
    """生成注入提示词的术语说明；flag 未开返回空串。

    base(2026-08-27): 章节 base(entry_<eid>)。传入时:
      - 作品知识库注入带本章章节简介(memory_hint with_summary)
      - 术语过滤降级为按本章语料(_filter_glossary_pairs base 参数)
    显式参数穿透(禁模块级全局): AA_CHAPTER_WORKERS 多章并行时各线程独立 base。
    2026-08-27 审核修: 术语对为空时不再提前 return——作品知识库(术语/人物/章节简介)仍需注入
    (按章过滤后空结果概率上升, 原实现把知识库整体吞掉)。
    """
    if not glossary_enabled():
        return ""
    pairs = _load_glossary_pairs(base)
    # 2026-08-25: 正则术语(re:开头)只用于本地替换, 不注入 prompt(模型看不懂正则)
    pairs = [p for p in pairs if not p[0].startswith("re:")]
    # 2026-10-08（宝可梦专属链）: 术语一致性已由**翻译前强制替换**（pre_terms）保证 ——
    # 预替换过的标准译名再注入一遍，只会留下"模型把已替换好的中文再改一次"的通道，
    # 且每次调用白发 ~1.6k token。故 pokemon 管线**停术语对注入**；
    # 【通用译名规则】（さん→桑）与知识库的语境/角色译名/章节简介**保留**（那是上下文不是术语表）。
    if _pokemon_pipeline():
        pairs = []
    out = ""
    if pairs:
        lines = "、".join(f"{jp}→{zh}" for jp, zh in pairs)
        out += (f"\n【术语表】以下词汇是本作的标准译名，翻译时【必须】使用列出的标准中文译名进行替换"
                f"（这个要求优先级最高，即使该词是角色名也要用标准译名，不要保留日文原文）：{lines}\n")
    if pairs or _pokemon_pipeline():
        # 2026-10-08: 这条通用规则**不随术语对一起停**（宝可梦链停术语表但保留它）；
        # 非宝可梦作品无术语表时行为不变（仍不加）。
        out += ("\n【通用译名规则】日语人名/称呼后的敬语「さん」译为「桑」（如 やる夫さん→やる夫桑），"
                "不要译为「先生」；「先生」仅用于教师/医生等真实含义。\n")
    # 2026-08-25: 作品记忆注入(校对积累的固定译法/语境/人物) + 2026-08-27 章节简介
    try:
        if WORK_ID:
            _td = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
            if _td not in sys.path:
                sys.path.insert(0, _td)
            import work_memory as _WM
            _mh = _WM.memory_hint(WORK_ID, base=base, with_terms=not _pokemon_pipeline())
            if _mh:
                out += _mh
    except Exception:
        pass
    return out

# ---------------------------------------------------------------------------
# 翻译前术语强制替换钩子（2026-09-22 · 宝可梦专属链）
#   背景：把 wiki/术语表条目注入 prompt 的做法实测无效（同词多种译法照旧出现）。
#   改为翻译前**强制替换**——只在"送模型的那份文本"上把术语换成标准译名，
#   原文文件、宽度 pad、art 列位校验、残留检测一律仍用**原行**（故零宽度风险）。
#   实现在 scripts/common/pre_terms.py；env AA_PRE_TERMS=0 关闭；非 pokemon 管线自动不生效。
# ---------------------------------------------------------------------------
_PRE_TERM_FN = None


def set_pre_term_fn(fn):
    """注册/注销翻译前替换函数 fn(text, base) -> text（None = 关闭）。测试/实验用。"""
    global _PRE_TERM_FN
    _PRE_TERM_FN = fn


def _pt(text, base=None):
    """送模型前对单行/单段文本做术语替换；未注册或异常一律原样返回（绝不阻断翻译）。"""
    fn = _PRE_TERM_FN
    if fn is None or not text:
        return text
    try:
        out = fn(text, base, WORK_ID)
        if os.environ.get("AA_PRE_TERMS_DEBUG", "").strip() == "1":
            print("[pre_terms-debug] base=%s changed=%s\n  in : %s\n  out: %s"
                  % (base, out != text, text[:90], (out or "")[:90]), flush=True)
        return out if isinstance(out, str) and out else text
    except Exception as e:
        if os.environ.get("AA_PRE_TERMS_DEBUG", "").strip() == "1":
            print("[pre_terms-debug] 异常 %s: %s" % (type(e).__name__, e), flush=True)
        return text


def _install_pre_terms():
    """按需装载 pre_terms（AA_PRE_TERMS=0 关闭；是否生效由该模块按管线判定）。

    必须自己把 `scripts/common` 塞进 sys.path：本模块在 `scripts/` 下，pre_terms 在
    `scripts/common/` 下，CLI 直跑时 common 不一定在 path 里 → 曾经 ImportError 被
    `except` 静默吞掉，表现为"机制装了但整章零替换"（2026-09-22 连续两次端到端 smoke
    都没发现）。故导入失败必须打印，不允许静默。
    """
    if os.environ.get("AA_PRE_TERMS", "1").strip() == "0":
        return
    try:
        _c = os.path.join(os.path.dirname(os.path.abspath(__file__)), "common")
        if os.path.isdir(_c) and _c not in sys.path:
            sys.path.insert(0, _c)
        import pre_terms as _PT
        set_pre_term_fn(_PT.apply)
    except Exception as e:
        print("[pre_terms] 装载失败(%s: %s) → 本次不做翻译前术语替换"
              % (type(e).__name__, e), flush=True)


# **必须在 import 时执行**，且必须在本文件末尾的 `main()` 调用**之前**：
# 生产入口 translate_runner 的 `_run_translate` 直接 `python aa_translate.py --work ...`
# → main() 在模块体末尾被调用；把 _install_pre_terms() 放在 main() 之后 = 实际从未装载
# （用 AA_PRE_TERMS_DEBUG=1 打印 hook=None 才抓到；两次端到端 smoke 因此全章零替换）。
_install_pre_terms()


def translate_lines(flagged, work_r18=None, low_r18=False, context="", base=None):
    """flagged: list of (num, line). Returns (dict num->newline, missed_nums, warnings)
    校验：宽度保持 + 残留假名清零（白名单制），可疑行当场补译。
    context(2026-08-25): 前 1 楼台词上下文(治本——「鏡→眼镜」需结合前文场景)。
    base(2026-08-27): 章节 base(entry_<eid>), 穿透给 glossary_prompt_hint(章节简介+按章过滤)。"""
    body = "\n".join(f"{i:05d}|{_pt(l, base)}" for i, l in flagged)
    if context:
        body = "【上文场景】" + context + "\n" + body
    pending = {i: l for i, l in flagged}
    fixed = {}
    warnings = []
    # 2026-09-04: 模型↔行号——包 call_api, 让 usage 落盘带上本次翻的行号
    try:
        from aa_backend import set_usage_lines, clear_usage_lines
    except Exception:
        set_usage_lines = clear_usage_lines = None
    for round_ in range(3):
        if not pending:
            break
        if round_ > 0:
            body = "\n".join(f"{i:05d}|{_pt(l, base)}" for i, l in pending.items())
        # M4b: glossary 术语前移（flag 开时注入提示词）+ 2026-08-27 章节简介
        if set_usage_lines:
            # 2026-09-05: usage 行号对齐工作台阅读界面(1-based 文件行号)——
            # flagged 的行号是 0-based enumerate idx, 落盘前 +1。
            set_usage_lines(sorted(k + 1 for k in pending.keys()))
        try:
            text = call_api(SYSTEM + glossary_prompt_hint(base), body, work_r18=work_r18, low_r18=low_r18)
        finally:
            if clear_usage_lines:
                clear_usage_lines()
        res = parse_result(text)
        retry = {}   # num -> (orig_line, current_line, reason)
        for num, nl in res.items():
            if num in pending and num not in fixed:
                cand = pad_to_width(nl, disp_width(pending[num]))
                sus = residual_suspect(cand)
                w_ok = disp_width(cand) == disp_width(pending[num])
                # M4b: 模型原样照抄日语行判失败（覆盖事故根因）
                copy_fail = is_copy_failure(pending[num], cand)
                # 2026-09-05: 主块 art 列锚点校验(无 segments 的 AA 图形行整行翻译后
                #   art 被弱模型推位/压缩 → 宽度对但图歪)。失败 = 重试/落人工, 不写盘。
                art_ok = art_stroke_preserved(pending[num], cand)
                if sus or not w_ok or copy_fail or not art_ok:
                    reasons = list(sus)
                    if copy_fail:
                        reasons.append('原样照抄')
                    if not w_ok:
                        reasons.append(f'宽度不符({disp_width(pending[num])}->{disp_width(cand)})')
                    if not art_ok:
                        reasons.append('art列位不符')
                    retry[num] = (pending[num], cand, reasons)
                else:
                    fixed[num] = cand
        # missed: 原文含假名但模型未返回
        for i, l in pending.items():
            if i not in res and i not in fixed and has_kana(l):
                retry.setdefault(i, (l, l, ['未返回']))
        if retry:
            hint_lines = []
            for num, (orig_l, cur_l, sus) in retry.items():
                reason = '、'.join(sus) if sus != ['未返回'] else '未翻译'
                hint_lines.append(f"{num:05d}|{cur_l}\n问题：{reason}")
            hint_sys = SYSTEM + glossary_prompt_hint(base) + "\n\n【补译】以下行有残留日语或未翻译，必须把残留的日语翻译成中文并【完整替换】原片段（角色名可保留）：\n" + "\n\n".join(hint_lines)
            try:
                if set_usage_lines:
                    set_usage_lines(sorted(retry.keys()))
                try:
                    text2 = call_api(hint_sys, "\n".join(f"{num:05d}|{cur_l}" for num, (_, cur_l, _) in retry.items()), work_r18=work_r18, low_r18=low_r18)
                finally:
                    if clear_usage_lines:
                        clear_usage_lines()
                res2 = parse_result(text2)
                for num, nl in res2.items():
                    if num in retry and num not in fixed:
                        cand = pad_to_width(nl, disp_width(pending[num]))
                        if (not residual_suspect(cand) and disp_width(cand) == disp_width(pending[num])
                                and art_stroke_preserved(pending[num], cand)):
                            fixed[num] = cand
            except Exception as e:
                warnings.append(f"补译失败: {e}")
        pending = {i: l for i, l in pending.items() if i not in fixed}
    missed = [i for i in pending if has_kana(pending[i])]
    return fixed, missed, warnings


# ---------------------------------------------------------------------------
# B2+B3 (M10): mixed 行段级翻译 + 段级 pad
# feature flag: AA_USE_MIXED_SEGMENTS 默认开（3话实验达标 2026-08-11 翻 flag；显式 =0 关闭）。
# mixed 行 meta 含 segments 时：只发 conf>=0.65 的 text 段进模型（格式 行号_段号|译文段），
# art 段与低置信 text 段本地原样保留；段级 pad 补在段尾 → art 段列位置不变（框不歪）。
# ---------------------------------------------------------------------------
def mixed_segments_enabled():
    """mixed 段级翻译是否启用（2026-08-11 翻 flag：默认开；显式 AA_USE_MIXED_SEGMENTS=0 关闭）。"""
    return _flag_on("AA_USE_MIXED_SEGMENTS", default_on=True)


MIXED_SEG_RE = re.compile(r'^(\d{1,5})_(\d{1,3})\|(.*)$')


def load_meta_segments_0based(meta_dir, base):
    """加载 v3 meta 的 segments：{0-based idx: [seg,...]}；仅 mixed 行有。缺失返回 None。"""
    path = os.path.join(meta_dir, base + '.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:
        return None
    out = {}
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        segs = v.get('segments')
        if not segs:
            continue
        try:
            idx = int(k) - 1
        except (ValueError, TypeError):
            continue
        if idx >= 0:
            out[idx] = segs
    return out


# ---- 2026-09-15 P2-a: 规则式台词段（补 conf 门槛挡下的混合行）----
# 实测动机: 只有 meta 的 conf>=0.65 text 段才走段级协议（art 本地拼回），而不少作品的
# 章节 text 段**全是 0.4** → 整章退回整行协议、art 全部由模型复述（已交付译文里 61.3%
# 的字符是绘画/填充）。真 API A/B: 同 15 行 整行 654 tok vs 只发台词段 96 tok（-85%）。
SEG_RULE_RUN_RE = re.compile(r"[\u3041-\u3096\u309d-\u309e\u30a1-\u30fa\u30fd-\u30fe"
                             r"\u4e00-\u9fff\u3005\u30fc]+")
_HIRA_RE = re.compile(r"[\u3041-\u3096]")
_KANJI_RE = re.compile(r"[\u4e00-\u9fff]")


def skip_non_jp_enabled():
    """无日语行是否本地照抄、不送模型（2026-09-15 P2-a；默认开，AA_SKIP_NON_JP=0 关闭）。"""
    return _flag_on("AA_SKIP_NON_JP", default_on=True)


def no_japanese(line):
    """整行不含任何假名与（非艺术）汉字 → 无可译内容（纯符号/ASCII 艺术行）。"""
    if has_kana(line):
        return False
    for c in line:
        if '\u4e00' <= c <= '\u9fff' and c not in ART_KANJI:
            return False
    return True


def mixed_seg_rule_enabled():
    """规则式台词段是否启用（2026-09-15 P2-a）。

    **默认关**（AA_MIXED_SEG_RULE=1 才开），2026-09-15 结论：**不上线**。

    ① 质量：校验已补齐（抠段判据要求"决定性证据" → 小写假名/全艺术假名碎片不算台词；
       规则段严照抄检测 `_rule_seg_copy_fail`；含艺术假名的残留复查；规则行失败**回退整行协议**
       ——`fallback_todo` 此前是死代码，失败行会静默留在产品里）。两起事故行已确认不再进段级协议：
       `マっ！`（entry_25417#605 漏译）、画里 `ぅこ`（entry_25418#1044/#1081 被译成「呜」）。

    ② 成本：同环境 3 章 A/B（第101~103話，TM 关、skip-non-jp 开）规则开 **+2.1%**
       （¥0.3010 vs ¥0.2947）——接管行仅 ~2~3 行/章，而既有 `seg_shrink` 失败率高
       （607 次失败事件/3 章）导致重试开销吃掉"不发 art"省下的量。
       要上线须先解决 `seg_shrink` 过严（放宽阈值或按段宽比例判），详见
       `doc/方案归档/token成本优化方案_2026-09-15.md` 附「P2-a 规则台词段补校验」。
    """
    return _flag_on("AA_MIXED_SEG_RULE", default_on=False)


# 小写假名（拗音/促音/小字）——单独出现**不算**台词证据（2026-09-15 P2-a 补校验）。
_SMALL_KANA = set("ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹ")


def _rule_dialogue_run_ok(run):
    """run 是否像"真台词"：含**决定性假名**（非艺术假名、非小写假名），或含≥2 汉字且无相邻重复字符。

    2026-09-15 修正（P2-a 补校验，`AA_MIXED_SEG_RULE` 启用的前置条件）:
      旧判据「含任一平假名」会把**整段艺术假名/小写假名**的碎片当台词抠走，实测两起事故——
      ① `マっ！`（マ∈ART_KATA、っ∈ART_HIRA）被抠成段后模型原样照抄 → **漏译**
         （entry_25417#605；整行路径基线正确译作「来了！」）；
      ② `ぅこ`（ぅ 小写、こ∈ART_HIRA）嵌在绘画笔画 `ｆぅこ¨´_` 里，被抠成段译作「呜」→ **画面被改**
         （entry_25418#1044/#1081；基线 art 完好）。旧判据下段级残留检查也拦不住：
         `kana_runs`/`real_kana` 都按设计排除艺术假名，看不见这类段。
      修正 = 证据必须"决定性"：小写假名不给证据（`ぅこ` 全靠 ぅ 一条顶上来；`ヽへノ`/`ノし` 同理）。
      实测第101~103話: 抠段 534 → **559**（丢 17 段全是 art 碎片、增 42 段多为片假名词），
      两起事故行均回退整行协议（整行路径有 art 描边守恒校验，安全）。
    """
    if not run or len(run) < 2 or disp_width(run) < 4:
        return False
    # 2026-10-10 P1-1: 判据统一委托 aa_art_kana.seg_is_art（写死表 ∪ 按语料标定表），
    # 与选段层/校对层同一个函数——本作用 ア/イ/キ/ト/フ/ニ 当笔画，写死表把它们全当台词证据。
    return not seg_is_art(run)


def _rule_seg_copy_fail(orig_seg, cand, names=None):
    """规则段专用严格照抄检测（2026-09-15）：原样照抄 = 漏译，返回 True。

    常规段级照抄检测用 `real_kana`（排除艺术假名）判"段里有没有真台词"，
    对「整段艺术/小写假名」天然开后门；规则段不设此豁免——抠段判据已保证段内有决定性证据，
    因此**原样照抄一律判失败**（回退整行协议或落人工），唯一豁免是整段就是角色名（保留日文是正确行为）。
    """
    a = (cand or "").strip("\u3000 ")
    b = (orig_seg or "").strip("\u3000 ")
    if not a or a != b:
        return False
    pool = KNOWN_NAMES if not names else (set(KNOWN_NAMES) | set(names))
    return a not in pool


def mixed_rule_segments(line):
    """规则抠台词段 → **覆盖整行**的 segments（art 段按空档补齐），供段级协议使用。

    返回 [] 表示该行没有可靠的真台词段（调用方保持整行协议）。
    产出的 text 段**不带 confidence 字段**（`_confident_text_segs` 视为高置信）。
    为什么必须覆盖整行: `rebuild_mixed_line` 按 segments 顺序拼回，空档会丢字符；
    `_verify_art_alignment` 也依赖 art 段（含 src_width）做列位置校验。
    """
    runs = []
    for m in SEG_RULE_RUN_RE.finditer(line):
        if _rule_dialogue_run_ok(m.group()):
            runs.append((m.start(), m.end()))
    if not runs:
        return []
    segs = []
    cur = 0
    for st, en in runs:
        if st > cur:
            segs.append({"kind": "art", "start": cur, "end": st,
                         "src_width": disp_width(line[cur:st])})
        segs.append({"kind": "text", "start": st, "end": en,
                     "src_width": disp_width(line[st:en]), "rule": True})
        cur = en
    if cur < len(line):
        segs.append({"kind": "art", "start": cur, "end": len(line),
                     "src_width": disp_width(line[cur:])})
    return segs


def _confident_text_segs(segs):
    """conf>=0.65 的 text 段下标（v3 meta 仅低置信段带 confidence 字段，缺省视为高）。"""
    return [si for si, sg in enumerate(segs)
            if sg['kind'] == 'text' and sg.get('confidence', 1.0) >= 0.65]

# ---------------------------------------------------------------------------
# 2026-10-10 P1-1: 段级艺术判据 —— 统一到「按作品语料标定的艺术假名表」(common/aa_art_kana.py)
#   · seg_is_art          → Rule A：段内无可译内容（艺术笔画碎片）→ 本地照抄、不进模型
#   · art_keep_violation  → Rule B：表内艺术假名在译文里丢失 → 该段回滚照抄/重试
#   表按**作品语料**标定（tools/calibrate_art_kana.py，零 API；`--all` 出内置基线表），
#   缺表时自动退回写死的 ART_HIRA/ART_KATA/ART_KANJI；开关 AA_ART_KANA_* 可调/可关。
#   为什么**现场重算**而不读 meta：meta 由生成端产出（可能是两周前的旧文件），判据换代后旧
#   meta 不会自动更新 → 只有现场算才让修复立刻生效（bulk-pipeline-changes §一.2 的缓存时效）。
# ---------------------------------------------------------------------------
def _ak_mod():
    try:
        import aa_art_kana as _m
        return _m
    except Exception:
        return None


def art_kana_tab():
    """本作品的艺术假名比表（缺表 → {}，判据退回写死表）。"""
    m = _ak_mod()
    if m is None:
        return {}
    try:
        return m.ratios(work=WORK_ID or "", work_dir=WORK_DIR or "")
    except Exception:
        return {}


def seg_is_art(text):
    """该文本片段是否「没有可译内容」（艺术笔画碎片）——Rule A（选段层）。"""
    m = _ak_mod()
    if m is None or not text:
        return False
    try:
        return m.seg_is_art(text, art_kana_tab())
    except Exception:
        return False


def art_keep_violation(src_seg, cand):
    """译文是否丢掉了源段里的艺术假名（→ 该段回滚照抄）——Rule B。返回原因串或 ''。"""
    m = _ak_mod()
    if m is None or not src_seg or not cand:
        return ""
    try:
        return m.art_keep_violation(src_seg, cand, art_kana_tab())
    except Exception:
        return ""


def _sendable_text_segs(line, segs):
    """该行**该送模型**的 text 段下标（conf>=0.65 ∩ 非艺术片段）——单一权威（2026-10-10）。

    「要不要把这段发进模型」的决策点全部走这里（选段 / 拼回校验 / fix_width 段级 / 行级分流），
    否则修一处漏一处（bulk-pipeline-changes §一.3）。
    """
    out = []
    for si in _confident_text_segs(segs):
        sg = segs[si]
        if seg_is_art(line[sg['start']:sg['end']]):
            continue
        out.append(si)
    return out


def pad_segment(translated, seg_width):
    """B3: 将译文段 pad 到目标宽度，U+3000 补在段尾（下一个 art 段之前，不改变 art 段列位置）。"""
    w = disp_width(translated)
    if w >= seg_width:
        return translated
    need = seg_width - w
    full, half = divmod(need, 2)
    return translated + '\u3000' * full + (' ' if half else '')


def _compress_seg_to_width(t, target_w):
    """2026-09-01: 段译文超宽时本地压缩到 target_w（零 API）。
    顺序: 删段尾/段首 U+3000 → 删段内连续 U+3000 → 全角标点转半角(！？。、→!?,. 各-1格)。
    返回压缩后的段或 None(无法压到 target_w)。"""
    w = disp_width(t)
    if w <= target_w:
        return t
    delta = w - target_w
    cur = t
    # 1) 删段尾/段首 U+3000
    for _ in range(delta // 2):
        s = cur.rstrip('\u3000')
        if len(s) < len(cur):
            cur = s
            continue
        s2 = cur.lstrip('\u3000')
        if len(s2) < len(cur):
            cur = s2
            continue
        break
    # 2) 删段内连续 U+3000（从右往左, 靠近文本前的空白）
    while disp_width(cur) > target_w:
        spans = [m for m in re.finditer(r'\u3000+', cur)]
        if not spans:
            break
        m = spans[-1]
        cur = cur[:m.start()] + cur[m.end():]
    # 3) 全角标点转半角（各 -1 格, 循环转直到达标——2026-09-05 修复: 原只转一个就
    #    break, 差 2 格时(如 '？'+'…')压不到位返回 None 导致整行放弃)
    if disp_width(cur) > target_w:
        HALF = {'！': '!', '？': '?', '。': '.', '、': ',', '：': ':', '；': ';'}
        for i, ch in enumerate(cur):
            if disp_width(cur) <= target_w:
                break
            if ch in HALF:
                cand = cur[:i] + HALF[ch] + cur[i + 1:]
                if disp_width(cand) < disp_width(cur):
                    cur = cand
    return cur if disp_width(cur) == target_w else None


def _verify_art_alignment(orig_line, cand, segments):
    """2026-09-01: 校验重建行中 art 段的列位置与原文一致（宽对框歪防御）。
    art 段按 segments 定位在 orig 中的列区间, 在 cand 中同样列区间必须仍是
    art 段原字符（段内逐字符相等）。text 段位置允许漂移（译文长度不同）。"""
    col = 0
    for sg in segments:
        if sg['kind'] != 'art':
            # text 段: 累计其源宽度, 不校验
            col += sg.get('src_width', disp_width(orig_line[sg['start']:sg['end']]))
            continue
        seg_txt = orig_line[sg['start']:sg['end']]
        # art 段起始列 col, 终止列 col + seg_w
        seg_w = sg.get('src_width', disp_width(seg_txt))
        # 取 cand 从 col 列开始、宽度 seg_w 的子串
        cand_piece = ''
        cur_col = 0
        for ch in cand:
            if cur_col >= col and cur_col < col + seg_w:
                cand_piece += ch
            cur_col += disp_width(ch)
            if cur_col > col + seg_w:
                break
        # 逐字符对比（宽度列上的字符序列）
        if cand_piece != seg_txt:
            return False
        col += seg_w
    return True


def rebuild_mixed_line(orig_line, segments, translations):
    """按 segments 顺序拼回整行。translations: {seg_index: 译文}。
    art 段与低置信 text 段原样保留；缺译返回 None（调用方回退整行旧逻辑）。
    B3 补充（2026-08-11 行首空白保护）: text 段拼回时先原样保留该段在源行中的
    行首空白（U+3000/半角空格，从 orig_line 对应位置截取），再拼译文（模型偶发
    保留的行首空白先 strip 避免双份），最后 pad 补段尾到该段 src_width
    （pad 数量 = src_width - 行首空白宽 - 译文宽；译文已超则不再补）。

    2026-09-01（宽对框歪修复）: 译文超宽不再直接返回超宽段（会把后续 art 推右错位），
    改本地压缩（_compress_seg_to_width, 零 API）；压缩后仍超宽返回 None → 调用方
    重试该段(落 review_inline)，绝不产出超宽段破坏 art 列位置。"""
    parts = []
    for si, sg in enumerate(segments):
        if sg['kind'] == 'art' or sg.get('confidence', 1.0) < 0.65:
            parts.append(orig_line[sg['start']:sg['end']])
        else:
            t = translations.get(si)
            if t is None:
                return None
            orig_seg = orig_line[sg['start']:sg['end']]
            lead = orig_seg[:len(orig_seg) - len(orig_seg.lstrip(' \u3000'))]
            t = t.lstrip(' \u3000')
            # src_width 缺失时用原文段实际宽度兜底（裸调用/测试夹具的段可能不带该字段）
            target_w = sg.get('src_width', disp_width(orig_seg)) - disp_width(lead)
            # 2026-09-01: 译文超宽 → 本地压缩(pad_segment 不压缩直接返回超宽, 会推右 art);
            # 译文不足 → pad_segment 补段尾(art 列不变)。压不动返回 None(重试/落盘)。
            if disp_width(t) > target_w:
                t = _compress_seg_to_width(t, target_w)
                if t is None:
                    return None
            else:
                t = pad_segment(t, target_w)
            parts.append(lead + t)
    return ''.join(parts)


SYSTEM_MIXED = """你是やる夫AA漫画的资深汉化翻译。用户会发来若干【文本片段】，每行以「行号_段号|」开头。这些片段是 AA 漫画某一行中需要翻译的文字部分（该行其余部分是与画面一体的绘画字符，已由系统处理，不要管）。

要求：
1. 把每个片段翻译成简体中文：自然、贴切、符合漫画语气，不增删内容。
2. **必须完整表达原文语义，不得省略任何实词**（名词/动词/形容词/副词一个都不能丢；宁可译文略长，也不要删字凑短）。
3. 只输出译文片段本身：不要添加任何绘画字符、边框、空格（留白由系统按原宽度补齐）。**绝对不要用空格填充宽度**。
4. 译文长度尽量接近或短于原文（原片段区域宽度固定，译文太长会放不下）；但**不得因宽度限制而删语义**。
5. 角色名、专有名词保留日文原文。
6. 如果片段其实没有可译文字（纯装饰），就原样照抄输出。
7. 必须翻译【所有】给出的片段，一条都不能漏。
8. 输出格式：每行「行号_段号|翻译后的片段」，一行一条，不要输出任何其他内容。

示例（画面字符已由系统处理，只译文字部分）：
- 「00012_0|いいお出かけ日和だお」→「00012_0|真是出门的好日子啊」   （完整译出"日和"）
- 「00045_1|それではやる夫、これからは日曜日に来てくれ」→「00045_1|那么やる夫，以后周日来这边吧」  （やる夫是角色名保留）
- 「00078_2|ヽ」→「00078_2|ヽ」   （纯装饰原样照抄）"""


# 段级「吞字」防线（2026-08-18 反哺② / 2026-10-10 重定口径）：译文比原段窄多少算吞字。
#
# 2026-10-10 实测（doc/方案归档/段级协议漏译根因与优化方案_2026-10-10.md）：固定 4 列阈值把**合法译文**
# 判成吞字（中文天生比日文短 30~50%：助词 + 每字 2 格假名），本作段级候选行 13,958 行里 8,684
# 行（62%）判失败并**整行丢弃**（失败行在成品里就是原样日文）。地面真值标定（655 条人工修订 =
# 被协议拒掉、人手译好的段）：合法译文的中/日宽度比 中位 0.86、1% 位 0.56、最低 0.33；而已知
# 真吞字样例 0.40 —— **两者重叠**，任何单一宽度阈值都只能二选一（保合法=放吞字，反之=继续误杀）。
# 故宽度判据降级：只否决「灾难性丢内容」（比 < MIN_RATIO，实测 0 例合法命中），中间带
# （比 < FLAG_RATIO）只**记录进复核清单**、绝不否决整行；段内留白由 pad_segment 本地补位，
# art 列位不受影响（该判据在段级路径上「保列位」的价值本来就是 0）。
SEG_SHRINK_TOL = 4                     # 旧口径（fix_width 段级修复 / 测试用）
SEG_SHRINK_RATIO = 0.0                 # 旧口径（同左）
MIXED_SHRINK_MIN_RATIO = 0.35          # 比 < 该值 → 判「灾难性丢内容」→ 重试（env AA_MIXED_SHRINK_MIN_RATIO）
MIXED_SHRINK_FLAG_RATIO = 0.60         # 比 < 该值 → 接受，但记入 work/mixed_leftover.txt（env AA_MIXED_SHRINK_FLAG_RATIO）

# 段级重试的定向提示词（2026-10-10）：旧实现每轮重发同一份 SYSTEM_MIXED，模型行为不变 →
# 确定性失败（照抄画面纹理 / 窄框放不下）必然 3 轮全败。改成按失败原因换提示词。
MIXED_COPY_HINT = ("\n\n【本轮重点】以下片段**必须译成简体中文**，不许照抄原文；"
                   "只有确实只是画面纹理（无实际词义）的片段才原样照抄。")
MIXED_SHORT_HINT = ("\n\n【本轮重点】以下片段的区域宽度**固定**，译文必须**比原文短或等长**"
                    "（可意译/精简），绝对不许照抄原文。")
MIXED_COMPLETE_HINT = ("\n\n【本轮重点】以下片段的上一版译文疑似**遗漏了原文内容**：必须完整译出"
                       "原文的每个实词（名词/动词/形容词/副词一个都不能丢），宁可译文略长也不要省略。")


def mixed_shrink_min_ratio():
    """段宽否决下限（比 < 该值 = 灾难性丢内容 → 重试）。env `AA_MIXED_SHRINK_MIN_RATIO`。"""
    try:
        return float(os.environ.get("AA_MIXED_SHRINK_MIN_RATIO", MIXED_SHRINK_MIN_RATIO))
    except (TypeError, ValueError):
        return MIXED_SHRINK_MIN_RATIO


def mixed_shrink_flag_ratio():
    """段宽复核线（比 < 该值 = 接受但记入 work/mixed_leftover.txt）。env `AA_MIXED_SHRINK_FLAG_RATIO`。"""
    try:
        return float(os.environ.get("AA_MIXED_SHRINK_FLAG_RATIO", MIXED_SHRINK_FLAG_RATIO))
    except (TypeError, ValueError):
        return MIXED_SHRINK_FLAG_RATIO


def mixed_partial_enabled():
    """末轮「接受部分成功」（仍不合格的段退原文、整行照常产出）。AA_MIXED_PARTIAL=0 回旧行为（整行丢）。"""
    return _flag_on("AA_MIXED_PARTIAL", default_on=True)


def _mixed_seg_evidence(piece):
    """段内是否有**决定性台词证据**（复用 2026-09-15 规则抠段的同一判据）。

    有 → 模型原样照抄 = 漏译（重试）；无（散落笔画/画面纹理/单个装饰字）→ 原样照抄是**正确行为**，
    不能判失败——旧实现按「段内有非艺术假名/汉字」一刀切，而本作用 ユ/ェ/ュ 与生僻汉字（汀/斧/竿/丐）
    作笔画，于是艺术碎片段被当台词段（实测占失败事件 26%），照抄判失败后**整行的真台词一起陪葬**。"""
    try:
        return _rule_dialogue_run_ok((piece or '').strip(' \u3000'))
    except Exception:
        return True


def _mixed_assemble(line, segs, trans, keep, pending_segs=()):
    """拼回整行并校验：整行宽 == SRC + art 列位守恒（_verify_art_alignment）。

    trans = {段号: 译文}；keep = {段号: True} 表示该段**退回原文**（画面纹理 / 段内放不下）。
    pending_segs = 本轮仍在重试的段（当成缺译文）→ 返回 None。任一段缺译文 → None。"""
    t2 = dict(trans)
    for si in keep:
        sg = segs[si]
        t2[si] = line[sg['start']:sg['end']]
    for si in _sendable_text_segs(line, segs):
        if si not in t2 or si in pending_segs:
            return None
    cand = rebuild_mixed_line(line, segs, t2)
    if cand is None:
        return None
    if disp_width(cand) != disp_width(line) or not _verify_art_alignment(line, cand, segs):
        return None
    return cand


def _mixed_seg_verdict(t, orig_seg, is_rule, rule_names, min_ratio, flag_ratio):
    """单段校验 → (verdict, reason, hint)，verdict ∈ ok / keep / flag / retry。

    keep = 无台词证据的照抄（画面纹理，原样保留）；flag = 译文偏短（接受但记复核清单）；
    retry = 判失败重试（reason 进 AA_MIXED_DEBUG，hint 是本轮提示词）。"""
    if t is None:
        return 'retry', 'model_miss', MIXED_COPY_HINT
    # 2026-10-10 P1-1 Rule B：源段里的艺术假名在译文里丢失 = 把笔画译成了中文 → 回滚/重试
    _vio = art_keep_violation(orig_seg, t)
    if _vio:
        if _mixed_seg_evidence(orig_seg):
            return 'retry', 'art_keep', MIXED_COPY_HINT
        return 'keep', 'art_keep', ''
    if is_rule and _rule_seg_copy_fail(orig_seg, t, rule_names):
        return 'retry', 'rule_copy', MIXED_COPY_HINT
    if is_rule:
        _src_run = ''.join(c for c in orig_seg if KANA_RE.match(c))
        if len([c for c in t if c in _src_run and c not in ART_HIRA and c not in ART_KATA]) >= 2:
            return 'retry', 'rule_residual', MIXED_COPY_HINT
    real_kana = [c for c in orig_seg if KANA_RE.match(c) and c not in ART_HIRA and c not in ART_KATA]
    has_hanzi = any('\u4e00' <= c <= '\u9fff' for c in orig_seg)
    seg_has_text = bool(real_kana or has_hanzi)
    if seg_has_text and t.rstrip() == orig_seg.rstrip():
        if _mixed_seg_evidence(orig_seg):
            return 'retry', 'copy_fail', MIXED_COPY_HINT
        return 'keep', 'art_copy', ''
    if not seg_has_text:
        # 纯装饰段（无真假名无汉字）：原样照抄正确，但**不许被打宽**（会推右后续 art）
        if disp_width(t) > disp_width(orig_seg):
            return 'retry', 'decor_wide', MIXED_SHORT_HINT
        return 'ok', '', ''
    if disp_width(t) > disp_width(orig_seg):
        return 'retry', 'seg_wide', MIXED_SHORT_HINT
    wj = disp_width(orig_seg.strip(' \u3000'))
    wt = disp_width(t.strip(' \u3000'))
    ratio = (wt / wj) if wj else 1.0
    if ratio < min_ratio:
        return 'retry', 'seg_shrink', MIXED_COMPLETE_HINT
    if ratio < flag_ratio:
        return 'flag', 'shrink_flag', ''
    return 'ok', '', ''


def mixed_leftover_path(work_dir=None):
    return os.path.join(work_dir or WORK_DIR, "mixed_leftover.txt")


def append_mixed_leftover(entries, work_dir=None):
    """段级残留清单（`文件名 | 行号 | 段号 | 原文 | 原因`）。

    去重键 = 文件名 | 行号 | 段号（重跑不追加重复，与 refine_aa 的 review_inline 同口径）。
    entries = [(文件名, 行号(1-based), 段号, 原文, 原因), ...]。返回新增行数。
    无工作目录（单测/裸调用）时不落盘。"""
    if not entries:
        return 0
    if not (work_dir or WORK_DIR):
        return 0
    p = mixed_leftover_path(work_dir)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        seen, out = set(), []
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for ln in f:
                    parts = ln.rstrip("\n").split(" | ")
                    if len(parts) >= 3:
                        seen.add((parts[0], parts[1], parts[2]))
        for fname, num, si, orig, why in entries:
            key = (fname, str(num), str(si))
            if key in seen:
                continue
            seen.add(key)
            out.append("%s | %s | %s | %s | %s" % (fname, num, si,
                                                   (orig or '').strip(' \u3000'), why))
        if not out:
            return 0
        with open(p, "a", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        return len(out)
    except Exception:
        return 0


def translate_lines_mixed(flagged_segs, work_r18=None, low_r18=False, base=None):
    """flagged_segs: list of (num, line, segments)。Returns (fixed, missed, warnings)。

    只发 conf>=0.65 的 text 段；段级 pad 拼回；校验整行宽 == SRC + art 列位守恒 + 段级残留。

    **2026-10-10 改版**（doc/方案归档/段级协议漏译根因与优化方案_2026-10-10.md）：
      ① 段宽判据由「固定 4 列」降级为「比 < MIN_RATIO 才否决」（见上方常量注释）；
      ② 照抄分两态：有决定性台词证据 → 重试；无（画面纹理）→ 原样保留，不判失败；
      ③ 重试按失败原因换提示词（照抄→MIXED_COPY_HINT / 超宽→MIXED_SHORT_HINT / 偏短→COMPLETE_HINT）；
      ④ **末轮接受部分成功**：仍不合格的段退回原文，整行照常产出（`AA_MIXED_PARTIAL=0` 回旧行为）；
      ⑤ 重试只重发**失败段**，已通过的段累积保留（旧实现每轮重发整行全部段，确定性失败必然烧满 3 轮）；
      ⑥ 退原文/偏短的段写 work/mixed_leftover.txt（人工可查）；其中**有真台词**的仍计入 missed。

    missed = 未能产出 **或** 产出了但含「真台词段退原文」的行（调用方据此落 review_inline，不再静默）。
    AA_MIXED_DEBUG 指向文件时追加每行失败原因（诊断用）。
    base(2026-08-27): 章节 base, 穿透给 glossary_prompt_hint。"""
    fixed, missed, warnings = {}, [], []
    pending = {num: {"line": line, "segs": segs, "trans": {}, "keep": {}, "todo": None, "hint": ""}
               for num, line, segs in flagged_segs}
    if not pending:
        return fixed, missed, warnings
    min_ratio, flag_ratio = mixed_shrink_min_ratio(), mixed_shrink_flag_ratio()
    partial = mixed_partial_enabled()
    fname = ((base or '') + '.txt') if base else ''
    # 2026-09-15: 规则段严格照抄检测的豁免池 = 全局 KNOWN_NAMES + 作品术语表日文侧专名
    #（专名保留日文是正确行为，不能被当成漏译判失败）
    try:
        _rule_names = {jp for jp, _zh in _load_glossary_pairs(base) if jp and not jp.startswith('re:')}
    except Exception:
        _rule_names = set()
    fail_log = []      # (num, reason) → AA_MIXED_DEBUG
    leftovers = []     # (num, si, 原因, 是否真台词, 原文片段)
    for round_ in range(3):
        if not pending:
            break
        jobs, hint = [], ''
        for num, st in pending.items():
            segs, line = st["segs"], st["line"]
            todo = st["todo"] if st["todo"] is not None else _sendable_text_segs(line, segs)
            todo = [si for si in todo if si not in st["trans"] and si not in st["keep"]]
            st["todo"] = todo
            jobs.extend((num, si, _pt(line[segs[si]['start']:segs[si]['end']], base)) for si in todo)
            if st["hint"]:
                hint = st["hint"]
        if not jobs:
            break
        body = "\n".join(f"{num:05d}_{si}|{seg}" for num, si, seg in jobs)
        # 2026-09-04: 模型↔行号——mixed 段翻译也算该行(按行号去重记 lines)
        try:
            from aa_backend import set_usage_lines, clear_usage_lines
        except Exception:
            set_usage_lines = clear_usage_lines = None
        if set_usage_lines:
            # 2026-09-05: 同 translate_lines——0-based idx +1 对齐工作台(1-based 文件行号)
            set_usage_lines(sorted({num + 1 for num, _si, _seg in jobs}))
        try:
            text = call_api(SYSTEM_MIXED + glossary_prompt_hint(base) + hint, body,
                            work_r18=work_r18, low_r18=low_r18)
        finally:
            if clear_usage_lines:
                clear_usage_lines()
        res = {}
        for ln in text.splitlines():
            m = MIXED_SEG_RE.match(ln)
            if m:
                res[(int(m.group(1)), int(m.group(2)))] = m.group(3)
        next_pending = {}
        for num, st in pending.items():
            line, segs = st["line"], st["segs"]
            trans, keep = st["trans"], st["keep"]
            fresh = set(st["todo"])          # 本轮新译的段（残留校验只认这些）
            retry, flags, hint2 = [], [], ''
            for si in st["todo"]:
                sg = segs[si]
                orig_seg = line[sg['start']:sg['end']]
                verdict, why, h = _mixed_seg_verdict(res.get((num, si)), orig_seg,
                                                     bool(sg.get("rule")), _rule_names,
                                                     min_ratio, flag_ratio)
                if verdict == 'ok':
                    trans[si] = res[(num, si)]
                elif verdict == 'keep':
                    keep[si] = True
                    if why == 'art_keep':      # Rule B 回滚可见（人工可查）
                        leftovers.append((num, si, 'art_keep', False, orig_seg))
                elif verdict == 'flag':
                    trans[si] = res[(num, si)]
                    flags.append((si, orig_seg))
                else:
                    retry.append(si)
                    hint2 = hint2 or h
                    fail_log.append((num, why))
            # 段级残留（译文含原文尾巴 / 规则段含艺术假名残留）→ 该段回炉重试
            for si in list(trans):
                if si not in fresh:
                    continue          # 旧轮已校验过的段不复查（gets 保留跨轮译文）
                if residual_suspect(trans[si]):
                    fail_log.append((num, f'residual seg{si}'))
                    retry.append(si)
                    trans.pop(si)
                    hint2 = hint2 or MIXED_COPY_HINT
            cand = _mixed_assemble(line, segs, trans, keep)
            if cand is None and trans and not retry:
                # 全部段都已有译文仍装不回去（段内放不下/压缩失败）→ 逐段退原文（先退最宽的）
                for si in sorted(trans, key=lambda k: -disp_width(line[segs[k]['start']:segs[k]['end']])):
                    if si not in fresh:
                        continue
                    keep[si] = True
                    trans.pop(si, None)
                    cand = _mixed_assemble(line, segs, trans, keep)
                    if cand is not None:
                        fail_log.append((num, f'fit seg{si}'))
                        leftovers.append((num, si, 'fit',
                                          _mixed_seg_evidence(line[segs[si]['start']:segs[si]['end']]),
                                          line[segs[si]['start']:segs[si]['end']]))
                        break
            if cand is not None and not retry:
                fixed[num] = cand
                for si, orig_seg in flags:
                    leftovers.append((num, si, 'shrink_flag', _mixed_seg_evidence(orig_seg), orig_seg))
                continue
            if round_ >= 2 and partial:
                # ④ 末轮接受部分成功：仍不合格的段退原文，整行照常产出
                keep2 = dict(keep)
                for si in retry:
                    keep2[si] = True
                cand2 = _mixed_assemble(line, segs, trans, keep2)
                if cand2 is None:      # 极端兜底：整行退回原文（至少行宽/art 与原文一致）
                    cand2 = _mixed_assemble(line, segs, {}, {si: True for si in range(len(segs))})
                if cand2 is not None:
                    fixed[num] = cand2
                    for si in retry:
                        orig_seg = line[segs[si]['start']:segs[si]['end']]
                        leftovers.append((num, si, 'revert', _mixed_seg_evidence(orig_seg), orig_seg))
                    for si, orig_seg in flags:
                        leftovers.append((num, si, 'shrink_flag', _mixed_seg_evidence(orig_seg), orig_seg))
                    continue
            st["todo"] = retry
            st["hint"] = hint2
            next_pending[num] = st
        pending = next_pending
    if pending:
        missed.extend(sorted(pending))
    if leftovers:
        rows, bad = [], set()
        for num, si, why, real, piece in leftovers:
            rows.append((fname, num + 1, si, piece, why))
            # 只有「真台词段被退回原文」（revert/fit）才算漏译留待人工；
            # shrink_flag 是"译了但偏短"的复核项，只进清单不报 missed（否则 review_inline 会被灌满）
            if real and why in ('revert', 'fit'):
                bad.add(num)
        append_mixed_leftover(rows)
        for num in sorted(bad):
            if num not in missed:
                missed.append(num)
    dbg = os.environ.get("AA_MIXED_DEBUG")
    if dbg:
        try:
            with open(dbg, "a", encoding="utf-8") as f:
                for num, reason in fail_log:
                    f.write(f"{num}\t{reason}\n")
        except Exception:
            pass
    return fixed, missed, warnings

def fix_width_mixed_lines(rows_by_file, work_r18=None, low_r18=False):
    """fix_width 的 mixed 段级修复（2026-08-11，MIXED_SEGMENTS 同步 gap 修复）。
    2026-08-28: 加 work_r18/low_r18 参数透传给 call_api(原引用未定义变量 NameError)。

    审阅 gap: fix_width 旧实现把整个 mixed 行（含 art 段）丢给模型重译，
    模型可能改动绘画字符 → art 段损坏/帧偏移。同步方案:
    宽度错位的 mixed 行按 segments 段级修复——只发 text 段进模型，
    art 段本地原样保留，rebuild 后整行宽 == SRC 才接受。

    rows_by_file: {fname: [(idx, src_line, tr_line, segs), ...]}，segs 来自
    line_meta_v2 的 segments（mixed 行才有；非 mixed 行交给旧 fix_batch 整行重译）。

    Returns (fixed, failed): fixed = {(fname, idx): new_line}，failed = [(fname, idx, src, tr, segs)]
    """
    # 段级修复 prompt: 只给 text 段，要求更短/等长（宽度错位通常是译文超宽）
    FIX_MIXED_SYSTEM = """你是やる夫AA漫画的资深汉化翻译。以下行的【当前译文宽度超过了原文行】，需要重新翻译修复。每行格式「行号_段号|文本片段」。

这些片段是 AA 漫画某一行中【超宽的文字区域】——该行其余部分是与画面一体的绘画字符，已由系统处理，不要管。

要求：
1. 把每个片段翻译成简体中文，译文必须【比原文片段更短或等长】（可精简、意译）——片段区域宽度固定，译文超宽放不下。
2. 只输出译文片段本身：不要添加任何绘画字符、边框、空格（留白由系统按原宽度补齐）。
3. 角色名/专有名词保留日文。不要留下任何未翻译的日语词句。
4. 输出「行号_段号|修复后的片段」，一行一条，不要输出任何其他内容。"""
    fixed = {}
    failed = []
    for fname, rows in rows_by_file.items():
        for idx, src, tr, segs in rows:
            # 只重译 conf>=0.65 的 text 段（低置信/art 段原样保留）
            tsi = _sendable_text_segs(src, segs)
            if not tsi:
                failed.append((fname, idx, src, tr, segs))
                continue
            jobs = [(idx, si, src[segs[si]['start']:segs[si]['end']]) for si in tsi]
            body = "\n".join(f"{num:05d}_{si}|{seg}" for num, si, seg in jobs)
            # 最多 3 轮补译
            for attempt in range(3):
                text = call_api(FIX_MIXED_SYSTEM, body, max_tokens=16384, work_r18=work_r18, low_r18=low_r18)
                res = {}
                for ln in text.splitlines():
                    m = MIXED_SEG_RE.match(ln)
                    if m:
                        res[(int(m.group(1)), int(m.group(2)))] = m.group(3)
                trans = {}
                ok = True
                for si in tsi:
                    t = res.get((idx, si))
                    if t is None:
                        ok = False
                        break
                    # 原样照抄检测（超宽行照抄必失败）
                    orig_seg = src[segs[si]['start']:segs[si]['end']]
                    if t.rstrip() == orig_seg.rstrip():
                        ok = False
                        break
                    trans[si] = t
                if not ok:
                    body = "\n".join(f"{idx:05d}_{si}|{src[segs[si]['start']:segs[si]['end']]}"
                                     for si in tsi if si not in trans)
                    if not body:
                        break
                    continue
                cand = rebuild_mixed_line(src, segs, trans)
                if cand is None:
                    break
                if disp_width(cand) == disp_width(src):
                    fixed[(fname, idx)] = cand
                    break
                # 仍超宽 → 精简重试
                body = "\n".join(f"{idx:05d}_{si}|{src[segs[si]['start']:segs[si]['end']]}"
                                 for si in tsi)
            if (fname, idx) not in fixed:
                failed.append((fname, idx, src, tr, segs))
    return fixed, failed


# T4.3(2026-08-18): ckpt 原子写——先写 tmp 再 os.replace，崩溃/中断不留下半截 JSON
def _atomic_write_ckpt(ckpt, data):
    tmp = ckpt + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, ckpt)


def _chapter_work_r18(base, cfg=None):
    """按 work_meta.r18_level + 章节 prep 判定后端链 (work_r18, low_r18)。

    work_r18: True=走 r18_backends, False=normal。
    low_r18: True=忽略显式 AA_BACKEND(低含量 R18 yes 章防 normal 链首覆盖), False=正常。

    2026-08-26 四态分级(用户方案):
      high   : 除了 no 章都走 r18 (yes/suspect→r18, no→normal); 校验最松
      medium : 默认档。yes→r18; no/suspect→normal; 正常校验
      low    : 全部章(含 yes)先走 normal 链首(低含量 R18 用免费/性价比后端), yes 由 low_r18 驱动; 校验最严
      空(=no): 非 R18 作品, 全部走 normal, 不做 prep 判定
    """
    try:
        import json as _json, os as _os
        # work_meta
        meta_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                                  "aa_web_app", "data", "work_meta.json")
        if not _os.path.exists(meta_path):
            return None, False
        meta = _json.load(open(meta_path, encoding="utf-8"))
        wid = WORK_ID or (cfg or {}).get("work_dir", "").split("\\")[-1]
        m = meta.get(wid, {})
        level = m.get("r18_level", "") or "no"   # 空 = no(非 R18)
        work_dir = (cfg or {}).get("work_dir", "")
        prep_p = _os.path.join(work_dir, "prep", base + ".json")
        hit = "no"
        if _os.path.exists(prep_p):
            d = _json.load(open(prep_p, encoding="utf-8"))
            hit = d.get("r18", {}).get("hit", "no")
        if level == "high":
            # 除了 no 章都走 r18; 无 prep 时保守走 r18(高含量作品)
            if hit == "no" and _os.path.exists(prep_p):
                return False, False
            return True, False
        if level == "low":
            # 全部章先走 normal 链首(low_r18 驱动: yes 章忽略显式 AA_BACKEND 防覆盖)
            return False, True
        if level == "medium":
            # yes → r18; no/suspect → normal
            if hit == "yes":
                return True, False
            return False, False
        # 空 = no: 非 R18, 全部 normal, 不做 prep 判定
        return False, False
    except Exception:
        return None, False


def _load_work_meta_local():
    """读 work_meta.json(作者字段用于楼主识别)。2026-08-25。"""
    try:
        _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "aa_web_app", "data", "work_meta.json")
        if not os.path.exists(_p):
            _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "aa_web_app", "data", "work_meta.json")
        with open(_p, encoding="utf-8") as _f:
            return json.load(_f)
    except Exception:
        return {}


def _chunk_context(lines, chunk_first_idx):
    """取 chunk 首行之前的「前 N 楼」台词(纯净文本)作为上下文。

    2026-08-25 治本: 翻译主流程带上下文——「鏡→眼镜」类多义词需结合前文场景。
    用帖头行切分楼边界, 从首行所在楼向前回溯最多 3 楼找台词(前 1 楼可能全是 AA 画面)。
    """
    res_starts = [i for i, l in enumerate(lines) if re.match(r'^\d+\s*：', l)]
    # 首行所在楼号
    cur_res = 0
    for i, h in enumerate(res_starts):
        if chunk_first_idx < h:
            cur_res = i
            break
    else:
        cur_res = len(res_starts)
    # 向前回溯最多 3 楼
    lo = res_starts[max(0, cur_res - 3)] if cur_res - 3 >= 0 and cur_res > 0 else 0
    if cur_res == 0:
        lo = 0
    parts = []
    for i in range(lo, chunk_first_idx):
        l = lines[i].strip()
        if not l or l.startswith('#') or re.match(r'^\d+\s*：', l):
            continue
        nz = [c for c in l if not c.isspace()]
        if not nz:
            continue
        kanji = sum(1 for c in nz if re.match(r'[一-龠]', c))
        kana = sum(1 for c in nz if re.match(r'[ぁ-んァ-ヶ]', c))
        art = sum(1 for c in nz if c in 'ニミ彡乂爻ﾆﾄﾘﾉﾊﾞﾏミノ')
        rep = sum(1 for c in nz if nz.count(c) >= 3)
        if (kanji / len(nz) < 0.3 and kana / len(nz) < 0.5) or art / len(nz) >= 0.3 or rep / len(nz) >= 0.5:
            continue
        segs = re.findall(r"[ぁ-んァ-ヶ一-龠]+[ぁ-んァ-ヶー・一-龠ｗ〜～！？。、]*", l)
        parts.extend(x for x in segs if len(x) >= 2)
    return " ".join(parts[-200:])   # 限长 200 段, 防上下文膨胀


def process_chapter(path, cfg=None):
    lines = open(path, encoding="utf-8").read().split("\n")
    os.makedirs(WORK_DIR, exist_ok=True)
    base = os.path.basename(path).replace(".txt", "")
    # 2026-08-24 章节级 R18 驱动: 读 work/prep/<base>.json 的 r18.hit (yes/suspect/no)
    #   high 作品(work_meta r18_level=high) → work_r18=True, low_r18=False (整部 r18 链)
    #   low 作品 yes 章 → work_r18=True, low_r18=True (忽略显式 AA_BACKEND)
    #   low 作品 no/suspect → work_r18=False (normal 免费)
    #   无 prep/无 r18_level → None(读 env 旧行为)。多章并行安全(每章独立判定)。
    work_r18, low_r18 = _chapter_work_r18(base, cfg)
    # 2026-09-15 (P1): 章边界重置端点记忆的触发条件放宽——pin 模式下 legacy 作品(r18_level 空,
    # work_r18=None)也要重置, 否则上一章降级到网关的记忆会让往后所有章都钉在零缓存的网关上。
    try:
        import aa_backend as _AB
        if work_r18 is not None or _AB.aa_config.pin_cache_endpoint():
            _AB.reset_preferred_backend()   # 每章重置降级记忆, 防上章污染下章 + 章首重新钉缓存端点
    except Exception:
        pass
    # M4a: line_meta 驱动翻译范围（feature flag 控制，默认开；AA_USE_LINE_META=0 关闭）
    meta = None
    if line_meta_enabled():
        meta_dir = os.path.join(WORK_DIR, "line_meta_v2")
        if not os.path.isdir(meta_dir):
            # 兼容 work/work_fc2 等目录命名: 在 aa 父级下扫描含 line_meta_v2 的 work* 目录
            parent = os.path.dirname(os.path.abspath(os.path.dirname(path)))
            try:
                for d in sorted(os.listdir(parent)):
                    p = os.path.join(parent, d, "line_meta_v2")
                    if os.path.isdir(p):
                        meta_dir = p
                        break
            except OSError:
                pass
        meta = load_meta_0based(meta_dir, base)
        if meta is None:
            # M4a 增强（2026-08-11）：meta 缺失时自动生成（零 API），保证后续翻译必用 meta
            meta_dir = _ensure_meta_flat(base, meta_dir, cfg)
            meta = load_meta_0based(meta_dir, base) if meta_dir else None
        if meta is None:
            print(f"  [M4a] meta 缺失/损坏, fallback is_text_line", flush=True)
    if meta is not None:
        flagged = [(idx, l) for idx, l in enumerate(lines) if is_translatable(idx, l, meta)]
    else:
        # 2026-10-08: 走 is_translatable(meta=None) 而非裸 is_text_line——同样过 art_line_veto
        flagged = [(idx, l) for idx, l in enumerate(lines) if is_translatable(idx, l, None)]
    if art_veto_enabled():
        _av = sum(1 for l in lines if is_text_line(l) and art_line_veto(l))
        if _av:
            print(f"  [art-veto] {_av} 行纯艺术笔画 → 本地照抄(不进模型)", flush=True)
    print(f"[{os.path.basename(path)}] {len(lines)} lines, {len(flagged)} flagged"
          + (f" (meta v2)" if meta is not None else ""), flush=True)
    ckpt = os.path.join(WORK_DIR, os.path.basename(path) + ".ckpt.json")
    done = {}
    if os.path.exists(ckpt):
        data = json.load(open(ckpt, encoding="utf-8"))
        # 指纹校验：行数必须一致，否则丢弃旧 ckpt（防止旧 ckpt 复用到改动过的 txt 造成行号错位）
        if data.get("__lines") == len(lines):
            done = {int(k): v for k, v in data.items() if k != "__lines"}
        else:
            print(f"  ckpt fingerprint mismatch ({data.get('__lines')} vs {len(lines)}), discard", flush=True)
    # B2: mixed 段级翻译分流（AA_USE_MIXED_SEGMENTS=1 且 meta 含 segments 且有可发段）
    mixed_segs = {}
    if mixed_segments_enabled() and meta is not None:
        seg_meta = load_meta_segments_0based(meta_dir, base)
        if seg_meta is not None:
            mixed_segs = {idx: segs for idx, segs in seg_meta.items()
                          if _sendable_text_segs(lines[idx], segs)}
    # 2026-09-15 P2-a: meta 置信度不足(整章 text 段 0.4)的 mixed 行 → 规则抠台词段补进段级协议。
    # 只补 kind=mixed（纯 text 行没有 art 可省）；校验不过由 translate_lines_mixed 回退整行。
    rule_added = set()   # 2026-09-15: 规则抠段接管的行（校验失败必须回退整行，不能静默落人工）
    if mixed_segments_enabled() and mixed_seg_rule_enabled() and meta is not None:
        _added = 0
        for _idx, _l in flagged:
            if _idx in mixed_segs:
                continue
            _entry = meta.get(_idx) if hasattr(meta, "get") else None
            _kind = _entry[0] if isinstance(_entry, tuple) else _entry
            if _kind != "mixed":
                continue
            _segs = mixed_rule_segments(_l)
            if _segs and _sendable_text_segs(_l, _segs):
                mixed_segs[_idx] = _segs
                rule_added.add(_idx)
                _added += 1
        if _added:
            print(f"  [mixed-seg-rule] {_added} 行改用规则段级协议(meta 置信度不足)", flush=True)
    if mixed_segs:
        seg_todo = [(idx, l, mixed_segs[idx]) for idx, l in enumerate(lines)
                    if idx in mixed_segs and idx not in done]
        normal_todo = [(idx, l) for idx, l in flagged if idx not in mixed_segs and idx not in done]
    else:
        seg_todo = []
        normal_todo = [(idx, l) for idx, l in flagged if idx not in done]
    normal_todo_orig = list(normal_todo)   # 2026-08-25: 保留原始(楼主/用户分块用)
    # 2026-09-15 P3 TM: 同作品已定稿译文整行复用 → **零调用直出**（本地 pad 到源宽）。
    # 三重护栏（宽度/残留假名/术语表）任一不过就留在 todo 里交回模型；AA_USE_TM=1 启用（默认关）。
    if tm_enabled() and WORK_ID:
        try:
            import aa_tm as _TM
            _tm = _TM.load(WORK_ID)
            if _tm:
                _gp = [(jp, zh) for jp, zh in _load_glossary_pairs(base) if not jp.startswith("re:")]
                _keep_n, _keep_s, _hit = [], [], 0
                for _idx, _l in normal_todo:
                    _r = _TM.reuse(_tm, _l, _gp)
                    if _r is None:
                        _keep_n.append((_idx, _l))
                    else:
                        done[_idx] = _r
                        _hit += 1
                for _idx, _l, _segs in seg_todo:
                    _r = _TM.reuse(_tm, _l, _gp)
                    if _r is None:
                        _keep_s.append((_idx, _l, _segs))
                    else:
                        done[_idx] = _r
                        _hit += 1
                normal_todo, seg_todo = _keep_n, _keep_s
                if _hit:
                    print(f"  [tm] {_hit} 行命中已定稿译文（零调用直出）", flush=True)
        except Exception as _e:
            print(f"  [tm] 跳过({_e})", flush=True)
    # 2026-08-25: 楼主楼/用户楼分块——用户楼短句多, 混在楼主长行里易被模型漏译。
    #   识别每行所属楼的作者(楼主=◆/★或作者名, 用户=名無し), 分别聚块。
    try:
        import extract_dialogue as _ED
        _wm = _load_work_meta_local()
        _author = _wm.get("author", "") if isinstance(_wm, dict) else ""
        _op_names = {x.strip() for x in re.split(r'[,，、]', _author or "") if x.strip()}
    except Exception:
        _op_names = set()
    _cur_op = None   # 当前楼归属: True=楼主, False=用户, None=未知
    _op_of = {}      # 行idx -> 楼主? (只记翻译行)
    for _idx, _l in enumerate(lines):
        try:
            _is_hdr, _is_op = _ED.parse_op_header(_l, _op_names)
        except Exception:
            _is_hdr, _is_op = False, None
        if _is_hdr:
            _cur_op = _is_op
        elif _idx in [i for i, _ in normal_todo_orig]:
            _op_of[_idx] = _cur_op
    # 按楼主/用户分块(保持原顺序, 只分组不重排)
    # 2026-08-27 修复: 作者归属未知(None, 帖头格式未识别)时回退楼主块——
    #   否则 cur_list=None 行被静默丢弃 → chunks=0 全部不翻译(入速出邸 21 话漏译根因)
    # 2026-09-22 打包修复(AA_CHUNK_PACK, 默认开): 旧实现「作者归属一变就新开一组」,
    #   帖子里楼主/用户逐条交替时(entry_7150: 166 帖 → 90 个 run, 其中 102 个 run 只有
    #   1 行待译)组永远到不了 FLAG_PER_CHUNK, 每组自成一块 → 实测本作 10 章 1,084 块
    #   (平均 4.1 行/块)而打包后只需 116 块(−89%); 每次调用都要重发 SYSTEM+术语表/知识库
    #   (~1,812 字符 ≈ 1.6k token), 于是 10 章 4.25M token 里 94% 是同一段头部被重复付费。
    #   现改为「每种归属各自攒满 FLAG_PER_CHUNK 再开新块」(楼主/用户仍不混块、行序不变);
    #   与旧实现的行序列逐项相同(10 章全部 True)。AA_CHUNK_PACK=0 回退旧行为(对比用)。
    op_chunks = []
    usr_chunks = []
    _pack = os.environ.get("AA_CHUNK_PACK", "1") not in ("0", "false", "False")
    if _pack:
        # 打包模式: 楼主楼/用户楼各有一个「正在填充的块」——归属切换只换块不封口,
        # 所以同一归属的行能跨帖攒满 FLAG_PER_CHUNK(行序仍是文件顺序, 只是不再
        # 一交替就断块)。归块判据用 `k is not False`(None=未知→楼主块, 不丢行)。
        _open = {False: None, True: None}
        for idx, l in normal_todo_orig:
            is_usr = (_op_of.get(idx, None) is False)
            cur = _open[is_usr]
            if cur is None or len(cur) >= FLAG_PER_CHUNK:
                cur = []
                (usr_chunks if is_usr else op_chunks).append(cur)
                _open[is_usr] = cur
            cur.append((idx, l))
    else:
        # 旧行为(AA_CHUNK_PACK=0, 逐字节等价): 作者归属一变就新开一组, 末尾再切片
        cur_list = None
        cur_key = None
        for idx, l in normal_todo_orig:
            k = _op_of.get(idx, None)
            if cur_list is None or k != cur_key:
                cur_key = k
                cur_list = []
                # None(未知)归入楼主块(默认), 不丢行
                (op_chunks if k is not False else usr_chunks).append(cur_list)
            cur_list.append((idx, l))
    # 重新分块(每块 FLAG_PER_CHUNK, 楼主/用户块分开)
    chunks = []
    for grp in op_chunks + usr_chunks:
        chunks.extend(grp[i:i + FLAG_PER_CHUNK] for i in range(0, len(grp), FLAG_PER_CHUNK))
    mixed_chunks = [seg_todo[i:i + FLAG_PER_CHUNK] for i in range(0, len(seg_todo), FLAG_PER_CHUNK)]
    print(f"  {len(chunks)} chunks of {FLAG_PER_CHUNK}"
          + (f" + {len(mixed_chunks)} mixed-seg chunks" if mixed_chunks else ""), flush=True)
    missed_log = []   # M4b: 补译仍失败的行（不静默，落盘追踪）
    fallback_todo = []
    # 2026-08-29 尽快停: 取消未开始块, 收完在飞块写 ckpt 后退出本章(可续跑)
    _asap_stop = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {}
        for c in chunks:
            # 2026-08-25: 翻译主流程带前1楼上下文(治本「鏡→眼镜」)
            ctx = _chunk_context(lines, c[0][0]) if c else ""
            # 2026-08-27: base 穿透(章节简介+按章过滤)
            futs[ex.submit(translate_lines, c, work_r18, low_r18, ctx, base)] = ('n', c)
        for c in mixed_chunks:
            futs[ex.submit(translate_lines_mixed, c, work_r18, low_r18, base)] = ('m', c)
        for fut in concurrent.futures.as_completed(futs):
            if fut.cancelled():
                continue
            kind, c = futs[fut]
            try:
                fixed, still_missed, warns = fut.result()
                done.update(fixed)
                _atomic_write_ckpt(ckpt, {"__lines": len(lines), **{str(k): v for k, v in done.items()}})
                note = f" missed={still_missed[:10]}{'...' if len(still_missed)>10 else ''}" if still_missed else ""
                note += f" warn={warns[:2]}" if warns else ""
                print(f"  @{c[0][0]:5d}-{c[-1][0]:5d}: +{len(fixed)}/{len(c)}{note}", flush=True)
                for idx in still_missed:
                    if kind == 'm' and idx in rule_added:
                        # 2026-09-15 P2-a: **规则抠段接管**的行校验失败 → 回退整行协议重译。
                        # 为什么必须回退: meta 段级失败行不整行重译(2026-09-01 教训: 整行发进模型会破坏
                        # art)，但规则行本来就来自"meta 不可用"的 mixed 行——整行协议正是它们在
                        # 规则关闭时的路径(实测安全, art 描边守恒校验兜底)。若不回退，这些行会
                        # 原样留在产品里 = 静默漏译（实测第101~103話 有 9 行，含 5 行真台词）。
                        fallback_todo.append((idx, lines[idx]))
                    elif kind == 'm':
                        # 2026-09-01: mixed 段级失败行不整行重译(会把 art 发进模型破坏)。
                        # 用原始行(art+失败段原文)落 review_inline, 位置天然正确;
                        # 已成功段由 done 保留。人工只需补失败段的词义, 无需调位置。
                        missed_log.append(f"{os.path.basename(path)} | line {idx} | {lines[idx] if idx < len(lines) else ''} [mixed-fallback]")
                    else:
                        missed_log.append(f"{os.path.basename(path)} | line {idx} | {lines[idx] if idx < len(lines) else ''}")
            except Exception as e:
                print(f"  @{c[0][0]:5d} FAILED: {e}", flush=True)
            if not _asap_stop and TR.check_stop(only="asap") == "asap":
                _asap_stop = True
                for _f2 in futs:
                    if _f2 is not fut and not _f2.done():
                        _f2.cancel()
                print("  [task] 尽快停: 取消未开始块, 收完在飞块后退出本章(ckpt 已存可续跑)", flush=True)
        # B2: 段级失败行回退整行旧逻辑（一次，避免漏译静默）
        if fallback_todo:
            print(f"  [B2] {len(fallback_todo)} 行回退整行协议重译(含规则抠段失败行)", flush=True)
            fb_chunks = [fallback_todo[i:i + FLAG_PER_CHUNK] for i in range(0, len(fallback_todo), FLAG_PER_CHUNK)]
            futs2 = {ex.submit(translate_lines, c, work_r18, low_r18,
                               _chunk_context(lines, c[0][0]) if c else "", base): c for c in fb_chunks}
            for fut in concurrent.futures.as_completed(futs2):
                c = futs2[fut]
                try:
                    fixed, still_missed, warns = fut.result()
                    done.update(fixed)
                    _atomic_write_ckpt(ckpt, {"__lines": len(lines), **{str(k): v for k, v in done.items()}})
                    for idx in still_missed:
                        missed_log.append(f"{os.path.basename(path)} | line {idx} | {lines[idx] if idx < len(lines) else ''}")
                    print(f"  @fb {len(c)}: +{len(fixed)}/{len(c)}", flush=True)
                except Exception as e:
                    print(f"  @fb FAILED: {e}", flush=True)
    if _asap_stop:
        raise TR.TaskStoppedError(base)
    if missed_log:
        review_p = os.path.join(WORK_DIR, "review_inline.txt")
        mode = "a" if os.path.exists(review_p) else "w"
        with open(review_p, mode, encoding="utf-8") as f:
            f.write("\n".join(missed_log) + "\n")
        print(f"  [M4b] {len(missed_log)} 补译失败行 -> {review_p}", flush=True)
    out = [done.get(idx, l) for idx, l in enumerate(lines)]
    return out

def main():
    """统一入口：支持两种模式——
    1) 旧模式（默认）：模块级 AA_DIR/OUT_DIR/WORK_DIR 常量（works/ 专用脚本 import 后赋值覆盖）
    2) registry 模式：python scripts/aa_translate.py --work <id> [--pilot eid1,eid2]
    """
    argv = sys.argv[1:]
    cfg = None
    if "--work" in argv:
        i = argv.index("--work")
        wid = argv[i + 1] if i + 1 < len(argv) else None
        if wid:
            import registry as _R
            cfg = _R.get(wid)
            global AA_DIR, OUT_DIR, WORK_DIR, WORK_ID
            AA_DIR, OUT_DIR, WORK_DIR = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
            WORK_ID = wid
            # 术语替换钩子在模块导入时已装(见 _install_pre_terms 定义处的即时调用)
            # 2026-08-19 按作品 R18 属性自动选后端: 未显式设 AA_BACKEND 时
            # 从配置 r18_backends/normal_backends 选第一个 key 可用的后端
            # 2026-08-25 修复: low r18_level 作品不设 AA_BACKEND——显式后端会覆盖
            #   normal 链首(章节级 r18=no 走 normal 免费, yes 由 low_r18 驱动)。
            #   仅 high(作品级 r18) 或无 prep 判定时设。
            # 2026-08-26: AA_FREE_MODE=1 时不设 AA_BACKEND(免费链接管)
            if not os.environ.get("AA_BACKEND") and os.environ.get("AA_FREE_MODE") not in ("1", "true", "True"):
                try:
                    _level = ""
                    _meta_p = os.path.join(ROOT, "aa_web_app", "data", "work_meta.json")
                    try:
                        import json as _json
                        _meta = _json.load(open(_meta_p, encoding="utf-8"))
                        _level = (_meta.get(WORK_ID, {}) or {}).get("r18_level", "")
                    except Exception:
                        pass
                    # 2026-08-26 四态分级: 仅空 level(=no, 非R18)作品设 AA_BACKEND
                    #   (normal 链首, 供无 prep 判定时用); high/medium/low 由每章
                    #   _chapter_work_r18 判定驱动(call_with_fallback 选链), 不设
                    #   全局 AA_BACKEND——否则显式后端会覆盖章节级选择(high 的 no 章
                    #   会被提头成 r18 链首)。
                    _is_no = _level not in ("high", "low", "medium")
                    if _is_no:
                        import aa_config as _CFG2
                        _b, _m = _CFG2.auto_backend(bool(cfg.get("r18")))
                        os.environ["AA_BACKEND"] = _b
                        # 各后端 call_* 用专用 env(AA_QWEN_VL_MODEL/AA_ZP_MODEL/...), 按后端映射
                        _model_env = {
                            "qwen-vl": "AA_QWEN_VL_MODEL",
                            "siliconflow": "AA_SF_MODEL", "commandcode": "AA_CC_MODEL",
                            "mimo": "AA_MIMO_MODEL", "minimax": "AA_MM_MODEL",
                            "runinfra": "AA_RI_MODEL", "openrouter": "AA_OR_MODEL",
                        }.get(_b, "AA_MODEL")
                        os.environ.setdefault(_model_env, _m)
                        _r18tag = "R18" if cfg.get("r18") else "非R18"
                        print(f"[auto-backend] {wid} {_r18tag} -> {_b} / {_m}", flush=True)
                    else:
                        # 2026-08-28 修复: high/medium 也设 AA_BACKEND=commandcode(r18 链首)。
                        #   不设的代价: backend()=cloud → call_api 走云端直接模式(mimo-v2.5),
                        #   mimo 对 50 行大块输出不全 → 大量漏译(akari_haisin 19.9% 中文行事故)。
                        #   设 commandcode → call_with_fallback 走 r18 链(muse-spark 主力+自动降级)。
                        #   high 的 no 章(极少)会被提头成 commandcode(可接受);
                        #   medium 的 no 章同理。low 仍走 normal 链(章节级判定驱动 yes→r18链)。
                        # low: 设 normal 链首(章节级判定驱动 yes→r18 链兜底)
                        #   全系耗尽后改走 normal 链首——不设显式后端, 由 low_r18 机制按章选链。
                        if _level == "low":
                            import aa_config as _CFG4
                            _b4, _m4 = _CFG4.auto_backend(False)   # normal 链首
                            os.environ["AA_BACKEND"] = _b4
                            if _m4:
                                os.environ.setdefault(_CFG4.model_env(_b4), _m4)
                            print(f"[auto-backend] {wid} low/章节级判定 -> {_b4}/{_m4}(normal链首)", flush=True)
                        else:
                            # high/medium: 显式设 r18 链首(commandcode), 避免 cloud 直连 mimo 漏译
                            import aa_config as _CFG3
                            _b3, _m3 = _CFG3.auto_backend(True)   # r18 链首
                            os.environ["AA_BACKEND"] = _b3
                            if _m3:
                                os.environ.setdefault(_CFG3.model_env(_b3), _m3)
                            print(f"[auto-backend] {wid} {_level}/章节级判定 -> {_b3}/{_m3}(r18链首, 防cloud直连mimo漏译)", flush=True)
                except Exception as _e:
                    print(f"[auto-backend] 自动选择失败({_e}), 用默认后端", flush=True)
        pilot = None
        for j, a in enumerate(argv):
            if a.startswith("--pilot"):
                val = a.split("=", 1)[1] if "=" in a else (argv[j + 1] if j + 1 < len(argv) else "")
                pilot = {int(x) for x in val.split(",")} if val else None
    else:
        pilot = None
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(WORK_DIR, exist_ok=True)
    files = sorted(f for f in os.listdir(AA_DIR)
                   if f.endswith(".txt") and not f.endswith("_台词.txt") and not f.endswith("_汉化.txt"))
    if pilot:
        def eid_of(name):
            m = __import__("re").search(r'(\d+)', name)
            return int(m.group(1)) if m else 0
        files = [f for f in files if eid_of(f) in pilot]
    files = [f for f in files if not os.path.exists(os.path.join(OUT_DIR, f.replace(".txt", "_汉化.txt")))]
    print("chapters to process:", files, flush=True)
    if cfg is not None and len(files) > 1:
        # registry 模式：章节级并行(总并发 ≈ CHAPTER_WORKERS 章, 章内块级再并行)
        CHAPTER_WORKERS = int(os.environ.get("AA_CHAPTER_WORKERS", "3"))
        TR.heartbeat(phase="翻译", idx=0, total=len(files), force=True)
        _done_cnt = 0
        _stop_req = None      # 收到停止请求后置位: 不再提交新章, 等已在飞章收尾
        _inflight = {}         # fut -> (f, t0)
        _idx = 0              # 下一个待提交文件下标
        with concurrent.futures.ThreadPoolExecutor(max_workers=CHAPTER_WORKERS) as ex:
            # 初始填满窗口(≤ CHAPTER_WORKERS 个在飞章)
            while _idx < len(files) and len(_inflight) < CHAPTER_WORKERS:
                _f0 = files[_idx]; _idx += 1
                _inflight[ex.submit(process_chapter, os.path.join(AA_DIR, _f0), cfg)] = (_f0, time.time())
            while _inflight:
                fut = next(concurrent.futures.as_completed(_inflight))
                f, t0 = _inflight.pop(fut)
                try:
                    out = fut.result()
                    outp = os.path.join(OUT_DIR, f.replace(".txt", "_汉化.txt"))
                    with open(outp, "w", encoding="utf-8") as fh:
                        fh.write("\n".join(out))
                    print(f"DONE {f}: {time.time()-t0:.0f}s", flush=True)
                    _done_cnt += 1
                    TR.heartbeat(phase="翻译", idx=_done_cnt, total=len(files))
                    if _stop_req is None:
                        _stop_req = TR.check_stop()
                    if _stop_req:
                        _wait = len(_inflight)
                        if _wait:
                            print(f"[task] 收到停止请求({_stop_req}): 已译 {_done_cnt}/{len(files)} 章, "
                                  f"等 {_wait} 个在飞章收尾后退出(不再启动新章)", flush=True)
                        else:
                            print(f"[task] 收到停止请求({_stop_req}): 在章节边界退出(已译 {_done_cnt}/{len(files)} 章)", flush=True)
                            raise TR.TaskStoppedError(f)
                    elif _idx < len(files):
                        _f1 = files[_idx]; _idx += 1
                        _inflight[ex.submit(process_chapter, os.path.join(AA_DIR, _f1), cfg)] = (_f1, time.time())
                except TR.TaskStoppedError:
                    print(f"[task] {f} 块边界停止, 中断后续章节(ckpt 可续跑)", flush=True)
                    if _inflight:
                        for _fut in concurrent.futures.as_completed(_inflight):
                            try:
                                _fut.result()
                            except Exception:
                                pass
                    raise
                except Exception as exc:
                    print(f"FAILED {f}: {exc}", flush=True)
                    if _idx < len(files):
                        _f2 = files[_idx]; _idx += 1
                        _inflight[ex.submit(process_chapter, os.path.join(AA_DIR, _f2), cfg)] = (_f2, time.time())
        return
    for f in files:
        t0 = time.time()
        try:
            out = process_chapter(os.path.join(AA_DIR, f), cfg)
            outp = os.path.join(OUT_DIR, f.replace(".txt", "_汉化.txt"))
            with open(outp, "w", encoding="utf-8") as fh:
                fh.write("\n".join(out))
            print(f"DONE {f}: {time.time()-t0:.0f}s", flush=True)
            TR.heartbeat(phase="翻译")
            if TR.check_stop():
                print("[task] 收到停止请求, 在章节边界退出", flush=True)
                raise TR.TaskStoppedError(f)
        except TR.TaskStoppedError:
            print(f"[task] {f} 块边界停止(ckpt 可续跑)", flush=True)
            raise

if __name__ == "__main__":
    _rc = 1
    try:
        main()
        _rc = 0
    except TR.TaskStoppedError as _tse:
        # 2026-08-29: 停止在 executor 块外统一退出(在 with 内 sys.exit 会被 executor 收尾吞掉)
        print(f"[task] 停止确认退出 (rc={TR.RC_STOPPED})", flush=True)
        _rc = TR.RC_STOPPED
        raise SystemExit(TR.RC_STOPPED)
    except SystemExit as _e:
        _rc = _e.code if isinstance(_e.code, int) else 1
        raise
    finally:
        try:
            TR.finalize(_rc)
        except Exception:
            pass

