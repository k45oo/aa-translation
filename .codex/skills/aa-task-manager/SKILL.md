---
name: aa-task-manager
description: Use when 维护/扩展 AA 统一任务管理器(翻译/下载编排, web/CLI/HTTP 双档停止).
---

# AA 统一任务管理器（2026-08-29 落地，方案 doc/翻译进程管理方案_2026-08-29.md）

把翻译/下载这类长耗时 runner 任务集中编排：注册表文件=单一事实源 + FIFO 队列 + 调度器 pid 锁 + 两档优雅停止 + 落盘持久化。Web `任务` 页 / CLI / HTTP 三入口，工作台不开也能提交（CLI 自动拉起独立调度器）。

## 架构（组件 & 单一事实源）

```
入口 ── 作品控制台页 / HTTP POST /api/tasks / CLI task_manager.py enqueue
          └──────► enqueue() ─► 注册表 data/tasks/<task_id>.json + queue.json(FIFO)
                              (文件锁原子写, 同作品串行 enqueue 即拒绝)
                              ▼
                        调度器主循环 (单例 .scheduler.lock pid 锁)
                        · 工作台内嵌线程；工作台没开时 CLI 拉独立进程排空队列后退出
                        · 全局并行 ≤ MAX_PARALLEL(2)，FIFO 严格出队，同作品串行
                              │ Popen(-u, env AA_TASK_*, 日志 runner_log.txt) + watchdog 线程
                              ▼
                        translate_runner / dl_runner 子进程
                        · 周期写心跳 → .hb_<task_id>.json (sidecar, 与任务文件分开防争写)
                        · 两档停止检测: 章/阶段边界(chapter) + 块边界(asap, 取消未开始块, ckpt 续跑)
                        · 退出前 finalize(rc) 写心跳, 退出码 41=用户停止
```

## 文件格式（data/tasks/）

- `<task_id>.json` 任务记录（管理器所有）：`{task_id,type,work,params,status,pid,rc,created/started/finished(+_ts),stop_mode,killed}`。status ∈ `queued|starting|running|stopping|done|failed|stopped|crashed|cancelled`。**整个 `data/tasks/` 已进 .gitignore（运行时状态不入库，非 work_meta.json 那种手写数据）。**
- `.hb_<task_id>.json` 心跳 sidecar（runner 所有，`scripts/common/task_report.py` 写）：`{task,phase,chapter_idx,chapter_total,detail,hb_ts,rc}`。
- `.stop_<task_id>.chapter` / `.stop_<task_id>.asap` 停止标志（请求停止时写，runner 见到即删）。
- `queue.json` `{"queue":[task_id,...]}` FIFO。
- `.scheduler.lock` `{"pid":N,"ts":...}` 调度器单例。

## 协议（scripts/common/task_report.py）

env 注入：`AA_TASK_ID` / `AA_TASK_HB` / `AA_STOP_CHAPTER_FILE` / `AA_STOP_ASAP_FILE`。
- 未设 `AA_TASK_HB`（直跑不经管理器）时全部 no-op，零侵入。
- `heartbeat(phase, idx, total)` 心跳，1s 节流、phase 变化/force 立即写。**idx 语义 = 已完成单位数，必须在本单位处理完**之后**报（章级循环写 `fi+1`，处理前写 `fi`）**——只在循环头报 `fi` 时最后一个单位永远报不到「完成」，任务卡/校对页就永远挂着「待 N 1」；**这类 off-by-one 的根在 runner 的报点，不在前端**（前端只展示 `tot-idx`）。已跑过的任务不会回填，重启新任务才生效。
- `check_stop(only=None)` 见标志即删并返回 `"asap"|"chapter"|None`；**asap 优先**；块边界循环必须用 `check_stop(only="asap")` 防误消费 chapter 标志。
- `peek_stop(only=None)`（2026-09-12）**只读不删**：内层阶段循环（如章节简介生成跑上千章）用它提前退出，标志必须留给外层章/块边界消费——内层若用 check_stop 消费掉，外层收不到请求会继续往下跑（实测 translate_runner 简介阶段只能靠 kill 才停住）。
- `finalize(rc)` 退出前写终态 rc；管理器 reconcile 时 pid 死 + 有 rc → 按 rc 定终态（0→done / 41→stopped / 其他→failed / 无 rc→crashed）。
- `TaskStoppedError` 块边界停止：取消未开始块、收完在飞块写 ckpt 后弃本章（ckpt 有 `__lines` 指纹防错位，可续跑）。
- 退出码约定：0=完成，30=zhipu guard，**41=用户停止**。
- **停止退出必须在 executor 块外 sys.exit**：`aa_translate.py` 章节并行循环是 `with ThreadPoolExecutor(...)` 包着，**在块内 `sys.exit(41)` 会被 executor 收尾吞掉**——日志打了「收到停止请求」但进程继续跑完当前章、`finalize(rc)` 没写（rc=null）→ 管理器 reconcile 只能标 `crashed`。正确姿势：块内 `ex.shutdown(cancel_futures=True)` + `raise TaskStoppedError(f)`，异常传播出 `with` 块后在 main() 底部 `except TR.TaskStoppedError: _rc=41; raise SystemExit(41)`，`finally: TR.finalize(_rc)` 保证 rc 写入。诊断 stopped-显示-crashed：查任务文件 `rc` 是否 null + 心跳无 `rc` + runner 日志尾「收到停止请求」痕迹。

## CLI / HTTP 入口

```bash
python aa_web_app/task_manager.py enqueue --type translate|download --work <id> --chapters <eids> [--force] [--no-gen] [--free-mode] [--env K=V ...]
python aa_web_app/task_manager.py list | status <tid> | stop <tid> --mode chapter|asap | kill <tid> | cancel <tid>
python aa_web_app/task_manager.py --scheduler   # 独立调度器, 排空队列+运行清空后退出
```
`--free-mode` = `--env AA_FREE_MODE=1`（免费链）；`--env` 可多次。**task 参数 `env` 透传**（`params.env` dict → runner 子进程 env）：task 级行为（免费链/后端选择）不依赖调度器环境，工作台/CLI 谁 spawn 都一样。HTTP `/api/tasks` body 也收 `env`。
HTTP：`GET /tasks`(页) `GET/POST /api/tasks` `POST /api/tasks/<tid>/stop|kill|cancel|remove`。作品页 `POST /api/work/<id>/translate` 已改走 enqueue（body 收 `eids/force/no_gen/free_mode`）；`.../status` 读该作品最近任务；`.../cancel` = force_kill（兼容旧语义）。`GET /api/tasks?log=1` 才带日志尾（轮询列表不带省 IO；前端只在有展开日志时带 `?log=1`）。

**⚠️ enqueue 前 registry 必须 reload（2026-08-31 实战）**：CLI 脚本直接改 registry.py 磁盘（`_append_registry_entry`）后调 `TM.enqueue`，**task_manager 内存里是旧 registry 快照** → 报「未知作品 <id>」但磁盘条目已写入（重跑会显示已存在）。修法：enqueue 前 `importlib.reload(registry)`（runner 子进程每次启动重新读磁盘不受影响，只有同一进程内的 enqueue 检查需要新快照）。

## 监控长任务与「哪些模型实际参与了」审计

- **轮询节奏**：`status <tid>` 看 `running/done` + phase + `chapter_idx/total` + usage + 日志尾。长任务（15 章 × 2400 行级）用**多轮「短 sleep(≲400s) + status」**，别指望一次 terminal 调用等到结束（超长 sleep 会被截断、丢输出）。**任务独立于会话**：调度器/runner 是独立进程，会话/终端断不影响——不要因为它跑到下个会话就重跑（重跑会绕 only_cleanup/重译）。
- **看「实际哪些模型参与」不能只看 `status` 的 `usage.backends`**（那只是去重后端名列表，无模型维度）：要 per-model 明细就按 `task` 字段过滤 `usage.jsonl` 聚 `(backend, model)`——配方与解读见 `references/usage-and-ui-notes.md`。
- **usage 涨不涨要分阶段读**：翻译阶段 usage 持续增长；校对(refine_aa) 阶段自 2026-09-13 起**也记账**（`tag="refine"`：GLM 主链走 `_log_refine_usage`，deepseek 兜底路径临时置 env `AA_USAGE_TAG=refine` 包住 `call_deepseek`）——查校对成本就筛 `tag=="refine"` 按 (backend,model) 聚合。**通用规则：任何自建 HTTP 的模型调用路径都必须挂 `_log_usage`（带 tag 区分）**，否则该阶段 token/成本/模型审计全是黑账，且只能靠假死豁免绕过去；新接入的 runner/模型路径先问一句「它写 usage 吗」。**别把「usage 停了」当假死**（免费链退避/格式不达标重试期间也会 5-10 分零记录），判据同下「假死诊断」。

