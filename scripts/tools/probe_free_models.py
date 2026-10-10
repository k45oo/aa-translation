# -*- coding: utf-8 -*-
"""探针: 测试免费模型可用性(基准同口径)。

用法:
  python scripts/tools/probe_free_models.py [--model THUDM/GLM-Z1-9B-0414] [--n 3]

已知免费候选(2026-08-31):
  - siliconflow  THUDM/GLM-Z1-9B-0414  (免费档, 走 SILICONFLOW_FREE_API_KEY; 实测可用但 24s/批慢, 已接入免费链双key)

测速: 短批(6行)+长批(42行), 格式遵守率/翻译质量/假名残留。
硅基流动用独立免费 key。
基准方法详见 doc/线上免费模型统计.md
"""
import sys, os, time, re, argparse
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(BASE, "scripts"))
sys.path.insert(0, os.path.join(BASE, "scripts", "common"))
import aa_backend as AB

SHORT = [
    "おいおい、また失敗したのかよ",
    "ち、ちげえよ！今のは狙ってやったんだからな！",
    "ｶﾞｸｶﾞｸ…もう無理だ…足が震えてる…",
    "まあまあ、落ち着けって。飯でも食おうぜ",
    "ふんっ、そんなもんかよ。俺様の敵じゃねえな",
    "や、やめろおおおおお！！",
]
LONG = SHORT * 7  # 42 行近似 40 行长批

SYSTEM = ("把以下日文 AA 漫画台词逐行翻译成简体中文。"
          "格式严格为 行号|译文, 每行独立, 不要加解释。"
          "角色名用中文译名, 拟声词也要译。")

CALLS = {
    "THUDM/GLM-Z1-9B-0414": lambda s, b, **k: AB.call_siliconflow(
        s, b, model="THUDM/GLM-Z1-9B-0414", key=AB._sf_free_key(), **k),
}


def _measure(fn, lines, max_tokens, timeout):
    body = "\n".join(f"{i+1}|{l}" for i, l in enumerate(lines))
    t0 = time.time()
    try:
        r = fn(SYSTEM, body, max_tokens=max_tokens, timeout=timeout)
    except Exception as e:
        msg = str(e)
        code = ("1113余额不足" if "1113" in msg else
                ("1305过载" if "1305" in msg else f"{type(e).__name__}: {msg[:60]}"))
        return None, code
    dt = time.time() - t0
    out = [l for l in r.split("\n") if l.strip()]
    ok = sum(1 for l in out if re.match(r"^\d{1,5}\|", l))
    kana = len(re.findall(r'[\u3040-\u30ff\u31f0-\u31ff]', r))
    return (dt, len(out), ok, kana, out), None


def run(name, n=1):
    if name not in CALLS:
        print(f"未知模型: {name} (可选: {list(CALLS)})")
        return
    fn = CALLS[name]
    print(f"\n===== {name} x{n} =====")
    passed = 0
    for i in range(n):
        short, err = _measure(fn, SHORT, 2000, 90)
        if err:
            print(f"  [#{i+1}] 短批失败: {err}")
            continue
        dt, ln, ok, kana, _ = short
        print(f"  [#{i+1}] 短批{ln}行 {dt:.1f}s 格式{ok}/{ln} 假名{kana}")
        long_r, err2 = _measure(fn, LONG, 6000, 150)
        if err2:
            print(f"      长批失败: {err2}")
            continue
        dt2, ln2, ok2, kana2, out2 = long_r
        print(f"      长批{ln2}行 {dt2:.1f}s 格式{ok2}/{ln2} 假名{kana2}")
        print(f"      样例: {out2[0][:50] if out2 else 'EMPTY'}")
        passed += 1
    print(f"  通过: {passed}/{n}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="", help="指定模型名; 空=测全部")
    ap.add_argument("--n", type=int, default=1, help="每模型重复次数(默认1)")
    a = ap.parse_args()
    models = [a.model] if a.model else list(CALLS)
    for m in models:
        run(m, n=a.n)
