# 视觉判定并行化 + 多站点 + 多模型（2026-08-19，touhou_teisou 前52章落地）

> 承接 vision-full73.md（73章串行落地）→ 本文记录**并行化改造 + 多站点支持 + 多模型分配 + 新模型实测**。
> touhou_teisou（やる夫のねっとり貞操逆転世界，touhouyaruosure.com）前52章：串行预估 4-6h → 并行 37.6 分钟（快 ~8 倍）。

## 章级并行（ProcessPool，vision_classify.py）

- **把单章处理抽成 `process_chapter(base, cfg, site_name, sys_prompt, all_models, limit, retry_failed, primary_model)`**，main 用 `ProcessPoolExecutor(max_workers=N)` 并行提交各章。
- **每章独立写自己的 v3 文件**（`line_meta_v3/<base>.json`）→ 无数据竞争，天然可并行。
- **日志按章缓冲返回主进程打印**（worker 里 `log.append(...)`，`fut.result()` 后统一 print）→ 防多进程交错。
- `--workers N` 默认 3（同 key 并发勿太高防限流）。
- **⚠️ Windows spawn 子进程不继承全局变量**：主力模型分配若用模块级 dict（`MODEL_ASSIGN`）在子进程里是空的 → 全部 fallback 到 all_models[0]。**必须用参数传递**（`primary_map[b]` 传给 process_chapter 的 `primary_model` 参数）。
- **断点续跑天然支持**：`has_v3 and not retry_failed` → 跳过。已判定的章自动 skip，重启只跑剩余。

## 多站点支持（--work 自动选 site）

- 原 vision_classify 硬编码 `from sites import fc2 as site`。改为 `site_map = {'saki_fc2': 'fc2', 'touhou_teisou': 'touhouyaruosure'}`，main 里 `site = __import__('sites.' + site_name, fromlist=[site_name])`。
- ⚠️ **模块级 `site` 变量是全局的**，main 里要 `global site` 重新绑定；process_chapter 里独立 `importlib.import_module('sites.' + site_name)`（spawn 子进程不共享主进程的 site）。
- **render_annotated 调用 render_block 时 `freeze_post` 重复传参**：`render_block(rows, site, cm, fs, width, font_path, min_width, text_bonus, meta, freeze_post, embed_v2, embed_v3)` 第 10 个位置参数就是 freeze_post → 不能再传 `freeze_post=` 关键字（报 "got multiple values"）。修法：位置参数只到 meta，freeze_post 用关键字。
- **line_meta/（colormap）缺失容错**：`meta = {}` + `if os.path.exists(mp): try load except: {}`——视觉判定不需要精确 colormap。
- **line_classify.py 同样扩展 --chapter 多值**（`--chapter` 后多个 eid 空格分隔，`any(c in f for c in chapters)` 过滤）。

## 多模型 round-robin 主力分配 + 模型→章节记录

- **models 元组统一 5 元素**：`(name, url, key, model_id, fmt)`。chat_vision 用 model_id（如 `Qwen/Qwen3-VL-32B-Instruct`），日志用 name（如 `Qwen3-VL-32B`）。⚠️ 元组长度不一致（有的 4 元素）会让 `for mname, url, key, *fmt_extra` 解包错位（model_id 被当 fmt）。
- **主力 round-robin**：`primary_map[b] = models[i % len(models)]` → 各章主用不同模型；主力失败自动 fallback 其余模型（`models_ordered = [primary] + [m for m in all_models if m != primary]`）。
- **模型→章节记录**：`used_model` 返回主进程 → 写 `work/vision_model_chapters.txt`（每个模型识别了哪些章节）。用户要求"记录不同视觉模型识别了哪些章节"。
- 实测：glm-4.6v 2.7s/块、Qwen3-VL-32B 1.5-21s/块、mimo2.5-cmd 13.9s/块。Qwen32B 最快且准。

## 新视觉模型实测（2026-08-19，安全审查测试）

| 模型 | 端点 | 安全审查 | 速度 | 说明 |
|---|---|---|---|---|
| **glm-4.6v-flashx** | open.bigmodel.cn（chat） | **无审查** ✅ | 2.1s | 8元/10M token；免费版（剩303万token）耗尽后的付费补充主力 |
| **xiaomi/mimo-v2.5** | **api.commandcode.ai/provider/v1**（chat） | **无审查** ✅ | 4.5-24.7s | commandcode 端点比 opencode-go 网关稳；**模型名必须带 `xiaomi/` 前缀**（裸 `mimo-v2.5` → "Model not supported"） |
| mimo-v2.5 | opencode.ai/zen/go/v1/responses（网关） | 无审核但**月度限额** | — | 现报 `GoUsageLimitError`（429，10天后重置） |

**测试图**：entry_8532 0086（曾被 xiaomi 官方端点以 high risk 拒）+ 0001。两个新通道都正常判定 → **安全审查是 xiaomi 官方端点独有的**。
- ⚠️ mimo2.5 响应带 `reasoning` 字段（26 reasoning tokens），content 正常输出；偶发 content 为空（换请求重试即可）。
- commandcode 端点（api.commandcode.ai/provider/v1）的 key 用 `COMMANDCODE_API_KEY`（config.yaml custom provider，GOAT）。

## touhou_teisou 反哺流程（前52章，完整链路）

1. **line_classify 生成 line_meta/**（101 章全量，45 秒——命令漏传 `--chapter` 反而全跑了，意外之喜）
2. **vision_classify 并行视觉判定**（52 章，37.6 分钟，3 进程）
3. **v3_backport_v2.py --work touhou_teisou**（378 行 / 495 段 text→art）
4. **vision_review.py 漏译扫描**——**必须按作品扩展 ART_KANA 和 PROPER_NAMES**：
   - touhou_teisou 高频纹理：`ﾆﾆ`/`ニニ`/`ﾉﾉ`/`ﾏﾑ`/`くノ`/`トミ`/`んツ`/`アヘ` 等（半角片假名范围 `ｦ-ﾟ` + 全角纹理字符都要补进 ART_KANA）
   - 角色名/术语白名单按作品维护：`しほ`/`まほ`/`ほむら`/`しぽりん`/`育代`/`育上`/`すずか`/`ルオ`/`ちっひ` 等
5. **真漏译补译**（29 处）：`この男→这男人`（宽度适配）、`もう一来→再来一次`、`はい決定→好，决定`、`ひと月→一个月`、`二人とも→两人都`、`あっ───→啊───`、`そう──→没错──` 等——**子串替换 + pad 回原宽**（超宽用更短译文，如"这个男人"4字超2格 → "这男人"3字恰好）
6. **全量渲染**（`--chapter` 逗号分隔 52 个 eid）→ CBZ → FTP 上传 `/TachiyomiJ2K/local/やる夫のねっとり貞操逆転世界/`

## ⚠️ 宽度校验陷阱

- **touhou_teisou 汉化有 11721 行宽度不匹配**（既有问题，翻译阶段没跑 scan_width）——**不是本次补译引入的**（git diff 显示汉化文件每个只改 2-10 行 = 29 处补译）。补译的 29 行已逐行验证宽度保持（替换 + pad）。
- 排查宽度问题先用 `git diff --numstat` 确认自己改了哪些文件/多少行，别把既有问题归到自己头上。
