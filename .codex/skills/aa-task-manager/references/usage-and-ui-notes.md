# 任务页 token 聚合 + 前端分区刷新实现要点（2026-08-29 会话现场）

## usage.jsonl 按任务聚合

- 三处 `_log_usage`（`scripts/aa_translate.py` / `scripts/common/aa_backend.py` / `scripts/tools/vision_classify.py`）的 rec 都加了 `"task": os.environ.get("AA_TASK_ID")`。runner 子进程由管理器 spawn，env 带 `AA_TASK_ID`，所以翻译/校对/视觉全部调用自动落 task 字段。
- `aggregate_usage(t)`：优先按 `r.get("task")==tid` 精确匹配；无 task 匹配时按时间窗 `created_ts ~ finished_ts` 兜底并标 `approx:True`（可能混入并行任务）。返回 `{prompt,completion,total,calls,backends:[去重]}`——`backends` 用于页面显示实际走的后端（免费链=commandcode/siliconflow 等）。

## 审计「这次运行实际有哪些模型参与」

`status <tid>` 的 `usage.backends` 只是**去重后端名列表**（如 `[commandcode, deepinfra, deepseek, runinfra]`），没有模型维度，也看不出占比——想知道链上哪个模型真在干活、有没有哪条后端异常占比，直接自己聚合：

```python
import json, collections
agg = collections.defaultdict(lambda: [0, 0, 0])      # (backend, model) -> [calls, in, out]
for fn in ("usage.jsonl", "usage.jsonl.1", "usage.jsonl.2", "usage.jsonl.3"):   # 滚动文件必看
    for line in open(fn, encoding="utf-8", errors="replace"):
        try: d = json.loads(line)
        except Exception: continue
        if d.get("task") != TID: continue               # 按 task 字段精确过滤，别用时间窗
        a = agg[(d.get("backend"), d.get("model"))]; a[0] += 1
        a[1] += d.get("prompt") or 0; a[2] += d.get("completion") or 0
```

**怎么读**：
- **全链成员都出现是正常的**（`call_with_fallback` 的端点均匀轮询把每章起手端点分散开）——链首占 30% 左右不代表异常；「链首通吃」反而要查是否退化为单端点。
- **`(backend, model)` 里出现 `cloud`/默认 mimo 且占比高** = auto-backend 决策错了（云端直连模式，大块漏译信号）；出现已摘除的后端（如未充值的 siliconflow `402`）说明配置文件没同步。
- 只想看免费/付费、不在意模型名时用 `status` 的就够了；**排查漏译/质量归因必须用上表**（能反查是不是某个模型在硬词章拖后腿）。
- **`usage.jsonl` 不看 `.1/.2/.3` 会漏历史记录**（写入侧滚动，见 `references/usage-rotation.md`）；**CLI 直跑的任务没有 `task` 字段**，按它过滤会漏（也正因此不会混入别的任务）。

## 校对阶段为什么 token 不涨

refine_aa 的 deepinfra GLM 路径自建 `urllib` 请求、**不经 `aa_backend._log_usage`** → 校对阶段的调用**不落 usage.jsonl**；只有降级到官网 deepseek 时才记一条。所以：
- 任务页/`status` 的 token 数在**校对阶段基本不动属正常**，不要据此判断任务停摆（看 phase/章节进度/runner_log）。
- 要给校对计 token，需在 `refine_aa._call_glm_deepinfra` 里补 `_log_usage(tag="refine")`（与 `扩展` 节第 5 条同根：不写 usage 还会让假死检测误杀）。

## `_task_public` 前端字段（main.py）

