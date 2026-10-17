# -*- coding: utf-8 -*-
"""fix_delivered_terms: 回修**已交付**汉化文本里「词表/补位造成的确定性错误」（零 API）。

背景（2026-10-11，ポケの子 用户报错）：
  词表脏条目/缺条目 + 段落补位落点，会在**已交付**成品里留下确定性错误。根因修好之后
  还要把存量补平，否则下次重译/校对会照旧复发。
  - 确定性可机械修：补位落点（`>>` 左侧、括号/斜杠注记内部、塞进 `>>N`/比分的字）、
    源锚定名词替换（タスキ→气势披带、とんぼ→折返、ちゅうがえり→空翻折返）。
  - 语义错译**不可**机械修（模型在「被替换过的日文」上写出来的中文，得重译）——
    本工具只把它们列进 `--todo` 清单（默认写 <work_dir>/retranslate_todo.txt）。

用法:
  python scripts/fix/fix_delivered_terms.py --work ポケの子            # 只报告
  python scripts/fix/fix_delivered_terms.py --work ポケの子 --apply     # 备份后写回
"""
import os, sys, argparse, shutil, time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE)))          # scripts/
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "common"))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "works"))

import aa_translate as T
import aa_common as C
import pre_terms as P
import registry

# 源锚定名词替换：(源必须含, 源必须不含, 译文替换表)
# 只放**词表口径**造成的名词错，不放语义/措辞问题。
TERM_RULES = [
    (("でっきー", "でっきぃ", "できる夫"), (), [("出木杉", "德其欧")]),
    (("タスキ",), ("ハチマキ",), [("气势头带", "气势披带")]),
    (("とんぼ", "トンボ"), ("つばめがえし",), [
        ("先行蜻蜓", "先行折返"), ("先制蜻蜓", "先制折返"), ("鬼蜻蜓", "鬼折返"),
        ("蜻蜓技", "折返技"), ("「燕返」", "「折返」"), ("燕返", "折返"),
    ]),
    # ------------------------------------------------------------------
    # 2026-10-12 果类意译 + 道具简写 + 市镇名统一（用户指定）→ 存量回修
    # 口径：带 のみ 加「果」、裸写不加；市镇/地区名按官方对照；同形词一律靠
    # must_not 或更长的替换词挡住（アムロ≠ムロ、睿智湖≠睿智、さざ波≠涟漪…）。
    # ------------------------------------------------------------------
    (("ボコ", "ポコのみ"), (), [("蒲桃果", "物半果"), ("波可果", "物半果"), ("蒲桃", "物半"), ("波可", "物半")]),
    (("マジュ",), (), [("真珠果", "特半果"), ("珍珠果", "特半果"), ("蒲桃果", "特半果"), ("波可果", "特半果"),
                       ("烛木果", "特半果"), ("真珠", "特半"), ("珍珠", "特半")]),
    (("イバン",), (), [("番荔果", "释陀果")]),        # 源写 イバン 却被译成别的果名（实测 6 行）
    (("オッカ",), (), [("巧可果", "抗火果")]),
    (("イトケ",), (), [("千香果", "抗水果")]),
    (("ソクノ",), (), [("烛木果", "抗电果")]),
    (("リンド",), (), [("罗子果", "抗草果")]),
    (("ヤチェ",), (), [("番荔果", "抗冰果")]),
    (("ヨプ",), (), [("莲蒲果", "抗斗果")]),
    (("ビアー",), (), [("通通果", "抗毒果")]),
    (("シュカ",), (), [("腰木果", "抗地果")]),
    (("バコウ",), (), [("棱瓜果", "抗飞果")]),
    (("ウタン",), (), [("福禄果", "抗超果")]),
    (("タンガ",), (), [("扁樱果", "抗虫果")]),
    (("ヨロギ",), (), [("草蚕果", "抗岩果")]),
    (("カシブ",), (), [("佛柑果", "抗灵果")]),
    (("ハバン",), (), [("莓榴果", "抗龙果")]),
    (("ナモ",), (), [("刺耳果", "抗恶果")]),
    (("リリバ",), (), [("霹霹果", "抗钢果")]),
    (("ロゼル",), (), [("洛玫果", "抗妖果")]),
    (("ホズ",), (), [("灯浆果", "抗普果")]),
    (("クラボ",), (), [("樱子果", "解麻果")]),
    (("カゴ",), (), [("零余果", "解眠果")]),
    (("モモン",), (), [("桃桃果", "解毒果")]),
    (("チーゴ",), (), [("莓莓果", "解烧果")]),
    (("おんみつマント", "隠密マント"), (), [("隐秘斗篷", "密探斗篷")]),
    (("レッカ", "レッドカ", "レドカ"), (), [("红卡", "红牌")]),
    # 地区：只换「区域名+地方」的整串（裸「地方」是正常词，换了会伤正文）
    (("シンオウ地方", "ホウエン地方", "カントー地方", "ジョウト地方", "イッシュ地方",
      "カロス地方", "アローラ地方", "ガラル地方", "パルデア地方"), (), [
        ("神奥地方", "神奥地区"), ("丰缘地方", "丰缘地区"), ("关都地方", "关都地区"),
        ("城都地方", "城都地区"), ("合众地方", "合众地区"), ("卡洛斯地方", "卡洛斯地区"),
        ("阿罗拉地方", "阿罗拉地区"), ("伽勒尔地方", "伽勒尔地区"), ("帕底亚地方", "帕底亚地区"),
    ]),
    # 市镇（每条都带「源必须不含」挡同形词）
    (("カイナ",), (), [("凯纳", "凯那")]),
    (("カナズミ",), (), [("卡纳兹", "卡那兹"), ("金涩", "卡那兹"), ("金策", "卡那兹"), ("金穗", "卡那兹")]),
    (("ムロ",), ("アムロ",), [("姆罗市", "武斗市"), ("姆罗镇", "武斗镇"), ("姆罗道馆", "武斗道馆"),
                              ("姆罗竞技场", "武斗竞技场"), ("穆罗镇", "武斗镇"), ("姆罗", "武斗"), ("穆罗", "武斗")]),
    (("コトブキ",), (), [("寿杯", "祝庆杯"), ("寿喜", "祝庆"), ("幸福镇", "祝庆市"), ("幸福市", "祝庆市"),
                         ("幸福电视", "电视祝庆")]),
    (("ヨスガ",), (), [("约瑟加", "家缘"), ("约瑟迦", "家缘"), ("缘寿", "家缘")]),
    (("キッサキ",), ("エイチ湖",), [("奇萨基", "雪峰"), ("切锋", "雪峰"), ("睿智", "雪峰")]),
    (("ノモセ",), (), [("野濑", "野原")]),
    (("テンガン",), (), [("天元", "天冠"), ("天岩", "天冠")]),
    (("マサゴ",), (), [("真新镇", "真砂镇")]),
    (("ソノオ",), (), [("园尾", "花苑"), ("园野", "花苑")]),
    (("キンセツ",), (), [("金涩", "紫堇"), ("金策", "紫堇"), ("金穗", "紫堇"), ("金泽", "紫堇")]),
    (("シダケ",), (), [("希达基", "绿茵")]),
    (("ヒワマキ",), (), [("吹雪竞技场", "茵郁竞技场"), ("吹雪杯", "茵郁杯"), ("桧皮", "茵郁"),
                         ("向日葵竞技场", "茵郁竞技场"), ("日轮木杯", "茵郁杯"), ("日轮杯", "茵郁杯"),
                         ("莲蒲藤", "茵郁"), ("火间田", "茵郁"), ("日轮", "茵郁")]),
    (("トクサネ",), (), [("德萨内", "绿岭"), ("德岛", "绿岭"), ("德音", "绿岭")]),
    (("キナギ",), ("さざ波",), [("涟漪镇", "暮水镇"), ("涟漪杯", "暮水杯"), ("金黄市", "暮水市"),
                                ("木凪杯", "暮水杯"), ("木凪", "暮水"), ("涟漪", "暮水"), ("金黄", "暮水")]),
    (("ミナモ",), (), [("米纳莫", "水静"), ("米娜莫", "水静"), ("水面竞技场", "水静竞技场"),
                       ("水面杯", "水静杯"), ("水面", "水静")]),
    (("ミシロ",), (), [("紫堇竞技场", "未白竞技场"), ("紫堇杯", "未白杯"), ("三崎", "未白"), ("美代志", "未白")]),
    (("クロガネ",), (), [("黑钢", "黑金")]),
    (("フエン",), (), [("风香", "釜炎"), ("芙恩", "釜炎"), ("丰缘温泉", "釜炎温泉"),
                       ("枫道馆", "釜炎道馆"), ("火岩道馆", "釜炎道馆"), ("枫杯", "釜炎杯")]),
    (("ハジツゲ",), (), [("霜雪竞技场", "秋叶竞技场"), ("霜雪杯", "秋叶杯"), ("初雪竞技场", "秋叶竞技场"),
                         ("初雪杯", "秋叶杯"), ("霜雪", "秋叶"), ("初雪", "秋叶")]),
    (("ルネ",), ("フルネーム", "トルネード", "トルネコ"), [("卢内", "琉璃"), ("露娜", "琉璃")]),
    (("トウカ",), ("トウカイテイオー",), [("桃花", "橙华"), ("冬花", "橙华"), ("胜利方竞技场", "橙华竞技场")]),
    (("カンナギ",), (), [("坎纳吉杯", "神和杯"), ("坎纳", "神和"), ("神奈木杯", "神和杯"), ("神奈", "神和")]),
    (("ナギサ",), ("様",), [("澄波", "滨海"), ("凪咲", "滨海"), ("渚道馆", "滨海道馆"), ("渚竞技场", "滨海竞技场")]),
    (("ハクタイ",), (), [("白檀", "百代")]),
]


