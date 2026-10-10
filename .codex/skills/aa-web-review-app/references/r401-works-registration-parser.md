# r401 纯原文作品注册进工作台 + 通用解析器（2026-08-20 实测）

## 触发
8 部 r401 分类作品（636 话）批量下载提取完成后，注册进 aa_web_app（registry.py）供工作台阅读。

## 注册要点（registry.py）
- 每条 WORKS 加 `site_name`（**去 TLD 短名**，用户要求「.com/.net 不用显示」；index 显示 `cfg.get("site_name") or cfg.get("site")`）：yaruomatomex / yaruo-matome / himanatokiniyaruo / yaruocheck / yomuaa
- `site="yaruomatomex_any"`（通用解析器，见下）
- `url_pattern` 必须与各站真实 URL 结构一致：
  - yaruomatomex.com / himanatokiniyaruo.com → `blog-entry-{eid}.html`
  - **yaruo-matome.com → `archives/{eid}`**（页面 canonical 是 archives 结构，写 blog-entry 就错）
  - **yomuaa.r401.net → `/episode/<n>` 且 eid=arcid**（YomuAA 全站单页无 canonical/og:url，og:url 只有 `yomuaa.r401.net/`；真实 URL 要从 **r401 分类页 exlink href 拉 arcid→episode 映射**重写所有 `# 原帖地址` 行）
- 纯原文作品（无汉化目录）：`work_stats` 的「汉化目录不存在」error 分支要改——**chapters = aa 原文数，error=None**（否则索引页「✗ 目录错误」）；汉化目录可空但目录要建（mkdir）

## 通用解析器 scripts/sites/yaruomatomex_any.py
一个解析器吃 5 个站点（r401 exlink 域名结构各不相同）：
- **容器**：`entry-body` > `entry-content` > `ently_body` > `article` > `<article class="aa"` > 整页（CONTAINERS 列表顺序）
- **帖头**：`<dl><dt>...</dt><dd>...` 按 `<dt>` split；无 `<dl>` 回退 `N ：/N 名前：` + `<br>` 切分（`parse_br_posts`，yaruocheck/YomuAA 用）
- **YomuAA 分镜**：`<dt><span id="koma-1">1</span></dt>` 纯数字分镜序号 → `header.isdigit()` 判空不当帖头
- 帖头色：render_header 动态给（◆作者绿粗 + ID 红粗），colormap 0 正常（原版 yaruomatomex 也 0，colormap 只存骰子红/加粗）
- `get_title` 取 `<h1/h2>` 回退 `<title>`（YomuAA 的 `<h1>` 是 "YomuAA" 要跳过，取 `<title>`）
- `_site_url` 从 canonical / og:url 取真实地址，都缺才 fallback url_pattern（Rance 就是踩这个）

## ⚠️ clean() 残留 `\r\n` 导致 AA 图被空行拉长（样式问题根因）
- 症状：阅读页 AA 图每行前多一个空行，画面拉长一倍（用户报「样式有问题」）
- 根因：`_base.clean()` 把 `\r\n  &nbsp;<br />` 的 `&nbsp;` 行转成空行、`\r` 残留在行首 → `parse_posts` split 后每段是 `'\n  AA'` → build_txt 写文件 = 换行+AA
- 修复：`clean(row_html).lstrip("\r\n")`（dt 和 br 两种解析路径都要）
- 验证：aa 文件空行占比 0%（Rance 8% 是自身「クリアリザルト」总结页分镜空行，正常）

## 抓工作台页面用 python urllib，勿 curl（用户明确指正）
- 「你每次抓工作台页面都超时，换个方式」——大作品单章 1.2MB+ 渲染，curl 每次都等超时
- `urllib.request.urlopen(url, timeout=8)` + `re.findall` 抓片段，0.3s 完成
- git-bash 的 curl 是 Windows 原生版，`-o /dev/null` 还报 size 0/exit 23 假异常
- 浏览器工具（playwright/computer_use）在此环境不可用（无 Chrome 发行版 / 无窗口匹配），urllib 是可靠替代

## 坑
- 分类页 SSL EOF 偶发 → 抓页函数每次新建 opener + 重试 3 次（见 r401-category-batch-download.md）
- registry 注入用 Python 脚本（`chr(92)` 拼反斜杠）勿 heredoc 手拼：`f'        aa_dir=BASE + r"{BS}{ddir}{BS}aa",'`（`\a` 在字符串里是响铃符，`\\aa` 会变 `\x07a`）；锚点找 `'    ),\n}'` 要小心重复 `),`（kikkoman 结尾被吃过一次）
- 提取后必须用 extract_all.py 重跑（site 解析器生成 colormap + 修空行），覆盖 dl 脚本的简化提取

## 参考脚本
- `temp/dl_r401_works.py` + `temp/dl_r401_retry.py` + `temp/r401_works_index.json`
- 8 部注册 id：r401_dungeon/kizoku/ryouri/jyusatsu/metsubou/rance/hyakka/kimyou（18 部总作品）