## 扩展（新任务类型）

1. `TYPES` 元组加类型名。
2. `_build_command(ttype, work, params)` 加分支。
3. runner 侧调 `task_report.heartbeat` / `check_stop` / `finalize`（无 env 自动 no-op）。
4. 测试 `scripts/tests/test_task_manager.py` 加用例（可 patch `_build_command` 到假 runner）。
5. **⚠️ 新增任务类型必须评估假死检测（2026-09-07 proofread 事故）**：`scheduler_tick` 的 watchdog 对 **ALL running 任务**跑 `_stall_check`（不只 translate）——若新 runner **不记 usage**（如 refine_aa 直连 deepinfra 自建 HTTP, 不走 aa_backend._log_usage），`_usage_index` 无该 task 记录 → 判停滞必误杀。要么给 runner 的模型调用加 usage 记录(tag 区分), 要么在 tick 循环里 `if t.get("type")=="<新类型>": continue` 豁免(适合短任务, 靠两档停止+force 兜底)。**教训：加新类型先查 runner 是否写 usage.jsonl; 不写就豁免, 否则任务跑几分钟被假死检测杀。**
6. **runner 适配 task_report 的最小姿势**（无 env 自动 no-op 零侵入）：模块内 `import task_report as TR`；章级循环开头 `TR.check_stop()`→命中抛自定义 `_Stop` 异常; `TR.heartbeat("阶段", i, n)`; main 收尾 `try:... TR.finalize(0) / except _Stop: TR.finalize(41); sys.exit(41)`。停止必须在**章/块边界**(循环内 check), 不要在单行处理中 check。
7. **长内层阶段必须配「三件套」（单次几十秒 × 上千次调用的批处理，如章节简介生成）**：①**逐项增量落盘**，且落盘用原子写（tmp + `os.replace`）——循环末尾统一 save 的写法，中途崩溃/被 kill 时已生成内容**全部丢失**（千项批次白跑几小时）；②**逐项心跳** `heartbeat(phase, i+1, total, force=True)`（force 绕 1s 节流，否则快速项不刷新进度）；③`peek_stop()` 在项边界退出且**不消费**标志（标志留给外层章/块边界）。**「进度恒 0/N」多是宿主 runner 写死 `heartbeat(phase, 0, ...)` 而内层不写心跳**，不是卡死；批量阶段要人工停时，标志不被内层消费就只剩 kill 一条路。

## 工作流（本用户偏好）

重大改动**先分析后执行**：grilling 访谈定稿 → 写 `doc/<方案>_<日期>.md` + commit → 实现按阶段推进、**每阶段独立 commit** → 全量 `python -m unittest discover -s scripts/tests` 绿后再收尾。本仓库 **227 用例基线**（task_manager 23 + task_report 9 + E2E 2 等）。

**翻译统一走本管理器（2026-08-30 用户硬性要求，禁直接跑 translate_runner）**：
- 入口：`python aa_web_app/task_manager.py enqueue --type translate --work <id> --chapters <eids> [--free-mode]` → 返回 task_id → `python aa_web_app/task_manager.py status <tid>` 轮询（running/done + phase + usage + 日志尾）。
- 实测（2026-08-30 idolmaster_yaruo 第15话 entry_74937，2655 行）：~23 分钟 done，340,883 tokens/102 calls（主译 commandcode + 校对 zhipu），0 宽度错位。翻译中 phase 依次 翻译→清理→校对→done；`usage.backends` 显示实际后端链（zhipu 免费可能被 commandcode 替代=自动降级，非异常）。
- 任务完成后验证清单：`scan_width --work <id>` 0 错位 + 残留扫描（纯图形碎片是误报）+ 抽查文本行质量。
  **2026-10-06 起任务链路里没有「生成合集」这一步了**（任务管理器/`translate_runner` 都无 `--gen`，合集一律手动）——任务 done 只代表 txt 产物到位；要看交付页的新章，必须自己跑 `python scripts/common/gen_aa_html.py --work <id>`（零 token，秒级；写出**交付文件夹** `交付HTML/<作品>_汉化合集/`）或点工作台「重生成合集」按钮，并确认章节数。

## 新增「非终态」状态：`scheduled` 定时任务的完整触点（2026-09-29）

`params.schedule_at`（unix 秒；`parse_schedule_at` 收 unix 数字串 / `YYYY-MM-DD HH:MM` / datetime-local 的 `T` 形式）
→ 未来时刻入队即 `scheduled`（仍在 `queue.json`），到点由 `scheduler_tick` 转 `queued` 再按 FIFO 启动。

**加一个非终态必须同步的七个点（漏一个就是「卡死/误杀/删不掉」）**：
1. **同作品串行检查要豁免它**——否则一个 00:00 的任务把该作品锁一整天（这是本功能的根本动机）。
2. **候选选取要看「到点」**：`_queue_head` 类函数里跳过未到点的条目，**但不能 break**——未到点的排头不能阻塞它后面的 queued 任务（回到「第一个 queued」）。
3. **并行计数 / 假死检测**（`_stall_check`）/ reconcile 不要把它当 running（无 pid → 会被误判 crashed）。
4. `cancel_queued` 对 queued/scheduled 同权（任务页两态都出「取消排队」）。
5. `prune_finished_tasks` / `_FINAL` 集合不要收它（未开始的任务不是「结束」）。
6. 前端 `tasks.html`：`STATUS_C` 加颜色、`ACTIVE`/`KEEP_ON_CLEAN` 加它（否则轮询停、一键清理会把它删掉）、按钮分支。
7. 与「链式后续 `then`」共存：`then` 只在 rc==0 入队，schedule_at **不要**继承进后续任务。

**测试姿势**（零 token）：patch `_build_command` 成 `pass`；「到点转 queued 启动」必须直接改任务 json 的 `params.schedule_at = now-5`（不是重新 enqueue，否则又要等）；「到点但同作品忙」要先让同作品真跑起来（假 runner 换成 `time.sleep(30)` 子进程，收尾 kill），
**不能**先用 `_task(status="running")` 造占用——enqueue 会被串行检查直接拒，根本到不了 tick。
用例集：`scripts/tests/test_schedule_task.py`（25 例，含回归：未到点不阻塞后续、死队列项剔除）。

## 链式后续任务 `params["then"]`（2026-09-13，「下载并翻译」）

**动机**：translate_runner 不再自动补下载 → 「下载并翻译」必须显式两段；链式由管理器内部完成，调用方不用自己轮询接续。

- **写法**：前置任务 `params["then"] = {"type":"translate", "chapters":"<eids>"[, "force"/"gen"/"env"/…]}`（`params` 子键可嵌套，平铺键直接透传；省略 `chapters` 时继承前置任务的 chapters）。CLI 等价 `enqueue --type download --work <id> --chapters <eids> --then-translate`。
- **执行点**：`_finalize(t, rc)` 末尾调 `_chain_followup(t, rc)`——**只在 rc==0 时入队**；停止/失败/崩溃只写 `t["followup"]={"ok":False,"error":…}` 不入队（防对半成品烧 token）。`_finalize` 的幂等早退保证不会重复链式；链式 enqueue 失败（如同作品被占用）不反噬前置任务状态，错误落 `followup.error`。
- **时序铁律**：链式必须在 `_clear_legacy_lock(t)` **之后**执行——否则旧 `.runner.lock` 仍带前置任务 pid，enqueue 的同作品串行/旧锁检查会拒绝后续任务。
- **可见性**：`followup` 由 main.py `_task_public` 透出（`{type, task_id, ok, error}`），任务页可显示「完成后自动接 <tid>」。
- **调用方**：`POST /api/work/{work}/update` body `{action:"translate"}` = enqueue **download** + `then={"type":"translate","chapters":<eids>}`（action=download 无 then）。
- **测试姿势（零 token 端到端）**：patch `TM._build_command` 成 `[sys.executable, "-c", "pass"]` → `enqueue("download", w, {"chapters":"5","then":{"type":"translate"}})` → `scheduler_tick()` → 轮询前置任务 `followup.task_id` → 再 tick 到后续任务 done。单测另覆盖 rc≠0 不链式、幂等不双链、env 透传。

## 销毁性导出一键按钮（2026-09-07 事故）

