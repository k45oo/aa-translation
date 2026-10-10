# usage 统计 call_* 漏记录坑（2026-08-20 实测 + 修复；2026-08-28 补 opencode_zen）

## 现象

時間停止探索者やる夫 1-10 话任务完成后统计用量：
- usage.jsonl 里 12:20-13:12（翻译主流程 10 话，~7 分钟）**完全无记录**
- 第一条本次任务记录出现在 13:13（pass2 阶段）
- 按分钟分桶只有 13:13 之后的数据

## 根因

- `_log_usage` 只在 `scripts/aa_translate.py` 的 **cloud 直接模式**（call_api 末尾的 urllib 路径，L694）调用
- **`scripts/common/aa_backend.py` 的 9 个 `call_*` 后端函数（call_commandcode/call_zhipu/call_qwen_vl/call_mimo/call_minimax/call_runinfra/call_siliconflow/call_openrouter/call_ollama）内部不记录 usage**
- `aa_config.auto_backend()`（2026-08-19 按作品 r18 属性自动选后端）设了 `AA_BACKEND` env → call_api 分发走 `aa_backend.call_*` → usage 全部丢失

## 修复（commit 1140238）

1. `aa_backend.py` 加模块级 `_log_usage(prompt_tokens, completion_tokens)`（复用 `aa_config.usage_file()` 路径 + `AA_USAGE_TAG` env，异常静默）
2. 9 个 `return msg` 前统一插入：
   ```python
   u = resp.get("usage", {}) or {}
   _log_usage(u.get("input_tokens") or u.get("prompt_tokens") or 0,
              u.get("output_tokens") or u.get("completion_tokens") or 0)
   ```
3. 批量替换注意：`return msg` 缩进混合（12/16 空格），用正则 `^([ \t]*)return msg$` 匹配任意缩进

## ⚠️ 2026-08-28 复发：call_opencode_zen 漏记录（盲区再教育）

2026-08-28 核对"免费模型消耗是否全统计"时发现：**`call_opencode_zen`（aa_backend.py）没有 `_log_usage`**——它直接 urllib 请求、返回 msg，不记消耗。而免费链 FREE_MODELS 有 **3 个模型走 opencode_zen 后端**（mimo-v2.5-free / hy3-free / big-pickle），usage.jsonl 里 `opencode_zen` 后端记录 = **0 条**。这是 2026-08-20 修复后的**新后端补漏失败**——opencode_zen 是后来新增的 call_* 函数，当初没跟 9 个老后端一起加 `_log_usage`。

**修复**：call_opencode_zen 的响应解析处补 `_log_usage(resp["usage"]...)`（同 1140238 模式）。

### 排查方法（验证某后端是否漏 usage）
```
grep -c '"backend": "opencode_zen"' usage.jsonl    # 0 = 该后端从未记 usage
grep -n "def call_\|_log_usage" scripts/common/aa_backend.py   # 每个 call_* 都应调 _log_usage
```

## 通用教训（2026-08-28 强化）

- **改后端分发/新增后端 call_\* 时，必须让所有路径都落 usage**（cloud 直接模式 + 各 call_* 都要）——**这条教训要重复到 2026-08-20 之后新增的每个 call_* 函数**（opencode_zen 就是漏网之鱼）
- 排查「usage.jsonl 缺数据」先确认走的是哪条代码路径：`AA_BACKEND` 未设 = cloud 直接模式（有记录）；设了 = call_*（2026-08-20 前无记录）
- **新加任一 call_<后端> 函数，验收清单必须含"usage.jsonl 该后端记录 >0"**——grep backend 计数是零成本高杠杆检查
- 免费链/FREE_MODELS 里列的新后端尤其要查——免费模型走 opencode_zen 时若漏记，整条免费消耗统计为 0

## 事后估算法（usage.jsonl 缺失时）

翻译主流程 token 从 `work/line_meta_v2/*.json` 反推：
- 数每章 flagged 行（kind in ('text','mixed')）
- 每块 50 行 → calls = ceil(flagged/50)
- 每 call ≈ 3500 tok（2000 prompt + 1500 completion）
- 例：時間停止探索者やる夫 10 话 1637 flagged → 38 块 → ~133K tok

清理流程（pass2/pass3/fix_width）有 usage 记录（它们在 13:13 后跑，且 `_log_usage` 的 cloud 路径或修复后的 call_* 路径都落盘）。
