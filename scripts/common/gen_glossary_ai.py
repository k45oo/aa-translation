# -*- coding: utf-8 -*-
"""术语表 AI 预提取：新作品开工前跑一次，生成待审校的候选术语 JSON 到 work 目录。

用法:
    python scripts/common/gen_glossary_ai.py --work <id> [--chars 4000] [--chapters 3]   # 采样(前N章)
    python scripts/common/gen_glossary_ai.py --work <id> --full [--batch 4000]          # 全量遍历全部章(开工前人物向导)
    python scripts/common/gen_glossary_ai.py --work <id> --promote                      # 确认候选进知识库人物/术语
    python scripts/common/gen_glossary_ai.py --aa <aa目录> [--dry] [--backend <后端>]

输出: <work_dir>/glossary_candidates.json
  {
    "schema": "术语表候选 v1",
    "work": <registry id>,
    "生成日期": ...,
    "采样": {...},
    "ai_候选": [ {"term","translation","type","note"} ],   # AI 提取，去重后待审校
    "ai_去重跳过": [...],                                    # 已在 glossary.json 的 term
    "高频假名片段": [ {"fragment","count","样例文件"} ],       # 零 API 频次统计，供 known_names 补录
    "统计": {...}
  }

审校后人工合并进 glossary.json：角色名 → 角色/known_names；替换对 → glossary；
高频假名片段（频次≥3）→ known_names（补的是 kana_runs 剥离后的完整片段，不是单字符）。

设计要点（参考网上 ai本地AA翻译器0.04 术语表功能）：
- 只取前 N 字符文本样本（省 token），系统提示要求严格 JSON 数组输出（禁 markdown 包裹），
  temperature 由后端控制（默认 0.4，接近参考的 0.3）；输出解析剥 markdown 壳兜底；
  与 glossary.json 按「去空白+小写」去重。
- 本地兜底：全 aa 语料高频 kana_runs 片段统计（零 API、权威），这是 pass3 白名单补录的数据源。
"""
import json, os, re, sys, time, argparse
from collections import Counter

# Windows: stdout 被工作台/守护脚本重定向到 UTF-8 文件时默认仍按 GBK 编码,
# print ✓ 等非 GBK 字符崩 UnicodeEncodeError(候选任务收尾崩溃根因)。显式 reconfigure。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))    # scripts/common
_SCRIPTS = os.path.dirname(_SCRIPT_DIR)                     # scripts
sys.path.insert(0, _SCRIPTS)
sys.path.insert(0, os.path.join(_SCRIPTS, "common"))
sys.path.insert(0, os.path.join(_SCRIPTS, "works"))

import aa_api as A            # flat 版 re-export：call_api / is_text_line / kana_runs
from aa_common import list_txt   # 压缩感知（.txt/.txt.gz）
import registry               # works/registry.py（from paths import BASE）

BASE = registry.BASE
GLOSSARY_JSON = os.path.join(BASE, "glossary.json")

MARKER_RE = re.compile(r'^(#|=====)')
HEADER_RE = re.compile(r'^\d+\s*：|^\d+\s*名前')
HAS_KANA = re.compile(r'[ぁ-んァ-ヶー]')
HAS_HANJI = re.compile(r'[\u4e00-\u9fff]')

# 常见语法助词/功能词（绝不能进 known_names，会掩盖漏译）——只打警示标记，不自动删除
FUNC_WORDS = set("""ある でも では なら やな やは よね なぁ ねぇ ちょ まで すぎる です から かな かも など
だけ よう なる んだ けど けれど しかし そして それで だから ながら こう そう それ これ あの この
そんな どう どの なぜ とても もう まだ いつ ここ そこ わたし ぼく おれ きみ あなた みんな これ
# 2026-08-22 补长语法短语(assist词/连写/敬语结尾/问候)——漏译照抄时同样会掩盖
おはよ おはよござます みんなおはよ おまえら からなあ そうか さて されまた それじゃあ たら ちなみに
ですか ですが ですね ですよ ですよね ます ますね また みなみますお るなら るよ れる ろか んじゃなか
んで さんが さんに さんは さんを てく てくさ ては ても なるほど 確か""".split())

