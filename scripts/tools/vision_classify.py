# -*- coding: utf-8 -*-
"""视觉判定: 存疑块渲染红框+编号 → 视觉模型判定台词/画面 → line_meta_v3
2026-08-19 并行优化: 章级 ProcessPool(每章独立 v3 文件无竞争), --workers N(默认3)
用法: python scripts/tools/vision_classify.py --work saki_fc2 [--chapter eid...] [--limit N]
      [--retry-failed] [--workers N]
"""
import argparse, base64, json, os, re, sys, time, urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
import io

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
sys.path.insert(0, os.path.join(BASE, 'scripts', 'common'))
sys.path.insert(0, os.path.join(BASE, 'scripts', 'works'))
sys.path.insert(0, os.path.join(BASE, 'scripts', 'tools'))

import registry
import sites  # noqa: 提供站点模块查找
from sites import fc2 as site  # noqa: overridden in main()
import aa_to_images as A
from aa_common import meta_load, json_read_any, list_txt, txt_read   # meta/正文透明读
from PIL import Image, ImageDraw

V3_DIR_NAME = 'line_meta_v3'


def read_env(key):
    # 2026-10-08 阶段 1: 统一走 aa_env（真实 env → 仓库根 .env → hermes 兜底）
    import aa_env
    return aa_env.read_key(key)


def _log_usage_vision(model, resp):
    """视觉调用落盘 usage.jsonl(tag=vision, 带 model/backend)。"""
    try:
        u = resp.get("usage", {}) or {}
        inp = u.get("input_tokens") or u.get("prompt_tokens") or 0
        out = u.get("output_tokens") or u.get("completion_tokens") or 0
        import json as _json, time as _time, os as _os
        rec = {"tag": "vision", "ts": _time.time(), "prompt": inp, "completion": out,
               "task": _os.environ.get("AA_TASK_ID"),
               "total": inp + out, "model": model, "backend": "vision"}
        # 2026-08-31: 大小限制+滚动（与翻译 usage 同机制）
        import sys as _sys
        _c = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "common")
        if _c not in _sys.path:
            _sys.path.insert(0, _c)
        from aa_usage import append_usage
        append_usage(rec)
    except Exception:
        pass


def chat_vision(url, key, model, sys_prompt, user_text, img_b64, timeout=90, fmt='chat'):
    """视觉对话。fmt='chat': OpenAI 兼容 image_url; fmt='responses': opencode-go 网关
    (input_image+image_url嵌套+浏览器UA,2026-08-17)"""
    data_url = f'data:image/png;base64,{img_b64}'
    if fmt == 'responses':
        body = {
            'model': model,
            'input': [
                {'role': 'user', 'content': [
                    {'type': 'input_image', 'image_url': {'url': data_url}},
                    {'type': 'input_text', 'text': f'{sys_prompt}\n{user_text}'},
                ]},
            ],
        }
        headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
                   'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36'}
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        t0 = time.time()
        resp = json.load(urllib.request.urlopen(req, timeout=timeout))
        dt = time.time() - t0
        text = '\n'.join(c.get('text', '') for o in resp.get('output', [])
                         for c in o.get('content', []) if c.get('text'))
        _log_usage_vision(model, resp)
        return dt, text
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': sys_prompt},
            {'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': data_url}},
                {'type': 'text', 'text': user_text},
            ]},
        ],
        'max_tokens': 1024,
    }
    headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
               'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36'}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    t0 = time.time()
    resp = json.load(urllib.request.urlopen(req, timeout=timeout))
    dt = time.time() - t0
    _log_usage_vision(model, resp)
    return dt, resp['choices'][0]['message']['content']


