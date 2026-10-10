# Ollama 本地模型接入 AA 管线 — 实测数据与结论（2026-08-13）

> **⚠️ 状态：历史数据，已停用（2026-08-13 同日用户拍板）**。GTX 960M 4GB 跑 8b 太慢（50行块 12.2s/行、冷加载 70-115s、并发排队），笔记本不适合作业；**开机启动项已删（启动文件夹 Ollama.lnk）、进程已停**。顽固行试译/残留检测改走 **opencode zen 免费模型**（`AA_GATEWAY="https://opencode.ai/zen/v1/chat/completions" AA_MODEL=deepseek-v4-flash-free`，限流 ~200 请求/5h）。本文保留为基准数据与 `aa_backend.py` 代码形态参考，**勿按此重启 ollama 流程**。
>
> 会话实战记录：`AA_BACKEND=ollama` 后端分发接入 + 顽固行试译基准。SKILL.md 有要点摘要，本文为完整数据。

## 代码形态

`scripts/common/aa_backend.py`（后端分发器，零依赖）：

```python
OLLAMA_URL = os.environ.get("AA_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("AA_OLLAMA_MODEL", "llama3.1:8b-instruct-q4_K_M")

def backend():
    v = os.environ.get("AA_BACKEND")
    return "ollama" if v and v.lower() in ("ollama", "local", "1", "true") else "cloud"

def call_ollama(system, body, max_tokens=16384, timeout=900, temperature=0.4):
    payload = {"model": OLLAMA_MODEL,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": body}],
               "stream": False,
               "options": {"temperature": temperature, "num_predict": max_tokens}}
    # POST {OLLAMA_URL}/api/chat, 3 次重试, 空响应 raise RuntimeError
```

两处 call_api 接入（AGENTS.md 铁律：两处对称）：
- `common/aa_api.py`：模块顶部 `import aa_backend`，`call_api` 首行 `if aa_backend.backend() == "ollama": return aa_backend.call_ollama(...)`
- 平铺 `scripts/aa_translate.py`：函数内 `sys.path.insert(0, os.path.join(_THIS_DIR, "common"))` + `try: import aa_backend`（ImportError 回落云端，兼容老环境）

## 资源消耗（推理中实测，GTX 960M 4GB / i7-6700HQ 16GB）

| 项 | 值 |
|---|---|
| 显存 | 3669/4096 MiB（89%） |
| GPU 利用率 | 41%（推理中） |
| ollama runner 进程内存 | 2.98GB（模型全量驻留） |
| ollama 服务进程内存 | 95MB |
| 冷加载 | 70-115s（一次性，测试进程启动即付） |
| 热状态单行 | 5.6s（prompt 短时） |

读取进程资源（git-bash 下 PowerShell 引号易碎，用 wmic）：
```
wmic process where "name like '%ollama%'" get Name,ProcessId,WorkingSetSize
nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader
```

## 速度/分块基准（50 行真实台词，白头第 1 话含假名行）

| 分块 | 总耗时 | 每行 | 解析率 | 宽度通过 | 残留 |
|---|---|---|---|---|---|
| 50 行×1块 | 611s | 12.2s/行 | 50/50 | 49/50 | 1 假阳 |
| 10 行×5块 | 408s | 8.2s/行 | **40/50（漏10）** | 39/50 | 0 |

结论：
1. **小块并不更好**——10 行块漏行率 20%（模型块间状态不稳），大块解析率 100%
2. **并发严格串行**——4GB 显存下 ollama 单模型队列，两个并发请求互相拖慢（50 行块 4 分钟→10 分钟）。流程必须串行，本地 MAX_WORKERS=1
3. **格式稳定性**：完整 SYSTEM 提示词（aa_api.SYSTEM）下多行「序号|译文」格式稳定；简化提示词（"只输出译文"）会丢序号前缀 → parse_result 解析不到
4. 10 行块速度快的部分是 prompt 处理摊销，但被漏行率抵消——顽固行仍用 20-50 行块

## 质量验证（translate_lines 全流程）

- 8 条混合行（含 2 条 pass3 顽固残留）走 `AA_BACKEND=ollama python -c "from aa_api import translate_lines; ..."` → **8/8 通过**，宽度/残留零问题，12.6s/行（含 3 轮补译/pad）
- 顽固行实例：`奴はその扉の先か？` → `她是在那扉后面吗？`（日文被完整翻译，无残留，宽度保持）——正是 refill_manual.tsv 卡壳场景
- 术语/角色名保留 OK（`白頭`→`白头` 正确）

## 验证模式（hermes-verify- 临时脚本，11 项）

1. py_compile ×3（aa_backend/aa_api/aa_translate）
2. 默认 backend()==cloud（云端零影响）
3. `AA_BACKEND=ollama` 切换生效
4. 两处 call_api 对称性 grep
5. call_ollama 真实连通返回文本
6. translate_lines 端到端 4/4（宽度全保持 + 残留全清零）

## 定位结论

- ✅ 顽固行试译（refill_manual.tsv width_fail 场景）——甜点，后台跑零成本
- ✅ 残留检测二分类（pass3 顽固残留）
- ❌ 主翻译不换（12s/行 vs 云端快 2 倍）
- 311 行顽固行 ≈ 60-65 分钟（后台可接受）；只译真残留（~10 条）≈ 2 分钟
- qwen2.5:7b 对照未测（基准 36/42 并列可用，中文语感略优）