SYSTEM_PROMPT = """你是日文AA漫画（やる夫系字符画）汉化团队的术语提取助手。用户会发来一段日文AA漫画的台词/旁白文本。
请提取其中【值得统一翻译的专有名词】：角色名、称号、职业名、技能名、魔法名、地名、组织名、特殊词汇、反复出现的拟声词/梗词。

规则：
1. 只输出一个 JSON 数组，不要 Markdown 代码块包裹（不要```json），不要任何额外文字。格式示例：
   [{"term":"やる夫","translation":"亚鲁欧","type":"角色","作品":"やる夫系AA原创"},{"term":"ロサリア","translation":"罗萨莉亚","type":"角色","作品":""},{"term":"婚姻義務","translation":"婚姻义务","type":"术语","作品":""}]
2. term 必须是样本中真实出现的日文片段（角色名/专有名词优先全名，连呼时也给出常用简称）。
3. type 取值只能是：角色 / 专有名词 / 术语 / 拟声梗。
4. translation 给推荐简体中文翻译；拿不准的可留空字符串 ""。
5. 【角色类型必须】作品 字段填该角色出身的**外部作品/IP名**（如 公主连结/偶像大师灰姑娘女孩/VOICEROID/东方Project 等）；
   【本作原创角色或やる夫系原创角色 作品留空字符串 ""，不要填"やる夫系AA原创"这类本作/类目名】。
6. 普通口语短句、语法助词（ある/でも/だから 等）、单个汉字/单个假名 不要提取。
7. 只返回你最有把握的 10~40 个词，不要凑数。"""


# ---------- 采样 ----------
def _ensure_dialogues(cfg, aa_dir):
    """确保 aa/<base>_台词.txt 全部存在（缺的章节用 extract_dialogue 生成）。

    2026-09-06 用户确认: 术语提取=先提原文台词(extract_dialogue)再 AI 提取, 非遍历全文。
    gen_glossary_ai 不应依赖外部先跑过台词提取——这里对缺失章节自动补生成
    (与 gen_chapter_summaries 同机制 ensure_dialogue_file src_only=True; meta 缺失
    自动生成, raw 缺失中断)。返回 (生成数, 失败清单)。
    """
    import extract_dialogue as ED
    made, failed = 0, []
    if cfg is None:
        return made, failed
    for fn in list_txt(aa_dir, exclude=("_台词.txt", "_汉化.txt")):
        if not (fn.endswith(".txt") and not fn.endswith(("_台词.txt", "_汉化.txt", "_台词.txt.gz"))):
            continue
        base = fn[:-4]
        if os.path.exists(os.path.join(aa_dir, base + "_台词.txt")):
            continue
        try:
            ED.ensure_dialogue_file(cfg, base, src_only=True)
            made += 1
            print(f"  台词文件: {base}_台词.txt 已生成({made})", flush=True)
        except Exception as e:
            failed.append((base, str(e)[:60]))
    return made, failed


def _missing_dialogues(aa_dir):
    """返回缺 _台词.txt 的原文章 base 列表（幂等判定, 供 --ensure-dialogues-only 用）。"""
    miss = []
    for fn in list_txt(aa_dir, exclude=("_台词.txt", "_汉化.txt")):
        if not (fn.endswith(".txt") and not fn.endswith(("_台词.txt", "_汉化.txt", "_台词.txt.gz"))):
            continue
        base = fn[:-4]
        if not os.path.exists(os.path.join(aa_dir, base + "_台词.txt")):
            miss.append(base)
    return miss