def parse_verdicts(text, suspects):
    """解析视觉模型输出 → {块内行序号: 'text'|'art'} (容错, 2026-08-17 优化③)
    支持: '行号N: 台词/画面'、'N: T/A'、'N: text/art'、'N: 台词(对话)' 等变体"""
    result = {}
    for i, tln, kind in suspects:
        result[i] = None
    # 匹配 "行号N: 台词/画面" 或 "N: text/art"
    for m in re.finditer(r'(?:行号)?(\d+)\s*[:：]\s*(台词|画面|text|art|T|A)\b', text, re.IGNORECASE):
        idx = int(m.group(1))
        verdict = m.group(2).lower()
        if idx in result and result[idx] is None:
            if verdict in ('台词', 'text', 't'):
                result[idx] = 'text'
            elif verdict in ('画面', 'art', 'a'):
                result[idx] = 'art'
    return result


def suspect_rows(b, v2):
    """块内存疑行: v2 判定与字符统计矛盾的(conf 0.4-0.8 区间的存疑)——只送视觉
    2026-08-17 优化⑥: 也包含 no_dialogue 误判(text段区域 art>35%)等"""
    out = []
    for i, (ln, t) in enumerate(b):
        if not t.strip() or t.startswith('#'):
            continue
        v = v2.get(str(ln + 1))
        if not isinstance(v, dict):
            continue
        if site.render_header(t):
            continue
        kind = v.get('kind')
        if kind == 'art':
            continue
        ns = [c for c in t if not c.isspace()]
        if not ns:
            continue
        art = sum(1 for c in ns if c in A.ART_SYMS or c in A.ART_KATA or c in A.ART_HIRA
                  or c in A.ART_KANJI or c in 'ヽ_ノー=―')
        artpct = art / len(ns)
        sp = A.v2_text_spans(v, t)
        has_han = bool(A._HAN2_RE.search(t))
        if sp and artpct > 0.35:
            out.append((i, ln + 1, '误嵌?'))
        elif not sp and has_han and artpct < 0.25:
            out.append((i, ln + 1, '漏嵌?'))
    return out


def render_annotated(block, fs, img_w, site, cm, font_path, meta, v2, suspects):
    """渲染块图 + 嫌疑行红框+编号标注(视觉辅助方式A,2026-08-16)"""
    img = A.render_block(block, site, cm, fs, img_w, font_path,
                         A.PAD, 0, meta, embed_v2=v2,
                         freeze_post=A.post_is_op(block, site))
    img = img.convert('RGB')
    d = ImageDraw.Draw(img)
    line_h = int(fs * A.LINE_RATIO)
    rows_y = []
    y = A.PAD
    for ln, t in block:
        if not t.strip():
            y += line_h
        elif site.render_header(t):
            y += line_h * 3
        else:
            y += line_h
        rows_y.append((ln, y - line_h))
    for i, (ln, t) in enumerate(block):
        if i in {s[0] for s in suspects}:
            y0 = rows_y[i][1]
            d.rectangle([A.PAD - 2, y0 - 2, img.size[0] - A.PAD + 2, y0 + line_h + 2],
                        outline=(255, 0, 0), width=2)
            d.text((A.PAD - 2, max(0, y0 - 16)), str(i), fill=(255, 0, 0))
    return img, rows_y


def load_failed_blocks(base):
    """解析日志里的失败块集合(每块只记最后状态: 成功→删,失败→留)"""
    last_state = {}
    for lp in (os.path.join(BASE, 'temp', 'vision_retry.log'),
               os.path.join(BASE, 'temp', 'vision_classify_full.log'),
               os.path.join(BASE, 'temp', 'vision_classify.log'),
               os.path.join(BASE, 'temp', 'vision_touhou52.log')):
        if not os.path.exists(lp):
            continue
        for ln in open(lp, encoding='utf-8'):
            m = re.search(r'entry_(\d+) 块(\d+):? (解析率低|.*判定)', ln)
            if m and ('entry_' + m.group(1)) == base:
                key = int(m.group(2))
                last_state[key] = 'fail' if '解析率低' in ln else 'ok'
    return {k for k, st in last_state.items() if st == 'fail'}


