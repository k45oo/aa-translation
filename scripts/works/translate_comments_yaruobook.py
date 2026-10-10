# -*- coding: utf-8 -*-
"""翻译 yaruobook 评论区：逐条翻译 content，输出 entry_<pid>_comments_zh.json（断点续跑：已有翻译的评论跳过）"""
import os, sys, json, glob, concurrent.futures, time
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根(2026-08-18 Phase 3 动态计算)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
import aa_translate as T

AA_DIR = os.path.join(ROOT, "aaManga", "魔法戦姫サマーカラーとスケベ体育教師やる夫", "aa")

CMT_SYSTEM = """你是やる夫AA漫画评论区的中日翻译。用户会发来一条日本网友的评论，请翻译成简体中文。

要求：
1. 翻译成自然贴切的中文，保留原语气（吐槽、玩梗、感叹等）。
2. 专有名词/角色名保留日文（如 やる夫、サマーカラー等）；游戏/作品名可保留日文或给出常用译名。
3. 若评论含 AA 绘画或引用行，绘画字符（│｜／＼＿￣、全角空格、半角片假名等）原样保留，只翻译其中的日语文字。
4. 多行评论保持多行结构，换行用 \n 表示。
5. 只输出译文本身，不要任何前缀、解释或引号。"""

def translate_one(cid, content):
    for attempt in range(3):
        try:
            text = T.call_api(CMT_SYSTEM, content, max_tokens=4096, timeout=200)
            return text.strip()
        except Exception as e:
            if attempt == 2:
                return None
            time.sleep(3)
    return None

def process_file(path):
    data = json.load(open(path, encoding="utf-8"))
    out_path = path.replace("_comments.json", "_comments_zh.json")
    done = {}
    if os.path.exists(out_path):
        prev = json.load(open(out_path, encoding="utf-8"))
        done = {c["id"]: c for c in prev if c.get("content_zh")}
    todo = [c for c in data if c["id"] not in done]
    if not todo:
        print(f"{os.path.basename(path)}: all translated ({len(done)})", flush=True)
        return
    print(f"{os.path.basename(path)}: {len(todo)} to translate", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(translate_one, c["id"], c["content"]): c for c in todo}
        for fut in concurrent.futures.as_completed(futs):
            c = futs[fut]
            zh = fut.result()
            if zh is None:
                print(f"  FAIL cid={c['id']}", flush=True)
                continue
            done[c["id"]] = {**c, "content_zh": zh}
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump([done[k] for k in sorted(done)], f, ensure_ascii=False, indent=1)
    print(f"  done {len(done)}/{len(data)}", flush=True)

def main():
    files = sorted(glob.glob(os.path.join(AA_DIR, "*_comments.json")))
    for f in files:
        process_file(f)

if __name__ == "__main__":
    main()
