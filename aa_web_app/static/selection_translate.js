/* 划词翻译 + 朗读（2026-09-03）—— reader.html 与 board.html 共用。
 *
 * 语义（用户确认）：
 *  - 原文/译文/行编辑弹窗内都可划词；统一日→中。
 *  - 译文页划词主要用途 = 抓漏翻残留日文；中日混排整段原样送模型。
 *  - mouseup 后选区静止 1.2s 弹悬浮框；期间改选/点击/滚动重置计时。
 *  - >500 字不弹（防误拖整章烧 token）。
 *  - 关闭：点弹窗外 / Esc / 新划词替换；弹窗出现后选区消失也不自动关。
 *  - 单击（无拖选）不弹 → 阅读器行号点击照旧开行编辑。
 *  - 付费三通道翻译（usage 记 selection_translate；耗时/通道另记 selection_log.jsonl）。
 *  - 朗读走 edge-tts 服务端合成（ja-JP-NanamiNeural，代理 + 缓存）。
 *
 * 2026-09-20（用户实测「译文仍是日文原文」+「等很久」）：
 *  - 「重译」按钮：force=true + avoid=上次通道 → 跳过照抄判定强制出结果、换模型再试。
 *  - echo=true（模型照抄原文）→ 黄色提示 + 禁用「替换」（防把日文替换进正文）+ 高亮重译。
 *  - 翻译中显示已等待秒数；× / Esc / 点外部即 abort 请求（原先只能干等）；150s 硬超时。
 *
 * 2026-09-21（用户要求三项）：
 *  - 选中项高亮 + 位置固化：弹窗弹出时把选中区间包 <span class="sel-hl">（只改背景色，不动字号/宽度），
 *    同时把 [start,end) 绝对偏移记进 selCtx → **翻译过程中点别处（原生选区消失）依然能替换**
 *    （旧实现替换时重取活的选区，点了别处就报「选区已变化」）。
 *    **顺序坑**：高亮必须在「量锚点」之后才打——hlRange 会 extractContents 掉原生选区，
 *    先打高亮则选区矩形取不到 → 回落 body 矩形（整页高）→ 弹窗被摆到页面底部，看起来「只高亮不弹窗」。
 *    另：锚点回落跳过 body/html（它们的 rect 是整页盒子），showAt 末尾再夹一次视口。
 *  - 「sfx 替换」按钮文字改成「替换」，字形用 sfx 样式（楷体+斜体+小字号）——语义仍是替换并打 sfx 标记。
 *  - 朗读 / 复制 / 重译 去掉文字，改纯图标按钮（🔊 / 📋 / 🔁，说明进 title + aria-label）。
 *
 * 2026-10-16（用户要求）：新增「批量替换」按钮——把（选中原文 → 译文）交给阅读器的「翻译替换」弹窗，
 *  由那边先预览、再应用（范围/章节勾选/art 行跳过/宽度校正/预览缓存全部复用，本脚本不自己扫库）。
 *  入口 = window.__aaTrBatch（阅读器汉化章里注册；board 页 / 未译章 / 原文页没有 → 按钮保持禁用）。
 *  照抄（echo）、无选区上下文、翻译中 → 禁用（与「替换」同口径）。纯半角假名 → 预勾 sfx（可取消）。
 */