def process_chapter(base, cfg, site_name, sys_prompt, all_models, limit, retry_failed, primary_model=None):
    """处理单章(独立进程): 读汉化txt+meta → 分块 → 存疑块视觉判定 → 写 v3
    2026-08-20: primary_model 可传整个模型列表(双模并行时 main 直接传 models 组)。
    返回 (base, n_blocks, n_rows, used_model, log_lines)"""
    import importlib
    site = importlib.import_module('sites.' + site_name)
    log = []
    v3_dir = os.path.join(cfg['work_dir'], V3_DIR_NAME)
    os.makedirs(v3_dir, exist_ok=True)
    v3_path = os.path.join(v3_dir, base + '.json')
    has_v3 = os.path.exists(v3_path) and os.path.getsize(v3_path) > 10
    if has_v3 and not retry_failed:
        return base, 0, 0, None, [f"{base}: 已有 v3 判定,跳过"]
    failed_blocks = load_failed_blocks(base) if retry_failed else {}
    if retry_failed and not failed_blocks:
        return base, 0, 0, None, [f"{base}: 无失败块,跳过"]
    if retry_failed:
        log.append(f"{base}: 重跑 {len(failed_blocks)} 个失败块")

    # 2026-08-20 用户: 按章 round-robin 分配主力模型(每章1个模型, 两主模型分章并行),
    # 同一存疑块只有 1 个模型判断; 主力失败才换组内下一模型(串行 fallback)
    primary = primary_model or all_models[0]
    models_ordered = [primary] + [m for m in all_models if m != primary]
    log.append(f"{base}: 主力模型={primary[0]}")

    lines = txt_read(os.path.join(cfg['out_dir'], base + '_汉化.txt'))
    rows = [(i, l) for i, l in enumerate(lines) if not l.startswith('#')]
    meta = {}
    mp = os.path.join(cfg['work_dir'], 'line_meta', base + '.json')
    if os.path.exists(mp):
        try:
            meta = json.load(open(mp, encoding='utf-8'))
        except Exception:
            meta = {}
    v2 = meta_load(os.path.join(cfg['work_dir'], 'line_meta_v2'), base, {}) or {}
    cm = {}
    cmf = os.path.join(cfg['aa_dir'], base + '_colormap.json')
    if os.path.exists(cmf):
        cm = json.load(open(cmf, encoding='utf-8'))

    blocks, cur = [], []
    for ln, txt in rows:
        if site.render_header(txt):
            if cur:
                blocks.append((A.post_is_op(cur, site), cur))
            cur = [(ln, txt)]
        else:
            cur.append((ln, txt))
    if cur:
        blocks.append((A.post_is_op(cur, site), cur))
    merged = A.merge_posts(A.split_long(blocks), meta=meta, site=site)

    v3 = {}
    n_blocks = 0
    used_model = models_ordered[0][0]  # 记录最终使用的模型(默认主力; 双模/兜底会覆盖)
    for bi, b in enumerate(merged, 1):
        if limit and n_blocks >= limit:
            break
        if retry_failed and bi not in failed_blocks:
            continue
        suspects = suspect_rows(b, v2)
        if not suspects:
            continue
        n_blocks += 1
        fs, img_w = A.block_layout(b, 600, site, False, 0, 0, meta, True, embed_v2=v2)
        img, rows_y = render_annotated(b, fs, img_w, site, cm, A.FONT_MAIN, meta, v2, suspects)
        buf = io.BytesIO()
        img_v = img.copy()
        if max(img_v.size) > 1024:
            ratio = 1024 / max(img_v.size)
            img_v = img_v.resize((int(img_v.size[0] * ratio), int(img_v.size[1] * ratio)))
        img_v.save(buf, format='PNG')
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        sus_idx = {i for i, tln, k in suspects}
        text_parts = []
        for i, tln, kind in suspects:
            text_parts.append(f'{i}: {b[i][1].rstrip()}')
        user_text = ("红框高亮行是存疑行(编号=块内行序号)。逐个判断每行是'台词'还是'画面'。\n"
                     "红框行文本:\n" + '\n'.join(text_parts) +
                     "\n\n严格按格式回答(只列红框行,每行一个):\n行号N: 台词/画面")

        verdicts = None
        # 2026-08-20 用户: 同一存疑块只有 1 个模型判断——主力模型先判,
        # 解析率<70% 或异常才换组内下一模型(串行 fallback)
        for mname, url, key, model_id, fmt in models_ordered:
            try:
                dt, text = chat_vision(url, key, model_id, sys_prompt, user_text, img_b64, fmt=fmt)
                verdicts = parse_verdicts(text, suspects)
                filled = sum(1 for v in verdicts.values() if v)
                if filled >= len(suspects) * 0.7:
                    log.append(f"  [{mname} {dt:.1f}s] 块{bi}: {filled}/{len(suspects)}判定")
                    used_model = mname
                    break
                else:
                    log.append(f"  [{mname}] 块{bi}: 解析率低 {filled}/{len(suspects)}, 换下一模型")
            except Exception as e:
                log.append(f"  [{mname}] 块{bi} 失败: {e}, 换下一模型")
        if verdicts:
            for i, tln, kind in suspects:
                v = verdicts.get(i)
                if v:
                    v3[str(tln)] = v
        time.sleep(0.5)

    out_path = os.path.join(v3_dir, base + '.json')
    if retry_failed and has_v3:
        old = json.load(open(v3_path, encoding='utf-8'))
        old.update(v3)
        v3 = old
    json.dump(v3, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    log.append(f"{base}: {n_blocks}存疑块, {len(v3)}行判定 → {out_path} (主力模型: {used_model})")
    return base, n_blocks, len(v3), used_model, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', default='saki_fc2')
    ap.add_argument('--chapter', default='', nargs='*',
                    help='指定章(可多个, 空格分隔; 空=全部)')
    ap.add_argument('--limit', type=int, default=0, help='最多处理块数(测试用)')
    ap.add_argument('--retry-failed', action='store_true',
                    help='重跑上次三模型全失败的块(读 vision_classify 日志的失败块清单)')
    ap.add_argument('--workers', type=int, default=3,
                    help='并行章数(默认3; 同 key 并发勿太高防限流)')
    a = ap.parse_args()

    cfg = registry.get(a.work)

    # 站点模块绑定(2026-08-19): 兼容多站点, --work 决定 site
    global site
    site_map = {
        'saki_fc2': 'fc2',
        'touhou_teisou': 'touhouyaruosure',
    }
    site_name = site_map.get(a.work, 'fc2')
    site = __import__('sites.' + site_name, fromlist=[site_name])
    print(f"[site={site_name}] workers={a.workers}")
    files = list_txt(cfg['out_dir'], '_汉化.txt')
    if a.chapter:
        chapters = a.chapter if isinstance(a.chapter, list) else [a.chapter]
        files = [f for f in files if any(c in f for c in chapters)]

    # 视觉模型配置(2026-08-20 分组): R18 作品用 models_r18(Qwen3-VL-32B+30B-A3B),
    # 非 R18 用 models_normal(mimo 兜底); 旧 models 兼容。
    # key_env 对应的 .env 有 key 才加入候选
    import aa_config as _CFG  # scripts/common(已 sys.path)
    r18_works = set(_CFG.vision_r18_works())
    is_r18_work = bool(a.work and a.work.lower() in r18_works)  # 保守: 不自动判 R18
    grp = "models_r18" if is_r18_work else "models_normal"
    print(f"[work={a.work}] R18={'是' if is_r18_work else '否'} → 视觉组 {grp}", flush=True)
    models = []
    for m in _CFG.vision_models_for(is_r18_work):
        if not m.get("key_env"):
            continue
        key = read_env(m["key_env"])
        if not key:
            continue
        models.append((m["name"], m["url"], key, m["model"], m.get("fmt", "chat")))
    if not models:
        print(f'无可用视觉模型 key(检查 model_config.toml [vision].{grp} 与 .env)'); sys.exit(1)
    print(f"可用模型({grp}): {[m[0] for m in models]}")

    sys_prompt = (
        "你是AA字符画(2ch文字絵)漫画分类专家。"
        "AA画面=用符号/假名/汉字拼成的人物图形、轮廓、装饰(如 | : : | 身体、ヽノ 耳形、二八 纹理);"
        "台词=独立可读的中文/日文句子或单词。\n"
        "关键规则:\n"
        "- 行内只要有可读的中文/日文句子→判'台词',即使该行前面有画面符号(混合行)\n"
        "- 纯符号/假名/纹理(无完整可读句子)→判'画面'\n"
        "- 红框高亮行是存疑行,行首有红色编号(编号=块内行序号)\n"
        "- 示例: '｜　大嘴巴やる夫　男子最强(笑)　→台词'; '| : : | 乂辷ﾝ 　→画面'\n"
        "- 严格按格式回答,只列红框行: '行号N: 台词' 或 '行号N: 画面',每行一个\n"
    )

    v3_dir = os.path.join(cfg['work_dir'], V3_DIR_NAME)
    os.makedirs(v3_dir, exist_ok=True)

    # 章级并行(2026-08-19): 每章独立 v3 文件无竞争; 日志按章返回主进程打印
    bases = [f.replace('_汉化.txt', '') for f in files]
    n_workers = max(1, min(a.workers, len(bases)))
    # 2026-08-20 用户: 按章 round-robin 分配主力模型(两主模型分章并行),
    # 每章只用 1 个主模型判块(失败才换组内下一模型)
    main_models = models[:2] if len(models) > 1 else models
    primary_map = {b: main_models[i % len(main_models)] for i, b in enumerate(bases)}
    print(f"主力分配(round-robin): " + ", ".join(f"{primary_map[b][0]}→{b}"
                                                  for i, b in enumerate(bases[:9])) + " ...")
    print(f"组内兜底: {[m[0] for m in models[2:]] or '无'}")
    print(f"待处理 {len(bases)} 章, 并行 {n_workers} 进程")
    t0 = time.time()
    # 模型→章节记录 {model_name: [chapters]}
    model_chapters = {}
    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        futs = {ex.submit(process_chapter, b, cfg, site_name, sys_prompt, models,
                          a.limit, a.retry_failed, primary_map[b]): b for b in bases}
        done = 0
        for fut in as_completed(futs):
            done += 1
            b = futs[fut]
            try:
                base, n_blocks, n_rows, used_model, log = fut.result()
                for l in log:
                    print(l, flush=True)
                print(f"  [{done}/{len(bases)}] {base} 完成 ({n_blocks}块/{n_rows}行)", flush=True)
                if used_model:
                    model_chapters.setdefault(used_model, []).append(base)
            except Exception as e:
                print(f"  章节处理异常 {b}: {e}", flush=True)
    dt = time.time() - t0
    # 记录: 每个视觉模型识别了哪些章节
    rec_path = os.path.join(cfg['work_dir'], 'vision_model_chapters.txt')
    with open(rec_path, 'w', encoding='utf-8') as f:
        f.write(f"# 视觉判定模型→章节 记录 ({time.strftime('%Y-%m-%d %H:%M')})\n")
        f.write(f"# 并行 {n_workers} 进程, 共 {len(bases)} 章, 耗时 {dt/60:.1f} 分钟\n")
        for mname in sorted(model_chapters):
            chs = sorted(model_chapters[mname])
            f.write(f"\n[{mname}] {len(chs)} 章:\n")
            for c in chs:
                f.write(f"  {c}\n")
    print(f"\n模型→章节记录: {rec_path}")
    for mname in sorted(model_chapters):
        print(f"  {mname}: {len(model_chapters[mname])} 章")
    print(f"\n全部完成: {len(bases)} 章, 耗时 {dt/60:.1f} 分钟")


if __name__ == '__main__':
    main()