def collect_sample(aa_dir, chapters, budget):
    """从 extract_dialogue 生成的 _台词.txt 取前 budget 字符的台词, 返回 (sample, used_files)。

    2026-08-30 收编：统一读 v3 meta 口径 _台词.txt（segments 段级剥离, mixed 行右侧台词也抓到）。
    """
    import extract_dialogue as ED
    files = sorted(f for f in os.listdir(aa_dir)
                   if f.endswith("_台词.txt"))[:chapters]
    chunks, total, used = [], 0, []
    for fn in files:
        if total >= budget:
            break
        try:
            entries = ED.parse_dialogue_file(os.path.join(aa_dir, fn))
        except Exception:
            continue
        if not entries:
            continue
        used.append(fn)
        for e in entries:
            st = e["text"].strip()
            if not st:
                continue
            chunks.append(st)
            total += len(st)
            if total >= budget:
                break
    return "\n".join(chunks), used


# ---------- AI 提取 ----------
def parse_terms(text):
    """严格解析模型输出 JSON 数组；剥 markdown 壳 / 摘取首对 [] 兜底。"""
    t = re.sub(r"^```(?:json)?\s*", "", (text or "").strip())
    t = re.sub(r"\s*```$", "", t).strip()
    arr = None
    try:
        arr = json.loads(t)
    except Exception:
        m = re.search(r"\[[\s\S]*\]", t)
        if m:
            try:
                arr = json.loads(m.group(0))
            except Exception:
                arr = None
    if not isinstance(arr, list):
        return []
    out = []
    for it in arr:
        if isinstance(it, dict) and it.get("term"):
            term = str(it["term"]).strip()
            if term:
                out.append({
                    "term": term,
                    "translation": str(it.get("translation") or "").strip(),
                    "type": str(it.get("type") or "").strip(),
                    "作品": str(it.get("作品") or "").strip(),
                    "别名_of": str(it.get("别名_of") or "").strip(),
                })
    return out


def _norm(s):
    return re.sub(r"\s+", "", (s or "")).lower()


def load_existing():
    """全局已有 term 集合（known_names、glossary 对、作品别名）+ roles.json 角色（日/中文/别名）。

    2026-09-20 缺口修复（doc/方案归档/术语表反哺与候选池差集修复方案_2026-09-20.md 决策 6）:
    原实现只读 glossary.json —— 角色块 2026-09-01 起已迁到 roles.json，故 130+ 条
    只在 roles.json 的角色会被采样路径重新提议；此处补读以与 build_term_pool.load_known
    口径一致。
    """
    exists = set()
    if os.path.exists(GLOSSARY_JSON):
        try:
            d = json.load(open(GLOSSARY_JSON, encoding="utf-8"))
        except Exception:
            d = {}
        for n in d.get("known_names", []):
            exists.add(_norm(n))
        for r in d.get("角色", []):
            exists.add(_norm(r.get("日文")))
            exists.add(_norm(r.get("中文")))
        for p in d.get("glossary", []):
            if isinstance(p, list) and p:
                exists.add(_norm(p[0]))
                if len(p) > 1:
                    exists.add(_norm(p[1]))
            elif isinstance(p, dict):
                exists.add(_norm(p.get("jp")))
                exists.add(_norm(p.get("zh")))
        for w in d.get("作品", []):
            for a in w.get("别名", []):
                exists.add(_norm(a))
    # roles.json（2026-09-20 补：角色主体已迁出 glossary.json）
    try:
        rd = json.load(open(os.path.join(BASE, "roles.json"), encoding="utf-8"))
        for r in rd.get("角色", []):
            exists.add(_norm(r.get("日文")))
            exists.add(_norm(r.get("中文")))
            for al in (r.get("别名") or []):
                exists.add(_norm(al))
    except Exception:
        pass
    return exists


def extract_ai(sample, backend=None, max_tokens=4000):
    """调 call_api 提取术语；返回候选 list。dry 时跳过。"""
    if backend:
        os.environ["AA_BACKEND"] = backend
    os.environ.setdefault("AA_USAGE_TAG", "gen_glossary_ai")
    raw = A.call_api(SYSTEM_PROMPT, sample, max_tokens=max_tokens)
    return parse_terms(raw)


def note_for(term):
    """本地警示：短纯平假名且为功能词的 term 加提示（不删除，交给人工）。"""
    if len(term) <= 3 and term in FUNC_WORDS:
        return "疑似语法功能词，入 known_names 会掩盖漏译"
    return ""


