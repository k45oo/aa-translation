# 工作台 work_meta 演进（status/intro）与旧进程重启坑（2026-08-24）

补充记录在 `references/works-table-author-grouping-20260824.md` 之后的字段与运维演进。

## work_meta 新增 status/intro 字段
- `{wid: {author, cn_name, jp_title, status, intro, tags, materials}}`
- `status` = 状态（连载中/完结/中断，详情弹窗 select）
- `intro` = 作品简介（详情弹窗 textarea rows=4，标签与素材人物之间，独立 dm-sec 区块）
- API `GET/PUT /api/work-meta/{work}` 读写；WorkMeta model 含 status/intro 字段；PUT 仅编辑 author/cn_name/status/intro/tags/materials
- 批量简介：用 zhipu glm-4.5-air 总结到 **≤150 字** 写回（实测 79-121 字质量好）

## 详情弹窗字段顺序（2026-08-24）
grid（汉化名/作者/状态/日文原题/站点）→ 标签 → **简介**（标签和素材之间）→ 素材人物 → 素材出现话数。

## 标签约定
成人向统一标 `R18`（勿用「成人向」变体）。拆词时去重：akari_ninshin/haisin 曾同时有 `R18`+`成人向`。

## ⚠️ 关键运维坑：改 main.py/模板后必须真正重启服务
**症状**：改了 main.py（加 status/intro 字段）和 edits 模板，但详情弹窗/API 不显示新字段。
**根因**：8765 端口跑的还是**旧进程**（旧代码）。
1. git-bash 里 `taskkill //F //PID <pid>` 会**静默失败**（MSYS 参数转义）——旧进程没被杀掉；
2. 起新进程时报 `Errno 10048 端口占用` 退出（新代码没起来）；
3. 结果旧代码继续对外服务，改的 main.py/模板全部"不生效"。
**正确做法**：
- 杀旧进程用 `taskkill.exe /F /PID <pid>`（`.exe` 后缀避免 MSYS 层转义），或 `powershell Get-NetTCPConnection -LocalPort 8765 ... Stop-Process`
- 杀后 `netstat -ano | grep :8765` 确认端口释放再起新进程
- 起新进程 = `terminal(background=true)` + `PYTHONPATH= ./.venv/Scripts/python.exe scripts/tools/aa_web.py`
- **重启后实测**：python urllib `GET /api/work-meta/{wid}` 确认 status/intro 等新字段在，勿只信「进程起来了/netstat 有 PID」

## 验证字段不落的工具
git-bash 里 `curl -s http://127.0.0.1:8765/... -o /tmp/x` 会因 MSYS 路径打不开 `-o /dev/null`/假 size 0 → 用 `.venv/Scripts/python.exe -c "urllib.request ..."` 请求并 json 解析。
