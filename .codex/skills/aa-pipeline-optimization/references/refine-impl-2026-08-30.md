# 精译（refine）独立环节实现落地（2026-08-30 实测）

承接 `references/refine-pass-design-2026-08-29.md` 的设计，本次真正实现并跑通 `scripts/runner/refine_runner.py`。**落地与设计有几处重要偏移**（设计基于 SiliconFlow V4-Flash，实际因通道问题切换；设计说"读原始 _汉化.txt + line_kind 门控"，实际用中日文台词表配对）。实现/继续精译前**先读本文件**，别照设计文档重做。

## 最终通道：commandcode deepseek-v4-flash（推理模型）—— ⚠️ 关键坑

**背景**：设计文档指定 SiliconFlow `deepseek-ai/DeepSeek-V4-Flash` 付费直连。实测该通道**30 段块持续挂起**（5 次重试全 `read operation timed out`，偶发几十秒才回），不可用。改为 **commandcode `deepseek/deepseek-v4-flash`**。

**推理模型三坑（缺一即失败）**：
1. **`reasoning_effort="low"` 必须**：不带则 `content` 空（推理吃光输出，`finish=length`）。
2. **`thinking: {"type":"enabled"}` 必须**：不带报 `HTTP 400`（`unsupported_model`/`invalid_request_error`）。这是 DeepSeek 官方文档的 v4 系列必需参数（`https://api-docs.deepseek.com/zh-cn/api/create-chat-completion`）。
3. **`max_tokens=32768`（大）**：reasoning 吃 token，小值导致 `content` 空。

**call_commandcode 不能直接用**：它对 model 显式指定时**只**对 `muse-spark`/`ox-alpha` 加 `reasoning_effort`，**不处理 deepseek**（裸调用 content 空）。且 `reasoning_effort="none"` 对 commandcode deepseek 报 400（只接受 low/medium/high）。→ **refine_runner 自建 `_call_commandcode_ds()`** 直接构造请求（带 UA 头，无 UA 403 code 1010）：参考 `scripts/runner/refine_runner.py` 的 `_call_commandcode_ds`。

**key 位置坑（本次最大的 400 元凶）**：`COMMANDCODE_API_KEY` 已换新 key，**只在新档案 `<hermes>\profiles\translation\.env`**（`user_6...VnjH`），`<hermes>\.env` 是旧值（`user_5...wLV1`）。`aa_backend._read_env_key(name)` 默认只读 `<hermes>\.env` → 读到旧 key → **HTTP 400**（不是 Unauthorized，是 Bad Request）。**必须显式传新档案路径**：`AB._read_env_key(rf.key_env, ("<hermes>/profiles/translation/.env",))`。诊断：`_read_env_key` 分别读两个 .env 对比前缀（`user_5...` vs `user_6...`）。

**模型 ID 确认**：`GET https://api.commandcode.ai/provider/v1/models`（带 UA 头）→ 列表有 `deepseek/deepseek-v4-flash`（无 `-0731` 后缀——那是 deepinfra 的 ID；SiliconFlow 版是 `deepseek-ai/DeepSeek-V4-Flash`）。加 UA 头否则 403。

## 落地架构（refine_runner.py）

**设计偏移**：设计说读原始 `_汉化.txt` + `line_kind` 门控；实际用**中日文台词表配对**（`extract_dialogue.parse_dialogue_file` 读 `aa/<base>_台词.txt` 原文 + `汉化/<base>_台词.txt` 汉化，按 `(line_no, seg_idx)` 键配对）。三套口径已收编到 v3 meta（见 `dialogue-extraction-impl-2026-08-29.md` 第八轮），台词表是权威。

