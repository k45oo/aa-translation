# 镜像站 eid 脱节：工作台目录顺序错（2026-08-21 jikan_teishi 实战）

用户在 grilling 后交付 runner 自动化方案的收尾阶段报告：「jikan_teishi 在工作台的目录顺序错了」。
排查 3 轮后定位为 **镜像站 eid 体系脱节** —— 这是一个 class 级坑，新作品接入 / chapter_order.json
生成 / registry 配置都可能踩，记录以完整还原判定路径。

## 现象
- 工作台阅读器 `/work/<id>/read` 的目录（`logic.merged_chapters` 消费 chapter_order.json）章节乱序：
  番外、最終話、编号大的章排到前面，已译的第1-10話也不连序。
- 工作台启用 `chapter_order.json` 时，`merged_chapters` 用 `_order = {eid: idx}` 匹配，
  **全部 eid 匹配落空就 fallback 按 eid 数字排序** → 目录按 eid 数字排，而本地 eid 是零散的。

## 根因：两套镜像 eid 体系
同一作品同时在多个镜像站发布，各镜像的 eid（帖子/文章编号）**完全不同**：
- **本地已下载数据**：时间是 yaruobook.com 版，270 话，eid 零散：
  `entry_42170.txt` = 第1話、`entry_2012.txt` = 第4話、`entry_87883.txt` = 最終話後編。
  本地 `aa/*.txt` 的 `# 原帖地址:` 行全部指向 `yaruobook.com/?p=<eid>`。
- **r401 分类页 19401**：指向 yaruo-matome.com 版，268 话，eid 连续 7154（第1話）… 27926（最終話後編）。
  两者标题/内容相同，但 eid 完全无交集。

上一轮把 `chapter_order.json` 用 r401 清单（yaruo-matome 的 eid）生成 → 与本地 aa eid 交集=0 → 乱序。
同时曾把 registry `jikan_teishi.url_pattern` 从 yaruobook.com 改成 yaruo-matome.com（为了对齐 r401），
这是**朝错误方向改**——本地真实数据源就是 yaruobook.com，最终又改回。

## 判定手段（这次最快的一步）
写一次性的比对脚本：遍历 `registry.WORKS`，对每个有 `work_dir/chapter_order.json` 的作品，
比对 `order[] eid` 与 `aa_dir/*.txt` 文件名提取的 eid 的交集。
输出交集=0 的即脱节。实测 **10 个有 order 的作品仅 jikan_teishi 交集=0**，其余 9 个完全一致
（那些作品的本地数据就是从 r401 对应镜像下载的，eid 对得上）。

## 修复：用本地 aa 目录自身重建 order（标题话数序）
本地 eid 零散没关系，**标题是有序的**——从每个 `aa/*.txt` 首行标题解析话数：
```
# 時間停止探索者やる夫　第8話      → 第8話
# 時間停止探索者やる夫　最終話...前編  → 最終話
# 時間停止探索者やる夫　番外編「...」 → 番外
```
排序键：(0, 话数, eid) 数字类在前；最終話/番外/小ネタ/あかり/埋めネタ 等解析不出话数的放尾部按 eid。
生成 `{"order": [eid,...]}`（270 条，与本地 aa 对齐）。工作台即按此正确排（第1話→第205話→最終話→番外）。

Key: 生成/更新 chapter_order.json 后**必须逐作品核对 order eid ∩ aa eid 交集非 0**，
交集=0 说明 order 来源与本地数据源脱节（镜像问题），用本地 aa 重建而非 r401 清单。

## 教训（沉淀为通则）
1. **本地已下载数据的镜像 ≠ r401 清单指向的站**（同一作品多镜像发布，eid 体系不同）。
   `dl_runner` 拉 r401 清单做新增发现没问题，但 chapter_order.json / registry 配置必须以本地数据源为准。
2. **dl_chapters 下载优先用条目自带 url**（`item.get("url") or cfg["url_pattern"]`），
   registry `url_pattern` 只是 fallback —— 不要为了对齐 r401 去改 registry 到错误镜像。
3. 工作台乱序时先跑「order eid ∩ aa eid」比对脚本，交集=0 即镜像脱节，跳过其它猜测。
4. 本地 aa eid 零散 ≠ 数据坏：标题话数有序即可，重建 order 用标题解析。

## 相关
- runner 方案主体：`doc/runner自动化方案_2026-08-21.md` + `references/runner-automation-design-2026-08-21.md`
- 工作台 merged_chapters 排序逻辑：`aa_web_app/logic.py`
- dl_chapters URL 修复同一天落在 `common/dl_chapters.py`（见 SKILL.md「下载 URL 硬编码坑」bullet）
