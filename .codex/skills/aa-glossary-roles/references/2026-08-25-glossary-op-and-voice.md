# 2026-08-25 增补：VOICEROID 批量入表 + sujin 楼主识别

## VOICEROID 角色批量加入全局术语表

**方法**：一次性脚本写三处（勿手改）——`角色` 数组 + `glossary` 替换对 + `known_names` 白名单。来源：萌娘百科（zh.moegirl.org.cn）VOICEROID 角色一览。

已入表 15 个：紲星あかり=绁星灯、結月ゆかり=结月缘、琴葉茜/葵=琴叶茜/葵、東北ずん子=东北俊子、東北きりたん=东北切蒲英、東北イタコ=东北伊达子、弦巻マキ=弦卷真纪、月読アイ/ショウタ=月读爱/翔太、桜乃そら=樱乃空、京町セイカ=京町精华、水奈瀬コウ=水奈濑煌、伊織弓鶴=伊织弓鹤、ついなちゃん=追傩酱。

## sujin 系楼主识别修正（sites/yaruobook.py render_header）

原 `author.startswith('◆')` 判楼主 → sujin「すじん ★」（无 ◆）被当普通用户（绿非粗/ID 黑）。
改为 `'◆' in author or '★' in author`（含即楼主绿粗 #008800 bold + ID 红粗 #ff0000）。

**关键**：sujin 不同作品楼主标记不同——
- 生娘シャブ漬け（sujin_shabu）：`すじん ★`（★ 标记）
- 最後の嫁（sujin_saigo）：`すじん ◆nc1PmAFAZo`（◆ tripcode，323 楼楼主 + 用户仅 25 楼）
- 多马甲：カエル/携帯手抜き◯/デイリー/バーニィ（可能为 sujin 马甲或读者）

⇒ 楼主判定必须兼容 ◆ 和 ★ 两种；export_dialogue.py 用 work_meta.author（逗号分隔多马甲）+ ◆/★ 标记双重识别。

## 关联
- export_dialogue.py `--src` 生成原文台词（aa/<章>_台词.txt），楼主判定同源（work_meta.author 多马甲）
- 工作台 4 处（merged_chapters/stats×2/check_chapter）须排除 `_台词.txt` 防误当原文章节
