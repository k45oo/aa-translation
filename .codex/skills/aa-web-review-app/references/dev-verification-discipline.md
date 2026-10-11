# 改工作台/仓库代码后的验收纪律（SKILL.md 超 100k 上限，本节独立成文）

适用：任何改动 `aa_web_app/`、`scripts/` 代码或新增脚本/测试的会话。每次必走，不跳步。

## 1. 新增/删除 `scripts/` 下任何文件（含 `scripts/tests/test_*.py`）→ 立刻重跑生成器

```
.venv/Scripts/python.exe scripts/tools/gen_inventory.py
```

不重跑则 `test_interface_docs` 挂 2 例（`test_inventory_fresh` / `test_docs_up_to_date`）。**坑**：失败文案是「接口文档过期或生成器报错」，会把人引去反复重跑 `gen_interface_docs.py` 却发现毫无变化——真因写在 stderr（`inventory 缺文件（先跑 gen_inventory.py）: scripts/...`）。**先读 stderr 再动手**。测试文件同样算脚本文件。

## 2. 改被测试直接调用的函数签名前，先 grep 测试树

```
grep -rn "<函数名>(" scripts/tests aa_web_app
```

测试是**手写调用**，不会随签名自动更新：删一个位置参数（如 `save_line` 的 `backup_root`）会连带打断 5~6 个用例，而且断言的**语义**也要跟着改（如「保存后建了备份」→「保存后不建备份」）。同一函数若被 `main.py` 的多个路由调用，路由也要一起改。

## 3. 全量测试报失败：先判归属，再决定改谁

1. **单跑该模块**：`.venv/Scripts/python.exe -m unittest discover -s scripts/tests -p "<mod>.py" -v`。**别写 `-m unittest scripts.tests.<mod>`**——那条路不把 `scripts/tests` 放进 `sys.path`，凡 `from _work_guard import repo_only` 的模块当场 ImportError（`ModuleNotFoundError: No module named '_work_guard'`），看起来像「测试文件坏了」其实是调用方式错；全量走 `discover -s scripts/tests`（也才带上玩具夹具路径）。顺序/时间依赖或进程相关的用例（任务管理器/调度器类）单跑常自愈。
2. **要对照纯净版本**：`git worktree add --detach <scratch> HEAD` 后**只跑该模块**。别拿 worktree 跑全量 `discover`——`aaManga/`、`交付HTML/` 已被 `git rm --cached`，数据缺失会产生十几例与代码无关的假错误（`test_gen_html` 快照、`test_line_meta` 作品表等）。
3. **并行会话的未提交改动**：`scripts/config/model_config.toml`、`glossary.json` 等常被其他会话改（例如给同一端点加第二条模型会让按后端名索引的用例常败）。`git diff` 看一眼，再判断是不是自己的锅——别去改别人正在改的文件。

## 4. 路由级 E2E：不碰真作品

打桩法对**所有**工作台路由通用（不只是替换类路由）：

```python
cfg = {"title": "E2E临时", "aa_dir": ..., "out_dir": ..., "work_dir": ..., "name_style": "eid"}
M.cfg_of = lambda w: cfg          # 只打桩作品解析，其余全走真实逻辑
r = M.api_replace_apply("e2e", M.ReplacePairsBody(pairs=[["a", "b"]], scope="all"))
```

- pydantic body 可直接构造：`M.LineSave(text=..., type=...)` / `M.ReplacePairsBody(pairs=[...], scope="all"|"from", start=...)` / `M.ApplyGlossaryBody(pairs=[...])`。
- 临时目录要造齐 `aa/`（原文）+ `汉化/`（产物），章节名用 `entry_1.txt` / `entry_1_汉化.txt` 的口径。
- **断言三样**：① 落盘内容（`logic.read_lines`）② 宽度 `logic.disp_width(新) == logic.disp_width(原文行)` ③ 留档记录（见第 5 条）。
- 跑完 `shutil.rmtree` 掉临时目录；别指向真实作品目录。

## 5. 改动留档的验证口径（整文件备份已废）

行保存 / 翻译替换 / 术语表替换**唯一留档** = 单文件 `<work_dir>/edits.jsonl`，每个改动行一条 `{ts, work, file, line(1-based), type, line_type, before, after}`（`main._log_edit` 单行 / `main._log_edits_bulk` 批量一次打开写）。

