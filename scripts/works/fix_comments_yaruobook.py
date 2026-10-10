# -*- coding: utf-8 -*-
"""评论补译：扫描 _comments_zh.json，content_zh 残留日语(非白名单假名≥4)的重新翻译"""
import os, sys, json, glob, re, concurrent.futures, time
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
import aa_translate as T

AA_DIR = os.path.join(ROOT, "aaManga", "魔法戦姫サマーカラーとスケベ体育教師やる夫", "aa")

# 作品特有角色名/专有名词(允许保留日文)
LOCAL_NAMES = {
    'サマーカラー', 'やる夫', 'やらない夫', 'モブ川', 'アリス', 'エレナ', 'エレナ先生',
    'エロ', 'エロゲ', 'VB', 'IF', 'オリ主', '二次創作', 'ファンディスク', 'セ〇スフォニア',
    'ネタ', 'あんこ', '安価', 'R-18', 'ハードモード', '輪姦', '陵辱', '攻略',
    'オリチャー', 'オリチャ', 'ガバチャー', 'ガバチャ', 'まど神様', 'まど', 'サマカラ', 'サマーカラ', 'サマ', 'カラ',
}
KANA = re.compile(r'[ぁ-んァ-ヶ]')

def residual(line):
    """残留假名(排除白名单片段)计数"""
    cnt = 0
    for m in re.finditer(r'[ぁ-んァ-ヶ]+', line):
        s = ''.join(c for c in m.group() if c not in T.ART_HIRA and c not in T.ART_KATA)
        if s in T.KNOWN_NAMES or s in LOCAL_NAMES:
            continue
        cnt += len(s)
    return cnt

CMT_FIX_SYSTEM = """你是やる夫AA漫画评论区的中日翻译。下面是一条【翻译失败】的评论，其中还残留着大量日语，请完整翻译成简体中文。

要求：
1. 【必须】把每一处日语文字都翻译成中文，一条都不能残留。角色名/专有名词（サマーカラー、やる夫、モブ川、アリス、エレナ等）可保留日文。
2. 翻译成自然贴切的中文，保留原语气（吐槽、玩梗、感叹等）。
3. 若含 AA 绘画行，绘画字符原样保留，只翻译其中的日语。
4. 多行评论保持多行结构，换行用 \n 表示。
5. 只输出译文本身，不要任何前缀或解释。"""

def fix_one(cid, content):
    body = f"原文：\n{content}\n\n请输出完整中文译文："
    for attempt in range(3):
        try:
            text = T.call_api(CMT_FIX_SYSTEM, body, max_tokens=4096, timeout=200)
            t = text.strip()
            if residual(t) < 4:
                return t
            print(f"  cid={cid} retry {attempt+1} residual={residual(t)}", flush=True)
        except Exception as e:
            print(f"  cid={cid} err {e}", flush=True)
        time.sleep(3)
    return None

def main():
    files = sorted(glob.glob(os.path.join(AA_DIR, "*_comments_zh.json")))
    for f in files:
        data = json.load(open(f, encoding="utf-8"))
        todo = [c for c in data if residual(c.get("content_zh", "")) >= 4]
        if not todo:
            continue
        print(f"{os.path.basename(f)}: {len(todo)} residual", flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(fix_one, c["id"], c["content"]): c for c in todo}
            for fut in concurrent.futures.as_completed(futs):
                c = futs[fut]
                zh = fut.result()
                if zh:
                    c["content_zh"] = zh
                    print(f"  fixed cid={c['id']}: {zh[:50]}", flush=True)
                else:
                    print(f"  FAIL cid={c['id']}", flush=True)
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