(() => {
  const D = document;
  let timer = null, seq = 0, box = null, busy = false;
  let closeAt = 0;                 // 允许「点外部关闭」的最早时刻（译文显示 +1s）
  let selCtx = null;               // 替换上下文：textarea → {el,start,end}；正文 → {lineIdx,start,end}
  let ac = null;                   // 在途请求 AbortController（关弹窗/超时即取消）
  let tick = null;                 // 等待计时器
  let lastPos = { x: 40, y: 40 };  // 上次弹窗锚点（重译时复用，选区可能已消失）

  const ABORT_MS = 150000;         // 前端硬超时（后端最坏 ≈ 3 候选 × 30s，留余量）
  const SLOW_SEC = 8;              // 超过 N 秒在等待文案提示可取消

  // 页面元数据（由各模板注入 window.AA_SEL）
  const CFG = window.AA_SEL || {};

  // 划词开关（设置页 AA_SEL_ENABLE，默认开；2026-10-08 阶段 3）：关掉时**整段脚本不再往下走**
  // —— 一个监听都不挂、也不建弹窗（划词完全无反应）。后端同样会拒（api_translate_selection / api_tts）。
  if (CFG.enabled === false) return;

  // ---- 本地判断：是否含日文（假名，含半角） ----
  function hasKana(s) {
    return /[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]/.test(s);
  }
  // 取选区文本（避开弹窗自身）
  function selText() {
    const s = D.getSelection();
    if (!s || s.isCollapsed || s.rangeCount === 0) return '';
    const t = s.toString().replace(/\s+/g, ' ').trim();
    return t;
  }

  // ---- 选中项高亮 + 区间记录（2026-09-21）----
  // 需求：划词后要高亮「选中项」；且翻译过程中点了别处（原生选区消失）依然能替换。
  // 做法：弹窗弹出时把选中区间包进 <span class="sel-hl">（只改背景色，不动字号/内边距 → 行宽不变），
  //      同时把 [start,end) 绝对偏移 + 原文记进 selCtx —— 替换时按记录定位，不再依赖活的选区。
  function textPos(root, off) {          // 字符偏移 → {node, offset}
    let acc = 0;
    const w = D.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = w.nextNode())) {
      if (acc + n.data.length >= off) return { node: n, offset: off - acc };
      acc += n.data.length;
    }
    return null;
  }

  function hlRange(span, start, end) {   // 用 .sel-hl 包住 [start,end)（文本内容不变 → 替换语义不变）
    const a = textPos(span, start), b = textPos(span, end);
    if (!a || !b) return null;
    try {
      const r = D.createRange();
      r.setStart(a.node, a.offset);
      r.setEnd(b.node, b.offset);
      const w = D.createElement('span');
      w.className = 'sel-hl';
      w.appendChild(r.extractContents());
      r.insertNode(w);
      return w;
    } catch (e) { return null; }
  }

  let hlNode = null;                     // 当前高亮节点（关弹窗/新划词时还原 DOM）
  function clearHl() {
    const n = hlNode;
    hlNode = null;
    if (n && n.isConnected && n.parentNode) {
      const p = n.parentNode;
      while (n.firstChild) p.insertBefore(n.firstChild, n);
      p.removeChild(n);
      try { p.normalize(); } catch (e) {}
    }
  }

  // 当前原生选区相对 span.textContent 的绝对区间（选区不在该行内 → null）
  function selOffsets(span) {
    const g = D.getSelection();
    if (!g || g.isCollapsed || !g.rangeCount) return null;
    const r = g.getRangeAt(0);
    if (!(span.contains(r.commonAncestorContainer) || r.commonAncestorContainer === span)) return null;
    try {
      const pre = r.cloneRange();
      pre.selectNodeContents(span);
      pre.setEnd(r.startContainer, r.startOffset);
      const start = pre.toString().length;
      const whole = r.cloneRange();
      whole.selectNodeContents(span);
      whole.setEnd(r.endContainer, r.endOffset);
      const end = whole.toString().length;
      if (!(end > start)) return null;
      return { start: start, end: end, seg: span.textContent.slice(start, end) };
    } catch (e) { return null; }
  }
  // 选区是否在弹窗内（弹窗出现后点它不触发新弹）
  function inBox(n) {
    return !!(box && n && box.contains(n));
  }

  function toast(msg, ok) {
    try {
      const el = D.createElement('div');
      el.className = 'sel-toast' + (ok === false ? ' bad' : '');
      el.textContent = msg;
      D.body.appendChild(el);
      setTimeout(() => el.remove(), 2400);
    } catch (e) {}
  }

  function stopWait() {
    if (tick) { clearInterval(tick); tick = null; }
  }
  function abortInflight() {
    if (ac) { try { ac.abort(); } catch (e) {} ac = null; }
    stopWait();
  }

  // 弹窗 DOM（单例, 直接 append 到 body 顶层）
  function ensureBox() {
    if (box && box.isConnected) return box;
    box = D.createElement('div');
    box.className = 'sel-pop';
    box.id = 'sel-pop';
    box.innerHTML =
      '<div class="sel-pop-head">' +
        '<span class="sel-pop-title">日→中</span>' +
        '<span class="sel-pop-src"></span>' +
        '<button type="button" class="sel-pop-close" title="关闭（Esc）—— 翻译中关闭即取消请求">×</button>' +
      '</div>' +
      '<div class="sel-pop-body"><span class="sel-pop-state">翻译中…</span></div>' +
      '<div class="sel-pop-meta"></div>' +
      '<div class="sel-pop-actions">' +
        '<button type="button" class="sel-btn sel-replace" title="把选中片段替换为译文（漏译修正）" disabled>替换</button>' +
        '<button type="button" class="sel-btn sel-sfx sel-sfxface" title="替换（sfx 样式）：替换同时标记为拟声词/效果音（楷体+斜体+小字号）" disabled>替换</button>' +
        '<button type="button" class="sel-btn sel-batch" title="批量替换：把这一对（选中原文 → 译文）填入「翻译替换」弹窗，先预览全部章节的命中行，再点「应用」写入（艺术行跳过、宽度自动校正、逐行记修改记录）" disabled>批量替换</button>' +
        '<button type="button" class="sel-btn sel-icon sel-reredo" title="重译：换模型强制翻译（跳过原文照抄判定）" aria-label="重译" disabled>🔁</button>' +
        '<button type="button" class="sel-btn sel-icon sel-listen" title="朗读原文（edge-tts 日文语音）" aria-label="朗读">🔊</button>' +
        '<button type="button" class="sel-btn sel-icon sel-copy" title="复制译文" aria-label="复制">📋</button>' +
      '</div>';
    D.body.appendChild(box);
    box.querySelector('.sel-pop-close').addEventListener('click', hide);
    // Esc 关闭（且不挡其它 Esc 处理）
    D.addEventListener('keydown', e => {
      if (e.key === 'Escape' && box && box.isConnected) { hide(); }
    }, true);
    // 点弹窗内部不关闭
    box.addEventListener('mousedown', e => e.stopPropagation());
    return box;
  }

  function showAt(x, y) {
    const b = ensureBox();
    b.classList.add('open');
    // 位置：优先放选区下方；越界翻到上方；水平 clamp
    b.style.left = '0px'; b.style.top = '0px';
    const r = b.getBoundingClientRect();
    let left = Math.min(Math.max(6, x), Math.max(6, window.innerWidth - r.width - 6));
    let top = y + 14;
    if (top + r.height > window.innerHeight - 6) {
      top = Math.max(6, y - r.height - 10);
    }
    // 兜底夹进视口：锚点坐标离谱时（曾因回落 body 矩形 → y≈文档高度）弹窗会落在屏幕外「看不见」
    top = Math.min(Math.max(6, top), Math.max(6, window.innerHeight - r.height - 8));
    b.style.left = left + 'px';
    b.style.top = top + 'px';
  }

  function hide() {
    if (!box) return;
    abortInflight();             // 关弹窗 = 取消在途请求（原先只能干等）
    box.classList.remove('open');
    box.querySelector('.sel-pop-body').innerHTML = '';
    box.querySelector('.sel-pop-src').textContent = '';
    box.querySelector('.sel-pop-src').classList.remove('pick');
    clearHl();                   // 还原选中项高亮（DOM 折叠回原样）
    const meta = box.querySelector('.sel-pop-meta');
    if (meta) { meta.textContent = ''; meta.classList.remove('warn'); }
    const rb = box.querySelector('.sel-replace');
    if (rb) rb.disabled = true;
    const sb = box.querySelector('.sel-sfx');
    if (sb) sb.disabled = true;
    const bb = box.querySelector('.sel-batch');
    if (bb) bb.disabled = true;
    const rrb = box.querySelector('.sel-reredo');
    if (rrb) { rrb.disabled = true; rrb.classList.remove('hot'); }
    selCtx = null;
    closeAt = 0;
    seq++;                       // 使在途请求结果作废
  }

  function setState(html) {
    const b = box || ensureBox();
    b.querySelector('.sel-pop-body').innerHTML = html;
  }

  function trigger(text, ctx, opts) {
    if (busy) return;            // 上一请求在途：忽略新划词（旧弹窗优先）
    opts = opts || {};
    seq++;
    const mySeq = seq;
    const b = ensureBox();
    b.dataset.srcText = text;    // 记录原文（供朗读按钮）
    selCtx = ctx || null;        // 替换上下文（textarea 或正文行）
    const rb = b.querySelector('.sel-replace');
    if (rb) rb.disabled = true;  // 翻译中禁用替换
    const sb0 = b.querySelector('.sel-sfx');
    if (sb0) sb0.disabled = true;   // 翻译中禁用 sfx 替换
    const bb0 = b.querySelector('.sel-batch');
    if (bb0) bb0.disabled = true;   // 翻译中禁用批量替换
    const rrb0 = b.querySelector('.sel-reredo');
    if (rrb0) { rrb0.disabled = true; rrb0.classList.remove('hot'); }
    const meta0 = b.querySelector('.sel-pop-meta');
    if (meta0) { meta0.textContent = ''; meta0.classList.remove('warn'); }
    b.querySelector('.sel-pop-src').textContent = text.length > 40 ? text.slice(0, 40) + '…' : text;
    setState('<span class="sel-pop-state">翻译中…</span>');
    // 选中项高亮（2026-09-21）：正文行内划词 → 把选中区间包 .sel-hl（点别处也看得见、也还能替换）；
    // 编辑框(textarea)划词 → 头部片段标成「选中的那段」（textarea 无法内联高亮）。
    // ⚠ 必须在下面「量锚点」之后才打高亮：hlRange 会 extractContents 掉原生选区，
    //   先打高亮会让选区矩形取不到 → 回落 body 矩形（整页高）→ 弹窗被摆到页面底部看不见。
    clearHl();
    const chip = b.querySelector('.sel-pop-src');
    chip.title = text;
    const pickable = !!(selCtx && (selCtx.kind === 'body' || selCtx.kind === 'ta') && selCtx.start >= 0);
    chip.classList.toggle('pick', pickable);
    // 锚点 = 划词行（选区）正下方，不是鼠标位置：
    //   普通文本 → 选区 range 的 bounding rect 底部；textarea/input → 控件底部（无 DOM 选区）
    let px = lastPos.x, py = lastPos.y, hasAnchor = false;
    if (opts.pos) { px = opts.pos.x; py = opts.pos.y; hasAnchor = true; }   // 重译：复用上次锚点
    const ae = D.activeElement;
    const inTextBox = ae && (ae.tagName === 'TEXTAREA' || ae.tagName === 'INPUT');
    if (!hasAnchor && !inTextBox) {
      const s = D.getSelection();
      if (s && !s.isCollapsed && s.rangeCount) {
        try {
          const r = s.getRangeAt(0).getBoundingClientRect();
          if (r && (r.width > 0 || r.height > 0)) { px = r.left + r.width / 2; py = r.bottom; hasAnchor = true; }
        } catch (e) {}
      }
    }
    // body/html 的 rect = 整页盒子（bottom ≈ 文档底部），拿它当锚点会把弹窗甩到页面外 → 排除
    if (!hasAnchor && ae && ae.tagName !== 'BODY' && ae.tagName !== 'HTML') {
      try {
        const r = ae.getBoundingClientRect();
        if (r && (r.width > 0 || r.height > 0)) { px = r.left + r.width / 2; py = r.bottom; hasAnchor = true; }
      } catch (e) {}
    }
    if (pickable && selCtx.kind === 'body' && selCtx.span && selCtx.span.isConnected &&
        selCtx.end > selCtx.start) {
      hlNode = hlRange(selCtx.span, selCtx.start, selCtx.end);
    }
    lastPos = { x: px, y: py };
    showAt(px, py);

    const payload = { text: text };
    if (CFG.work) payload.work = CFG.work;
    if (CFG.base) payload.base = CFG.base;
    if (opts.force) { payload.force = true; payload.avoid = opts.avoid || ''; }

    busy = true;
    abortInflight();
    ac = new AbortController();
    const myAc = ac;
    const hardT = setTimeout(() => { try { myAc.abort(); } catch (e) {} }, ABORT_MS);
    // 等秒数（2026-09-20：原来只写「翻译中…」，慢的时候看不出在动）
    const t0 = Date.now();
    tick = setInterval(() => {
      if (mySeq !== seq) { stopWait(); return; }
      const el = b.querySelector('.sel-pop-body .sel-pop-state');
      if (!el || el.textContent.indexOf('翻译中') !== 0) { stopWait(); return; }
      const s = Math.round((Date.now() - t0) / 1000);
      el.textContent = s >= SLOW_SEC ? ('翻译中… ' + s + 's（较慢，可点 × 取消）') : ('翻译中… ' + s + 's');
    }, 500);
    fetch('/api/translate-selection', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: myAc.signal
    })
      .then(r => r.json().catch(() => ({ ok: false, detail: 'HTTP ' + r.status })))
      .then(j => {
        clearTimeout(hardT);
        stopWait();
        busy = false;                  // 必须先清 busy（early return 也要清）
        if (mySeq !== seq) return;     // 已被新划词/关闭作废（丢弃结果即可）
        if (j.ok) {
          const zh = j.text || '';
          const echo = !!j.echo;
          b.dataset.tr = zh;
          b.dataset.via = j.via || '';
          b.querySelector('.sel-pop-body').innerHTML = '<div class="sel-tr"></div>';
          b.querySelector('.sel-tr').textContent = zh;
          // 2026-09-03: 译文显示 1s 后才允许点外部关闭（防译文刚出被误关）
          closeAt = Date.now() + 1000;
          // 通道/耗时（用于反查超时；echo 时标黄）
          // 2026-10-10: 显示**完整**通道名（backend/model）——同模型名可挂在多个后端上
          // （opencode_go 与 deepseek 官网都是 deepseek-v4.1-flash），只显示模型分不出实际走了哪条
          const meta = b.querySelector('.sel-pop-meta');
          if (meta) {
            const bits = [];
            if (j.via) { bits.push(String(j.via)); meta.title = '本次通道: ' + j.via; }
            if (j.elapsed != null) bits.push(j.elapsed + 's');
            if (j.auto_retry) bits.push('已自动换通道重试');
            meta.textContent = bits.join(' · ');
            meta.classList.toggle('warn', echo);
          }
          if (echo) {
            // 模型照抄原文：提示 + 禁用替换（防把日文替换进正文）+ 高亮重译
            const w = D.createElement('div');
            w.className = 'sel-pop-warn';
            w.textContent = '⚠ 这次结果与原文相同（模型照抄）。点「重译」换模型强制翻译。';
            b.querySelector('.sel-pop-body').appendChild(w);
          }
          // 替换按钮：有选区上下文才启用（翻译完成、可执行替换；照抄时禁用）
          const rbn = b.querySelector('.sel-replace');
          if (rbn) rbn.disabled = !selCtx || echo;
          const sbn = b.querySelector('.sel-sfx');
          if (sbn) sbn.disabled = !selCtx || echo;
          // 批量替换（2026-10-16）：走阅读器的「翻译替换」弹窗（先预览、后应用）——需要本页注册了
          // window.__aaTrBatch（只有汉化章的阅读器有：board 页 / 未译章 / 原文页保持禁用，并在 title 里说清原因）
          const bbn = b.querySelector('.sel-batch');
          if (bbn) {
            const noHook = typeof window.__aaTrBatch !== 'function';
            bbn.disabled = echo || !selCtx || noHook;
            bbn.title = noHook
              ? '批量替换：把这一对交给「翻译替换」弹窗先预览再应用——本页没有该弹窗（仅已汉化章节的阅读页可用）'
              : '批量替换：把这一对（选中原文 → 译文）填入「翻译替换」弹窗，先预览全部章节的命中行，再点「应用」写入（艺术行跳过、宽度自动校正、逐行记修改记录）';
          }
          const rrbn = b.querySelector('.sel-reredo');
          if (rrbn) { rrbn.disabled = false; rrbn.classList.toggle('hot', echo); }
        } else {
          setState('<span class="sel-pop-state err">' + (j.detail || '翻译失败') + '</span>');
          closeAt = Date.now();   // 失败态：立即允许点外部关闭
          const rrbn = b.querySelector('.sel-reredo');
          if (rrbn) { rrbn.disabled = false; rrbn.classList.add('hot'); }
        }
      })
      .catch(err => {
        clearTimeout(hardT);
        stopWait();
        busy = false;
        if (mySeq !== seq) return;
        const aborted = err && (err.name === 'AbortError' || /abort/i.test(String(err)));
        setState('<span class="sel-pop-state err">' +
          (aborted ? '已取消或超时（可点「重译」再试）' : '网络错误：' + err) + '</span>');
        closeAt = Date.now();     // 失败态：立即允许点外部关闭
        const rrbn = b.querySelector('.sel-reredo');
        if (rrbn) { rrbn.disabled = false; rrbn.classList.add('hot'); }
      });
  }

  // ---- 纯半角假名判定（拟声词/效果音：去空白/省略号后全为 U+FF61-FF9F，≥2 字） ----
  function isHankakuSfx(s) {
    const t = (s || '').replace(/[\s\u3000…‥、。！？!?,.]/g, '');
    return t.length >= 2 && /^[\uff61-\uff9f]+$/.test(t);
  }

  // ---- sfx 标记载荷：mode='sfx'（「sfx 替换」按钮）恒加标记；'plain' 仅当被替换片段是纯半角假名时加。
  //   两种模式都清掉被替换区间上原有的标记（同一区间只留一条，防旧标记漂到别处）。
  function sfxPayload(mode, off, segLen, seg, zh) {
    const sfx = [], clear = [];
    if (segLen > 0) clear.push([off, segLen]);
    if (zh && (mode === 'sfx' || isHankakuSfx(seg))) sfx.push([off, zh]);
    return { sfx: sfx, sfx_clear: clear };
  }

  // ---- 选中区间的当前文本（偏移漂移时按唯一命中兜底）----
  // 2026-09-21：替换不再读「活的选区」——改用划词当时记下的 [start,end)（翻译中点了别处也不丢）。
  function pickNow(ctx) {
    if (!ctx) return null;
    const full = (ctx.kind === 'ta' && ctx.el) ? ctx.el.value : (ctx.span ? ctx.span.textContent : null);
    if (full == null) return null;
    if (ctx.kind === 'body' && !ctx.span.isConnected) return null;
    let st = ctx.start, en = ctx.end;
    if (!(st >= 0) || !(en > st) || full.slice(st, en) !== ctx.seg) {
      const seg = (ctx.seg || '').trim();        // 漂移兜底：整行/整框里恰好命中一次才用
      if (!seg) return null;
      const i = full.indexOf(seg);
      if (i < 0 || full.indexOf(seg, i + 1) >= 0) return null;
      st = i; en = i + seg.length;
    }
    return { full: full, start: st, end: en, seg: full.slice(st, en) };
  }

  // ---- 替换（漏译修正）：把选中日文片段换成译文并保存 ---- mode='sfx' = 同时打 sfx 样式标记
  async function doReplace(mode) {
    if (!box || !selCtx || busy) return;
    const zh = (box.dataset.tr || '').trim();
    if (!zh) { toast('无译文可替换', false); return; }
    const btn = box.querySelector('.sel-replace');
    const sbtn = box.querySelector('.sel-sfx');
    const reEnable = () => { if (btn) btn.disabled = false; if (sbtn) sbtn.disabled = false; };
    if (btn) btn.disabled = true;
    if (sbtn) sbtn.disabled = true;

    try {
      const pk = pickNow(selCtx);
      if (!pk) { toast('选中片段已变化或不可定位，请重新划词', false); reEnable(); return; }
      // 场景 A：行编辑弹窗 textarea 内划词 → 直接改 textarea 值 + 触发 reader 保存（漏译）
      if (selCtx.kind === 'ta' && selCtx.el) {
        const el = selCtx.el;
        const v = pk.full;
        const st = pk.start, en = pk.end;
        const seg = pk.seg;
        const p = sfxPayload(mode, st, en - st, seg, zh);
        const nv = v.slice(0, st) + zh + v.slice(en);
        el.value = nv;
        el.selectionStart = el.selectionEnd = st + zh.length;
        // 联动行编辑面板：类型切「漏译」 + 待写 sfx 标记（reader.js saveEdit 读取后清空）→ 触发保存
        try {
          const panel = D.getElementById('edit-panel');
          if (panel && (p.sfx.length || p.sfx_clear.length)) panel.dataset.pendingSfx = JSON.stringify(p);
          const radio = panel && panel.querySelector('input[name="em-type"][value="漏译"]');
          if (radio) radio.checked = true;
          const sv = panel && panel.querySelector('#em-save');
          if (sv && typeof sv.click === 'function') {
            sv.click();
            toast(`已替换并保存 ✓（漏译${p.sfx.length ? '；sfx 样式' : ''}）`);
          } else toast('已替换到编辑框，请点保存', true);
        } catch (e) { toast('已替换到编辑框，请点保存', true); }
        hide();
        return;
      }
      // 场景 B：正文行内划词 → 拼整行 → PUT line（漏译）→ 行重渲染
      if (selCtx.kind === 'body' && selCtx.span && selCtx.span.isConnected) {
        const mode2 = D.getElementById('chapter-pre')?.dataset.mode;
        if (mode2 !== 'tr') { toast('请切回汉化模式再替换', false); reEnable(); return; }
        const span = selCtx.span;
        // art 行（纯图形）不替换——save_line 会按宽度硬约束拒绝，提前提示
        const rowNo = span.dataset.idx != null ? +span.dataset.idx + 1 : null;
        const rowBtn = rowNo != null ? D.querySelector(`.lc-no[data-line="${rowNo}"]`) : null;
        if (rowBtn && rowBtn.classList.contains('lt-art')) { toast('该行是 AA 图形行，不支持替换', false); reEnable(); return; }
        const full = pk.full;
        // 位置取划词当时记下的区间（不再读活的选区）→ 翻译中点了别处也能替换
        const st0 = pk.start, pos = pk.end, seg = pk.seg;
        const nfull = full.slice(0, st0) + zh + full.slice(pos);
        const p = sfxPayload(mode, st0, pos - st0, seg, zh);
        // PUT 保存（复用行保存接口，type=漏译；sfx/sfx_clear 为样式标记，见后端 api_save_line）
        const rcfg = window.READER_CFG || {};
        const wid = rcfg.work || CFG.work, fname = rcfg.fname || '', idx = span.dataset.idx;
        if (!wid || !fname || idx == null) { toast('无法定位章节/行（board 页不支持替换）', false); reEnable(); return; }
        const base = `/api/work/${encodeURIComponent(wid)}/chapter/${encodeURIComponent(fname)}/line/${idx}`;
        const r = await fetch(base, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: nfull, type: '漏译', sfx: p.sfx, sfx_clear: p.sfx_clear })
        });
        const j = await r.json();
        if (!r.ok) { toast('保存失败：' + (j.detail || JSON.stringify(j)), false); reEnable(); return; }
        if (j.result === 'unchanged' && !j.sfx_changed) { toast('内容未变化'); hide(); return; }
        // 行重渲染
        const frag = await (await fetch(base + '/html')).json();
        span.innerHTML = frag.html || ' ';
        const no = D.querySelector(`.lc-no[data-line="${+idx + 1}"]`);
        if (no) { no.classList.add('flash'); setTimeout(() => no.classList.remove('flash'), 1500); }
        // 窗口化（2026-09-21）：通知阅读器失效该行所在块的缓存（区内会就地重建，区外下次滚到取新数据）
        if (window.__aaLineChanged) { try { window.__aaLineChanged(+idx); } catch (e) {} }
        toast(`已替换并保存 ✓（漏译${j.sfx_added ? '；sfx 样式' : ''}${j.padded ? '；已自动补齐宽度' : ''}）`);
        hide();
        return;
      }
      toast('替换上下文不可用（仅行编辑框或正文汉化行支持）', false);
      reEnable();
    } catch (e) {
      toast('替换失败：' + e, false);
      reEnable();
    }
  }

  // ---- 批量替换（2026-10-16 用户要求）：把这一对交给阅读器的「翻译替换」弹窗 ----
  // 语义 = 选中原文 → 译文 作为一条替换对，扫本作品汉化章**先预览**、再点「应用」写入。
  // 不在划词弹窗里另造一套范围/章节勾选/预览流程（那套在 tr-replace-modal 里已完整，含 art 行跳过、
  // 宽度校正、预览缓存、逐行记修改记录）。
  function doBatchReplace() {
    if (!box) return;
    const zh = (box.dataset.tr || '').trim();
    if (!zh) { toast('尚无译文', false); return; }
    if (typeof window.__aaTrBatch !== 'function') {
      toast('当前页面不支持批量替换（需在已汉化章节的阅读页）', false); return;
    }
    const pk = pickNow(selCtx);
    const find = (pk && pk.seg) ? pk.seg : (box.dataset.srcText || '');
    if (!find || !find.trim()) { toast('选中片段已变化或不可定位，请重新划词', false); return; }
    // 纯半角假名（拟声/效果音）→ 与「替换」按钮同口径，先把弹窗里那一对的 sfx 勾上（可再取消）
    const sfx = isHankakuSfx(find);
    hide();
    window.__aaTrBatch(find, zh, sfx);
  }

  // 替换 / sfx 替换 / 批量替换 / 重译 按钮点击（事件委托，与朗读/复制并列）
  D.addEventListener('click', e => {
    if (!box) return;
    if (e.target.closest('.sel-sfx')) doReplace('sfx');
    else if (e.target.closest('.sel-replace')) doReplace('plain');
    else if (e.target.closest('.sel-batch')) doBatchReplace();
    else if (e.target.closest('.sel-reredo')) {
      // 强制重译（2026-09-20）：跳过照抄判定 + avoid=上次通道（换个模型再试）
      const src = box.dataset.srcText || '';
      if (!src) { toast('无原文', false); return; }
      trigger(src, selCtx, { force: true, avoid: box.dataset.via || '', pos: lastPos });
    }
  });

  // ---- 朗读 ----
  function listen(text) {
    const btn = box && box.querySelector('.sel-listen');
    if (btn) { btn.disabled = true; btn.textContent = '…'; btn.title = '合成中…'; }
    fetch('/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text })
    })
      .then(r => {
        if (!r.ok) return r.json().catch(() => ({ detail: 'HTTP ' + r.status })).then(j => { throw new Error(j.detail || 'TTS 失败'); });
        return r.blob();
      })
      .then(blob => {
        const url = URL.createObjectURL(blob);
        const au = new Audio();
        au.src = url;
        au.onended = () => { URL.revokeObjectURL(url); };
        au.play().catch(e => toast('播放失败：' + e, false));
      })
      .catch(err => toast('朗读失败：' + err.message, false))
      .finally(() => {
        if (btn) { btn.disabled = false; btn.textContent = '🔊'; btn.title = '朗读原文（edge-tts 日文语音）'; }
      });
  }

  // 弹窗出现后点外部：翻译完成（译文显示）1s 后才关闭；翻译中/未满 1s 不关
  // 用冒泡 click（document capture 对 target=body 派发的事件不触发——捕获方向是 document→target）
  D.addEventListener('click', e => {
    if (!box || !box.classList.contains('open')) return;
    if (box.contains(e.target)) return;              // 点弹窗内部不关（box 已 stopPropagation，兜底）
    if (!closeAt || Date.now() < closeAt) return;    // closeAt=0(翻译中)永不关；设了时间戳则到点才关
    hide();
  });

  // ---- 事件委托：mouseup 静止 1.2s 判定 ----
  function onMouseUp(e) {
    // 忽略：弹窗内部 / 修饰键拖选复制
    if (inBox(e.target)) return;
    setTimeout(() => {
      // textarea/input（行编辑译文框等）：window.getSelection 拿不到 → 取控件选区
      const tag = (e.target && e.target.tagName || '').toLowerCase();
      let t = '';
      let ctx = null;
      if (tag === 'textarea' || tag === 'input') {
        const el = e.target;
        const st = el.selectionStart || 0, en = el.selectionEnd || 0;
        if (st !== en) {
          t = el.value.substring(st, en).replace(/\s+/g, ' ').trim();
          // 替换上下文：控件 + 划词当时的绝对区间（翻译中点了别处/控件失焦也能定位）
          ctx = { kind: 'ta', el: el, start: st, end: en, seg: el.value.substring(st, en) };
        }
      } else {
        t = selText();
        // 正文场景：选区锚点向上找 .aa-line（阅读器行）
        const node0 = (() => {
          const g = D.getSelection();
          if (!g || g.isCollapsed || !g.rangeCount) return null;
          let n = g.getRangeAt(0).commonAncestorContainer;
          while (n && n.nodeType === 3) n = n.parentNode;   // 文本节点→元素
          return n;
        })();
        const lineSpan = node0 && node0.closest ? node0.closest('.aa-line') : null;
        if (lineSpan) {
          // 记绝对区间（划词当时取，最准）→ 高亮 + 替换定位都用它
          const off = selOffsets(lineSpan);
          ctx = { kind: 'body', span: lineSpan,
                  start: off ? off.start : -1, end: off ? off.end : -1, seg: off ? off.seg : '' };
        }
      }
      if (!t) { clearTimeout(timer); timer = null; return; }
      if (t.length > 500) { clearTimeout(timer); timer = null; return; }
      if (!hasKana(t)) { clearTimeout(timer); timer = null; return; }
      // 静止 1.2s 才弹；textbox 需判断选区仍在
      clearTimeout(timer);
      const myT = t;
      timer = setTimeout(() => {
        let still = false;
        if (tag === 'textarea' || tag === 'input') {
          const el = e.target;
          const s = el.value.substring(el.selectionStart || 0, el.selectionEnd || 0).replace(/\s+/g, ' ').trim();
          still = s === myT;
        } else {
          const g = D.getSelection();
          still = !!(g && g.toString().replace(/\s+/g, ' ').trim() === myT);
        }
        if (still) trigger(myT, ctx);
        timer = null;
      }, 1200);
    }, 0);
  }
  function resetTimer() {
    // 改选/点击/滚动 → 重置（新 mouseup 会重建 timer；这里只清旧 timer）
    if (timer) { clearTimeout(timer); timer = null; }
  }
  D.addEventListener('mouseup', onMouseUp, true);
  D.addEventListener('mousedown', resetTimer, true);
  D.addEventListener('scroll', resetTimer, true);

  // 复制译文
  D.addEventListener('click', e => {
    const cp = e.target.closest('.sel-copy');
    if (!cp || !box) return;
    const tr = box.dataset.tr || '';
    if (!tr) { toast('尚无译文', false); return; }
    const done = () => toast('译文已复制 ✓');
    const fail = () => { try { const ta = D.createElement('textarea'); ta.value = tr; D.body.appendChild(ta); ta.select(); D.execCommand('copy'); ta.remove(); done(); } catch (e2) { toast('复制失败', false); } };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(tr).then(done).catch(fail);
    } else fail();
  });
  // 朗读（存的是原文：弹窗数据里同时记原文）
  D.addEventListener('click', e => {
    const lb = e.target.closest('.sel-listen');
    if (!lb || !box) return;
    const srcText = box.dataset.srcText || '';
    if (!srcText) { toast('无朗读文本', false); return; }
    listen(srcText);
  });

  // 供行编辑等异步场景手动触发
  window.__selTranslate = (text) => { trigger(text); };
})();
