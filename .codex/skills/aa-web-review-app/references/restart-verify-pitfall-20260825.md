# 工作台改代码后不生效：旧进程加载旧模块（2026-08-25 实测）

## 症状
代码层已修（直接调用 `logic.merged_chapters` 验证 0 台词章），但用户报「阅读页目录还有台词章」
「作品列表是否已翻译也算上台词章」。运行中的工作台是**旧代码**。

## 根因
工作台不热重载，但「改代码 → 杀旧进程 → 重启」仍可能不生效：
- 重启的进程可能**没活下来**（被其他实例/旧 PID 替代）
- 同一端口 8765 被**旧 PID 占用**，加载的是提交前的旧模块
- 症状表现：首页 HTTP 200 正常（旧进程也 200），但具体页面（read 目录）是旧逻辑

## 正确姿势（三步）
1. **杀旧后确认端口无监听**：`powershell Get-NetTCPConnection -LocalPort 8765 -State Listen`
   空 = 杀干净，再启动；有残留 = 旧进程还占端口
2. **启动后核对监听 PID 是新的**：netstat 查 8765 的 OwningProcess 要对得上刚启动的进程
3. **实际验证受影响页面**：如 `/work/<id>/read?f=entry_XXX.txt` 页面源码 grep 无「台词」链接；
   不能只看首页 HTTP 200

## 相关
- work_stats 磁盘缓存 `stats_cache.json` 可能含旧文件类型条目，但缓存 key 按 mtime+size，
  文件变动时自动失效（无需手动清）
- 台词文件（`汉化/*_台词.txt`、`aa/*_台词.txt`）被误当章节的 4 处：
  `merged_chapters` / stats 章节数×2 / `check_chapter`（见 SKILL.md 正文）
