# 工作台「设置」页 + 仓库根 .env 配置层

给工作台加/改设置类页面时先读这份；SKILL.md 只留规则，这里放表与配方。

## 分层与优先级（唯一口径）

```
真实环境变量  >  <仓库根>\.env  >  scripts/config/model_config.toml  >  代码默认
```

- `.env` = **唯一用户可改层**，所有设置页写入都落它；**TOML 只读**（它带大量「实测依据」注释，程序回写会抹掉）。
- 读写统一走 `scripts/common/aa_env.py`（零依赖）：`load_dotenv()`（import 时把 AA_* + key 白名单灌进 `os.environ`，**不覆盖**已存在的真实 env）、`read_paths()`（仓库根 .env → hermes 两处只读兜底）、`write_values()`（行级替换 + 原子写，**保留注释与未改动行**，缺失才追加）、`mask()`。
- key 读取路径只有 `aa_env.read_key()` 一处实现：`aa_translate.get_key` / `aa_backend._read_env_key` / `aa_config._env_key_exists` / `vision_classify.read_env` 全部委托它。新增任何 key 读取点一律走它，别再 `open(.env)` 自己解析。

## env 一览（设置页写的）

| env | 默认 | 作用 |
|---|---|---|
| `AA_CHAIN` | 不写=整条链 | 翻译链条目 id 列表 |
| `AA_REFINE_BACKEND` | 不写=跟随付费链 | 校对链条目 id |
| `AA_SEL_CHAIN` | 不写=TOML `[selection].backends` | 划词链条目 id |
| `AA_SEL_ENABLE` | `1` | 划词+朗读总开关（关=前端不挂监听 + 后端 403） |
| `AA_PROXY` / `AA_USE_PROXY` | TOML `[default].proxy` / `auto` | 代理地址 / 策略 `auto\|0\|1` |
| `AA_BASE` | 仓库根 | 数据根（`works/paths.py` BASE；`CODE_ROOT` 仍是代码根） |
| `AA_PORT` | 8765 | 工作台端口（`aa_web.py` 与重启 bat 都读） |
| `AA_ENV_PATH`（测试用） | — | 把「用户可改层」指到别处（**测试隔离的唯一正确姿势**） |

## 保存语义（改设置页时别破坏这几条）

- **保存即生效**：原子写 `.env` → `load_dotenv(force=True)` → `aa_config.reload()`；已在跑的任务不受影响（子进程 env 在 spawn 时已定），新启动的任务自动吃新值。
- **只写改动项**：密钥留空 = 不改，清除要显式提交 key 名。绝不做整表重写（枚回掩码串当新值 = 把 key 写坏）。
- **空 = 不收窄 / 等于默认集 = 不写**：勾选集为空或恰好等于默认集 → 删掉该 env（保住 `fallback_only` 兜底端点）；否则写条目 id 列表。
- **密钥只回掩码**（尾 4 位）+ 来源层（`env` / `.env` / `hermes`），永不回明文。

## 「来源层」判定的坑

`aa_config` 在 import 时已把 `.env` 灌进 `os.environ`，所以「在 os.environ 里」**不等于**来自真实环境变量。判来源要拿文件值比对：`env_val != file_val` → `env`，否则文件里有 → `.env`，再否则 `hermes`。

## 页面自检（两个按钮）必须真连

- 「测试连接」：用该 key 所属后端发一次最小请求（`aa_backend._call_named(b, ..., max_tokens=8, timeout=30)`），错误分类成 401/402/403/429/超时/SSL 六类文案 —— 打包给别人的场景里「key 错 or 欠费」是最常见故障，光看界面状态查不出来。
- 「测试代理」：给三条结论 —— 代理端口能否 TCP 连上、**不走代理**能否到付费端点、经代理能否到。端点回 401/404 也算「通了」（区分「网络不通」与「没带 key」）。

## 浏览器 / 端到端测试配方（不动用户正在跑的实例）

```bash
# 另起一个实例到别的端口（scheduler 单例 pid 锁会让它打印「已有调度器在跑，退出」→ 不会双跑任务）
cd <仓库根> && AA_PORT=8799 AA_ENV_PATH=<临时 .env> .venv/Scripts/python.exe -c \
  "import sys; sys.path.insert(0,'.'); import uvicorn; from aa_web_app.main import app; \
   uvicorn.run(app, host='127.0.0.1', port=8799, log_level='warning')"
```

- `AA_ENV_PATH` 指到临时文件 → 页面上所有保存都写临时文件，**真 `.env` 零污染**（先在临时文件里手写注释 + 一条无关键，测完 grep 断言注释与未改动行逐字节还在）。
- 后台起、测完按 session 杀（**别按端口杀**；父重定向器 + worker 是两种进程，杀父会连坐）。
- 静态 JS 改了一律 bump 模板里的 `?v=`；模板 HTML 改完刷新即可（Jinja 热加载），**改 `main.py` 路由必须重启工作台**。
- 用不用 TestClient：仓库 `.venv` 没装 httpx，`fastapi.testclient` 会直接报错 → 端点验收**直调路由函数**（`main.api_settings()` 这种），模板用 `jinja2.Environment(FileSystemLoader(templates))` 直接 `render()` 抓语法/变量错；要验交互/渲染才起真实例。
- 页面交互验证优先用一次 `browser_console` eval 打点断言（DOM 状态/`getComputedStyle`/几何），别只截图。工具点击偶发不生效时，`document.getElementById('x').click()` 是可靠的等价驱动（先确认 handler 已绑定：`String(el.onclick)`）。
