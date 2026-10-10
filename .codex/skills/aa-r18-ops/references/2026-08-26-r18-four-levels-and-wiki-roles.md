# R18 四态分级 + 角色来源更新（2026-08-26）

## R18 分级四态（用户规格逐字确认）

| level | r标 | r18校验阈值 | 后端策略 |
|---|---|---|---|
| **low** | 浅红 `#f8b0b8` | 最严：STRONG≥1 或 NORMAL≥2 即 yes | **全部章先走非 r18**（zhipu 免费），触发审查(1301)降级 r18 链 |
| **medium（默认档）** | **普通红 `#c22`（当前色）** | 正常：STRONG≥2 或 NORMAL≥3 | yes→r18；no/suspect→非 r18 |
| **high** | 深红 `#7a1020` | 最松：STRONG≥3 或 NORMAL≥4 | **除了 no 章都走 r18**（yes/suspect→r18，no→非 r18）；无 prep 保守走 r18 |
| **空(=no)** | 无 R 标 | 不判 | 非 R18 作品，全走非 r18 |

**用户强调："medium是普通红（默认色）"**——medium 的 r-badge 不带颜色类，就是默认 `#c22`。

## 关键实现（commit bcb889a）

- `aa_translate.py _chapter_work_r18` 四态判定：`m.get("r18_level","") or "no"`；high 的 no 章 `hit=="no" and prep存在` → (False,False)；low 全部章 (False,True)；medium yes→(True,False)；空→(False,False)
- **AA_BACKEND 设置**：仅空 level(=no) 作品设全局 AA_BACKEND；high/medium/low 由每章判定驱动（否则显式后端会覆盖章节级选择——high 的 no 章会被提头成 r18 链首）
- `chapter_prep.py prep_chapter(level=...)` 按 level 调阈值；prep_work 从 work_meta 读 level（CLI 传 --work，translate_runner 传 work=）
- `translate_runner.py` 清理环节：high→AA_R18=1；medium/low/空→0
- `main.py`：WorkMeta 加 r18_level，PUT 支持人工修改（非法值丢弃保留原值）
- `index.html`：r 标按 level 加颜色类；详情弹窗 R18 分级可编辑下拉（no/medium/low/high + 摘要）
- `style.css`：`.r-badge.low` 浅红 / `.r-badge.high` 深红

## 角色统一译名来源（用户纠正 2026-08-26）

**用户纠正："优先用 wikipedia 作品名+角色列表，找不到再提示我去萌娘百科找"**——角色添加/查询来源优先级：
1. **维基百科**（作品名+角色列表，如 `zh.wikipedia.org/wiki/蔚藍檔案角色列表`）——一次拿全部角色
2. 找不到 → **提示用户去萌娘百科**（zh.moegirl.tw，用户之前偏好，但现在是第二选择）

### 维基百科角色列表提取法（实测可用）
- 角色条目在 `<dt>` 标签：`中文名（日文名）` 格式
- 提取：删除 rt（读音）/rp/link/style 标签 → 取纯文本 → 中文名=括号前，日文名=括号内
- 中文名繁转简：`uv pip install zhconv --python ./.venv/Scripts/python.exe`，`from zhconv import convert`
- 样例：`砂狼白子（砂狼シロコ）` → 中文=砂狼白子，日文=砂狼シロコ

### 萌娘百科角色页日文名提取（备选，用户指路）
- baLinksMain 导航块含全部角色链接（a href/title）
- 角色页"基本资料"区域：本名=lang=ja 文本（ruby rb + 片假名，去 rt 读音）；譯名=`<b>` 内
- 示例：`<ruby><rb><span lang="ja">砂狼</span></rb>...シロコ</span>` → 日文=砂狼シロコ

## 测试确认（2026-08-26）

- `_chapter_work_r18` 11 用例全过（high/medium/low/no × yes/suspect/no）
- chapter_prep：1 次 STRONG → low=yes（最严）/ medium=high=suspect
- 完整测试 168 用例 7 failures+2 errors+1 skipped，与基线一致（3 个 chapter_prep 失败是预存：测试期望 STRONG≥1→yes 与旧代码不符，stash 对比确认非本次回归）
