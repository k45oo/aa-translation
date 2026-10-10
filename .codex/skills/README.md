# AA 汉化项目 — Codex 技能包（自动生成，勿手改）

本目录是**派生副本**：由 `python scripts/tools/sync_codex_skills.py` 从技能库的 AA 相关技能生成，
转成 Codex CLI / 其他支持「技能目录」机制的 agent 可直接使用的格式。

- 唯一事实源 = 技能库（默认按 `AA_SKILLS_SRC` / `HERMES_SKILLS_DIR` / `HERMES_HOME` 探测，
  也可 `--src <技能库路径>` 指定）。**改技能请改源，然后重跑同步**：

```bash
python scripts/tools/sync_codex_skills.py            # 全量重建（含删除副本里多出来的旧目录/旧文件）
python scripts/tools/sync_codex_skills.py --check    # 只校验：rc=2 表示副本失同步或有脱敏残留
```

## 生成时做了什么

1. **格式转换**：frontmatter 只保留 `name` + `description`；去掉 agent 专属工具/编排措辞
   （技能查看、子代理、定时任务等改为通用说法）；统一 LF 行尾。
2. **信息脱敏**：本机绝对路径与个人标识一律替换为占位符，副本可外发而不泄露生成者的目录结构：

   | 原文形态 | 替换为 |
   |---|---|
   | 仓库根绝对路径（含盘符 / `/f/...` 形式） | `<repo>` |
   | 仓库所在盘的上一级 | `<repo-parent>` |
   | 裸仓库（远端镜像目录） | `<repo-bare>` |
   | 技能库目录 | `<skills>` |
   | agent 安装目录 | `<hermes>` |
   | 用户目录（`<user>`、`<user>`、用户名本身） | `<user>` |

3. **门禁**：写盘后复查——出现任何未脱敏路径、或疑似密钥形状（`sk-` / `tp-` / `ghp_` 前缀、40 位 hex）
   **直接拒绝出包**（rc=2），不会留下半脱敏的副本。

## 包含的技能（17）

| 技能 | 用途 |
|---|---|
| `aa-manga-translation` | 汉化管线本体：抓取 → 分块翻译 → 艺术行保护 → 漏译清理 |
| `aa-pipeline-optimization` | 管线提速 / 省 token / 分类器改进 / 质量保护 |
| `aa-site-parsers` | 站点解析器（新增站点 / FC2 帖头 / 年龄门） |
| `aa-task-manager` | 统一任务管理器（翻译/下载编排，双档优雅停止） |
| `aa-web-review-app` | 本地矫正工作台（FastAPI 阅读器 / 矫正 / 术语表） |
| `aa-translation-proofreading` | 校对质量（漏译 / 误译 / 宽度 / 行数错位 / 台词导出） |
| `aa-glossary-roles` | 角色统一译名 / 术语表 / 作品知识库 |
| `aa-r18-ops` | R18 分级与章节级后端选择、视觉辅助 |
| `aa-free-model-rotation` | 免费模型池轮换与排查 |
| `aa-model-chain-ops` | 模型链增删 / 替换后端 |
| `aa-model-benchmark` | 模型基准评估与选型 |
| `aa-delivery` | 交付适配：响应式 HTML 合集、手机端实测 |
| `aa-html-reparse` | 已汉化交付 HTML 反解析回原文 txt |
| `aa-image-export` | 竖屏图片 / CBZ 导出（渲染语义复刻浏览器） |
| `aa-release-packaging` | 打「开箱即用」发行包（脱敏、白名单、冻结 exe） |
| `aa-research` | 搜索日本 AA 作品、排行与读者讨论 |
| `board-raw-download` | 镜像站收录不全时回原始揭示板下载 / 解析 |

> 不含各技能自带的 `scripts/`（那是仓库本体脚本的旧拷贝，一律以仓库 `scripts/` 为准），
> 也不含非 AA 技能（图漫下载、通用工具等）。

## Codex 用法

Codex CLI 会自动加载项目根 `.codex/skills/` 下的技能。在仓库根目录启动即可：

```bash
cd <repo>
codex                                                   # 交互
codex exec "把某作品的最新话渲染成竖屏长图并打包 CBZ"      # 一次问答
```

也可以在对话里显式引用技能名（如「按 `aa-image-export` 的流程做」）。
技能里的 `<repo>` / `<user>` 等占位符按你本机实际路径理解；所有命令都假定**从仓库根运行**。