def dedupe_candidates(cands, exists):
    kept, skipped = [], []
    for c in cands:
        key = _norm(c["term"])
        if key in exists:
            skipped.append(c["term"])
            continue
        c["note"] = note_for(c["term"])
        kept.append(c)
    return kept, skipped


# ---------- 本地频次（零 API）----------
def freq_fragments(aa_dir, top=60, min_freq=3):
    """全 aa 语料 kana_runs 片段频次统计（pass3 白名单补录的同口径）。

    2026-08-30 收编：读 extract_dialogue 生成的 _台词.txt（已剥离 art, 只含台词段）。
    """
    import extract_dialogue as ED
    cnt, ex = Counter(), {}
    files = sorted(f for f in os.listdir(aa_dir) if f.endswith("_台词.txt"))
    for fn in files:
        try:
            entries = ED.parse_dialogue_file(os.path.join(aa_dir, fn))
        except Exception:
            continue
        for e in entries:
            for frag in A.kana_runs(e["text"]):
                cnt[frag] += 1
                ex.setdefault(frag, fn)
    keep = [(f, n) for f, n in cnt.items() if n >= min_freq]
    keep.sort(key=lambda x: -x[1])
    items = []
    for f, n in keep[:top]:
        item = {"fragment": f, "count": n, "样例文件": ex[f]}
        if f in FUNC_WORDS:
            item["note"] = "语法功能词？勿入 known_names（会掩盖漏译）"
        elif not HAS_KANA.search(f) and not HAS_HANJI.search(f):
            item["note"] = "纯符号/ASCII，疑似艺术字符"
        items.append(item)
    return items


# ---------- 全量遍历 + promote (2026-08-27 开工前人物向导) ----------
# 2026-09-20: 删除死代码 collect_all_batches() + extract_full()——自 2026-09-07 起
# --full 已改走 extract_full_pool()(候选池→模型筛选)，两者无调用方，留着构成
# 「第三份差集口径」(load_existing/extract_full 的 exists 与 load_known 不一致)，
# 见 doc/方案归档/术语表反哺与候选池差集修复方案_2026-09-20.md 决策 6。


