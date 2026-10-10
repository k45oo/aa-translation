# AA 翻译 —— やる夫系 AA 漫画汉化管线

把日文 AA 漫画（2ch 字符画）批量汉化为简体中文：**抓页 → 提取 → 分块翻译 → 漏译清理 →
宽度/残留校验 → 术语统一 → HTML 合集**。艺术行（纯字符画）本地拼接、不送模型，只把文本行
交给大模型，因此成本低、误伤少；翻译过程有术语表注入、R18 分级、端点自动降级兜底。

* **命令行零依赖**：`scripts/` 下的脚本只用 Python 标准库，Python 3.11+ 直接跑。
* **图形界面（矫正工作台）**：本地 Web 应用（FastAPI）——单章矫正、翻译控制台、校对、术语表、
  任务看板、作品导入导出、设置页；默认 <http://127.0.0.1:8765>。

## 一、环境准备

```bash
python -m venv .venv
.venv/Scripts/pip install fastapi uvicorn jinja2      # 只用命令行的话可以跳过
```

## 二、填一个 API key

* 启动工作台（仓库根双击 `一键重启工作台.bat`，或 `python scripts/tools/aa_web.py`）→ 右上
  「设置 → 密钥」→ 填 `DEEPSEEK_API_KEY`（<https://platform.deepseek.com/> 注册后在 API Keys 创建）
  → 「测试连接」显示可用即可。key 只写在本机 `.env`，不会外传。
* 也可以直接建仓库根 `.env`：`DEEPSEEK_API_KEY=sk-…`。优先级：真实环境变量 > `.env` >
  `scripts/config/model_config.toml` > 代码默认。

## 三、跑通一部作品

| 步骤 | 做法 |
|---|---|
| 添加作品 | 工作台首页「＋ 添加作品」→ 粘贴原站**分类页 URL**（yaruobook 三站 / r401 等）→ 确认 |
| 下载原文 | 作品列表「更多 → 更新」；或 `python scripts/runner/dl_runner.py --work <id> --chapters all` |
| 生成术语表 | **翻译前必须有作品术语表**（缺表 runner 会中止并给出命令）：工作台作品页「生成术语候选」→ 勾选确认；或零 token 派生 `python scripts/common/gen_work_glossary.py --work <id> --force` |
| 翻译 | 工作台「翻译控制台」勾章节；或 `python scripts/runner/translate_runner.py --work <id> --chapters new` |
| 校对 | 翻译后自动跑 `refine_aa`（漏译 / 残留假名 / 艺术行还原一次覆盖）；也可单独跑校对任务（工作台校对页选章） |
| 生成合集 | 工作台「重生成合集」；或 `python scripts/common/gen_aa_html.py --work <id>` |

**产物**：`aaManga/<作品>/汉化/*_汉化.txt`（与原文逐行等宽）+ `aaManga/交付HTML/<标题>_汉化合集/`
（`index.html` + 章节数据，右上可切「译 / 原文」；外置数据形态要整目录拷贝才能离线打开）。

## 四、命令行速查

```
python scripts/runner/dl_runner.py         --work <id> [--chapters all|new|1-20]  # 下载章节
python scripts/runner/translate_runner.py  --work <id> [--chapters new] [--force] # 翻译 + 清理(+校对)
python scripts/common/scan_width.py        --work <id>                            # 宽度校验
python scripts/fix/refine_aa.py            --work <id> [--eids …] [--write]       # 漏译清理 / 校对
python scripts/common/gen_aa_html.py       --work <id>                            # 生成交付文件夹
```

全部命令与 env 见 `doc/接口文档.md`；脚本分级 / 废弃清单见 `scripts/_inventory.json`；
`scripts/` 结构的权威说明见 `scripts/README.md`。

## 五、打包成开箱即用的发行包

```bash
一键打包发行包.bat                      # 或 python scripts/tools/build_release.py --out 导出
```

产出 `导出/AA翻译_发行包_<日期>.zip`：干净源码 + `AA工作台.exe`（不装 Python 也能双击用）+
示例作品 + 独立 README / requirements.txt / .env.example。详见 `scripts/README.md`。

## 六、注意事项

* **代理**：抓原站、DeepInfra 等端点需要本地代理（如 v2rayN `http://127.0.0.1:10808`），
  在工作台「设置 → 代理」里填。
* **R18 作品**：部分后端有内容审查（dashscope 不可用、MiniMax 不支持），作品可标 R18 等级，
  翻译时自动走 `r18_backends` 链。
* **术语表**：全局 `glossary.json` + 角色表 `roles.json` 随仓库提供；**作品级术语表需要自己生成**。
* **作品数据不在仓库内**：`aaManga/`（原文 / 汉化 / 交付物）与作品手写详情由 runner 生成，
  `.gitignore` 已排除，不会误提交。
* 工作台只监听本机 `127.0.0.1`、无鉴权，不要暴露到公网。
* 自测：`python -m unittest discover -s scripts/tests`（约 2 分钟，零 API）。

## 许可 / 来源

* **代码**：MIT，见 [`LICENSE`](LICENSE)。
* **字体**：`fonts/SarasaMonoSC-Regular.ttf`（Sarasa Mono SC，等距更纱黑体）采用 SIL Open Font
  License 1.1，许可证全文见 [`fonts/OFL.txt`](fonts/OFL.txt)（分发字体时请一并保留该文件）。
* **作品内容**版权归原作者：本仓库不含任何作品文本；Releases 的发行包内含 1 部示例作品的
  前 3 话原文，仅用于跑通流程，可自行删除。