**凡触发会覆写已译内容的操作（refine --write 全量/批量替换/重生成覆写汉化），前端不用「一键按钮直接 enqueue」**——一个按钮点下去就全量 `--write` 会意外销毁大量已汉化章（本会话误触覆写 3 章，靠 `work/backup_refine_aa_<ts>/` 回滚）。正确形态 = **链接到独立控制页**（如 `/work/{work}/proofread`，对称翻译控制台），带**章节勾选** + 明确「启动」确认，默认只列**已译章**供选。

**回滚姿势**：runner 每章覆写前自动存 `work/backup_refine_aa_<ts>/entry_<eid>_汉化.txt`——误覆写后用对应 backup 目录还原 `汉化/` 文件（还原前先把当前被覆写版另存 `*.REFINE_AFTER` 备核），再 `wc -l` 校验行数与原文章节一致。销毁性功能交付前必须浏览器实测按钮/链接触发路径，别只验证路由存在。

## Web 页（tasks.html）用户偏好（2026-08-29 迭代）

- **复用 status API 的轮询页必须对齐 `_task_public` 返回字段，勿臆造 `msg`**（2026-09-07 校对控制页空白事故）：`/api/work/{work}/translate/status` 返回的是 `_task_public` 的字段 = `status/chapters/phase/chapter_idx/total/started/finished/rc/type/log/usage/free_mode/title`——**没有 `msg`**。自建轮询页若写 `box.innerHTML = (j.msg || '')` 会恒空白且不显示日志。正确姿势 = **直接复用 translate.html 的 refreshStatus 脚本**（`j.status` 上色 + `j.phase/chapters/rc` 拼接 + 5s `setInterval` 轮询 + 终态停轮询），改 workId 即可，别从零写。**作品控制台页不渲染日志**（2026-09-13 用户定：日志块与 `$('task-log')` 填充分支已从 translate.html/proofread.html 删除，日志只在 `/tasks` 菜单页看）——照旧抄 `$('task-log').textContent = j.log` 会因元素不存在抛 TypeError，该页后续脚本（含轮询）全不执行。这类页也用 `/translate/status` 端点（读该作品最近任务，proofread/translate 通用）。
- **分区刷新**：`render()` 按 task_id 复用已有卡片 DOM，只更新状态区 `.st`，日志区 `.log-box` 保留——**日志展开状态不因 3s 轮询折叠**。新增卡片 append，不留空 DOM。
- **日志只显示最新 20 行、高度足够全显示不滚动**：后端 `_log_tail` n=20；前端 log-box `max-height:none; overflow:visible`（彻底不滚动，比"自动滚动到底"方案简单可靠——滚动逻辑在轮询下反复触发是用户痛点）。
- 卡片：作品名 `.w-title` 亮灰(#e8e8e8) + 日文标题 `.jp-title` 蓝灰同显；免费链/付费链徽章；阶段 + 已翻译/待译 + 进度条 + 已运行时长 + 预计剩余 + token + 去重后端列表；运行中显示两档停止 + 强制终止按钮，**终态显示「删除」按钮**（`remove_task`，终态才能删，运行中拒绝）。
- 作品列表页（index.html）：操作列「阅读 详情 打开 更多」**四按钮同一行**（`.primary-row` flex 内）——「打开」是独立按钮（在更多左边），**更多菜单去掉省略号**（`更多…`→`更多`），菜单内不再有「打开」（已移出）。
- 翻译控制台（translate.html）：**「模型」下拉**（默认付费链 / 免费模型=AA_FREE_MODE=1）→ 提交 body `free_mode` → 后端 enqueue params.env 加 `AA_FREE_MODE=1`。

## 并行上限动态配置（2026-08-31/09-01）

- **默认值**：`MAX_PARALLEL`=2（付费翻译）、`MAX_PARALLEL_DL`=4（下载）、`MAX_PARALLEL_FREE`=1（免费翻译）——模块加载时读 env。
- **2026-09-01 三池分离**：`data/tasks_parallel.json`（`{"translate":10,"download":4,"free_translate":1}`）+ `_parallel_limits()` 每次调度读盘 → **改文件即时生效，无需重启**。CLI：`python aa_web_app/task_manager.py set-parallel --translate 10 --free-translate 1 --download 4`。
- **免费/付费翻译分池计数**：scheduler_tick 里 `_is_free(t)`（params.env.AA_FREE_MODE=1）分 `n_tr_free`/`n_tr_paid` 两池——免费限 1、付费可到 10，互不占名额（否则免费任务会被付费任务挤掉）。粗筛 `n_running >= mp_tr+mp_dl+mp_ft`，细筛按 head 类型+免费与否选对应池。
- **免费任务章内块级并行=10（2026-09-01 用户拍板，注意层级！）**：`_start_task` 对 `AA_FREE_MODE=1` 的 translate 任务注入 `AA_FREE_WORKERS=10`（→ aa_translate 的 `MAX_WORKERS`，单章内 API 调用并发）。**并行分三层，改前先分清**：①任务级=调度器池（free_translate/translate/download）；②章节级=`AA_CHAPTER_WORKERS`（免费模式 translate_runner:261-262 **强制串行=1**，注入此变量对免费任务无效！）；③章内块级=`AA_FREE_WORKERS`/`MAX_WORKERS`（免费模式默认=3，10 需注入）。「免费翻译单章串行章内并行」= ②串行 + ③并行。付费任务不注入保持默认。
- **坑：改 env 不重启不生效**——工作台内嵌 scheduler 的 env 是进程级的，`AA_TASK_MAX_PARALLEL` 只在 import 时读一次。改并行走配置文件即可（`set-parallel` 写盘即时生效），无需重启。
- **⚠️ 工作台重启后 scheduler 线程可能不自动 tick**（2026-08-31 实测：任务卡 queued 数分钟不启动，日志/心跳全停）——`@app.on_event("startup")` 启动的线程可能未跑/已崩。诊断：任务文件 mtime 停摆 + API 正常 → 手动 `TM.scheduler_tick()` 一次即启动（`_reconcile_task` 接管 running 任务）。重启后跑完第一个任务前先手动 tick 验证。
- **stalled 是已杀终态（2026-09-01 事故）**：`_handle_stalled` 标 stalled 后进程已杀，但 `enqueue` 同作品串行检查把它当占用拒绝——akari_series 第 3 次自动重入队被压掉后永远无法手动重入队（报「作品已有任务（stalled）同作品串行」）。修复：`enqueue` 检查 `status != "stalled"` 豁免 + scheduler_tick running 列表不包含 stalled（不占并行名额/不进假死检测）。
- **stalled 删除失败根因（2026-09-01）**：`remove_task` 只放行 `_FINAL` 内状态，而 `_FINAL` 原不含 `"stalled"` → UI 删除按钮报「任务未结束」。修复：`_FINAL`（task_manager.py）与 **main.py 的 `_FINAL_STATUSES` 同步加 `"stalled"`**——后者还有两处硬编码 `("done","failed","crashed","stopped","cancelled")` 元组（`_latest_task_for_work` 的 live 判断 + `_task_public` 的 elapsed/eta 判断）也要一起改，否则 stalled 被当 live 占作品状态。改完 curl POST `/api/tasks/<tid>/remove` 实测可删。

## 坑（本仓库 CRLF 脚本，必读 references/pitfalls.md）：本机 Windows `os.kill(pid, 0)` 对所有 pid（含存活）抛 WinError 87 → 不可用。第一版用 `OpenProcess(0x1000, False, pid)` 非 0 即活——**2026-08-31 实测仍误报**：进程被 taskkill 杀掉后 OpenProcess 仍返回非零句柄（句柄表/pid 复用），死进程被判 alive → kill 后等待退出循环永远不返回。**正确实现 = OpenProcess + GetExitCodeProcess == STILL_ACTIVE(259) 才判活**（死进程返回真实退出码如 0xC000013A ≠ 259）。配套 `TestPidAlive`。教训：判活逻辑改动后用「起进程→taskkill→立即 `_pid_alive` 应 False」实测验证，别只看逻辑。
- **task_manager CLI 必须 `sys.path` 含 `scripts/works`**：`registry` 在 `scripts/works/registry.py`，CLI 直跑缺路径会 `ModuleNotFoundError: No module named 'registry'`。
- 本项目几乎所有 `.py`/`.md`/模板都是 **CRLF**，`patch` 工具会写坏（ESC 控制字符）——改文件用 python 脚本（读 bytes→decode→`replace('\\r\\n','\\n')`→精确替换→`replace('\\n','\\r\\n')` 写回），改完 `python -m py_compile`。

## 坑（本仓库 CRLF 脚本，必读 references/pitfalls.md）

- 本项目几乎所有 `.py`/`.md`/模板都是 **CRLF**，`patch` 工具会写坏（ESC 控制字符）——改文件用 python 脚本（读 bytes→decode→`replace('\r\n','\n')`→精确替换→`replace('\n','\r\n')` 写回），改完 `python -m py_compile`。
- **按行精确匹配前先归一行尾**：换行风格**逐文件不同**（实测 `scripts/works/registry.py` 整份 CRLF，而 `model_config.toml`/`aa_env.py`/`main.py` 是 LF）——`read().split("\n")` 后写 `lines[i] == "}"` 会静默失配（表现为「找不到某块」）。读时剥尾随 `\r` 并记下主导 eol、写回按原风格还原（`build_release._read_lines/_write_lines` 就是这形态）。
- **`str.replace` 锚点缩进子串误匹配陷阱**：一行 20 空格的 `print(f"DONE ...")` 会匹配到 12 空格版锚点子串（12 空格从 20 空格行的第 8 格起正好接 'p'）。要么用**整行完整缩进**锚点，要么锚定**整块**（`for f in files:` ... 到 `sys.exit(30)` 整块），count 校验 `text.count(old)` 必须 ==1。
- 裸 `python` = WindowsApps stub `exit 49`——用 `source .venv/Scripts/activate` 或 `.venv/Scripts/python.exe`。
- Python `open(p,'w')` 在 Windows 文本模式会把文件转成 CRLF（误把 LF 测试文件变 CRLF）——写回前想好目标行尾。
- 测试跑法：`python -m unittest discover -s scripts/tests -p "test_<名>.py"`（`scripts.tests.x` 语法不行，scripts/tests 非包）。FastAPI/starlette TestClient 需要 `httpx`，仓库未装——路由冒烟改用 introspect `app.routes`。
- **test_task_manager 有既有偶发失败（2026-08-30 worktree 基线坐实，非本次改动）**：全量跑偶发 1 个失败，用例在 `test_no_env_passthrough_default` / `test_env_passthrough_to_runner` / `test_tick_starts_head_and_finalizes` 之间漂移 = TestUsage env 泄漏 + TestScheduler 时序竞态；单独跑该文件/该用例则 OK。基线上同样 3/3 失败 → 不阻塞交付，修它属独立任务。判定法见 `aa-pipeline-optimization/references/git-stash-ab-regression.md` 的「偶发失败三连跑确认法」。

- **kill 缺陷已修（2026-08-31）**：`force_kill` 原只 `os.kill(pid,15)` 杀直接子进程——r401_kizoku 实战中 kill 后 17808→15844 消失但 18832(aa_translate --pilot) 成孤儿残留（CPU 0.015s 假死态）。**已新增 `_kill_tree(pid)`**：taskkill `/T /F`（Windows 原生整树）+ 等退出（16×0.5s，用修复后的 `_pid_alive` 确认）。force_kill 改调 `_kill_tree`。**kill 后仍要验证整条链清干净**：`Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*<work>*" -and $_.Name -like "python*" }`（含命令行匹配比父 pid 链可靠——kill 后父进程已死无法溯源）。

## 任务失败排查（日志在哪，2026-09-01）

任务文件 `data/tasks/<tid>.json` **不含日志正文**（只有 status/rc/时间戳）。失败任务查日志固定路径：

1. 读任务文件拿 `work`（work id）与 `rc`。
2. `registry.WORKS[work]['work_dir']` 查真实 work 目录——**work id ≠ 目录名**（akari_series → `aaManga/あかりちゃんシリーズ/work`，sujin_otona → `大人のお店`），勿猜路径。
3. 日志 = `<work_dir>/runner_log.txt`（管理器 spawn runner 时整个 stdout/stderr 重定向到它，`_log_tail` 也读它）。`tail -100` 看 Traceback。**注意它是「按作品」而非「按任务」**：同一作品的多次任务共用同一个文件、每次启动以 `w` 截断重写 → 旧任务卡片打开日志会显示**最新一次跑**的输出（用户会以为「日志串了/不更新」）。要按任务隔离得改存 `data/tasks/<tid>.log`。
4. 旁证：`.hb_<tid>.json`（最后心跳 phase/章节）+ `usage.jsonl` grep task_id（最后一次 API 调用）。

**翻译完成但任务 failed（收尾崩溃）的恢复**：Traceback 在全部章节 `DONE` 之后 → 翻译产物/ckpt 完好，**禁重译**——同参数重新入队即可（默认只译未译章 → 全部已译 → 自动走 only_cleanup 补跑 pass2/pass3）。**2026-09-05 起校对默认禁用**（`--proofread` 才跑），only_cleanup 不含校对/漏译补译。注意 only_cleanup 的「清理 0/N」N 是作品全量章节数，非本次章数。

**坑：`Thread(...).start()` 返回 None（2026-09-01 实战）**：`translate_runner._run_translate_guard` 的流式透传把 stderr 收线程写成 `_t = _th.Thread(...).start()`，后面 `_t.join(timeout=2)` 炸 `AttributeError: 'NoneType' object has no attribute 'join'`，rc=1 把成功翻译误判 failed。修=两行分开（`_t=Thread(...); _t.start()`）。**改流式透传/线程收尾代码时：线程对象要先赋值再 start**，`.start()`/`.join()` 链式写法一律是 bug。

## 假死诊断与恢复（2026-08-31 r401_kizoku 实战定稿）

**症状**：免费翻译任务长时间（≥1-2h）无进度、0 章完成。**判定证据按可靠度排序**：

1. **CPU 累计秒数（最硬）**：`Get-Process -Id <pid> | Select CPU`——假死进程从启动到数小时只积累 0.01-0.03s CPU（纯阻塞 I/O 等待，非计算）。正常慢任务 CPU 会持续增长（哪怕免费模式主要等网络，2h 也应远大于 0.03s）。
2. **CLOSE_WAIT 网络连接**：`netstat -ano | grep <pid>` 出现 CLOSE_WAIT（连接已断但进程未释放/未感知）→ 请求永不返回 → 无限挂起。这是「卡在已死 socket 上」的确定信号。
3. **心跳 + ckpt 双双停摆**：`.hb_<tid>.json` 的 `hb_ts` 与最新 ckpt mtime 都 ≥1.5h 不动。注意**心跳可能比 ckpt 更早停**（翻译阶段进入后心跳不常写）。
4. **对照健康任务排除系统性问题**：同模式（如同样 AA_FREE_MODE=1）的另一任务若正常推进（ckpt/usage 持续更新），则免费链整体没死，是单任务/单请求偶发挂起。

**⚠️ usage 停滞 ≠ 假死（2026-09-01 akari_series 实战，kill 前必读）**：免费链退避（`_call_free_chain` 全部模型失败退避 10→300s 封顶）或**块内容被拒/格式不达标后的 fallback 循环**期间可能 5-10 分钟零 usage 记录——看起来像假死但不是。**kill 前先看 runner_log 的块日志**：有 `@NNNNN: +0/N missed=[...]` 说明模型在返回但格式/内容不达标（GLM-4-9B 对 mixed 段 `NNNNN_si|段` 格式输出差，akari 42 块全失败案例），重跑同样失败，不是挂死。**反复 kill 重跑免费任务永远到不了 20min 假死判定，反而破坏 ckpt 续跑节奏**。真假死判定：CPU 不动 + CLOSE_WAIT + usage/ckpt 双双停 ≥1.5h，而非「几分钟无 usage」。

**进度条/日志「不动」≠ 卡死（工作台任务页）**：阶段进度来自 runner 的 `heartbeat(phase, idx, total)`——该阶段写死 idx=0 或不写心跳时进度恒 0/N，日志也只在每项成功后打一行，看着像停摆。三步确认真伪：①心跳 sidecar 的 `hb_ts` 只在阶段/章边界更新，**不是活性证据**；②任务文件 `data/tasks/<tid>.json` 的 `pid` 查 `Get-Process -Id <pid>`（CPU 几乎不涨＝在等网络，正常）与 `Get-CimInstance Win32_Process` 命令行（确认是目标 runner）；③**真实速率 = 已完成项数 ÷ (now − 进程 StartTime)**（时长取 `(Get-Process -Id <pid>).StartTime`；CLI 直跑的任务在 usage.jsonl 里没有 task 字段，按它过滤会混入别的任务，别用来算速率）。算出剩余时间再决定「停」还是「等」。

**免费链假死三大根因（2026-09-01 bar_miku 连续 5+ 次假死实战定稿）**：
1. **推理模型 GLM-Z1-9B 超长生成**：`enable_thinking=false` 对 Z1 不生效（deepseek-r1 类推理模型），thinking 混入 content + 超长生成（b2d8 一次 completion=17246，单请求生成 20 分钟）→ 占死 worker → 10 worker 全占 → B2 回退 submit 排队拿不到线程 → CPU 0 假死。修复：**FREE_MODELS 移除 Z1**（免费链只用非推理模型 GLM-4-9B/laguna/mimo）。
2. **finish_reason=length 截断被当成功**：模型输出打满 `max_tokens=16384` 截断，`call_siliconflow` 只取 content 没查 finish_reason → 半截译文当正常返回 → parse_result 解析失败 → 块失败重试退避 → 假死。usage 里 `completion>=16000` = 截断信号。修复：**call_siliconflow / call_openrouter / call_opencode_zen 三处加 `finish_reason=="length"` 检测抛异常**（走重试/换模型）。
3. **_call_free_chain 无限重试占死 worker**：`while True` 全模型失败退避 10→300s 封顶**无限循环**，worker 全在退避 → 新任务永远拿不到线程。修复：**`FREE_MAX_ROUNDS=5` 轮次上限**，超限抛 RuntimeError（调用方捕获按漏译跳过落 review，任务继续推进不假死）。
另：**`socket.setdefaulttimeout(300)`** 兜底 CLOSE_WAIT 半开连接读挂死（`urlopen(timeout=N)` 对半开连接读可能不触发超时）。

**usage.jsonl 时间线诊断法（找假死元凶模型，2026-09-01 实战 5 分钟定位 Z1）**：`grep "<task_id>" usage.jsonl` 每行 json 解析，①**每个任务最后一条 usage 的 model = 死前最后一次调用**——连续多任务都死在同模型 = 元凶；②`completion>=16000` = max_tokens 截断；③>5min 间隔 = 卡死点（看间隔两端模型）；④`Counter(model)` 看免费链轮换是否正常。**改代码（如移除 Z1）只对新启动的任务生效**——正在跑的旧任务进程已加载旧 FREE_MODELS，kill 重跑才能吃新配置。

**gen_chapter_summaries 失败哨兵（2026-09-01）**：翻译任务每次重跑都走「章节简介」阶段（增量 skip 已有键，正常不烧 token，日志「新增0 跳过63 失败2」是常态），但**永远失败的章**（番外/特别篇台词混 AA 艺术字符，模型输出总带日文假名 → 校验丢弃，如 bar_miku entry_1651/1675）每次重跑都重新生成重试烧 token。修复：`gen_chapter_summaries.py` 失败路径（生成异常/空输出/重试仍含日文）写 `SUMMARY_FAILED="__summary_failed__"` 哨兵到知识库「章节简介」，skip 检查识别哨兵不再重试；`--force` 仍可强制重试。**注入侧也必须识别哨兵**：`work_memory.memory_hint` 原只判 `if s:`，把 `__summary_failed__` 字面当简介拼进翻译 prompt（实测 59 条 / 5 个作品）——已加 `s != SUMMARY_FAILED` 跳过。用户质疑「为什么每次都生成」时先查知识库缺哪几章——缺的就是永远失败的那几个。

**恢复流程（kill → 补杀残留 → 重跑 → 检测活性）**：
1. `python aa_web_app/task_manager.py kill <tid>`（标 failed 是预期终态）
2. **验证并补杀孙子进程——严禁裸杀（2026-09-01 误杀 TUI 事故）**：父链消失后 aa_translate 常残留，但**手动 `Stop-Process -Force` 按 pid 清残留时，pid 可能不是目标进程**（TUI/gateway/工作台全是 python，只有 CommandLine 含 `aa_translate`/`--pilot`/作品名 才算残留）——本会话误杀当前对话 TUI 致用户会话崩溃重启。正确：①清残留前 `Get-CimInstance Win32_Process` 查 CommandLine **确认含 aa_translate/--pilot/作品名**；②优先 `task_manager.py kill <tid>`（内部 `_kill_tree`=taskkill /T 整树，只杀任务文件记录的 pid 树）；③杀完命令行匹配复查，不匹配=不是残留，别碰。
   - **坑：kill 后命令行 grep 匹配乱码进程**（2026-09-06）：PowerShell 输出 GBK，日文作品名命令行在 bash 侧 grep `ダディ` 会**匹配到 GBK 编码的乱码字节串**——报一堆「残留」pid，实际是**同一个任务树的多个真进程**（translate_runner shim 父 15244 + 真身 6084 + aa_translate 7068/6592，uv shim 双进程结构）或已消失的假象。判别：再查这些 pid 的完整 CommandLine 是否真含 runner 特征串（`aa_translate.py --work`/`translate_runner.py --work`）；task_manager kill 已带 `_kill_tree` 整树杀，杀完一般无残留，乱码匹配结果勿当真。
3. 同参数重跑：`enqueue --type translate --work <id> --chapters <eids> --free-mode --no-gen`（ckpt 有 `__lines` 指纹 + 行级断点，自动续跑零损失）。
4. **活性检测（关键：usage.jsonl 是金标准）**：`status <tid>` 的 usage 聚合**有缓存/时间窗，会短暂显示不增长造成误判**；直接 grep `usage.jsonl` 里该 task 最后一条记录的 `ts`，距当前 <1-2 分钟 = 活着。`grep "<task_id>" usage.jsonl | tail` 看最后 `model/backend` 还能确认免费链轮换正常（如 siliconflow GLM ↔ commandcode laguna 交替=负载均衡生效，非卡死）。CPU 不动但 usage 增长 = 正常 I/O 等待，不是假死。

**翻译主体 DONE 后清理阶段挂死（2026-09-06 ダディ 6 章付费任务实锤，新形态）**：全部章节 `DONE entry_XXXX` 打完后、收尾清理阶段（定向 pass2/漏译补译，无新 translate usage）**卡死 7-10 分钟零推进**——edits.jsonl/backup 最后写入时间冻结、runner_log 停在同一行、进程 CPU 静止（无网络 CLOSE_WAIT、无 usage 增长、无线程数异常）。
- **判别（区别于免费链假死三根因）**：翻译 usage 已停是**正常**（进入清理阶段不再调翻译链），别拿「translate usage 停滞」当假死证据——看 **edits.jsonl mtime**（清理阶段写盘信号）是否冻结 + runner_log 是否停在 DONE 后；`backup/<ts>/` 目录持续生成 = 清理在动，不生成 + edits 冻结 = 挂死。
- **处理**：产物/ckpt 已完整落盘（各章 `_汉化.txt` 行数==src），**禁重译**——kill（确认无残留）→ **同参数重入队** → 已译章自动走 only_cleanup 补跑清理收尾（实测 94s done，含漏译/错译修正）。重入队后验证：`status` 到 done + scan_width 定向章 0 错位 + 行数一一对应。
- **诱因推测**：卡点常在**行尾 art 段 + 漏译补译的混合行**（本章卡在 entry_21095 L1146-1206 区域，全是含 art 图形+右侧台词的 mixed 行）——疑似半开连接/读挂死，300s socket 兜底未触发（7 分钟>5min 未恢复，机制未明，遇之按上法恢复即可，无需 root-cause 阻塞交付）。

## 假死自动检测（2026-08-31 已落地，scheduler_tick 内 watchdog）

r401_kizoku 卡死 2h 后用户要求「修复 kill + 加假死检测」，已实现并部署：

- **判定**：`_stall_check(t)` —— translate 任务 `usage.jsonl` 最后一条 `ts` 停滞 ≥ `STALL_MINUTES`（默认 20min，env `AA_TASK_STALL_MINUTES` 可调）且 pid 存活 → 判假死。**无 usage 记录（尚未开始调 API）不判**（防误杀启动初期）。阈值 20min 依据：免费链退避封顶 5min + 余量。
- **处理**：`_handle_stalled(t)` —— `_kill_tree` 杀整树 + 标 `stalled`（中间态，非 _FINAL，UI 红色） + **自动重入队**（同参数 ckpt 续跑，`enqueue(..., _stall_retry_of=tid)` 豁免同作品串行检查）；连续假死 ≥ `STALL_MAX_RESTARTS`（默认 3）标 failed 停止。**stall_count 继承**：重入队时 `params["_stall_count"]=n` 传给新任务，`_handle_stalled` 取 `max(stall_count, params._stall_count)+1`——否则新任务计数归零、上限失效。
- **调试注意**：scheduler_tick 的假死检测每 2s tick 自动跑（日志 `[task_manager] 假死检测: <work> 停滞 n/N 次`）。
- **⚠️ `_stall_check` 禁止 `_usage_index(force=True)`（2026-08-31 任务页卡死系统事故根因）**：force=True 每次全量解析 usage.jsonl（12MB/68K 行实测 381ms/次）——调度器每 2s tick × 每个 running 翻译任务 → 持续 ~20% CPU，多任务叠加**卡死整个系统**（用户报：任务页开久了系统卡死、页面关了还卡、重启资源管理器才好）。修复=`_usage_index()` 非 force（(mtime,size) 指纹增量缓存，缓存命中 0.09ms）——翻译中 usage 增长自动重读新增行、停滞时文件不变直接命中，检测语义不变。**教训：任何周期循环里的 usage 读取必须走指纹缓存，force=True 只留给一次性显式操作**。
- **⚠️ `_log_tail` 禁止 `readlines()[-n:]`（同事故第二元凶）**：读**全文件**再切尾部——翻译日志持续增长（几 MB），任务页 3s 轮询 × 多展开任务全量读 → IO/内存暴涨。改**尾部 seek**：`open(lp,'rb')` → `seek(0,2)` → 回退 min(size,64KB) → read → decode → splitlines()[-n:]。任务页 API 修复后实测 17ms。

## 翻译阶段日志缺失根因（2026-09-01，capture_output 吞输出）

**症状**：runner_log.txt 在「=== 翻译 (aa_translate) ===」后长时间无任何块进度（`@NNNNN: +N/N`），任务看起来像假死；子进程退出后突然补 40 行尾巴。

**根因**：`translate_runner._run_translate_guard` 的 `one_round` 用 `subprocess.run(capture_output=True)` **等 aa_translate 完全退出才取 stdout 尾部 40 行**——翻译全程（几十分钟）stdout 全吞在管道里。

**修复**：改 `Popen(stdout=PIPE, stderr=PIPE, bufsize=1)` + `for line in proc.stdout: log(line)` 实时流式透传；**stderr 必须起 daemon 线程独立读**（防管道填满死锁）；`proc.wait()` + join 后取 err 尾 8 行；返回 `proc.returncode`。改后翻译中块进度实时可见。

## 校对阶段同类坑（2026-09-14，硬超时 + 异常被吞 = 假 done）

**症状**：translate 任务 status=done / rc=0，卡片却显示「待译 N」（`_task_public` 的 `tot-idx`；type=proofread 显示「待校对 N」）；runner_log 的「=== 校对 (refine_aa) ===」下**只有一行** `[refine-proofread] 子进程异常: ... timed out after 3600 seconds`，逐章 `[entry_xxxxx] 三类修复/L#### [act/OK/reason]` 全无。

**根因（两层）**：① `translate_runner._run_refine_proofread` 用 `subprocess.run(capture_output=True, timeout=3600)`——总时长硬上限与单章预算错配（单章 150~3500 行、refine 调用 73 次/章、2.45s/次，20 章实测需 ~95min），跑一半被砍；② 超时异常被上层 `except Exception` 当「失败(不阻塞)」吞掉 → rc=0 → 报 done。capture_output 同时吞掉全程 stdout（超时路径一行不打）= 日志整段缺失。

**判据（三件证据自洽）**：`work/backup_refine_aa_<ts>/` 目录数 = 实际完成章数（每章一份 → 数目录即知砍在第几章）；`usage.jsonl` 筛 `task==<tid> and tag=="refine"` 的首末 ts 差 ≈ 超时线（3591s ≈ 3600）；`.hb_<tid>.json` 的 `chapter_idx` = 已完成章数（被杀停在那里，finalize 只补 rc 不改 idx）。**残余信号**：未校对章的 `汉化/*.txt` mtime 停在翻译阶段（早于 refine 起始），也无对应 backup 目录。

**修复（`translate_runner.py`）**：
1. 新增 `_run_stream_proc(cmd, cwd, log, on_line, total_limit, idle_limit)`：stdout 逐行实时透传、stderr daemon 线程、看门狗按「总时长」+「无新输出」双阈值杀进程，返回 `(rc, killed_reason, err_tail)`。
2. 总预算 = `AA_REFINE_TIMEOUT`（0=关）或 `max(3600, AA_REFINE_PER_CHAPTER × 章数)`，默认单章 900s（观测最长单章 ~15min，留 2× 余量）；`AA_REFINE_IDLE_TIMEOUT` 默认 1800s 判挂死（**注意单章内模型调用期无任何输出，idle 阈值必须 > 最长单章**，不能设几分钟）。
3. 失败即执行码上报：超时/rc≠0 抛 `ProofreadIncomplete` → main 记 `proofread_err`（后续阶段照跑）→ 结尾 `return 1` → rc=1 → 任务 failed（红），日志给「未校对 N/M 章: eid 列表 + 补跑命令」。
   **⚠️ 还必须在 `__main__` 末尾加 `sys.exit(_rc)`**：原实现 `_rc = main() or 0` 后**只调 `TR.finalize(_rc)`, 没把 _rc 变成进程退出码** → 管理器 `_watch` 判 `proc.returncode=0` 就标 done（实测：心跳已写 `rc:1`、任务仍 done）。**通病模板**：`dl_runner.py` / `aa_translate.py` 的 `__main__` 同一形态（现在两者 main() 恒返 0，属潜在地雷）——任何 runner 要让“非零 rc”真正生效，先确认 `sys.exit(_rc)` 存在。
4. **端到端验证（零 token）**：找一个已全译作品 + 单章，走 only_cleanup（不重译），入队时 `--env AA_REFINE_TIMEOUT=2` 强制看门狗杀掉刚启动的 refine → 应见①runner_log 逐行校对日志（含未校对 eid + 补跑命令）②任务 status=failed / rc=1。比只跑单测多盖一层「runner 退出码 → 任务终态」的接线。

**补跑姿势**：**独立 proofread 任务直跑 `refine_aa`（`_build_command` 里无包装、无超时）**——`task_manager.py enqueue --type proofread --work <id> --chapters <未校对eid>`，或 `POST /api/work/{w}/proofread`。别再重跑 translate（对已译章会走 only_cleanup 把整批重校对一遍，白烧 token）。

**改了 runner 代码后「重启进行中的阶段」**（已启动的子进程 import 时就锁定了旧代码，磁盘上的改动对它无效——不重启永远看不到新行为）：
1. `stop <tid> --mode chapter` 优雅停（章边界生效，终态 stopped/rc=41）；轮询 `status` 直到它离开 `running/stopping`（旧流程单章 1~3min，等几分钟是正常的）。
2. 用 `.hb_<tid>.json` 的 `chapter_idx`（已完成数）与任务 `params.chapters` 列表算**剩余**章节 = `[idx:]`；停止时机导致的边界章重跑一次无害。
3. `enqueue --type proofread --work <id> --chapters <剩余eids>`。**同作品只要还在 running/queued，enqueue 一律被拒**——必须等真到 stopped 再入队。
4. 验证新流程真的生效，别只看「任务在跑」：`<work>/work/refine_ckpt/` 开始出文件（新链+ckpt 的确定性物证）+ `status` 的心跳**秒级**前进（旧流程是分钟级）+ usage.backends 是预期后端。

**测试**：`scripts/tests/test_refine_stream.py`（12 例，零 token：假子进程覆盖流式透传/双阈值杀进程/未校对 eid/异常类型 + 源码守卫防旧形态复活）。

## 清单来源：dl_runner 实时拉原站 / translate_runner 只读本地（2026-08-31 定型，2026-09-13 解耦）

**症状**（ドスケベナイトあかりちゃん）：更新按钮报 1 个新章，但「下载」失败（待处理 0 话）；「下载并翻译」不下载新章、反重跑已翻译 33 章 pass2（用户强制终止）。

**根因**：清单来源不一致——dl_runner 读 `work/chapters.json` 旧缓存（33 条），而更新按钮用 site_url.json **实时拉**原站（检出新章 29990）→ 下载 0 话；translate_runner 的 `select_eids('new')` 选不出新章 → `sel_eids` 空 → **触发 only_cleanup 对全部已译章跑 pass2/pass3**（烧 token）。

**现行规则（两条，勿混）**：
1. **dl_runner = 唯一实时拉原站分类页的 runner**：yaruobook 系（`chapters_source=cache` **或** `site=="yaruobook"`）用 `work/site_url.json` 的 `?cat=N` 拉 yaruobook 分类页（`_fetch_yaruobook_entries`），r401 系拉 r401_index，失败才回退 chapters.json 缓存。**sujin_* 等 r401_cid 是历史错配置**（r401_cid=860 实为 yaruobook.com/?cat=860 同数字不同站）——site=yaruobook 就按 yaruobook 拉，勿走 r401。
2. **translate_runner 不读网络（2026-09-13 起）**：下载已与翻译解耦（translate_runner 也不再自动调 dl_runner），`load_entries` = **本地 aa/ 已有原文的章节**，顺序取 `work/chapter_order.json`（无则 eid 升序）。理由：aa_translate 只处理 aa/ 里存在的 txt，实时清单里「原站有而本地没下」的章根本译不了，白拉上千条。
   - **缺原文章先跑 dl_runner / 工作台「更新」**；translate_runner 只翻译不补下载。
   - **`sel_eids` 空 → only_cleanup 是既定行为**（对该作品全部已译章跑清理，烧 token）：显式 eids 与本地 aa/ 口径不一致会静默清空勾选，指定章节前先确认这些章本地有原文。
3. `main.py api_work_update`：**用 check_updates 报的精确 eid 列表入队**（`chapters=30118`），禁 `chapters="new"`（new 语义=本地缺的全部含历史缺口，baitou 只下 42 章会误拉 176 条旧章）。

**验证**：dl_runner 单独下 29990 成功（843KB raw + chapters.json 自动更新 34 条）；select_eids(new) 正确选出 ['29990']；sujin_shabu 走 yaruobook 32 条而非错误 r401 10 条；浏览器实测 r401_kizoku 更新入队 `chapters=30118` 下载成功。

## 冻结 exe（发行包）起子任务：统一走 `aa_spawn.py`

工作台/runner 起子任务（下载/翻译/校对/生成合集/术语候选/调度器）命令一律以 `sys.executable` 开头 ——
源码态没问题，但**冻结成 PyInstaller exe 后 `sys.executable` 就是 exe 自己**：`[exe, dl_runner.py, --work x]`
会被入口当成「再起一份工作台」（脚本参数被无视、端口被占、父进程等一个永不退出的服务）
→ 任务卡住，用户看到的就是**下载/翻译失败**。

- **规则**：凡 `subprocess` 起本项目脚本，用 `from aa_spawn import py_prefix, child_env` →
  `subprocess.run(py_prefix(["-u"]) + [脚本, *参数], env=child_env(), ...)`。
  `py_prefix()` 冻结态返回 `[exe, "--cli"]`（`-u` 丢弃，入口 `aa_web_exe.py` 的 `--cli` 用 `runpy`
  在本进程跑目标脚本并透传退出码）；`child_env()` 保证子进程拿到 `AA_BASE`/`AA_ENV` = 数据根（exe 目录）。
  嵌套 spawn 同样适用——`translate_runner → aa_translate`、`dl_runner → dl_chapters/extract_all` 都要改，
  只改直接调用点会让第二层再踩一次。
- **收尾自查（必做）**：改/加 spawn 后 grep `\[sys\.executable`（scripts + aa_web_app），
  生产路径不该再有命中（测试 mock / 构建工具 / legacy 除外）。
- 冻结 exe 里代码在 PyInstaller 解包目录、**数据根 = exe 所在目录**（`AA_BASE` 覆盖）——
  往解包目录写的数据每次启动就丢。
- **子进程必须能读到数据根的作品注册表**：`--cli` 那条路也要在入口里先做数据根引导
  （`aa_web_exe._ensure_dirs()` + `_bootstrap_registry()` 排在**任何分支之前**，别 `if --cli: return` 写在它前面）
  ——否则子进程 `import registry` 命中的是解包目录那份（只有随包示例作品），报「未知作品: X（可用: baitou）」，
  表现为**使用者一按按钮就失败**（服务进程有引导、子进程没有，所以作品页能看、任务全挂）。
  **⚠️ 光有顺序还不够（实测只修顺序后点按钮仍报同一句）**，另两条配套缺一不可：
  ① `--cli` 的**绝对**脚本路径若落在**解包目录**，要映射到数据根里的同一份再跑——那些脚本用 `__file__`
  反推 `_BASE`，跑解包目录那份时 `_BASE` = 解包目录，它自己的
  `sys.path.insert(0, <解包目录>/scripts/works)` 会把刚引导进去的数据根注册表**盖回去**；
  ② 引导落好数据根那份后**删掉解包目录那份 `registry.py`**（物理杜绝被任何 sys.path 插入翻出来）。
  诊断一条命令：`AA工作台.exe --cli scripts/tools/check_registry.py <作品id>`（打印实际命中的 registry 路径 + 作品清单）。
  **验证不能用手工 `--cli scripts/...`**：相对路径命中的是数据根那份脚本，**手工验永远是绿的**——
  要验子任务视图就拿门禁探针真起一次服务端子任务（`aa-release-packaging/scripts/probe_exe_spawn.py`）。
- 守卫：`scripts/tests/test_aa_spawn.py`（前缀形态 + `child_env` 数据根，零成本回归）。

## runner 进程隔离（重启不中断）与 reconcile 竞态（2026-08-31）

**目标**：bat 重启工作台（`一键重启工作台.bat` 杀 8765 旧 uvicorn）不中断正在跑的翻译/下载任务。

- **`_start_task`/`ensure_scheduler` 的 Popen 必须带 `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`（2026-08-31）**：
  - `CREATE_NO_WINDOW`(0x08000000) = 不弹黑窗（测试/后台跑 unittest 不闪窗，用户明确要求）；`CREATE_NEW_PROCESS_GROUP`(0x200) = 独立进程组，bat 杀 uvicorn 的 console 关闭事件不广播到 runner。
  - **`DETACHED_PROCESS`(0x8) 与 `CREATE_NO_WINDOW` 互斥**——同时传时 CREATE_NO_WINDOW 被忽略，仍弹窗（实测踩坑：先加了 DETACHED|NO_WINDOW 还弹，去掉 DETACHED 才不弹）。
  - 重启不中断的完整保证 = runner 心跳自收尾（`finalize(rc)` 写 .hb_）+ 新工作台 scheduler reconcile 读心跳接管，**不依赖 DETACHED 脱离父子**（taskkill 无 /T 只杀单 pid，父子关系本来就不连坐）。
  - 验证 detach 效果用「父进程 taskkill 自杀 → runner 存活 + 心跳 rc 正常写」实测，别只看 PPID（DETACHED 不改变 PPID，会误判）。
- **reconcile 竞态 v3（2026-08-31）**：`_reconcile_task` 在 pid 死 + 心跳无 rc 时**不立即判 crashed**——进程退出与 `_watch`/`finalize` 写终态之间存在异步窗口，快速退出的 runner（测试/短任务）会被误判 crashed。修复：`task_id in _watch_procs` → 交给 `_watch` 线程收尾不抢判；无 watch 时**启动 <10s（收尾窗口）保持 running**，启动久且无 rc 才判 crashed（真崩溃）。症状：单测偶发 `'crashed' != 'done'`（10s 超时才失败）。
- **测试 tearDown 必须等 `_watch` 收尾**（`p.wait(timeout=3)` 再 kill），直接 `_watch_procs.clear()` 会留半收尾线程跨测试污染 reconcile。
- **uv shim 双进程不是双实例**：`.venv\Scripts\python.exe` 是 uv 生成的 shim，会 spawn 真 `uv\python` 子进程——查 aa_web 进程看到两个 python（shim 父 + 真身子，PPID 链）是正常单实例，别误杀。

## 清单实时性（新章发现）+ 更新按钮精确 eid（2026-08-31）

**事故**：ドスケベナイト「更新检查」报 1 新章但下载失败；「下载并翻译」没下载新章反而重跑已译 33 章 pass2（被强制终止）。

**根因**：`dl_runner`/`translate_runner` 的 `load_entries` 只读 `work/chapters.json` 缓存（chapters_source=cache 系）→ 新章不在缓存 → `--chapters new` 选不出 → sel_eids 空 → translate_runner 走 **only_cleanup（对全部已译章跑 pass2/pass3，烧 token）**。

**修复（三个层面）**：
1. **实时性只对 dl_runner 成立**（2026-09-13 起 translate_runner 只读本地 aa/，见上「清单来源」节）：dl_runner 对 yaruobook 系（`chapters_source=cache` **或** `site=yaruobook`——sujin_* 等只配了 site 没配 chapters_source 也要走）用 `work/site_url.json` 的 `?cat=N` 拉分类页；失败回退缓存。r401 系实时拉 r401_index。注意 **sujin_* 的 r401_cid 是历史错配**（指向别作品/404），site=yaruobook 必须优先 yaruobook 分类页，否则拉错作品。
2. **`check_updates` 逐页拉、遇已有即停**（优化，2026-08-31）：`fetch_updates_paged` 从 page=1 最新往回扫，遇到本地已有 eid 立即整页停（baitou 218 条 6 页 → 只拉 1 页 2.7s）；首页满页且全为新章才继续拉（找本地 eid 确认非源变更）。`matched_any` 判定源变更：本地有章但原站从未匹配到（fukou 2nt→touhou 镜像、jikan_teishi r401 分类内容变化）→ 报「站点迁移/换源，请手动确认」而非误报全量更新。
3. **`/api/work/{work}/update` 用 check_updates 报的精确 eid 列表入队，禁 `chapters="new"`**——new 语义=「本地缺的全部」（含历史缺口：baitou 只下载了最新 42 章、原站 218 条 → 会误拉 176 条旧章）。更新按钮 = 只处理「比本地最新还新的连续新章」的精确 eid（如 `chapters=30118`）。

## usage.jsonl 大小滚动（2026-08-31）

usage.jsonl 持续增长（实测 12MB/82K 行）——除读取侧指纹缓存外，写入侧限大小：超限滚动生成新文件。

- **`scripts/common/aa_usage.py`（新，usage 文件管理唯一权威）**：`rotate_if_needed()`（主文件 > 10MB，env `AA_USAGE_MAX_BYTES` 可调 → 删 `.3` → `.2→.3` → `.1→.2` → 主→`.1`）；`append_usage(rec)`（写前滚动检查 + append，失败静默）；`iter_usage_files()`（主 + `.1/.2/.3` 统一遍历，统计工具合并读）。
- **写入点统一走 `append_usage`**：`aa_backend._log_usage`（翻译）+ `vision_classify._log_usage_vision`（视觉）。
- **统计工具合并读**：`usage_total.py` / `usage_stats.py` 用 `iter_usage_files()` 遍历主 + 滚动文件，历史数据不丢。
- **`task_manager._usage_index` 兼容**：滚动后主文件变小 → (mtime,size) 指纹变化 → 自动重建缓存，不崩（已实测）。
- `.gitignore` 加 `usage.jsonl.[0-9]`（滚动归档不入库）。
- 超限滚动只发生在写入时（翻译/视觉调用），无任务不自动滚；必要时可手动调 `aa_usage.rotate_if_needed()` 立即归档。

## 验证工具链教训（2026-08-31，测试误删真实任务文件事故）

用 execute_code kernel 验证 task_manager 时 kernel 崩溃了两次（mock registry + `_handle_stalled` 组合），残留了半写的任务文件（`t_stall_verify_001` crashed）；清理脚本用 `any(tt["task_id"] in f for tt in list_tasks(...))` 判断文件名**误删了 data/tasks/ 下全部真实任务文件**（任务文件名 = task_id，当然全匹配）。恢复：进程未受影响（翻译继续），从运行中进程反查 command line 重建任务文件（pid→cmdline→work/params）。

**教训（必须遵守）**：
1. **验证 task_manager 用独立进程脚本（`python -u temp/xxx.py`），不要用 execute_code kernel**——`_handle_stalled` 里 `enqueue→ensure_scheduler→Popen` 在 kernel 环境不稳定，且模块级 mock（TASKS_DIR/ROOT/registry）污染持久 kernel 后续调用。
2. **假任务 pid 绝不写 `os.getpid()`**——`_handle_stalled` 会 `_kill_tree(pid)` 把自己杀了（无输出 exit 1）。用不存在的 pid（如 999999，kill 无害）。
3. **清理测试文件时禁止 `any(tid in f for f in all_tasks)` 通配删**——文件名恰含 task_id 会全删。要么精确列要删的 tid，要么临时 TASKS_DIR 隔离。
4. 任务文件被误删的恢复：进程没死就反查 `Get-CimInstance Win32_Process` command line 重建 `{task_id,type,work,params,status,pid,...}` + `.hb_`（hb_ts=now，防假死检测误判）。
5. PowerShell 输出是 GBK：`text=True` 默认 utf-8 解码会 UnicodeDecodeError——subprocess 调用 PowerShell 用 `capture_output=True` 不带 `text=True`，或 GBK 解码。

## CRLF 编辑终极坑（2026-09-01 会话，两次踩爆）

- **python heredoc 里写 `"\\r\\n"` 落盘变成真实 CRLF 字节**（外层 bash 先解释一层 + Python 字符串层），`rstrip("\r\n")` 写成语法错误——heredoc 改 CRLF 文件里的转义序列，**用 `chr(13)+chr(10)` 字节级拼接或 write_file 重写整个函数**，不要试图在 heredoc 里写转义。
- **write_file 会整个覆盖文件**（本会话把 900 行 translate_runner.py 覆盖成 24 行只剩函数体）——write_file 前先确认文件小/就是要全量重写；大文件用行号定位替换（`lines[start:end] = new.split('\n')`）。误覆盖立即 `git checkout -- <file>` 恢复（未提交改动丢失但比文件损坏好）。

## runner_log 编码双坑（2026-09-08 refine_aa 校对事故）

**症状**：proofread 任务 failed，runner_log 里 traceback `UnicodeEncodeError: 'gbk' codec can't encode character '\uff64'`（print 校对行含半角､），且日志中文显示 `����` 乱码。

**根因（双层）**：①`_start_task` 把 `runner_log.txt` 以 `encoding="utf-8"` 打开传给 Popen stdout——但 **Popen 只 dup fd，encoding 参数是摆设**；子进程 Python 对非 tty stdout 用 **locale(GBK)** 编码写 → 文件里是 GBK 字节，而 `_log_tail` 按 utf-8 读 → 乱码。②GBK 编码不了日文假名/✓/半角､(U+FF64) → print 即崩。

**修复（两层）**：
1. `_start_task` env 加 `PYTHONIOENCODING=utf-8`——所有 runner 子进程 stdout 强制 utf-8，与 `_log_tail` 的 utf-8 解码对齐，且不再崩。**任何 spawn runner 的地方都查 env 是否设此变量**。
2. `scripts/aa_translate.py` 顶层（import 区后）加 `sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")`（try/except 包）——**74+ 个脚本 import aa_translate，一次覆盖全部 CLI 直跑场景**（含 refine_aa）。工作台主进程 stdout=DEVNULL 无副作用。

**排查法**：会 print 日文/✓/⚠ 等非 GBK 字符的独立 CLI（不 import aa_translate 的，如 backfill_aa_comments/work_memory）逐个补同款 reconfigure；只 print ASCII+中文的（GBK 全兼容）不用补。GBK 字符集：中文✓、`→`✓、日文假名✗、✓✗、⚠✗、半角假名区✗。

- **一键清理结束任务（2026-09-19）**：`TM.prune_finished_tasks()` + `POST /api/tasks/prune-finished` + `/tasks` 页 `#clean-finished` 按钮（先按当前列表报数让用户 confirm）。语义 = 删**全部终态**（done/failed/stopped/crashed/cancelled/stalled）的任务文件+心跳+停止标志+队列残留，**不看天数**（区别 `prune_stale_tasks(days=3)` 自动清理）；**保留 running/starting/stopping 与 queued**——删排队任务 = 静默丢待跑工作，不能算「结束」。列任务用 `list_tasks()`（_reconcile=True，先把死 pid 收尾成终态再删）。验证：临时 TASKS_DIR + `_task(tid, status=...)` 造全状态 → 直调路由函数断言 removed/kept/队列未变。

## 参考

- `references/pitfalls.md`：本会话 CRLF 编辑坑的现场记录（锚点误匹配实例、修复）。
- `references/usage-rotation.md`：usage.jsonl 滚动归档（aa_usage.py 阈值/归档/合并读/坑）。
- `references/usage-and-ui-notes.md`：token 聚合（usage.jsonl `task` 字段）+ `_task_public` 字段（free_mode/elapsed/eta/title）+ 前端分区刷新与日志 20 行实现要点（本会话 2026-08-29 迭代）。