**管线**：`RefineCfg(work)` 读 `[refine]` 节 → `_pair_dialogue`（配对+净化）→ `_chunk`（≤chunk_max_segs 段）→ `_build_prompt`（知识库前缀+前文续接+疑点标注）→ `_refine_chunk`（调 `_call_refine` + 解析 + 防线）→ `_backfill`（段级回填+宽度）→ 原子写 `精译/<base>_精译.txt` + ckpt（`work/refine_ckpt/<base>.json`，done_chunks/sha1 脏章检测）。

**配置** `scripts/config/model_config.toml` `[refine]` 节（改 backend/model/key 不碰代码）：
```toml
[refine]
backend = "commandcode"
model = "deepseek/deepseek-v4-flash"
key_env = "COMMANDCODE_API_KEY"
max_tokens = 32768
temperature = 0.1
reasoning_effort = "low"
chunk_max_segs = 30
chunk_max_tokens = 3000
retry_single = 1
retry_chunk = 1
```

## 台词净化（`_clean_dialogue_text`，重要）

v3 meta 会把**纯 art 行/mixed 行误判 text**（如 L39 `､　　お待ちどぉ` 行首带 art 前缀、L262 `＿_fメメメﾒ≧` 纯 art、L278 `/;i;i:i:i:/　「尾やヒレ...` mixed）。refine 侧必须净化：
- `_ART_LEAD` 正则剥离行首 art 残留（`\s\u3000` + 半角假名符号 `､ｨｲ...` + ASCII 画线 `/;:iI|!\\(\){}<>_=+~^*'\"\`\\-`）。
- **纯 art 检测**：净化后无汉字（`_HANZI`）且 `kana_runs` 为空 → 返回空串跳过；无汉字且假名 run 全是单字符重复（`メメメ/ﾒﾒﾒ` art 纹理）→ 空串。
- `_pair_dialogue` 里空串段 `continue`（不送模型），回填时原样保留。

**实测收益**：净化前第 2 块 30 段 → 25 段含新增假名被拒（模型把 art 复制进译文）；净化后 30 段全干净，0 拒。

## 防线（`_refine_chunk`）

- **`_has_kana(text, old)` 新旧对比**：只拒"新增"假名（`run not in old_kana and run not in old_text`）。旧译已有的角色名/art 照抄不拒。之前"含假名即拒"误伤 7 段。
- **批量重试**（不逐段）：第一遍收集所有含新增假名的段 → **一次调用**重试（SiliconFlow 挂起时逐段重试=灾难）→ 重试仍含假名 → 拒用落 `refine_review.txt`。
- **块级截断**：`len(parsed) < len(expect)*0.6` → 整块重试 1 次。
- **`_parse_output` 过滤错觉行**：`{k:v for k,v in parsed.items() if k in expect}`（模型偶发输出错位行号/幻觉，过滤防污染）。

## 汉化台词表提取源（extract_dialogue 注意）

`extract_dialogue.ensure_dialogue_file(src_only=False)` 的"汉化台词表"**默认仍从原文提取**（代码 `txt_lines = open(aa_dir/base.txt)` 不区分 src_only）——**产的是日文不是中文**。refine 的 `_pair_dialogue` 靠**自己的过期检测**（`汉化文件 mtime > 汉化台词表 mtime + 1` → overwrite 重生成）触发。**要产中文台词表**，需让 `src_only=False` 从 `_汉化.txt` 读（本项目未改 extract_dialogue——用户明确"不要随意修改台词提取代码"）。

## 工作台「精译」档切换（2026-08-30 最终形态，多次迭代后定型）

**演进**：起初加**独立第三档按钮**（汉化/精译/原文三档，`reader.js?v=20260830f`）——但用户反馈后改为**合并「汉/精」为一个切换按钮 + 快捷键 h + 颜色区分**（commit `7e59e362`，`reader.js?v=20260830h`）。

**main.py `reader_page`**：读 `精译/<base>_精译.txt` 渲染 `rendered_refine` + `has_refine`（`os.path.dirname(out_dir)/精译/<base>_精译.txt`，行号与原文对齐）；模板注入 `rendered_refine`/`has_refine`。