# ---------- 两段式: 候选池 → 模型筛选 (2026-09-07, benchmark 定参 top200×batch20) ----------
def extract_full_pool(aa_dir, cfg, backend=None, top=200, batch=20):
    """候选池两段式提取:
      ① 本地 janome 分词+复合合并+词频 → 候选池(零API, 覆盖汉字/片假名专名
         —— 旧 kana_runs 只提纯假名段, 汉字专名全漏)
      ② 模型对候选池分批筛选(判真专名+给译名), top×batch 由 benchmark 定参。
      返回 (ai_cands, skipped, batch_n)。ai_cands=[{term,translation,type,confirm,确认,来源}]。
      去重: 候选池与已知词(roles/known_names/manual)差集; 模型筛选只留 confirm=true。

      【分批持久化 2026-09-07】后台长任务(10批≈6min)在批4后被系统回收的中断问题——
      每批筛选结果增量写 <work_dir>/pool_progress.json; 续跑(有 progress 文件)跳过
      已完成批次, 只筛剩余 → 中断不丢已筛专名、幂等可续。
    """
    import build_term_pool as BTP      # 候选池构建(独立脚本可复用)
    work_dir = cfg.get("work_dir") if cfg else os.path.join(os.path.dirname(aa_dir), "work")
    prog_p = os.path.join(work_dir, "pool_progress.json")

    # ① 本地候选池(零API, 某中断后仍重算——纯本地快)
    candpool, stats = BTP.build_pool(cfg.get("id") or os.path.basename(aa_dir), aa_dir,
                                     top=top, min_freq=3)
    if not candpool:
        print("[extract_full_pool] 候选池为空(无语料/频次不足)", flush=True)
        return [], [], 0
    pool_terms = [c["term"] for c in candpool]
    print(f"[extract_full_pool] ①本地候选池 top{top}: {len(pool_terms)} 词({stats.get('不同词')} 不同词)",
          flush=True)
    sample_file = {c["term"]: c["样例文件"] for c in candpool}

    # 续跑: 读已有 progress, 跳过已完成批次
    done_idx = set()
    ai_cands = []
    if os.path.exists(prog_p):
        try:
            prog = json.load(open(prog_p, encoding="utf-8"))
            if prog.get("top") == top and prog.get("batch") == batch:
                done_idx = set(prog.get("done_batches") or [])
                ai_cands = prog.get("cands") or []
                print(f"[extract_full_pool] 续跑: 已有 {len(ai_cands)} 专名, 跳过 {len(done_idx)} 完批",
                      flush=True)
        except Exception:
            done_idx, ai_cands = set(), []

    # ② 模型筛选(分批, 含续跑)
    import screen_term_pool as STP
    os.environ.setdefault("AA_USAGE_TAG", "term_filter")
    skipped = []
    n_batches = (len(pool_terms) + batch - 1) // batch
    for i in range(0, len(pool_terms), batch):
        bi = i // batch
        if bi in done_idx:
            continue
        chunk = pool_terms[i:i + batch]
        sample = json.dumps(chunk, ensure_ascii=False)
        try:
            raw = A.call_api(STP.FILTER_SYSTEM, sample, max_tokens=4000)
            got = STP._parse_filter(raw)
        except Exception as e:
            print(f"  [pool] 批{bi+1}/{n_batches}: 失败 {str(e)[:60]}", flush=True)
            continue
        got_map = {g["term"]: g for g in got}
        seq = 0
        for ct in chunk:
            g = got_map.get(ct)
            if g and g.get("confirm"):
                # 防重复(续跑时同 term 已进)
                if not any(x["term"] == ct for x in ai_cands):
                    ai_cands.append({
                        "term": ct,
                        "translation": (g.get("translation") or "").strip(),
                        "type": (g.get("type") or "专有名词").strip(),
                        "confirm": True,
                        "确认": False,
                        "来源": sample_file.get(ct, "")[:60],
                    })
                seq += 1
        # 每批结束即时写 progress(含该批 done)
        done_idx.add(bi)
        _wk(prog_p, top, batch, list(done_idx), ai_cands)
        print(f"  [pool] 批{bi+1}/{n_batches}: 输入{len(chunk)} → 专名{seq} (累计 {len(ai_cands)}, 已存progress)",
              flush=True)
    # 全跑完后清理 progress(避免下次误判旧数据)
    if len(done_idx) >= n_batches and os.path.exists(prog_p):
        try:
            os.remove(prog_p)
        except OSError:
            pass
    return ai_cands, skipped, n_batches


def _wk(prog_p, top, batch, done_idx, cands):
    """写 pool_progress.json(分批持久化续跑)。"""
    try:
        with open(prog_p, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"top": top, "batch": batch, "done_batches": done_idx, "cands": cands},
                      f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def _add_global_alias(target_jp, alias_term):
    """把 alias_term 作为别名加进全局角色表 target_jp 条目。返回 True/目标不存在 False。

    2026-09-01 三级重构: 角色块迁出 glossary.json → roles.json。
    """
    import json as _j
    rp = os.path.join(BASE, "roles.json")
    try:
        rdata = _j.load(open(rp, encoding="utf-8"))
        roles = rdata.get("角色", [])
    except Exception:
        return False
    for r in roles:
        if r.get("日文") == target_jp or r.get("中文") == target_jp:
            al = r.setdefault("别名", [])
            if alias_term not in al:
                al.append(alias_term)
            rdata["更新日期"] = time.strftime("%Y-%m-%d")
            with open(rp, "w", encoding="utf-8", newline="\n") as f:
                _j.dump(rdata, f, ensure_ascii=False, indent=2)
            return True
    return False


