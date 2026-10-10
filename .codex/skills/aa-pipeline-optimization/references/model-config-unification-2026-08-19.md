# 模型配置统一化(TOML)设计 — 2026-08-19 方案定稿

> 方案文档: `<repo>\doc\模型配置统一化方案_2026-08-19.md`(commit 2a541e8)
> 状态: **✅ 已执行完成**(2026-08-19, commit a711ff2 + ac230a1 + df25d4e)。落地细节与实测坑见方案文档「十、执行记录」。
> 关键实现: `scripts/config/model_config.toml` + `scripts/common/aa_config.py`(tomllib 加载器)。

## 目标

一个配置文件统一管模型/网关/key/参数,脚本零硬编码模型常量。优先级: **env(AA_*) > TOML > 代码默认值**。

## 硬编码盘点(2026-08-19 与磁盘核对,全部点)

| 位置 | 内容 |
|---|---|
| `scripts/aa_translate.py` L18-20 | `GATEWAY="https://opencode.ai/zen/go/v1/responses"`、`MODEL="deepseek-v4-flash"`、`UA`、`PROXY` |
| `scripts/common/aa_backend.py` L30-93 | SF/OR/ZP/MM/MIMO/RI/CC/Ollama 8 套 `*_URL`/`*_MODEL`/`*_KEY`/`*_UA` 硬编码 |
| `scripts/works/refill_manual_saki.py` L16-17 | `os.environ["AA_MODEL"]="deepseek-v4-pro"` + `AA_GATEWAY=opencode...`(写死,强制覆盖) |
| `scripts/tools/vision_classify.py` L312-339 | 4 套视觉模型硬编码(zhipu glm-4.6v/flashx / SF Qwen3-VL-32B / CC mimo2.5-cmd / opencode 兜底) |
| `scripts/tools/usage_stats.py`、`usage_total.py`、`verify/metrics_report.py` | usage 默认路径硬编码 `仓库根/usage.jsonl` |

### key 读取分散
- `aa_translate.py` `get_key()` 读 `OPENCODE_GO_API_KEY`(<hermes>\.env)
- `aa_backend.py` 每后端一个 `_xx_key()` 函数,读不同 env
- `vision_classify.py` `read_env()` 读 5 个 key(BIGMODEL/SILICONFLOW/XIAOMI_MIMO/COMMANDCODE/OPENCODE_GO)

### usage 现状(5 类)
- `aa_translate.py` `_usage_file()`/`_log_usage()` L370-379 = **唯一落盘实现**(读 AA_USAGE_FILE env,默认仓库根/usage.jsonl;AA_USAGE_TAG env,默认 translate)
- `aa_api.py` `record_usage()` = 薄壳转发
- works/fix_manual_*.py、promote_mixed_kikkoman.py、refill_manual_saki*.py 设 `AA_USAGE_TAG=环节名` setdefault
- usage_stats.py/usage_total.py/metrics_report.py 聚合读取

### vision 现状
- 只有 `tools/vision_classify.py` 调模型(其余 vision_review/vision_annotate/v3_backport_v2/aa_to_images 纯渲染工具,不调)
- `chat_vision()` 支持 fmt='chat'(OpenAI image_url)与 fmt='responses'(opencode 网关 input_image 嵌套)两格式,responses 分支保留
- 模型优先逻辑: zhipu_key→glm-4.6v+flashx / sf_key→Qwen3-VL-32B / 无zhipu+cmd_key→mimo2.5-cmd / 无zhipu+mimo_key→opencode兜底

## 方案:TOML 结构

```toml
[default]        # backend/model/gateway/max_workers/max_output_tokens/temperature/use_proxy
[backend.<name>] # commandcode/siliconflow/zhipu/minimax/mimo/runinfra/openrouter/ollama
                 # 字段: url/model/fallback_model/key_env/ua/temperature/ocr_model/qwen_vl_model
[vision]
models = [ {name,url,key_env,model,fmt}, ... ]   # 按序 round-robin; 失败 fallback 下一个
[usage]
file = "usage.jsonl"   # 相对仓库根
tag = "translate"
```

加载器 `scripts/common/aa_config.py`: tomllib(py3.11 标准库)+ `get(section,key)` env 覆盖(AA_<SEC>_<KEY>)+ `reload()`。无 TOML 时回落内置默认 + 警告。

## 改造点

1. `aa_backend.py`: 常量 → `aa_config`; `_xx_key()` 的 env 名从配置 `key_env` 读
2. `aa_translate.py`: GATEWAY/MODEL → `[default]`; get_key 的 key 名 → 配置; usage → `[usage]`
3. `vision_classify.py`: 模型列表 → `[vision].models`(逐个检查 key_env 存在才入候选,替代 if/elif 链)
4. `works/refill_manual_saki.py` 等: 删硬编码 env,改从配置读
5. usage 聚合三工具: 默认路径 → 配置

## 兼容性铁律

- 环境变量全部保留(最高优先级); 旧脚本零破坏(签名不变)
- **works 脚本的 AA_USAGE_TAG setdefault 保留**——是运行时环节语义,不是配置
- 测试 mock 不破坏: 配置只在常量初始化读一次, `A.FLAT_MOD.call_api = fake` 语义不变
- `.env` 不动,配置只指 key 名

## 执行清单(8 步)

1. 新建 `scripts/config/model_config.toml`(迁移 aa_backend.py 全部后端注释+常量)
2. 新建 `scripts/common/aa_config.py`
3. 改 `aa_backend.py` → 4. 改 `aa_translate.py` → 5. 改 `vision_classify.py` → 6. 改 usage 三工具
7. 全量测试(`python -m unittest discover` 154 用例)+ 实测翻译一次
8. 更新 AGENTS.md + 技能文档

## 风险对策

- TOML 解析失败 → load() 回落内置默认 + 明确报错
- 后端配置缺失 → backend(name) 返回空 dict → 调用方用默认值
- env 覆盖被破坏 → 加载器 env 优先于 TOML,顺序测试保证
- 技能/AGENTS 过时 → 同步更新

## 实测坑(2026-08-19 落地,必读)

1. **`backend()` 未设 AA_BACKEND 时保持返回 `"cloud"`**, 勿改配置默认——否则 call_api 走 aa_backend.call_*(用 aa_backend 自己的 urllib), 绕过测试 mock 的 `T.urllib.request`(test_stability 会失败)
2. **commandcode 端点不接受 `reasoning_effort` 参数**(400); cloud 直接模式 chat 格式 payload 不能带它。需要关推理的后端(siliconflow 等)显式设 AA_BACKEND 走 aa_backend.call_*
3. **`get_key()` 读配置 key_env 后要回落 `OPENCODE_GO_API_KEY`**(旧测试写死该 key 名 + 旧 .env 兼容)
4. **`[default].key_env` 必须与 `[default].backend` 对应**(gateway 是 commandcode 但 key_env 缺省 OPENCODE_GO → 401)
5. `r18_safe("cloud")` 要映射 `[default].backend` 的 r18(cloud 是未设 AA_BACKEND 时的虚拟后端名)
6. vision R18 作品名单用配置 `[vision].r18_works`(registry id), 勿硬编码进脚本
