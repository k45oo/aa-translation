# 免费模式补全 + 工作台迭代（2026-08-26 晚）

## 免费模型池：GLM-4-9B-0414 加入链首（commit e7ad996）

FREE_MODELS 改 **(模型, 后端)** 结构，支持多后端分发：

```python
FREE_MODELS = [
    ("THUDM/GLM-4-9B-0414", "siliconflow"),        # 链首：最快+无过滤
    ("stealth/ox-alpha", "commandcode"),           # 文本+视觉
    ("poolside/laguna-s-2.1-free", "commandcode"), # 快
]
```

- `_call_free_model(mdl, backend, ...)` 按后端分发 call_siliconflow / call_commandcode
- GLM-4-9B 实测 0.3-2s（比 ox-alpha 快 10 倍）、**无 R18 过滤**（露骨台词直翻）

## 免费模式三环节全覆盖（commit afd3e70）

| 环节 | 入口 | AA_FREE_MODE=1 行为 |
|---|---|---|
| 翻译 | call_api → call_with_fallback | ✅ 免费链 |
| 翻译辅助脚本（pass2/3/7/9/10、refill 等 20+） | call_api | ✅ 免费链 |
| 校对 | proofread.py → call_with_fallback | ✅ 免费链 |
| 视觉 | call_qwen_vl | ✅ 免费分支 → ox-alpha image_url |

- **call_qwen_vl 加免费分支**：`AA_FREE_MODE=1 and (image_b64 or image_url)` → `_call_free_vision`（ox-alpha 视觉 + 无限重试；图片超限/内容拒直接抛不重试）
- **call_commandcode 加 image_b64 参数**：OpenAI 兼容 image_url 格式（`content: [{type:image_url,...},{type:text,...}]`）
- r18_vision_pilot 等调 call_qwen_vl 的工具**自动享受**免费视觉分支
- 视觉实测：AA_FREE_MODE=1 下 ox-alpha 10.1s 看图识别

## 免费模式不设 AA_BACKEND（commit 0164d03）

**坑**：AA_FREE_MODE=1 时 translate_runner/aa_translate 的 auto-backend 逻辑仍会设 AA_BACKEND=zhipu（打印误导日志 `[auto-backend] -> zhipu`，且免费链某处回退时可能误用付费模型）。
**修复**：`if not os.environ.get("AA_BACKEND") and os.environ.get("AA_FREE_MODE") not in ("1","true","True")` 两处都加。

## 免费模式启动翻译

