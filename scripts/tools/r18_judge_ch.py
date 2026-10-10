#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r18_judge_ch.py — 汉化版(中文台词)章节级 R18 判定独立脚本。

对目录下每章 *.html 提取可见文本, 用中文 R18 特征词判定该章是否含"实际 H 场景"。
要求收紧(2026-08-22, 依用户校准): 纯讨论设定/话题/生理词(色情/自慰/做爱/怀孕/勃起/阴茎/发情…)
不算 R18——它们多出自作品设定设定(如 111 话"色情领域"讨论)或玩家楼调侃, 非实际色情场景。
真正的 R18 需命中"具体性行为/性器官实词"(肉棒/小穴/插入/射精/口交/乳交/精液…),
且达到强度门槛。

规则:
  R18_HARD(具体性行为/性器官实词): >=2 个不同词 → 判 R18
      (也可: 单个实词出现 >=4 次视为明显 H 场景, 但保留 >=2 不同词为主规则)
  R18_CTX(话题/设定/生理/抽象词): 仅当与 HARD 词共存时作佐证; 单独出现不算
  SOFT (裸体/全裸/工口/色色…): 需 >=5 个不同词才判(防设定堆叠误判)
输出 <dir>/r18_verdict.json : { "<完整文件名>": {"r18":"yes|no","words":[命中词...]} }
用法: python scripts/tools/r18_judge_ch.py --dir 交付HTML/时间停止探索者亚鲁欧 [--out ...]
校准基准(人工给定 2026-08-22):
  001/062/112后篇=真 R18; 003/008/012/013/015/111/112前篇=少量设定/话题, 不标
"""
import re, os, sys, json, glob, argparse, html as H

# 具体性行为/性器官实词: 出现即指向实际 H 场景(双方/单人实操)
R18_HARD = set("""肉棒 小穴 插入 内射 口交 乳交 射精 精液 龟头 大屌 抽插 颜射
阴蒂 阴道 生殖器 爱液 淫水 手交 腿交 精囊 精液 爆射 体内射精 中出 全裸交合""".split())

# 话题/设定/生理/抽象词: 作品设定或讨论楼常见, 单独出现不算 R18, 仅佐证
R18_CTX = set("""做爱 性交 交合 交配 发情 色情 色欲 欲火 自慰 手淫 怀孕 受精 勃起 阴茎
乳头 快感 高潮 敏感带 下体 私处 赤裸 情欲 淫荡 淫乱 交欢 爱抚 抚摸 挑逗 诱惑""".split())

R18_SOFT = set("""裸体 全裸 工口 色色 色气 挺胸 大胸 丰满 身材 性感 意乱情迷""".split())


def clean_text(path):
    t = open(path, encoding='utf-8').read()
    t = re.sub(r'<style.*?</style>', '', t, flags=re.S)
    t = re.sub(r'<script.*?</script>', '', t, flags=re.S)
    t = re.sub(r'<[^>]+>', '', t)
    return H.unescape(t)


def judge_text(t):
    # hard 实词: 计每词出现次数(低频零散设定词不算, 需明显密集 H 场景)
    hard_cnt = {w: t.count(w) for w in R18_HARD if w in t}
    hard_total = sum(hard_cnt.values())
    hard_u = set(hard_cnt)
    ctx = [w for w in R18_CTX if w in t]
    soft = set(w for w in R18_SOFT if w in t)
    # 门槛: 实词总出现次 >=4 且 至少 2 个不同实词 → 实 H
    if hard_total >= 4 and len(hard_u) >= 2:
        return "yes", sorted(hard_u)
    if len(hard_u) >= 3:
        # 虽总次不足 4, 但 3+ 种不同实词也倾向实 H
        return "yes", sorted(hard_u)
    if len(soft) >= 6 and not hard_u:
        return "yes", sorted(soft)
    return "no", (sorted(hard_u) + sorted(set(ctx))[:6])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='交付HTML/时间停止探索者亚鲁欧',
                    help='章节 html 目录(相对仓库根)')
    ap.add_argument('--out', help='判定 json 输出路径(默认 <dir>/r18_verdict.json)')
    ap.add_argument('--only', help='只看指定文件名(逗号分隔, 调试用)')
    a = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dirpath = a.dir if os.path.isabs(a.dir) else os.path.join(root, a.dir)
    outpath = a.out or os.path.join(dirpath, 'r18_verdict.json')

    only = set(a.only.split(',')) if a.only else None
    files = sorted(glob.glob(os.path.join(dirpath, '*.html')))
    verdict, yes_list = {}, []
    for f in files:
        b = os.path.basename(f)
        if b == '章节顺序.txt':
            continue
        if only and b not in only:
            continue
        v, words = judge_text(clean_text(f))
        verdict[b] = {'r18': v, 'words': words}
        if v == 'yes':
            yes_list.append(b)

    json.dump(verdict, open(outpath, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"判定完成: 总 {len(verdict)} 章  R18={len(yes_list)}  无={len(verdict)-len(yes_list)}")
    print(f"已写 {outpath}")
    if only:
        for b in sorted(verdict):
            print(f"  [{verdict[b]['r18']}] {b} <{verdict[b]['words']}>")


if __name__ == '__main__':
    main()