**reader.html**：`src-toggle` 区只有**两个按钮**——`#btn-refine-toggle`（汉/精合并，`data-mode="tr"`，disabled 当无汉化）+ 原文按钮（`data-mode="src"`）。`refine-data` JSON 块仅 `has_refine` 时输出。CSS 加 `.seg-btn.active.refine { background:#3a5a9a }`（精译紫色）。

**reader.js `render()`**（重写，要点）：
- `render(m)` 三档路由 `m==='src' ? srcHtml : (m==='refine' ? refineHtml : trHtml)`；**合并按钮显示逻辑**：`refineToggle.textContent = m==='refine' ? '精译' : '汉化'`；`classList.toggle('active', m==='tr'||m==='refine')` + `classList.toggle('refine', m==='refine')`（紫色）；`title` 动态「汉化/精译（快捷键 h 切换）」。
- **`toggleRefine()`**：三态——`src→` (`hasTr?'tr':(hasRefine?'refine':'src')`)，`tr↔refine`（`mode==='tr'&&hasRefine?'refine':'tr'`）。
- **坑（必须 stopPropagation）**：`#btn-refine-toggle` 的事件若不加 `e.stopPropagation()`，会同时冒泡到 `seg` 容器的 click handler → 双触发 toggle（切过去又切回来，看起来没反应）。修复：`refineToggle.addEventListener('click', e=>{e.stopPropagation(); toggleRefine();})` + seg handler 里 `if(b.id==='btn-refine-toggle') return`。
- **快捷键 h**（`document.keydown`）：`e.key==='h'||'H'` 且 target 非 input/textarea/select/button → `toggleRefine()`。
- **实测**：initial 按钮显示「汉化」+`seg-btn active`（棕）；点击→「精译」+`active refine`（紫）+`mode=refine`+`hint=精译`；往返切换正常；原文模式按 h→汉化→再按→精译（三态往返正确）。`refineDataLen=2402`（=汉化/原文行数对齐）。

**工作台三档切换的正交细节**：精译 **行号与原文/汉化对齐**（`_backfill` 保证），切换纯本地渲染不跑模型，台词纠正/行编辑事件委托在 render 重建后仍有效。

**用户偏好（精译阅读交互）**：用户要「精译按钮改为 1 个按钮 汉/精 用颜色区分，加快捷键 h 切换，悬浮按钮提示按键」——即**不想要独立第三按钮**（节省顶部空间），改用**合并 toggle + 颜色 + 快捷键**。读者/阅读器类 UI 的「三档并列按钮」形态要留意，用户可能偏好合并 toggle。

## 试跑作品选择教训

用户**实际要求试跑作品**：先 `akari_haisin`（r18 高、漏翻严重，生成中日文台词文件测试）再 `akari_series`（entry_27892）——**非方案文档写的 r401_ryouri**。**测试作品以用户当前指令为准，别照方案文档字面**（本会话照方案试跑 r401_ryouri 被用户质疑"谁让你测 r401_ryouri 了"）。

## 用户硬约束（审阅/调试 extract 类代码时）

用户明确（2026-08-30）：①**不要随意修改 extract_dialogue 代码**（只查看它是否支持中日文提取，不做多余改动）；②**不要随便判断检查质量**；③**不要用个例去判断对错**；④**1 步步来**（先生成中日文台词文件，再专注精译测试）。——这些是**工作方式偏好**，审阅/调试公共模块（extract_dialogue/classifier）时要克制改动范围、用批量/权威口径而非单例推导结论、逐步推进。

## 验证

- 实测 entry_27892（akari_series）：107 段（净化后）→ 4 块，done=True，26 行改动，译文质量提升。
- `已译段数 == 配对段数`（0 失配）是配对正确的关键指标，必查。
- commandcode 快且稳（12s），逐步替代 SiliconFlow 作为精译主通道。