```python
d["chapters"] = params.chapters
d["title"]    = registry.get(work)["title"]      # 日文名，页面与 work id 同显
d["phase/chapter_idx/chapter_total"] = hb.*      # 放顶层，不在 t.hb —— 前端读 t.phase 而非 t.hb.phase
d["free_mode"] = params.env.AA_FREE_MODE == "1"
d["elapsed"] = started_ts ? (finished_ts|now) - started_ts : None
d["eta"] = running && chapter_idx>0 ? elapsed/idx*(total-idx) : None
d["usage"] = aggregate_usage(t)
```
`api_tasks_list(log: int = 0)`：**不带 log 参数时返回列表不带 log 字段**（省 IO）；`?log=1` 才 `get_task(tid, log_tail=20)`。

## 新增任务控制页的 refreshStatus 必须镜像 translate.html（2026-09-07 proofread 页事故）

新建带任务状态的页面（如校对控制页 `proofread.html`）时，`refreshStatus` 必须**照着 translate.html 的写法**：
- 读 `_task_public` 的字段：`j.status / chapters / phase / started / finished / rc / log / type`。**`_task_public` 从不返回 `msg` 字段**——用 `box.innerHTML = j.msg` 会恒空白（本会话 proofread.html 就这么写，任务状态空白）。
- **必须给日志元素赋值**：`$('task-log').textContent = j.log || ''`——漏了则日志永远不显示（哪怕服务器 runner_log 明明有内容，用户报「任务有但没日志输出」）。
- **必须建 5s 轮询**：启动成功后 `pollTimer = setInterval(() => refreshStatus(), 5000)`，终态 `done/failed/crashed` 才停；只在页面加载调一次=永不更新（用户报「启动后页面没反应」）。
- 页面路由和 API 都用 `/api/work/{work}/translate/status`（读最近任务，兼容各类型页轮询），不必为 proofread 单独建 status 接口。

## 前端分区刷新（tasks.html，核心防折叠方案）

- `openLogs` 是模块级 `Set<task_id>`；`render()` 按 `data-tid` 找已有 `.task-card`，**复用 DOM 只更新 `.st`（状态区）innerHTML**，日志区 `.log-box` 保留不重建 → 3s 轮询不折叠。
- 「打开日志/关闭日志」显式按钮（`toggleLog`），不再用 `<details>`（details 重建会折叠，且 toggle 事件绑定在重建后丢失是用户痛点）。
- `refresh()` 只在 `openLogs.size` 非空时请求 `?log=1`，render 里对展开中的 box 直接 `box.textContent = t.log`。
- **日志 20 行全显示不滚动**（用户明确要求，比自动滚动到底简单可靠）：后端 `_log_tail(t, n=20)` + 前端 `.log-box { max-height:none; overflow:visible }`。轮询下"自动滚动"方案会反复触发滚动打断阅读——放弃滚动，让内容高度自适应全部可见。

## 浏览器实测工作流（本用户要求交付前浏览器验证）

- 页面改动后：`python - <<'PY' subprocess.Popen(['cmd','/c','start','','一键重启工作台.bat'], cwd=仓库根, creationflags=0x8|0x200, stdout/stderr/stdin=DEVNULL, close_fds=True) PY` 重启 → `browser_navigate` → `browser_console` 用 `document.querySelectorAll` / `getComputedStyle` / `fetch('/api/tasks')` 断言真实 DOM（title 颜色、进度条宽度、日志行数、`scrollHeight<=clientHeight` 无滚动、4s 后 `display` 仍 block）。
- **页面 JS 缓存坑**：改模板后旧 JS 可能被浏览器缓存，`browser_navigate` 重进页面会拉到新模板（Jinja 渲染内联 script），但 `location.reload(true)` 不一定清缓存——直接重新 navigate 即可。

## 状态纠偏（用户停止被误标 crashed 时）

用户停止 → 进程在章边界退出但 rc 没写（executor 吞 SystemExit bug）→ reconcile 标 `crashed`。手动纠偏：改 `data/tasks/<tid>.json` 的 `status="stopped"`、`rc=41`（日志有「收到停止请求」痕迹即可确认是用户停止）。根治见 SKILL.md 协议段（TaskStoppedError + executor 外 sys.exit）。