def _hits_jp(src, base, pairs):
    try:
        _, hits = P.apply_hits(src, base=base, pairs=pairs, work=None)
    except Exception:
        return set()
    return {(h.get("jp") if isinstance(h, dict) else "") for h in hits or []}


def process(work, apply=False, todo_path=None, no_todo=False):
    cfg = registry.get(work)
    aa_dir, out_dir, work_dir = cfg["aa_dir"], cfg["out_dir"], cfg["work_dir"]
    files = sorted(f for f in os.listdir(out_dir) if f.endswith("_汉化.txt"))
    stat = {"files": 0, "pad": 0, "term": 0, "todo": 0, "width_bad": 0}
    todo = []
    changed_files = []
    for fn in files:
        base = fn[:-len("_汉化.txt")]
        ap = os.path.join(aa_dir, base + ".txt")
        if not os.path.exists(ap):
            continue
        src = T.txt_read(ap)
        tr = T.txt_read(os.path.join(out_dir, fn))
        if len(src) != len(tr):
            print(f"[{base}] !! 行数不齐 {len(src)}/{len(tr)} → 跳过")
            continue
        stat["files"] += 1
        pairs = None                      # 词表按需构建：只报告改名时不必付 pairs_for 的代价（810 章 ≈ 40 分钟）
        new = []
        n_pad = n_term = 0
        for i, (s, t) in enumerate(zip(src, tr)):
            o = T.normalize_pad_placement(t)
            if o != t:
                n_pad += 1
                t = o
            n_before = n_term
            for must, mustnot, swaps in TERM_RULES:
                if not any(x in s for x in must) or any(x in s for x in mustnot):
                    continue
                for a, b in swaps:
                    if a not in t or a == b:
                        continue
                    if a in b and b in t:
                        continue        # 目标词里含源词（围巾→讲究围巾／折返→空翻折返）且已是目标形 → 别叠字
                    t2 = t.replace(a, b)
                    if t2 != t:
                        t = t2
                        n_term += 1
            if "ちゅうがえり" in s and "空翻折返" not in t and "折返" in t:
                t = t.replace("折返", "空翻折返")
                n_term += 1
            if n_term != n_before and C.disp_width(t) < C.disp_width(s):
                t = T.pad_to_width(t, C.disp_width(s))   # 改名变短 → 补回源宽度（否则 scan_width 报短宽）
            if C.disp_width(t) < C.disp_width(s):
                stat["width_bad"] += 1
            new.append(t)
        stat["pad"] += n_pad
        stat["term"] += n_term
        # 语义错译清单（用修好的词表回算：该替换却没替换的，说明中文是模型在被污染的日文上写的）
        hj = None
        for i, (s, t) in enumerate(zip(src, tr)):
            if no_todo:
                break
            if ("德其欧" in t and "できる" in s and "できる夫" not in s and "でっき" not in s) \
               or ("波导" in t and "はどう" in s) or ("灼伤" in t and "やけど" in s):
                if hj is None:
                    if pairs is None:
                        pairs = P.pairs_for(base, work=work)
                    hj = _hits_jp(s, base, pairs)
                need = []
                if "德其欧" in t and "できる" in s and "できる夫" not in s and "でっき" not in s:
                    need.append("できる")
                if "波导" in t and "はどう" in s and "はどう" not in hj:
                    need.append("はどう")
                if "灼伤" in t and "やけど" in s and "やけど" not in hj:
                    need.append("やけど")
                if need:
                    todo.append((fn, i + 1, "/".join(need), s, t))
                    stat["todo"] += 1
        if (n_pad or n_term) and apply:
            changed_files.append((fn, new))
    print(f"[stat] 文件 {stat['files']} / 补位修正 {stat['pad']} 行 / 名词替换 {stat['term']} 行 "
          f"/ 需重译 {stat['todo']} 行 / 变短行 {stat['width_bad']}")
    if apply and changed_files:
        bak = os.path.join(work_dir, "backup_fixdelivered_" + time.strftime("%Y%m%d%H%M%S"))
        os.makedirs(bak, exist_ok=True)
        for fn, new in changed_files:
            p = T.txt_existing(os.path.join(out_dir, fn))
            shutil.copy2(p, os.path.join(bak, os.path.basename(p)))
            T.txt_write_form(p, new)
        print(f"[apply] 写回 {len(changed_files)} 文件，备份 {bak}")
        T.prune_backup_dirs(work_dir, "backup_fixdelivered_")
    tp = todo_path or os.path.join(work_dir, "retranslate_todo.txt")
    if no_todo and not todo:
        print("[todo] 跳过（--no-todo，不动 work/retranslate_todo.txt）")
        return
    with open(tp, "w", encoding="utf-8") as f:
        for fn, ln, why, s, t in todo:
            f.write(f"{fn}:{ln}\t{why}\n  SR: {s}\n  TR: {t}\n")
    print(f"[todo] {len(todo)} 行 → {tp}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--todo", default=None)
    ap.add_argument("--no-todo", action="store_true",
                    help="跳过语义错译清单（那一步要按章构建 pre_terms 词表，810 章要几十分钟）；只做补位+改名时用")
    a = ap.parse_args()
    process(a.work, apply=a.apply, todo_path=a.todo, no_todo=a.no_todo)


if __name__ == "__main__":
    main()
