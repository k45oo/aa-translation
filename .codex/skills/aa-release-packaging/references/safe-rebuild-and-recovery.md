# 安全重建与恢复（产物目录 = 活的数据根）

适用：用户把发行包当**实例**在用（双击 `导出/<包>/AA工作台.exe`，数据根 = 包目录），而现在要出新版包。
这个场景下「重建」不再是安全操作，本文是那套流程。

## 危险点

* **历史**：`build_release` 曾 `shutil.rmtree(out_dir)` 删同名产物目录。`rmtree` 是**逐个删**的：
  撞到被占用的目录才报错退出，**此前删掉的文件不会回来**（占用源：实例自己的 `workbench.log`、
  其实例起的子进程、用户开着该目录的 Explorer 窗口）。
* **现状**：产物目录 `os.replace` 成 `<名>.old_<时间戳>`（`_set_aside_out_dir()`，保留最近 2 份），
  **永不删除**；`_looks_like_live_data_root()` 命中只是「早拒绝」（rc=6），**不是**唯一防线——
  它实测漏过一次（沙箱里对同样结构返回 True，原因未定论），那次把整棵数据根换成了干净包：
  用户 1 章翻译 + 校对产物永久丢失。**数据安全靠「挪开」机制，不靠守卫判断准。**
* 仍是风险的部分：`.env`、`aaManga/`、`aa_web_app/data/`、数据根 `scripts/works/registry.py` 都在产物目录里，
  只有备份能救它们 —— **动产物目录前先备份数据根**。

## 做之前的四步

1. **备份数据根**（不是整包，只备数据）：`.env`、`aaManga/`（含 `aaManga/交付HTML/`）、`导出/`、`temp/`、
   `aa_web_app/`（含 `data/` 与 `workbench.log`）、数据根 `scripts/works/registry.py` →
   放到 `temp/pkg_userdata_backup/`（`temp/` 不进包、不入库，随时可删，但恢复时是唯一依靠）。
2. **停实例**：`Get-NetTCPConnection -LocalPort 8766` 拿 PID → `taskkill /PID <pid> /T /F`。
   判活只看 8766 LISTENING。
3. **看目录是不是被别的进程占着**（Explorer 窗口典型）：
   `(New-Object -ComObject Shell.Application).Windows() | % { $_.LocationURL }`；真占着就别删那个目录。
4. **决定路线**：目录干净 → 正常 `build_release.py`；目录是活数据根或被占用 → 走下面的「另建 + 同步」。

`build_release.py` 现在自带门禁：`_looks_like_live_data_root()` 命中（带 `.env`、非空 `aaManga/`、
`aa_web_app/data/work_meta.json`）→ **拒绝出包 rc=6**，并提示换 `--out`。

## 路线 A：另建一份包，只同步代码（保留用户数据）

```
python scripts/tools/build_release.py --out temp/release_out      # 干净包落在临时目录
```

然后只把**代码/资源**覆盖进用户实例目录，**数据一律不动**（这套流水线现成实现：
`<本技能目录>/scripts/sync_pkg_code.py <新包目录> <实例目录> <新zip>`，下面那份清单就是它执行的规则；
它还会做数据保留核对，并处理下文那条 registry 缺失的风险）：

* 覆盖：`AA工作台.exe`、`scripts/`（但**跳过 `scripts/works/registry.py`**）、`aa_web_app/*.py` + `templates/` +
  `static/`、`doc/`、`fonts/`、`README.md`、`requirements.txt`、`.env.example`、两个 bat、
  `glossary.json`、`roles.json`、`.release_package`；
* 保留：`.env`、`aaManga/`（含 `aaManga/交付HTML/`）、`导出/`、`temp/`、`aa_web_app/data/`、`aa_web_app/log*`、
  `scripts/works/registry.py`；
* zip 也一并换成新的（那份 zip 仍是干净产物，可以外发）。

⚠️ 跳过 registry 的同步脚本要**先确认目标那份还在**：若它已被上一次失败的重建删掉，跳过就等于
「包里有、实例里没有」→ 必须先从新包种子拷一份回去。

## 路线 B：数据已被删，恢复

1. 备份放回（`.env`/`aaManga`/`aaManga/交付HTML`/`导出`/`temp`/`aa_web_app`）。
2. **注册表条目**：用户新增的作品若只是写进过临时解包目录，条目还留在
   `%TEMP%\_MEI*\scripts\works\registry.py` 里 —— 从那里把条目块抠出来、插进数据根 registry 的
   `WORKS` 字典（按 `    "<id>": dict(` 到配对 `),` 提块），别指望重敲。
   注意 `_MEI*` 是**上一次启动**残留的目录，用户重装/清理过就没了 → 备份要趁早。
3. 重启实例后按验收清单核：作品页章节链接数、`/manga` 表格行数、`exe --cli …check_imports` rc=0。

### 路线 B 的补全（实测走通的顺序）

1. **`.env`**：从 `temp/pkg_userdata_backup/.env` 拷回；汇报时只核**键名**，别把值写进日志/回复。
2. **注册表条目 + `work_meta`**：逐个丢掉的作品重建。**路径用 `os.path.join(BASE, "aaManga", …)` 形式**——
   别写死盘符，更别抄另一个实例的绝对路径（那样「删除这部作品」会删掉那个实例的数据）。
3. **章节目录清单借副本**：`work/chapters.json` / `chapter_order.json` / `site_url.json` 也丢了的话，
   `dl_runner` 会直接失败（`无 r401_cid 且未给 --json/无缓存；无法拉清单`）。从**另一处实例**
   （如仓库开发实例的同名作品 `aaManga/<作品>/work/`）拷这三个文件过去，再重下原文。
4. **原文重下（零 token）**：包目录内 `AA工作台.exe --cli scripts/runner/dl_runner.py --work <id> --chapters all`
   → 日志出现 `ALL DONE` / `=== dl_runner DONE ===` 即齐（核 `aa/` 与 `raw/` 文件数）。
5. **哪些救不回来要说清**：已翻译章（`汉化/*_汉化.txt`）、`work/refine_ckpt/`、`work/glossary_*`、
   `work/term_pool.json` 没有备份就真没了 —— 除非该作品此前**导出过 rar**（`导出/<作品>_<日期>.rar`）。
   恢复完明确告知丢了什么，并**问一句**要不要重译（花他的 token，别自行决定）。

## 基线过期的处置

「基线之外零失败」判据的基线是**出包那一刻**仓库套件的失败集合；并行会话此刻正在改代码时，包内那条
「新失败」往往在仓库里**同样失败**（与打包无关）。处置顺序：

1. 先在仓库跑那一条用例（同样用 `.venv/Scripts/python.exe`）确认是不是并行改动；
2. 是 → 刷新基线（重跑仓库套件生成 `temp/release_baseline_tests.json`），然后对**已构建产物复验**
   （解包 → 单测 → `probe_exe_in_package`），**不要为一条过期基线重建 6~10 分钟**——
   一条命令搞定：`<本技能目录>/scripts/reverify_release.py <仓库根> <包目录> [<zip>]`；
3. 不是（仓库过、包里不过）→ 这才是真·打包问题，回到 SKILL.md 的铁律自查。