- 断言口径：看 `edits.jsonl` 尾部逐行 `before/after`，**不要**再断言 `work/backup/<ts>/` 生成（`logic.backup_files` 只剩术语表 JSON 的 `.glossary_backup/` 在用；`/api/work/<id>/backups` 与 restore 只服务遗留目录）。
- 回滚口径：按记录把 `before` 逐行写回（不需要整文件副本）——单测里可以用「记录 → 写回 before → 断言等于原始行」把这条能力锁住。
- 写入路径适配：`_plan_writes` 必须每章带 `changes = [(idx0, before, after)]`（直写支与单章重算支都要带），否则批量替换的路由拿不到前后值。

## 6. 验证要走「用户那条路」，不是等价的便捷路

同一功能常有两套入口，走错那套会得到**永远绿**的假验证：

- 手工 `python scripts/common/x.py --work W`（相对路径，命中仓库那份）vs 用户点按钮（服务端用
  `os.path.join(_ROOT, "scripts", ...)` 起子进程）。冻结成 exe 后 `_ROOT` = PyInstaller **解包目录**，
  子脚本用 `__file__` 反推的 `_BASE` / `sys.path` / 数据根与仓库不同 → 手工全绿、按钮全红，连修三轮才发现。
- **规则**：先看清用户动作实际走哪个进程、哪条路径，再照那条路复现。日志里的脚本路径是证据——
  出现 `_MEI*` 就是解包目录那份在跑。别用「等价」的便捷调用替代真路径。
- 复现优先挑**同一条链上零成本的那一段**（不调模型/不联网的阶段）当验收证据，随后把这条断言加进自动探测，
  下次回归自动拦。
- 要动用户正在跑的实例目录（数据根 = 包目录，含他自己的作品与 `.env`）时：先停实例，**只同步代码、不覆盖数据**。
- **分开「崩溃」与「跑完但 0 结果」**：`全量完成 ✓` / `筛选 0 条` 是正常结局（语料空、无候选），
  失败必然带 traceback 或非 0 退出码。只看「没产出」会把正常结局当 bug 去修。
- 包装脚本把崩溃报成成功（「无进度文件 = 已完成」）会让上一条更难判——排查时**退出码与产物两条都要对得上**。

## 7. 前端 UI 改动的浏览器验收（别信代码，也别信旧 ref）

- **两层证据都要有**：先用宿主 python/curl 直连接口证明后端按新口径返回（如 `GET /api/work/<id>/models` 的 `selected`/`head`），再用浏览器证明页面按这份数据渲染。只做一层时「后端对了前端没接」与「前端对了数据不对」长得一模一样。
- **静态 JS/CSS 改了先 bump 模板里的 `?v=`** 再验，否则看到的可能是旧文件；改 `main.py` 路由要重启工作台，改模板/静态文件不用（Jinja 热加载 + `?v=` 变更即拉新）。
- **点元素用页面内 `el.click()`，别用快照 ref**：`browser_console` 里写 `document.querySelector('#src-toggle .seg-btn[data-mode="src"]').click()`。快照 ref 可能对应上一轮页面状态，`browser_click` 会点到别的元素（实测点阅读器「原文」却跳去了首页作品列表）。
- **一次 eval 内「点 → 等 → 读」**：切换/保存是异步的（先改 state、再 fetch 重渲染），点完立刻同步读 `dataset.mode`/行 HTML 会拿到旧值。用 async IIFE：`(async () => { const el = document.querySelector(...); el.click(); await new Promise(r => setTimeout(r, 400)); return {...}; })()`——`await` 一个 setTimeout promise 是可用的，读状态前必须先等渲染完。
- **断言要读到 computed style，不能只看 class**：「点击有效但样式没变」类报障本质是 class 加了而 CSS 没生效/被覆盖，所以断言 `getComputedStyle(activeEl).backgroundColor/fontWeight` 与预期一致，并同时读该状态对应的 DOM 标记（如 `pre.dataset.mode`）与页面提示文本——三者一致才算改完。
- **顺手看 console**：不传表达式的 `browser_console` 会回 console 消息与未捕获异常——一个 IIFE 抛错会让该页后续脚本整段不执行，症状正是「元素在、点了没反应」。
- **验证跑完 `git status`**：真点按钮/真保存会写真实数据（edits.jsonl、sfx 标记、.env），要么走只看不改的路径，要么按留档还原；console 表达式写坏时还可能在仓库根留下残片文件。
