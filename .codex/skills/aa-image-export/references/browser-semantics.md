# 浏览器字体语义测量与 CDP 手机模拟(2026-08-09 实测)

## 为什么需要
AA 图片渲染的对齐基准 = 用户在浏览器看交付 HTML 的效果。HTML 字体栈 `"ＭＳ Ｐゴシック","MS PGothic",...,"MS Gothic","SimSun",monospace` 中 **MS PGothic 是比例字体**,浏览器按每个字符第一个命中字体的 advance 布局。图片渲染必须复刻该语义,否则用户判定"错位"。

## 浏览器实测数据(Edge 151, 15px)
canvas measureText(与 DOM 渲染同引擎 DirectWrite):

| 字符 | 宽度 | 格数(7.5px/格) |
|---|---|---|
| `\|` | 4.0px | 0.53 |
| `／` | 8.0px | 1.07 |
| `あ` | 14.0px | 1.87 |
| `A` | 9.0px | 1.20 |
| `ﾉ` | 7.0px | 0.93 |
| `¨` | 8.0px | 1.07 |
| `—` | 10.0px | 1.33 |
| `　`(U+3000) | 10.0px | 1.33 |
| `｜＼＿￣漢′¨×°…` | 15.0px | 2.00 |

MS Gothic(ttc index 0)是完美等宽(|=1格,｜=2格,—=1格,¨′=2格);MS PGothic(index 2)是比例(|=0.47格)。**布局基准必须用 PGothic 的实测 advance**。

## CDP 测量脚本要点(node,Edge headless --remote-debugging-port=9222)
```js
// Runtime.evaluate 内:
const ctx = document.createElement('canvas').getContext('2d');
ctx.font = '15px "ＭＳ Ｐゴシック","MS PGothic",...,"SimSun",monospace';
ctx.measureText(ch).width;   // 逐字符
```
PIL 侧 `font.getlength(ch)`(FreeType hmtx)与 measureText 对比,30px vs 15px 容差 ≤2.5px。

## Edge headless 手机模拟(必须 mobile:true)
- 无 viewport meta 的页面:desktop UA + 窄窗口 = 布局视口=窗口宽,**不触发手机 980px 行为**。
- 正确模拟:`Emulation.setDeviceMetricsOverride {width:390, height:844, deviceScaleFactor:2.625, mobile:true}` → innerWidth 变 980(无 meta)/390(有 meta)。
- 截图:Page.captureScreenshot;测量:Runtime.evaluate + getBoundingClientRect。
- node v22+ 原生 WebSocket 即可写 CDP 客户端,无需依赖。

## fontTools 硬验证(替代不可靠的 getlength tofu 检测)
- getlength 对缺字形返回 .notdef advance(看起来"正常"),测不出缺字。
- 可靠:TTFont cmap 查覆盖(fontTools 装于项目 .venv);hmtx 查 advance 与期望格数 × UPM/2 是否一致。
- MS PGothic cmap 已导出:项目 `fonts/msgothic_cmap.json`(16127 码位,简体"说们这红饭钱车东长门问"缺→走 SimSun 回退,与浏览器一致)。
