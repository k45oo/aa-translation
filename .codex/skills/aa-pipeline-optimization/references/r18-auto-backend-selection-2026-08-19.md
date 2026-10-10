# 按作品 R18 属性自动选后端 — 2026-08-19 落地（commit 00f830a）

> 场景：`python scripts/aa_translate.py --work <id>` 未设 AA_BACKEND 时，按作品是否 R18 自动选最合适的后端+模型。用户要求：**根据作品 R18 要素自动选模型（翻译+视觉）**，zhipu（免费额度）、muse-spark（低价）、mimo-v2.5（备选）纳入配置。
> 关联：references/model-config-unification-2026-08-19.md（TOML 统一配置基础）/ doc/模型比对_全后端翻译与视觉_2026-08-19.md（实测依据）/ doc/模型配置统一化方案_2026-08-19.md 第十一节。

## 数据源与配置

1. **registry.py 每作品 `r18: bool`**（唯一数据源）：
   - R18（6）：fukou / yaruobook / touhou_teisou / yaruohiroba / last_dungeon / kikkoman
   - 非 R18（3）：baitou / saki / saki_fc2
   - 新作品登记必须带 r18 字段（缺省按 False 保守处理）。
2. **model_config.toml `[default].r18_backends` / `normal_backends`**（优先级表，从高到低，每项 `{backend, model}`）：
   - R18: qwen-vl(32B 质量首选) → siliconflow(GLM-4-9B 免费) → commandcode(mimo-v2.5) → mimo → minimax
   - 非 R18: zhipu(glm-4.5-air 免费) → siliconflow(GLM-4-9B) → commandcode(muse-spark 低价) → commandcode(mimo-v2.5) → qwen-vl(30B-A3B 速度)
3. **`[vision].models` 重排**（2026-08-19）：32B → 30B-A3B(速度首选 2.6s) → glm-4.6v-flashx → mimo2.5-cmd → glm-4.6v(非R18) → mimo-v2.5(网关兜底)。R18 作品只留 r18=true 候选。

## aa_config.py 新增

```python
def auto_backend(work_r18):
    """返回 (backend_name, model_override)。R18 必须 r18_safe; key_env 有值才可用。"""
    table_key = "r18_backends" if work_r18 else "normal_backends"
    for item in load().get("default", {}).get(table_key, []):
        name = item["backend"]
        if work_r18 and not r18_safe(name):
            continue                      # R18 作品绝不用 zhipu(1301 过滤)
        key_env = load()["backend"][name].get("key_env", "")
        if key_env and not _env_key_exists(key_env):
            continue                      # 没有 key 的后端跳过
        return name, item.get("model") or cfg.get("model", "")
    return load()["default"]["backend"], load()["default"]["model"]  # 全部不可用回落

def vision_r18_works():
    """优先读 registry 的 r18 字段; TOML r18_works 兜底(旧配置兼容)。"""
```

`_env_key_exists(key_env)`：查翻译档案 .env（<hermes>\profiles\translation\.env）优先、全局 <hermes>\.env 兜底。

## 三个实测坑（2026-08-19，必读）

### 坑①：后端专用 env 是 import 时读的 → call_api 需 model 透传
`aa_backend.py` 的 `QWEN_VL_MODEL = os.environ.get("AA_QWEN_VL_MODEL") or ...` 是**模块级常量，import 时一次性读**。main() 里 `os.environ.setdefault("AA_QWEN_VL_MODEL", _m)` 在 import 之后设置 → **不生效**（call_* 内部用常量）。
**修复**：call_api 分发处每次调用按 env 取模型透传：
```python
def _m():
    me = {"siliconflow": "AA_SF_MODEL", "qwen-vl": "AA_QWEN_VL_MODEL", "zhipu": "AA_ZP_MODEL",
          "commandcode": "AA_CC_MODEL", "mimo": "AA_MIMO_MODEL", "minimax": "AA_MM_MODEL",
          "runinfra": "AA_RI_MODEL", "openrouter": "AA_OR_MODEL"}.get(b)
    return os.environ.get(me) if me else None
return aa_backend.call_qwen_vl(..., model=_m())   # 每个后端分支都传 model
```
auto_backend 设置 env 时也要按后端映射（qwen-vl→AA_QWEN_VL_MODEL，zhipu→AA_ZP_MODEL…），不是通用 AA_MODEL。

### 坑②：muse-spark 显式传参必须 reasoning_effort=low
call_commandcode 的 `attempts = [(model or CC_MODEL, {}, 60)]`——model 非 None 时第一条不带 low。auto_backend 的 normal_backends 用 muse-spark 会经 model 参数传入 → 思考吃光输出空。
**修复**（call_commandcode 内）：
```python
if model is not None and "muse-spark" in model:
    attempts[0] = (model, {"reasoning_effort": "low"}, 120)
```

### 坑③：vision_r18_works 统一数据源
`vision_classify.py` 的 R18 作品判断原用 TOML `[vision].r18_works` 硬编码名单（曾漏 yaruohiroba 等新站）。改为 `aa_config.vision_r18_works()` **优先读 registry r18 字段**（自动覆盖全部作品），TOML 字段仅兜底。

## 实测验证

- 自动选择结果：baitou/saki/saki_fc2 → zhipu(glm-4.5-air)；fukou/yaruobook/touhou_teisou/yaruohiroba/last_dungeon/kikkoman → qwen-vl(Qwen3-VL-32B-Instruct)
- 真实调用：zhipu 译「こんにちは、お元気ですか？」→「你好，你好吗？」✓；qwen-vl 32B 译 R18 露骨「このおちんちんが好き…子宮が喜んじゃってるっ」→「我喜欢这个小弟弟……子宫都在高兴呢」直过无审查 ✓
- 154 单元测试全绿；边界测试（R18 后端全 r18_safe / normal 首选 zhipu / vision 含 30B-A3B / registry 9 作品全有 r18 字段）全过

## 后端选择铁律（2026-08-19 定稿）

| 场景 | 首选 | 备选 | 禁用 |
|---|---|---|---|
| R18 翻译+视觉双用 | SiliconFlow Qwen3-VL-32B（AA_BACKEND=qwen-vl） | GLM-4-9B 免费 / 30B-A3B 速度 / mimo-v2.5 | zhipu 全系（code 1301）、Qwen3.5 全系（思考吃空） |
| 非 R18 翻译 | zhipu glm-4.5-air（免费额度） | GLM-4-9B / muse-spark(low) / mimo-v2.5 | — |
| 视觉分类 | Qwen3-VL-32B（9/9） | 30B-A3B（快）/ glm-4.6v-flashx / mimo2.5-cmd | glm-4.6v（r18=false）用于 R18 作品 |