```bash
AA_FREE_MODE=1 ./.venv/Scripts/python.exe scripts/runner/translate_runner.py --work <id> --chapters all --no-dl
```
- `--no-dl`：源文件已在 aa/ 且 work/chapter_order.json 存在时跳过下载
- 空 r18_level 作品（=no 非 R18）免费流程完全适用（GLM 无过滤）
- 后台跑（background=true + notify_on_complete），64 话全量需要较长时间
- 进度看 work/runner_log.txt + 汉化/ 目录文件数 + work/*.ckpt.json

## 免费模型实测补充

- **Nous Portal**：免费模型 6 个（laguna-s-2.1:free 质量好 R18 无过滤），但 **OAuth 非 API key**（access_token 1h 寿命、refresh token 单次使用会吊销 Hermes 会话）——暂不接入
- minimax/m2.7-free、m3-free 当前 503 不可用（免费目录轮换）

---

# 2026-08-27：ox-alpha 下架 + opencode zen 加入 + 多模型并发 + 素材统计防误判

## 1. ox-alpha 下架，免费链重构（commit aa458ae）

`stealth/ox-alpha` 从 commandcode `/models` 列表消失（确认下架）。变更：
- FREE_MODELS 移除 ox-alpha，替换为 `minimax/minimax-m3-free`（1M ctx 兜底）
- `_call_free_vision` 删除（死代码）；`call_qwen_vl` 移除免费视觉分支——**视觉一律回退 Qwen3-VL（付费）**（laguna/minimax 视觉不可靠）
- **教训**：免费模型目录会轮换，free 模型下架后视觉免费化会失效——视觉保持付费是稳妥默认

## 2. opencode zen 免费模型加入（commit 77a1cb4）

用户指定 6 个 opencode 免费模型逐个测试（zen 端点 `https://opencode.ai/zen/v1/chat/completions` + `OPENCODE_GO_API_KEY` + `reasoning_effort=none` + UA）：

| 模型 | 结果 |
|---|---|
| **mimo-v2.5-free** | ✅ 1.6-2.6s 快稳，R18 直翻 |
| **hy3-free** | ✅ 2.5-3.2s 质量好 |
| **big-pickle** | ✅ 2.9s（偶发连接失败重试即过） |
| nemotron-3-ultra-free | ⚠️ 21-30s 慢 + reasoning_effort=low 偶发响应结构异常 |
| muse-spark-1.2-contributor-free | ❌ 500 |
| nemotron-3.5-lightning-free | ❌ 200s 超时 |

接入：`call_opencode_zen()` + `_call_free_model` 加 `opencode_zen` 后端分发。**FREE_MODELS 扩到 6 模型（三平台限流独立）**：
```
GLM-4-9B(siliconflow) → mimo-v2.5-free(zen) → laguna(commandcode)
→ hy3-free(zen) → big-pickle(zen) → minimax-m3(commandcode)
```

## 3. 多模型并发（commit 51e29f0 + 调整）

用户要求"多免费模型并发加快效率"：
- `_call_free_chain` 加**轮询指针**（`_free_round_robin_start`，线程安全锁）——每个 worker 从不同免费模型起步，天然分散到不同模型并行
- `aa_translate` 免费模式 `MAX_WORKERS = AA_FREE_WORKERS`（默认 3，可调 5-6）
- 验证：6 worker 并发测试 GLM(0.4-0.8s)/laguna 交替使用
- **关键认知**：3 平台（siliconflow/commandcode/opencode）限流配额独立，并行不互相挤占；某模型限流该 worker 自动轮换不阻塞其他

## 4. 素材统计防误判（commit b1a5c9c + d8e3c5f + aebe22e）

**问题**：r401_metsubou 素材「L（死亡笔记）」误判 2851 次——汉化文本行号前缀 `L<数字>` 碰撞；「爱丽丝（多娜多娜）」「泉奈（碧蓝档案）」匹配不到（术语表无多娜多娜条目 / 文本用短名）。

**修复**（logic.py）：
- `_material_match_names(name)`：素材名 → 匹配词集合（基础名+角色全名+日文原名+去姓日文短名+繁简变体）；支持「仅日文」后缀；**排除 len<2 单字符**（L）
- `_jp_short_name(jp)`：日文原名去姓短名（久田イズナ→イズナ、天童アリス→アリス）
- `_works_aliases()`：作品 id→别名缓存，「爱丽丝（多娜多娜）」匹配「多娜多娜 一起来干坏事吧」
- `material_display_parts`：短名部分匹配（泉奈→久田泉奈）+ 作品别名匹配
- `validate_glossary`：角色日文重复检查**排除 texture 分类**（允许白名单 アリス 与真实角色 アリス 同名）
- 实测：爱丽丝 189 次（愛麗絲39+爱丽丝137+アリス13）、泉奈 67 次、L=0

**多娜多娜角色入库**：维基提取 11 个，多数已存在（备注=那由多帮派等），只补 4 个（虎太郎/爱丽丝/小丑/玖光）。**坑**：新增前先查日文名已存在——平假名とらたろう vs 片假名トラタロ 重复，**保留备注更具体的旧条目**删新增，否则 validate 拦截。

## 5. 翻译管线 chunks=0 全漏译事故（commit c007df8）

**现象**：r401_jyusatsu 免费流程 21 话 15 分钟"完成"但大量日文残留（几乎没翻译）。

**根因**（aa_translate.py 分块逻辑）：帖头格式未被 `is_op_header` 识别 → `_cur_op` 全程 None → 分块循环里 `cur_list = op_chunks if k else (usr_chunks if k is False else None)` → k=None 时 cur_list=None → `if cur_list is not None` 才 append → **所有行被静默丢弃** → `flagged=2270 但 0 chunks` → 全部不翻译，原文直接落盘。

**修复**：None（作者归属未知）归入楼主块（默认），绝不丢行；加 `first` 标志处理首行 k==cur_kind==None 边界。验证：5163 单话重跑 `0 chunks` → `46 chunks of 50`。

**教训**：
- **翻译完成后必须抽查汉化文件内容**（不是只看文件数）——15 分钟 21 话这种"异常快"是危险信号
- 判定信号：`flagged=N 但 0 chunks`（分块计数）直接暴露丢行
- 大批量翻译前先跑 1-2 话验证质量，确认无误再全量