def _promote_confirmed(work_id, work_dir):
    """把 glossary_candidates.json 中 确认:true 的候选进知识库 人物/术语 块。

    角色 + 别名_of → 进全局角色表目标条目的 别名(不建独立人物);
    角色(无别名_of) → 人物; 其余 → 术语。返回进知识库条数。
    """
    cand_p = os.path.join(work_dir, "glossary_candidates.json")
    if not os.path.exists(cand_p):
        print(f"[gen_glossary_ai] 无候选文件: {cand_p}")
        return 0
    data = json.load(open(cand_p, encoding="utf-8"))
    cands = [c for c in data.get("ai_候选", []) if c.get("确认")]
    if not cands:
        print("[gen_glossary_ai] 无已确认候选(编辑 glossary_candidates.json 把要进知识库的项设 确认:true)")
        return 0
    sys.path.insert(0, os.path.join(_SCRIPTS, "tools"))
    import work_memory as WM
    mem = WM.load_knowledge(work_id)
    terms = mem.setdefault("术语", {})
    people = mem.setdefault("人物", {})
    n_t = n_p = 0
    for c in cands:
        t = (c.get("term") or "").strip()
        tr = (c.get("translation") or "").strip() or "(待定译名)"
        typ = c.get("type") or ""
        if not t or t in FUNC_WORDS:
            continue
        if "角色" in typ or "人物" in typ or "人名" in typ:
            _alias_of = (c.get("别名_of") or "").strip()
            if _alias_of:
                # 2026-08-28: 别名归并——候选是某角色的别名(如 青蛙子 是 洩矢諏訪子 的别名)
                #   进全局角色表目标条目的 别名, 不建独立人物
                _av = _add_global_alias(_alias_of, t)
                if _av:
                    n_p += 1  # 计数为"归并动作数"
                else:
                    print(f"  [warn] 别名_of 目标 {_alias_of!r} 不在全局角色表, 先 promote 主角色", flush=True)
                continue
            _w = (c.get("作品") or "").strip()
            _val = f"{tr}({_w})" if _w and _w not in tr else tr
            if t not in people:
                people[t] = _val
                n_p += 1
            elif _w and "(" not in people.get(t, ""):
                # 2026-08-27: 已存在但新候选带作品 → 补作品(如 洩矢諏訪子 → 泄矢诹访子(东方Project))
                people[t] = f"{people[t]}({_w})"
                n_p += 1
        else:
            if t not in terms:
                terms[t] = tr
                n_t += 1
    # 2026-08-28 审批1: promote 落地后同步清理知识库 待确认区 对应项(防双写残留)
    _pend = mem.get("待确认") or {}
    if n_t or n_p:
        for c in cands:
            t = (c.get("term") or "").strip()
            if not t:
                continue
            _typ = c.get("type") or ""
            _bucket = "人物" if ("角色" in _typ or "人物" in _typ or "人名" in _typ) else "术语"
            (_pend.get(_bucket) or {}).pop(t, None)
        if not _pend.get("术语") and not _pend.get("人物"):
            mem.pop("待确认", None)
        WM.save_knowledge(work_id, mem)
    # 从候选移除已提升项
    data["ai_候选"] = [c for c in data.get("ai_候选", []) if not c.get("确认")]
    json.dump(data, open(cand_p, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=2)
    print(f"[gen_glossary_ai] promote: 人物+{n_p} 术语+{n_t} 条 -> {work_id}_knowledge.json")
    return n_t + n_p


# ---------- 主入口 ----------
def main():
    ap = argparse.ArgumentParser(description="术语表 AI 预提取（生成待审校候选 JSON）")
    ap.add_argument("--work", help="registry 作品 id")
    ap.add_argument("--aa", help="直接指定 aa 文本目录（覆盖 --work）")
    ap.add_argument("--chars", type=int, default=4000, help="AI 采样字符预算（默认 4000）")
    ap.add_argument("--chapters", type=int, default=3, help="采样文件数（默认前 3 个）")
    ap.add_argument("--top", type=int, default=60, help="高频片段输出条数")
    ap.add_argument("--min-freq", type=int, default=3, help="片段频次阈值")
    ap.add_argument("--dry", action="store_true", help="只做本地频次统计，不调 API")
    ap.add_argument("--backend", help="AA_BACKEND 覆盖（如 commandcode/siliconflow/runinfra）")
    ap.add_argument("--full", action="store_true", help="全量遍历全部章节分批AI提取(开工前人物向导, 覆盖--chapters)")
    ap.add_argument("--batch", type=int, default=4000, help="--full 每批字符预算(默认4000)")
    ap.add_argument("--promote", action="store_true", help="把 glossary_candidates.json 中 确认:true 的候选进知识库人物/术语")
    ap.add_argument("--ensure-dialogues-only", action="store_true",
                    help="2026-09-07: 只补齐所有缺失的 原文台词(_台词.txt) 就退出(独立阶段, 供 run_full_pool_until_done 先确保台词齐全再进候选筛选)")
    args = ap.parse_args()

    cfg, work_id = None, None
    if args.aa:
        aa_dir = args.aa
        work_id = "direct:" + os.path.basename(os.path.normpath(args.aa))
    elif args.work:
        if args.work not in registry.WORKS:
            raise SystemExit(f"未知作品: {args.work!r}（可用: {', '.join(registry.WORKS)}）")
        cfg = registry.get(args.work)
        aa_dir = cfg["aa_dir"]
        work_id = args.work
        if cfg.get("r18"):
            os.environ.setdefault("AA_R18", "1")
    else:
        raise SystemExit("必须提供 --work <id> 或 --aa <目录>")

    if not os.path.isdir(aa_dir):
        raise SystemExit(f"aa 目录不存在: {aa_dir}")
    work_dir = cfg["work_dir"] if cfg else os.path.join(os.path.dirname(aa_dir), "work")
    os.makedirs(work_dir, exist_ok=True)

    # 2026-08-27: --promote 把已确认候选进知识库人物/术语(不跑提取)
    if args.promote:
        n = _promote_confirmed(work_id, work_dir)
        print(f"[gen_glossary_ai] promote: 进知识库 {n} 条")
        return

    print(f"[gen_glossary_ai] work={work_id}  aa={aa_dir}")

    # 2026-09-06: 术语提取=先提原文台词再 AI 提取——确保 _台词.txt 存在(缺的章节自动补生成)
    if cfg is not None and not args.promote:
        _m, _fl = _ensure_dialogues(cfg, aa_dir)
        if _m:
            print(f"[gen_glossary_ai] 台词文件补生成 {_m} 章")
        if _fl:
            print(f"[gen_glossary_ai] [warn] {len(_fl)} 章台词提取失败: "
                  + "; ".join(f"{b}({e})" for b, e in _fl[:5]))
    elif cfg is None:
        print("[gen_glossary_ai] [warn] --aa 直连模式无 cfg, 跳过台词文件确保(依赖已有 _台词.txt)")

    # 2026-09-07: 独立补台词阶段——只补齐所有缺失台词后退出, 不进 AI 筛选。
    # run_full_pool_until_done 先调本模式(逐轮续, 幂等)确保台词齐全, 再进候选筛选,
    # 补台词不受模型筛选中断影响。返回码: 0=全补齐, 9=仍有缺(调用方续跑)。
    if args.ensure_dialogues_only:
        need = _missing_dialogues(aa_dir)
        if need:
            print(f"[gen_glossary_ai] --ensure-dialogues-only: 仍有 {len(need)} 章缺台词, 需续跑(返回9)", flush=True)
            sys.exit(9)
        print("[gen_glossary_ai] --ensure-dialogues-only: 全部台词齐 ✓", flush=True)
        sys.exit(0)

    # 1) 零 API 频次统计（全语料）
    frags = freq_fragments(aa_dir, top=args.top, min_freq=args.min_freq)
    print(f"[gen_glossary_ai] 高频假名片段: {len(frags)} 条")

    # 2) AI 提取
    ai_cands, skipped, sample_info = [], [], {"字符数预算": args.chars}
    if args.full and not args.dry:
        # 2026-09-07: --full 改两段式(基准测试定参: 候选池 top200 × 每批20词)。
        #   ① 本地 janome 分词+复合合并 + 词频 → 候选池(零API)
        #   ② 模型对候选池分批筛选(判真专名+给译名) —— 取代旧"整批4000字丢模型"的空批率高方案
        ai_cands, skipped, nb = extract_full_pool(aa_dir, cfg, backend=args.backend,
                                                  top=200, batch=20)
        sample_info = {"模式": "full_pool", "候选池top": 200, "每批": 20, "批数": nb}
        print(f"[gen_glossary_ai] --full(pool): AI 筛选 {len(ai_cands)} 条专名候选, 跳过 {len(skipped)} 条(已在表)")
        for c in ai_cands[:20]:
            print(f"  - {c['term']}  ->  {c['translation'] or '(空)'}  [{c['type'] or '?'}]")
        if len(ai_cands) > 20:
            print(f"  ... 及 {len(ai_cands) - 20} 条见输出文件")
    elif not args.dry:
        sample, used = collect_sample(aa_dir, args.chapters, args.chars)
        sample_info = {"字符数预算": args.chars, "实际采样字符": len(sample),
                       "采样文件数": len(used), "文件": used}
        if not sample.strip():
            print("[gen_glossary_ai] 采样为空，跳过 AI 提取（--dry 可只看本地频次）")
        else:
            exists = load_existing()
            cands = extract_ai(sample, backend=args.backend)
            ai_cands, skipped = dedupe_candidates(cands, exists)
            for c in ai_cands:
                c["确认"] = False
            print(f"[gen_glossary_ai] AI 提取 {len(cands)} 条 → 去重后新增 {len(ai_cands)} 条（已在表 {len(skipped)} 条）")
            for c in ai_cands[:20]:
                print(f"  - {c['term']}  ->  {c['translation'] or '(空)'}  [{c['type'] or '?'}]")
            if len(ai_cands) > 20:
                print(f"  ... 及 {len(ai_cands) - 20} 条见输出文件")
    else:
        sample_info["说明"] = "--dry：未调 API"

    out = {
        "schema": "术语表候选 v1",
        "work": work_id,
        "生成日期": time.strftime("%Y-%m-%d"),
        "采样": sample_info,
        "ai_候选": ai_cands,
        "ai_去重跳过": skipped,
        "高频假名片段": frags,
        "统计": {"ai新增": len(ai_cands), "去重跳过": len(skipped), "片段条数": len(frags)},
    }
    out_path = os.path.join(work_dir, "glossary_candidates.json")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[gen_glossary_ai] 候选已写: {out_path}")

    # 2026-08-27 分流: AI 候选同时写入作品知识库 待确认区(作品专属)
    #   角色/人物类型 → 待确认.人物; 其余 → 待确认.术语。审校后由 work_memory 注入。
    if cfg is not None and ai_cands:
        try:
            sys.path.insert(0, os.path.join(_SCRIPTS, "tools"))
            import work_memory as _WM
            mem = _WM.load_knowledge(work_id)
            pend = mem.setdefault("待确认", {})
            pt = pend.setdefault("术语", {})
            pp = pend.setdefault("人物", {})
            n_t = n_p = 0
            for c in ai_cands:
                t = (c.get("term") or "").strip()
                if not t:
                    continue
                tr = (c.get("translation") or "").strip() or "(待定译名)"
                typ = c.get("type") or ""
                _w = (c.get("作品") or "").strip()
                if "角色" in typ or "人物" in typ or "人名" in typ:
                    if t not in pp:
                        # 2026-08-27: 角色带作品字段
                        pp[t] = f"{tr}({_w})" if _w and _w not in tr else tr
                        n_p += 1
                else:
                    if t not in pt:
                        pt[t] = tr
                        n_t += 1
            if n_t or n_p:
                _WM.save_knowledge(work_id, mem)
                print(f"[gen_glossary_ai] 作品知识库待确认区: 术语+{n_t} 人物+{n_p} -> {work_id}_knowledge.json")
        except Exception as e:
            print(f"[gen_glossary_ai] 知识库写入跳过: {e}")


if __name__ == "__main__":
    main()
