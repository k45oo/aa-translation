# CDP 手机视口实测方法(验证网页在手机上的真实渲染)

## 为什么必须用 CDP
- `--headless --window-size=390,844` 截图**不是**手机行为:headless 默认桌面 UA,无 viewport meta 时布局视口=窗口宽(390),不触发手机浏览器的 980px 行为 → 截图失真
- 真实手机(无 viewport meta):布局视口=980px,整体缩放到屏宽 → 15px 字物理显示 ~6px

## 步骤
1. 启动 Edge headless + CDP 端口(注意 `--user-data-dir` 用 Windows 原生路径;已有 Edge 实例会冲突退出码 21,先 taskkill)
   ```
   "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --disable-gpu --remote-debugging-port=9222 --user-data-dir="F:\tmp\edge_profile" about:blank
   ```
2. node 脚本(node ≥22 原生 WebSocket)连 `http://127.0.0.1:9222/json/list` 的 page websocket
3. **关键**:`Emulation.setDeviceMetricsOverride {width:390, height:844, deviceScaleFactor:2.625, mobile:true}` — `mobile:true` 才触发 980 布局视口行为
4. `Runtime.evaluate` 读布局数据 + `Page.captureScreenshot` 截图

## 实测数据(390 宽,deviceScaleFactor 2.625)
| 版本 | innerWidth | nav | main | pre 字号 | 结论 |
|---|---|---|---|---|---|
| 现状(无 viewport meta) | **980**(缩放 39.8%) | 240px 占满整屏高 | 物理 ~295px | 15px→**~6px** | 不可读 |
| 仅加 viewport | 390 | 240px 固定 | 仅 150px | 15px | 侧栏挤爆 |
| 移动适配版(目录顶部化+11px) | 390 | 顶部 287px 可滚动 | 全宽 390 | 11px | 可读,横幅横滚 |

## 布局断言要点
- `document.documentElement.clientWidth` = 390 才算适配生效(980 = 未加 viewport)
- pre 的 `scrollWidth/clientWidth` > 1 表示该章有行需横向滚动(11px 下 100 格行 ~全屏,150+ 格横幅横滚)
- `bodyScrollW == 390` 表示无横向溢出

## 附:playwright MCP 不可用时的替代
- `mcp__playwright__browser_navigate` 若报 Chromium 未安装,直接用系统 Edge headless + CDP 上面的方案(零安装)
- vision 辅助模型不熟悉 AA:会把 AA 艺术误判为"乱码/错位/漫画图片",布局判断以 CDP 数字为准,视觉仅作参考
