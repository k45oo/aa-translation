/* 阅读器：行号点击 → 该行原地展开修改（行内编辑，浮层不破坏逐行对齐）→ 保存即渲染 + 记录 */
(() => {
  const CFG = window.READER_CFG;
  const base = `/api/work/${encodeURIComponent(CFG.work)}/chapter/${encodeURIComponent(CFG.fname)}`;

  // 目录滚动定位当前章：整页跳转后 nav 滚动位置回到顶部，话数多时看不见 active 高亮；
  // 这里把当前章滚到 nav 容器内可视区（offsetTop 相对 sticky 定位的 nav，只滚容器不动页面）
  const nav = document.querySelector('.reader-nav');
  const activeLink = nav && nav.querySelector('a.active');
  if (nav && activeLink) {
    nav.scrollTop = Math.max(0, activeLink.offsetTop - (nav.clientHeight - activeLink.offsetHeight) / 2);
  }

  function dw(s) {
    let w = 0;
    for (const ch of s) {
      const c = ch.codePointAt(0);
      w += (c < 0x100 || (c >= 0xFF61 && c <= 0xFF9F)) ? 1 : 2;
    }
    return w;
  }

  // 行号闪烁（保存/跳转/续读提示）：窗口化后行号按钮会被重建，改由 WIN 按 k 记状态（渲染时带 class）
  function flashNo(lineNo, ms) {
    if (window.__aaWin) { window.__aaWin.flashLine(lineNo, ms); return; }
    const no = document.querySelector(`.lc-no[data-line="${lineNo}"]`);
    if (no) { no.classList.add('flash'); setTimeout(() => no.classList.remove('flash'), ms || 1500); }
  }

  // ---- 行内插入式编辑：编辑行插入正文流（与行号列逐行对齐），控件在悬浮编辑面板（2026-09-03 改 fixed 悬浮，不占位不遮正文） ----
  const mask = document.getElementById('edit-mask');
  let curIdx = -1, curSrcW = null;
  // 编辑框草稿：窗口重建抹掉 .edit-row 时的兜底（历史 bug：editK 口径错 → paint 误重建 → 「保存没反应」丢改动）
  let editDraft = '', editDraftIdx = -1;
  let __restoreSave = null;   // 2026-09-03: Shift+R 还原原文并保存的回调（openEdit 内绑定，closeEdit 清除）

  function isEditing() { return curIdx >= 0; }
  // sfx 标记载荷（划词翻译「替换 / sfx 替换」暂存在 #edit-panel.dataset.pendingSfx，保存时读取后清空）
  function clearPendingSfx() {
    const p = document.getElementById('edit-panel');
    if (p) delete p.dataset.pendingSfx;
  }
  // 编辑状态暴露为全局标志（供 reader.html 方向键翻章监听器判断：编辑中禁用翻章）
  window.__aaEditing = false;
  function setEditing(on) { window.__aaEditing = !!on; }

  function closeEdit() {
    closeOvMenu();
    setEditing(false);
    document.querySelectorAll('.src-row, .edit-row, .lc-ph').forEach(el => el.remove());
    // 恢复被隐藏的原译文行及其行号按钮
    if (curIdx >= 0) {
      const sp = document.querySelector(`#chapter-pre span.aa-line[data-idx="${curIdx}"]`);
      if (sp) sp.classList.remove('editing-hidden', 'hl');
      const nb = document.querySelector(`.lc-no[data-line="${curIdx + 1}"]`);
      if (nb) nb.classList.remove('editing-hidden');
    }
    const panel = document.getElementById('edit-panel');
    if (panel) panel.remove();
    if (mask) mask.classList.add('hidden');
    __restoreSave = null;
    editDraft = ''; editDraftIdx = -1;
    if (window.__aaWin) window.__aaWin.setEditDelta(-1, 0);   // 撤销「插入行」的几何位移
    curIdx = -1; curSrcW = null;
  }

  async function openEdit(lineNo) {
    const idx = lineNo - 1;
    if (isEditing()) closeEdit();
    // 窗口化几何用「k（可渲染行序号）」，不是 txt 行号：两者差 = 该行之前的跳过行数（# / =====），
    // 传 idx 会让 editK 落在窗口外（章内末几行必然 > k1=N-1）→ WIN.paint 的编辑冻结判据失效 →
    // 闪烁超时那次 paint() 把刚插入的编辑行/原文行整块重建掉（用户实测：点最后几行，1.5s 后编辑行消失、点保存没反应）
    const winK = window.__aaWin ? window.__aaWin.kOfLine(lineNo) : -1;
    // 窗口化：该行不在当前窗口时先挪窗口（否则 span/按钮不存在，编辑行无处插入）
    if (window.__aaWin) {
      try { await window.__aaWin.ensureVisible(lineNo); }
      catch (e) { toast('行定位失败：' + e, false); return; }
      // 先钉住编辑行（px 暂记 0）：SYNC 的冻结判据是「editK 在窗口内」，
      // 不先钉住的话期间任何滚动都会走 closeEdit()，把刚插入的编辑行/原文行整块删掉（2026-09-21 修）
      if (window.__aaWin.setEditDelta) window.__aaWin.setEditDelta(winK, 0);
    }
    setEditing(true);
    // 防御：清掉任何残留插入行（异常路径可能绕过 closeEdit）
    document.querySelectorAll('.src-row, .edit-row, .lc-ph').forEach(el => el.remove());
    fetch(`${base}/line/${idx}`).then(r => r.json()).then(j => {
      curIdx = idx;
      const skip = /^(#|=====)/.test(j.src);
      curSrcW = skip ? null : dw(j.src);
      const span = document.querySelector(`#chapter-pre span.aa-line[data-idx="${idx}"]`);
      const no = document.querySelector(`.lc-no[data-line="${lineNo}"]`);

      // ① 编辑时隐藏原译文行；编辑行插到原行位置（上面）；原文行在编辑行下（可切换显隐）
      const srcRow = document.createElement('div');
      srcRow.className = 'src-row';
      srcRow.textContent = j.src || '';
      const er = document.createElement('div');
      er.className = 'edit-row';
      er.id = 'edit-row';
      er.innerHTML = `<textarea id="er-tr" class="er-tr mono" wrap="off" rows="1" spellcheck="false"></textarea>`;
      if (span) {
        span.classList.add('editing-hidden');        // 原译文行隐藏
        span.insertAdjacentElement('afterend', er);  // 编辑行在原行位置
        er.insertAdjacentElement('afterend', srcRow);
      }
      er.querySelector('#er-tr').value = j.tr;
      editDraft = j.tr; editDraftIdx = idx;

      // ② 行号列：隐藏原行号按钮 + 插入两个等高占位（编辑行 + 原文行）
      const ph = document.createElement('div');
      ph.className = 'lc-ph';
      const ph2 = ph.cloneNode();
      if (no) {
        no.classList.add('editing-hidden');
        no.insertAdjacentElement('afterend', ph);
        ph.insertAdjacentElement('afterend', ph2);
      }

      // ③ 「日」小按钮：切换原文行显示/隐藏（在编辑行左侧行号列位置，隐藏原文行时占位同步）
      const toggleBtn = document.createElement('button');
      toggleBtn.type = 'button';
      toggleBtn.className = 'ie-src-toggle on';
      toggleBtn.title = '显示/隐藏日文原文行';
      toggleBtn.textContent = '日';
      er.appendChild(toggleBtn);
      toggleBtn.addEventListener('click', () => {
        const hidden = srcRow.classList.toggle('hidden');
        ph2.classList.toggle('hidden', hidden);
        toggleBtn.classList.toggle('on', !hidden);
        // 窗口化几何：原文行隐藏时不再占位 → 位移改为 0（k 口径，勿传 txt 行号）
        if (window.__aaWin) window.__aaWin.setEditDelta(winK, hidden ? 0 : lineHeight());
      });

      // ④ 编辑控制面板：悬浮 fixed 覆盖层，生成在目标行左侧（不占位、不把正文右推、不遮目录）
      //    （2026-09-03：由原来的左侧 240px 占位面板改为 body 级 fixed 悬浮，正文流插入编辑行/原文行保持不变）
      const panel = document.createElement('div');
      panel.className = 'edit-panel';
      panel.id = 'edit-panel';
      panel.innerHTML = `
        <div class="ep-head">
          <span class="ep-title">修改第 ${lineNo} 行</span>
          <button type="button" class="ep-close" title="关闭（Esc）">×</button>
        </div>
        <div class="em-type">
          <label><input type="radio" name="em-type" value="" checked title="自动：保存时按改前/改后判定（纯空白→错位 / 中文换中文→校对 / 日文减少→漏译 / 其余→错译）"> 自动</label>
          <label><input type="radio" name="em-type" value="错译"> 错译</label>
          <label><input type="radio" name="em-type" value="漏译"> 漏译</label>
          <label><input type="radio" name="em-type" value="错位"> 错位</label>
          <label><input type="radio" name="em-type" value="校对"> 校对</label>
        </div>
        <div id="em-width"></div>
        <div class="actions">
          <button id="em-restore" class="btn ghost" title="将译文改回日文原文（仍需点保存才写入）">还原原文</button>
          <button id="em-sfxclear" class="btn ghost" title="清除本行 sfx 样式标记（拟声词/效果音）" style="display:none">清除 sfx</button>
          <button id="em-cancel" class="btn ghost">取消</button>
          <button id="em-save" class="btn">保存修改</button>
        </div>`;
      document.body.appendChild(panel);
      // 悬浮定位：垂直锚定到所点击行（edit-row 在正文流中可见、rect 有效——span/no 已被 editing-hidden 隐藏 rect=0）；
      // 水平停靠左缘（.edit-panel CSS 控制 left/width：宽度稍宽于目录、右缘到行号列左缘之前，不遮挡行号）
      {
        const row = er.getBoundingClientRect();
        const ph = panel.offsetHeight;
        let top = row ? row.top : 120;
        top = Math.max(52, Math.min(top, window.innerHeight - ph - 10));
        panel.style.top = top + 'px';
      }

      // ⑤ 遮罩（其他行变暗）+ 原行高亮 + 滚动到编辑行可见
      mask.classList.remove('hidden');
      if (span) span.classList.add('hl');
      er.scrollIntoView({ block: 'nearest' });
      flashNo(curIdx + 1, 1500);
      // 窗口化几何：编辑行 + 原文行插入正文流（原行被隐藏 → 净增 1 行高），
      // 供滚动/进度映射使用；此后窗口在编辑期间冻结（见 WIN.sync）。
      // 必须是 k 口径（winK）：传 txt 行号（curIdx）时末几行 editK > k1 → 冻结失效 → 编辑行被 paint 抹掉。
      // 跳过行（# / =====，无 span、不插行）不占位 → 位移清 0，否则整章几何凭空多 1 行高。
      if (window.__aaWin) window.__aaWin.setEditDelta(span ? winK : -1, span ? lineHeight() : 0);

      // ⑥ 事件：输入实时宽度、Ctrl+Enter 保存、Esc/×/遮罩 取消
      const tr = er.querySelector('#er-tr');
      const emWidth = panel.querySelector('#em-width');
      function updateWidth() {
        const w = dw(tr.value);
        if (curSrcW === null) { emWidth.textContent = `宽度 ${w}（跳过行，不限宽）`; emWidth.className = ''; return; }
        const ok = w === curSrcW;
        emWidth.textContent = `宽度 TR ${w} / SRC ${curSrcW} ${ok ? '✓' : (w > curSrcW ? '超宽（文本行允许）' : '✗ 保存时自动补齐')}`;
        emWidth.className = ok ? '' : 'badw';
      }
      tr.addEventListener('input', updateWidth);
      // 手动改动译文 → 丢弃划词翻译暂存的 sfx 标记载荷（偏移随编辑失效，防误标）
      tr.addEventListener('input', clearPendingSfx);
      tr.addEventListener('input', () => { editDraft = tr.value; editDraftIdx = idx; });
      tr.addEventListener('keydown', e => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); saveEdit(); }
        if (e.key === 'Escape') { e.preventDefault(); closeEdit(); }
      });
      panel.querySelector('#em-save').addEventListener('click', saveEdit);
      panel.querySelector('#em-cancel').addEventListener('click', closeEdit);
      panel.querySelector('.ep-close').addEventListener('click', closeEdit);
      // 还原原文：把编辑框内容改回日文原文（不直接保存，仍需点「保存修改」）
      // 2026-08-26: 还原原文 = 修正误译 → 类型自动切为「错译」
      function restoreSrc() {
        if (!(j.src || '').trim()) { toast('本章无日文原文行，无法还原', false); return false; }
        tr.value = j.src;
        clearPendingSfx();     // 填原文 → 划词替换暂存的 sfx 标记作废（偏移已变）
        updateWidth();
        const rErr = panel.querySelector('input[name="em-type"][value="错译"]');
        if (rErr) rErr.checked = true;
        return true;
      }
      panel.querySelector('#em-restore').addEventListener('click', () => {
        if (!restoreSrc()) return;
        tr.focus();
        tr.setSelectionRange(tr.value.length, tr.value.length);
        toast('已填入日文原文（类型→错译）—— 确认后点击「保存修改」写入');
      });
      // 清除 sfx 标记（2026-09-15）：该行有 sfx 标记时才显示 → 走同一保存请求（sfx_clear_all）
      const sfxClearBtn = panel.querySelector('#em-sfxclear');
      if (sfxClearBtn && (j.sfx || []).length) {
        sfxClearBtn.style.display = '';
        sfxClearBtn.addEventListener('click', () => {
          panel.dataset.pendingSfx = JSON.stringify({ sfx: [], sfx_clear: [], sfx_clear_all: true });
          saveEdit();
        });
      }
      // Shift+R = 还原原文并保存（2026-09-03）：填原文 + 切错译 + 直接 saveEdit
      __restoreSave = () => {
        if (!restoreSrc()) return;
        saveEdit();
      };
      updateWidth();
      tr.focus();
      tr.setSelectionRange(tr.value.length, tr.value.length);
    }).catch(e => toast('行数据加载失败：' + e, false));
  }

  async function saveEdit() {
    if (curIdx < 0) return;
    const panel = document.getElementById('edit-panel');
    if (!panel) return;
    const er = document.getElementById('edit-row');
    const tr = er ? er.querySelector('#er-tr') : null;
    // 编辑行可能已被窗口重建抹掉（见 openEdit 的 winK 注释）→ 用草稿兜底，别静默丢改动
    if (!tr && editDraftIdx !== curIdx) { toast('编辑行已失效，请重新点击行号再改', false); return; }
    const type = panel.querySelector('input[name="em-type"]:checked').value;
    const btn = panel.querySelector('#em-save');
    // 划词翻译「替换 / sfx 替换」暂存的 sfx 标记载荷（读过即清：防后续手动保存复用旧偏移）
    const payload = { text: tr ? tr.value : editDraft, type: type };
    const sfxRaw = panel.dataset.pendingSfx || '';
    clearPendingSfx();
    if (sfxRaw) {
      try {
        const p = JSON.parse(sfxRaw) || {};
        payload.sfx = p.sfx || [];
        payload.sfx_clear = p.sfx_clear || [];
        payload.sfx_clear_all = !!p.sfx_clear_all;
      } catch (e) {}
    }
    btn.disabled = true;
    const saveIdx = curIdx;   // ⚠ closeEdit() 会把 curIdx 置 -1：所有保存后的刷新/闪烁必须用这里的快照
    try {
      const r = await fetch(`${base}/line/${saveIdx}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const j = await r.json();
      if (!r.ok) { toast('保存失败：' + (j.detail || JSON.stringify(j)), false); return; }
      if (j.result === 'unchanged' && !j.sfx_changed) { toast('内容未变化'); closeEdit(); return; }
      // 更新正文渲染 + 行号闪烁（窗口化：先收面板再失效块缓存就地重建）
      closeEdit();
      if (window.__aaWin) {
        await window.__aaWin.markChanged(saveIdx);
      } else {
        const frag = await (await fetch(`${base}/line/${saveIdx}/html`)).json();
        const span = document.querySelector(`#chapter-pre span.aa-line[data-idx="${saveIdx}"]`);
        if (span) { span.innerHTML = frag.html || ' '; span.classList.remove('hl'); }
      }
      flashNo(saveIdx + 1, 1500);
      const sfxTip = j.sfx_added ? '；已标 sfx 样式' : (j.sfx_cleared ? '；已清 sfx 标记' : '');
      // 2026-10-01: 类型可能是后端自动判定的（radio 停在「自动」→ 提交空串）→ 回显实际落盘类型
      const etype = (j.type || type || '—') + (j.type_auto ? '（自动）' : '');
      if (j.residual && j.residual.length) toast(`已保存 ✓（类型：${etype}${sfxTip}；⚠ 疑似残留: ${j.residual.join('、')}）`);
      else toast(`已保存 ✓（类型：${etype}${sfxTip}${j.padded ? '；已自动补齐宽度' : ''}）`);
    } catch (e) {
      toast('网络错误：' + e, false);
    } finally {
      btn.disabled = false;
    }
  }

  // 右键行号 = 直接还原原文并保存（无需打开编辑面板；与 Shift+R 同口径，2026-09-03）
  async function restoreAndSaveLine(lineNo) {
    const idx = lineNo - 1;
    const pm = document.getElementById('chapter-pre')?.dataset.mode;
    if (!CFG.hasTr) { toast('本章未汉化，仅原文可读，不可编辑', false); return; }
    if (pm === 'src') { toast('原文模式不可编辑，请切回汉化', false); return; }
    if (window.__aaWin) {                       // 窗口化：先保证该行在窗口内（右上角菜单/右键都走这里）
      try { await window.__aaWin.ensureVisible(lineNo); }
      catch (e) { toast('行定位失败：' + e, false); return; }
    }
    let j;
    try {
      j = await (await fetch(`${base}/line/${idx}`)).json();
    } catch (e) {
      toast('行数据加载失败：' + e, false);
      return;
    }
    if (!(j.src || '').trim()) { toast('本章无日文原文行，无法还原', false); return; }
    if (j.src === j.tr) { toast('已经是原文，无需还原'); return; }
    // 直接在行内改（art 行会被后端按宽度硬约束拒绝），type 固定「错译」
    try {
      const r = await fetch(`${base}/line/${idx}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: j.src, type: '错译' })
      });
      const jr = await r.json();
      if (!r.ok) { toast('保存失败：' + (jr.detail || JSON.stringify(jr)), false); return; }
      if (jr.result === 'unchanged') { toast('内容未变化'); return; }
      if (window.__aaWin) {
        await window.__aaWin.markChanged(idx);
      } else {
        const frag = await (await fetch(`${base}/line/${idx}/html`)).json();
        const span = document.querySelector(`#chapter-pre span.aa-line[data-idx="${idx}"]`);
        if (span) { span.innerHTML = frag.html || ' '; }
      }
      flashNo(lineNo, 1500);
      toast(`已还原原文并保存 ✓（类型：错译${jr.padded ? '；已自动补齐宽度' : ''}）`);
    } catch (e) {
      toast('网络错误：' + e, false);
    }
  }

  // ---- 行分类 override：行号列每行 hover 出 ⚙ 小图标 → 六选一菜单（text/art/mixed/header/blank + 还原为自动） ----
  // 只写 work/line_meta_overrides/<base>.json（图片加大消费），翻译数据/flag 不动；选中后 lt-<type> 类即时同步。
  // 顺序：前三为高频易错项（aa艺术/混合/文本），配快捷键 a/s/d；header/blank/auto 无键。
  const ovBase = `/api/override/${encodeURIComponent(CFG.work)}/${encodeURIComponent(CFG.base)}`;
  const OV_LABEL = { art: 'AA 艺术', mixed: '混合', text: '文本', header: '帖头', blank: '空行', auto: '还原为自动' };
  const OV_KEY = { a: 'art', s: 'mixed', d: 'text' };

  function closeOvMenu() {
    const m = document.querySelector('.ov-menu');
    if (m) m.remove();
  }

  function openOvMenu(ic) {
    closeOvMenu();
    const no = ic.closest('.lc-no');
    if (!no) return;
    const cur = no.dataset.ov || 'auto';
    const menu = document.createElement('div');
    menu.className = 'ov-menu';
    menu.innerHTML = Object.entries(OV_LABEL).map(([k, label]) => {
      const key = Object.keys(OV_KEY).find(x => OV_KEY[x] === k);
      return `<div class="ov-item${k === cur ? ' cur' : ''}" data-kind="${k}">${label}${key ? `<span class="ov-key">${key}</span>` : ''}</div>`;
    }).join('');
    no.appendChild(menu);
    const apply = async kind => {
      const item = menu.querySelector(`.ov-item[data-kind="${kind}"]`);
      if (!item) return;
      try {
        const r = await fetch(`${ovBase}/${no.dataset.line}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ kind })
        });
        const j = await r.json();
        if (!r.ok) { toast('行分类设置失败：' + (j.detail || JSON.stringify(j)), false); return; }
        const cls = kind === 'auto' ? no.dataset.auto : (kind === 'art' ? 'aa' : kind);
        no.classList.remove('lt-aa', 'lt-text', 'lt-mixed', 'lt-header', 'lt-blank');
        no.classList.add('lt-' + cls);
        no.dataset.ov = kind === 'auto' ? '' : kind;
        closeOvMenu();
        toast(`行 ${no.dataset.line} 分类：${OV_LABEL[kind]}${kind === 'auto' ? '（还原为自动）' : ''} ✓`);
      } catch (err) {
        toast('网络错误：' + err, false);
      }
    };
    const onKey = e => {
      const kind = OV_KEY[e.key.toLowerCase()];
      if (kind) { e.preventDefault(); e.stopPropagation(); apply(kind); }
    };
    menu.addEventListener('click', async e => {
      const item = e.target.closest('.ov-item');
      if (!item) return;
      e.stopPropagation();
      apply(item.dataset.kind);
    });
    document.addEventListener('keydown', onKey, { once: true });
  }

  // 行号列点击：⚙ 图标开菜单；行号本身 → 行内展开；遮罩点击 → 收起（原文模式禁编辑）
  document.getElementById('line-col').addEventListener('click', e => {
    const ic = e.target.closest('.ov-ic');
    if (ic) { e.stopPropagation(); openOvMenu(ic); return; }
    if (e.target.closest('.ov-menu')) return;      // 菜单项由菜单自身处理（已 stopPropagation）
    const b = e.target.closest('.lc-no');
    if (b) {
      if (window.__aaDlgMode) {  // 台词纠正模式：点行号切换台词标签（屏蔽行内编辑）
        toggleGold(+b.dataset.line, b);
        return;
      }
      const pm = document.getElementById('chapter-pre')?.dataset.mode;
      if (!CFG.hasTr) { toast('本章未汉化，仅原文可读，不可编辑', false); return; }
      if (pm === 'src') { toast('原文模式不可编辑，请切回汉化', false); return; }
      closeOvMenu(); openEdit(+b.dataset.line);
    }
  });
  // 右键行号 = 直接还原原文并保存（2026-09-03；屏蔽浏览器右键菜单）
  document.getElementById('line-col').addEventListener('contextmenu', e => {
    const b = e.target.closest('.lc-no');
    if (!b) return;
    e.preventDefault();
    e.stopPropagation();
    closeOvMenu();
    // 若正打开编辑面板，先收起再还原，避免 .er-tr 残留互相干扰
    if (window.__aaEditing) closeEdit();
    restoreAndSaveLine(+b.dataset.line);
  });
  if (mask) mask.addEventListener('click', closeEdit);
  // Shift+R = 还原原文并保存（2026-09-03）：编辑中（__aaEditing）触发，回调在 openEdit 内绑定
  document.addEventListener('keydown', e => {
    if (!window.__aaEditing || !__restoreSave) return;
    if (e.shiftKey && (e.key === 'R' || e.key === 'r')) {
      e.preventDefault();
      e.stopPropagation();
      __restoreSave();
    }
  });
  document.addEventListener('click', e => {
    if (!e.target.closest('.ov-menu') && !e.target.closest('.ov-ic')) closeOvMenu();
  });

  // 悬浮行号 → 高亮正文对应行（.hl 浅黄；行号 N ↔ data-idx N-1；编辑遮罩打开时被拦截自然失效）
  let hoverLine = 0;
  function clearLineHl() {
    if (hoverLine) {
      const old = document.querySelector(`#chapter-pre span.aa-line[data-idx="${hoverLine - 1}"]`);
      if (old) old.classList.remove('hl');
      hoverLine = 0;
    }
  }
  const lineCol = document.getElementById('line-col');
  lineCol.addEventListener('mouseover', e => {
    const b = e.target.closest('.lc-no');
    if (!b) return;
    const n = +b.dataset.line;
    if (n === hoverLine) return;
    clearLineHl();
    hoverLine = n;
    const s = document.querySelector(`#chapter-pre span.aa-line[data-idx="${n - 1}"]`);
    if (s) s.classList.add('hl');
  });
  lineCol.addEventListener('mouseleave', clearLineHl);

  // 修改记录
  const editsModal = document.getElementById('edits-modal');
  const editsBody = document.getElementById('edits-body');
  const esc = escapeHtml;
  document.getElementById('edits-btn').addEventListener('click', async () => {
    editsModal.classList.remove('hidden');
    editsBody.innerHTML = '加载中…';
    try {
      const j = await (await fetch(`/api/work/${encodeURIComponent(CFG.work)}/edits`)).json();
      if (!j.edits.length) { editsBody.innerHTML = '<p class="muted">暂无修改记录</p>'; return; }
      editsBody.innerHTML = '<table class="edits-table"><thead><tr><th>时间</th><th>章节</th><th>行</th>' +
        '<th>类型</th><th>修改前</th><th>修改后</th></tr></thead><tbody>' +
        j.edits.slice().reverse().map(x =>
          `<tr><td class="mono">${esc(x.ts)}</td><td>${esc(x.file.replace('_汉化.txt', ''))}</td>` +
          `<td class="mono">${x.line}</td><td>${esc(x.type)}</td>` +
          `<td class="mono" title="${esc(x.before)}">${esc(x.before.slice(0, 60))}</td>` +
          `<td class="mono" title="${esc(x.after)}">${esc(x.after.slice(0, 60))}</td></tr>`).join('') +
        '</tbody></table>';
    } catch (e) {
      editsBody.innerHTML = '<p class="bad">加载失败：' + esc(e) + '</p>';
    }
  });
  document.getElementById('edits-close').addEventListener('click', () => editsModal.classList.add('hidden'));
  let _emDown = null;
  editsModal.addEventListener('mousedown', e => { _emDown = e.target; });
  editsModal.addEventListener('click', e => { if (e.target === editsModal && _emDown === editsModal) editsModal.classList.add('hidden'); });

  // 搜索跳转（?line=N）与续读恢复：窗口化后由几何表定位 → 见下方 WIN.ready 初始化块

  // ---- 阅读进度：滚动节流保存（章节/行），重新打开时续读 ----
  // 行高随字号微调联动，每次实时读 computed style（不再硬编码 16px）
  function lineHeight() {
    const pre = document.getElementById('chapter-pre');
    const v = pre ? parseFloat(getComputedStyle(pre).lineHeight) : 0;
    return v > 0 ? v : 16;
  }
  let lastSave = 0;
  function currentLine() {
    const pre = document.getElementById('chapter-pre');
    if (!pre) return 0;
    // 窗口化：几何表直接算（不依赖区外 DOM；旧实现按 spans 数量夹取）
    if (window.__aaWin) return window.__aaWin.lineAtScroll();
    const spans = pre.querySelectorAll('.aa-line');
    if (!spans.length) return 0;
    const firstTop = pre.getBoundingClientRect().top + window.scrollY + 8;  // padding-top
    const idx = Math.max(0, Math.min(spans.length - 1,
      Math.floor((window.scrollY - firstTop) / lineHeight())));
    return +spans[idx].dataset.idx + 1;    // txt 行号（1-based）
  }
  function saveAAResume() {
    const pre = document.getElementById('chapter-pre');
    fetch(`/api/aa/${encodeURIComponent(CFG.work)}/progress`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      // keepalive：刷新/关页/整页重载时请求也要送出去（否则重载后续读回不到原处）
      keepalive: true,
      // 2026-09-03: mode=实际渲染模式(tr/refine=汉化类→自动置阅读中; src=原文→不触发)
      body: JSON.stringify({ fname: CFG.fname, line: currentLine(), mode: pre ? (pre.dataset.mode || (CFG.hasTr ? 'tr' : 'src')) : '' })
    }).catch(() => {});
  }
  window.__saveAAResume = saveAAResume;    // 供阅读器键盘翻章前调用
  // 2026-09-21：滚动保存改「收尾防抖」。旧的领先沿节流在滚动停下的那一刻经常不触发，
  // 最后一次位置丢失 → 刷新 / 翻译替换整页重载后续读回不到原处（用户实测反馈）。
  let saveTimer = 0;
  window.addEventListener('scroll', () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => { saveTimer = 0; lastSave = Date.now(); saveAAResume(); }, 350);
  }, { passive: true });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) saveAAResume();
  });
  window.addEventListener('pagehide', saveAAResume);     // 刷新/关页也尽力落盘（fetch keepalive）

  // 续读恢复：由几何表定位（见下方 WIN.ready 初始化块）

  // ---- 字体微调：字号/粗细（CSS 变量 + localStorage 持久化） ----
  // 粗细 = 位移双印加粗（text-shadow），不改变字形宽度，不会错位；
  // 勿用 font-weight≥600：真粗体字形变宽且 ASCII/全角增量不同，行间字符列会漂移
  const FONT_KEY = 'aa_reader_font';
  const BASE_FS = 14, BASE_LH = 16, FS_MIN = 10, FS_MAX = 24;
  const BOLD_SHADOW = '0.6px 0 0 currentColor, -0.6px 0 0 currentColor';
  function loadFont() {
    try { return Object.assign({ fs: BASE_FS, bold: false }, JSON.parse(localStorage.getItem(FONT_KEY) || '{}')); }
    catch (e) { return { fs: BASE_FS, bold: false }; }
  }
  let font = loadFont();
  function applyFont() {
    const lh = Math.round(font.fs * BASE_LH / BASE_FS);   // 行高随字号同比缩放
    const r = document.documentElement.style;
    r.setProperty('--aa-fs', font.fs + 'px');
    r.setProperty('--aa-lh', lh + 'px');
    r.setProperty('--aa-shadow', font.bold ? BOLD_SHADOW : 'none');
    const val = document.getElementById('fs-val');
    if (val) val.textContent = font.fs + 'px';
    document.querySelectorAll('.fw-btn').forEach(b =>
      b.classList.toggle('on', (+b.dataset.bold === 1) === font.bold));
    // 窗口化：行高变了 → 几何表/窗口重算（滚动位置由浏览器保持）
    if (window.__aaWin) { if (window.__aaEditing) closeEdit(); window.__aaWin.repaint(); }
  }
  function saveFont() {
    try { localStorage.setItem(FONT_KEY, JSON.stringify(font)); } catch (e) {}
  }
  const fsDec = document.getElementById('fs-dec');
  const fsInc = document.getElementById('fs-inc');
  if (fsDec) fsDec.addEventListener('click', () => {
    font.fs = Math.max(FS_MIN, font.fs - 1); applyFont(); saveFont();
  });
  if (fsInc) fsInc.addEventListener('click', () => {
    font.fs = Math.min(FS_MAX, font.fs + 1); applyFont(); saveFont();
  });
  const fwGroup = document.getElementById('fw-group');
  if (fwGroup) fwGroup.addEventListener('click', e => {
    const b = e.target.closest('.fw-btn');
    if (!b) return;
    font.bold = +b.dataset.bold === 1; applyFont(); saveFont();
  });
  const fontReset = document.getElementById('font-reset');
  if (fontReset) fontReset.addEventListener('click', () => {
    font = { fs: BASE_FS, bold: false };
    try { localStorage.removeItem(FONT_KEY); } catch (e) {}
    applyFont();
  });
  applyFont();   // 与头部防闪烁脚本一致（head 已按存储值设置，这里再同步 UI 标签）

  // ================= 窗口化渲染（2026-09-21 S2/S3）=================
  // 只渲染「视口 ± 缓冲」的行（按 200 行块向 /range 区间 API 取行 HTML），上下用等高 spacer 撑出
  // 整章高度 → 常驻 DOM/内存与章节行数无关（旧实现整章 ×2~3 份内联 JSON：2.2 万行 = 21.9 MB / 10 万节点）。
  // 几何（行高 + 楼距）全在本地算：阅读进度、跳转、字号切换都不依赖窗口外的 DOM。
  // 编辑面板打开期间冻结窗口（重建 innerHTML 会抹掉编辑行），编辑行滚出窗口才收面板；
  // 章节内 Ctrl+F 只能命中已渲染的窗口（文档已记）。
  const WIN = (() => {
    const preEl = document.getElementById('chapter-pre');
    const colEl = document.getElementById('line-col');
    let meta = {};
    try { meta = JSON.parse(document.getElementById('win-data')?.textContent || '{}') || {}; } catch (e) {}
    // 逃生口（方案 §6）：?win=0 或 localStorage['aa_reader_window']='0' → 全量渲染（一次性取完所有块，
    // 行为退回今天的整章 DOM；排障/对照用）。localStorage 开关不需要重开 URL。
    const FULL = (() => {
      if (CFG.win === 0) return true;
      try { return localStorage.getItem('aa_reader_window') === '0'; } catch (e) { return false; }
    })();
    const N = meta.n | 0;
    const skips = (meta.skips || []).slice();          // 跳过行（# / =====）的 txt idx，升序
    const mtArr = (meta.mt || []).slice();             // [[k, mtk], ...] 升序，只含非零楼距
    const mtPre = [0];
    for (const e of mtArr) mtPre.push(mtPre[mtPre.length - 1] + e[1]);
    const totalMt = mtPre[mtPre.length - 1];
    const BLOCK = 200;            // 与 /range 单块行数一致（也是缓存粒度）
    const LRU_MAX = 24;           // 块缓存上限（24×200 行 ≈ 1.5 MB 文本）
    const PAD = 1;                // 视口外上下各多渲染的块数
    const nBlocks = Math.max(1, Math.ceil(N / BLOCK));
    const api = `/api/work/${encodeURIComponent(CFG.work)}/chapter/${encodeURIComponent(CFG.fname)}/range`;
    const cache = new Map();      // `${mode}:${blockNo}` → rows（插入序 = LRU）
    const afterPaint = [], modeFns = [];
    let mode = meta.mode || (CFG.hasTr ? 'tr' : 'src');
    let k0 = 0, k1 = -1;          // 当前渲染窗口（闭区间，k = 可渲染行序号）
    let inflight = null, wake = false, seq = 0;
    let editK = -1, editPx = 0;   // 编辑面板插入行造成的额外高度（px）+ 生效起点
    let flashK = -1, flashUntil = 0;   // 行号闪烁（窗口重建会换掉 DOM → 由状态驱动，渲染时带 class）
    const flashSet = new Map();        // 批量闪烁（翻译替换等一次改多行）：k → 过期时间戳

    function lhPx() {
      const v = parseFloat(getComputedStyle(preEl).lineHeight);
      return v > 0 ? v : 16;
    }
    function padTopPx() {
      const v = parseFloat(getComputedStyle(preEl).paddingTop);
      return v > 0 ? v : 0;
    }
    function pageTopOf(el) { return el.getBoundingClientRect().top + window.scrollY; }
    function lbK(k) {                                     // mtArr 中第一个 key ≥ k 的下标
      let lo = 0, hi = mtArr.length;
      while (lo < hi) { const m = (lo + hi) >> 1; if (mtArr[m][0] < k) lo = m + 1; else hi = m; }
      return lo;
    }
    function mtkBefore(k) { return mtPre[lbK(k)]; }        // Σ_{j<k} mtk(j)
    function contentTop(k) {                               // 第 k 行在 pre 内容坐标里的顶边
      let t = padTopPx() + k * lhPx() + mtkBefore(k) * lhPx();
      if (editK >= 0 && k > editK) t += editPx;
      return t;
    }
    function totalH() { return padTopPx() + N * lhPx() + totalMt * lhPx() + (editPx > 0 ? editPx : 0); }
    function kAt(yTop) {                                   // 内容坐标 → k（contentTop 单调，二分）
      let lo = 0, hi = N;
      while (lo < hi) { const m = (lo + hi + 1) >> 1; if (contentTop(m) <= yTop) lo = m; else hi = m - 1; }
      return Math.max(0, Math.min(Math.max(0, N - 1), lo));
    }
    function skipsLE(i) {
      let lo = 0, hi = skips.length;
      while (lo < hi) { const m = (lo + hi) >> 1; if (skips[m] <= i) lo = m + 1; else hi = m; }
      return lo;
    }
    function kOfIdx(idx) { return Math.max(0, Math.min(Math.max(0, N - 1), idx - skipsLE(idx))); }
    function idxOfK(k) {                                   // k → txt idx（跳过行取其后第一个可渲染行）
      let lo = 0, hi = N + skips.length;
      while (lo < hi) { const m = (lo + hi) >> 1; if (m - skipsLE(m) < k) lo = m + 1; else hi = m; }
      return lo;
    }
    function rowAt(k) {                                    // 缓存里取行（缺 → null）
      const b = Math.floor(k / BLOCK);
      const rows = cache.get(`${mode}:${b}`);
      return rows ? (rows[k - b * BLOCK] || null) : null;
    }

    function rowPreHtml(r) {
      const mt = r.mtk ? ` style="margin-top:calc(var(--aa-lh,16px) * ${r.mtk})"` : '';
      return `<span class="aa-line${r.dlg ? ' dlg-line' : ''}" data-idx="${r.idx}"${mt}>${r.html}</span>`;
    }
    function rowColHtml(r) {
      const mt = r.mtk ? ` style="margin-top:calc(var(--aa-lh,16px) * ${r.mtk})"` : '';
      const autoDlg = (r.auto === 'text' || r.auto === 'mixed') ? '1' : '0';
      const gold = (CFG.gold || {})[String(r.n)];
      const yes = gold === true || (gold === undefined && autoDlg === '1');
      const fl = ((r.k === flashK && Date.now() < flashUntil) || (flashSet.get(r.k) || 0) > Date.now()) ? ' flash' : '';
      return `<button type="button" class="lc-no lt-${r.type}${CFG.hasTr ? '' : ' noedit'}${fl}"` +
        ` data-line="${r.n}" data-auto="${r.auto}" data-ov="${r.ov || ''}" data-autoDlg="${autoDlg}"${mt}` +
        ` title="${escapeHtml(r.tip)}">${r.n}` +
        `<span class="dlg-mark ${yes ? 'yes' : 'no'}">${yes ? '◆' : '◇'}</span>` +
        `<span class="ov-ic" title="行分类 override（只喂图片导出，翻译不动）">⚙</span></button>`;
    }

    function paint() {
      if (k1 < k0 || !N) return;
      // 编辑中：正文流里插着 JS 生成的编辑行/原文行（.edit-row/.src-row），重建内部 HTML 会把它们抹掉
      // → 编辑行在窗口内时冻结不重建（2026-09-21 修「打开编辑后没有原文行/无法编辑」）
      if (window.__aaEditing && editK >= k0 && editK <= k1) return;
      const top = Math.max(0, contentTop(k0) - padTopPx());
      const bot = Math.max(0, totalH() - contentTop(k1 + 1));
      const rows = [], cols = [];
      for (let k = k0; k <= k1; k++) {
        const r = rowAt(k);
        if (!r) continue;
        rows.push(rowPreHtml(r));
        cols.push(rowColHtml(r));
      }
      const sp = `<div class="win-sp" style="height:${top}px"></div>`;
      const sp2 = `<div class="win-sp" style="height:${bot}px"></div>`;
      preEl.innerHTML = sp + rows.join('') + sp2;
      colEl.innerHTML = sp + cols.join('') + sp2;
      preEl.dataset.mode = mode;
      for (const fn of afterPaint) { try { fn(); } catch (e) {} }
    }

    async function fetchBlock(b, m) {
      const r = await fetch(`${api}?from_k=${b * BLOCK}&to_k=${b * BLOCK + BLOCK - 1}&mode=${m}`);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const j = await r.json();
      return j.lines || [];
    }
    function cachePut(key, rows) {
      cache.delete(key);                       // 重新插入 → 挪到 LRU 末尾
      cache.set(key, rows);
      // 全量模式（?win=0）不能淘汰：paint() 靠 rowAt(k) 取行，淘汰会让正文出现空洞
      while (cache.size > (FULL ? Number.MAX_SAFE_INTEGER : LRU_MAX)) cache.delete(cache.keys().next().value);
    }
    async function load(b0, b1, m) {
      m = m || mode;
      const miss = [];
      for (let b = Math.max(0, b0); b <= Math.min(b1, nBlocks - 1); b++) {
        if (!cache.has(`${m}:${b}`)) miss.push(b);
      }
      if (!miss.length) return;
      const res = await Promise.all(miss.map(b => fetchBlock(b, m).then(rows => [b, rows])));
      for (const [b, rows] of res) cachePut(`${m}:${b}`, rows);
    }

    function winBlocks() { return [Math.floor(k0 / BLOCK), Math.floor(k1 / BLOCK)]; }
    function cacheFull(b0, b1) {                // 窗口内块是否都在缓存里（缺块会让 paint() 渲染出空洞 → 高度/位置跳）
      for (let b = Math.max(0, b0); b <= Math.min(b1, nBlocks - 1); b++) {
        if (!cache.has(`${mode}:${b}`)) return false;
      }
      return true;
    }

    async function moveTo(k, m) {              // 把窗口挪到以 k 为中心（k ± PAD 块）
      m = m || mode;
      const b = Math.floor(k / BLOCK);
      const b0 = Math.max(0, b - PAD), b1 = Math.min(nBlocks - 1, b + PAD);
      const my = ++seq;
      await load(b0, b1, m);
      if (my !== seq || m !== mode) return;
      k0 = b0 * BLOCK; k1 = Math.min(N - 1, (b1 + 1) * BLOCK - 1);
      paint();
    }

    async function sync() {                     // 滚动驱动：按视口算窗口，需要才重建
      if (!N || FULL) return;                   // 全量模式：整章已在 DOM，无需窗口同步
      if (window.__aaEditing) return;           // 编辑中：窗口冻结（重建会抹掉编辑行/原文行；也不 closeEdit——会丢用户输入）
      if (inflight) { wake = true; return; }
      const yTop = window.scrollY - pageTopOf(preEl);
      const kA = kAt(yTop - window.innerHeight);
      const kB = kAt(yTop + window.innerHeight * 2);
      const b0 = Math.max(0, Math.floor(kA / BLOCK) - PAD);
      const b1 = Math.min(nBlocks - 1, Math.floor(kB / BLOCK) + PAD);
      const [c0, c1] = winBlocks();
      if (b0 === c0 && b1 === c1 && cacheFull(c0, c1)) return;   // 窗口同且块齐 → 无需重建（缺块必须补齐，否则空洞）
      const my = ++seq;
      inflight = (async () => {
        await load(b0, b1);
        if (my !== seq) return;
        k0 = b0 * BLOCK; k1 = Math.min(N - 1, (b1 + 1) * BLOCK - 1);
        paint();
      })();
      try { await inflight; } catch (e) { toast('区间加载失败：' + e, false); }
      finally { inflight = null; if (wake) { wake = false; sync(); } }
    }

    async function setMode(m) {
      if (m === mode) return;
      if (window.__aaEditing) closeEdit();
      mode = m;
      for (const fn of modeFns) { try { fn(mode); } catch (e) {} }
      if (k1 >= k0) {
        const [b0, b1] = winBlocks();
        await load(b0, b1, mode);
        paint();
      }
      sync();
    }

    async function ensureVisible(lineNo) {      // 保证该行在窗口内（编辑/还原前调用）
      const k = kOfIdx(Math.max(0, (lineNo | 0) - 1));
      if (k >= k0 && k <= k1 && rowAt(k)) return k;
      await moveTo(k);
      return k;
    }
    async function scrollToLine(lineNo, center) {
      const k = await ensureVisible(lineNo);
      const y = pageTopOf(preEl) + contentTop(k) - (center ? Math.max(0, (window.innerHeight - lhPx()) / 2) : 0);
      window.scrollTo({ top: Math.max(0, y), behavior: 'auto' });
      return k;
    }
    function lineAtScroll() {
      if (!N) return 0;
      return idxOfK(kAt(window.scrollY - pageTopOf(preEl))) + 1;
    }
    function setEditDelta(k, px) { editK = k; editPx = px; }
    function flashLine(lineNo, ms) {            // 行号闪烁（状态驱动：窗口重建后依然亮）
      const k = kOfIdx(Math.max(0, (lineNo | 0) - 1));
      flashK = (N && k >= 0) ? k : -1;
      flashUntil = Date.now() + (ms || 1800);
      if (k >= k0 && k <= k1) paint();
      setTimeout(() => { if (Date.now() >= flashUntil) { flashK = -1; paint(); } }, (ms || 1800) + 40);
    }
    function flashMany(lineNos, ms) {           // 批量闪烁：一次 paint（逐行调 flashLine 会逐行整窗重建）
      ms = ms || 1500;
      const until = Date.now() + ms;
      for (const n of lineNos) {
        const k = kOfIdx(Math.max(0, (n | 0) - 1));
        if (N && k >= 0 && k < N) flashSet.set(k, until);
      }
      paint();
      setTimeout(() => {
        const t = Date.now();
        for (const [k, v] of [...flashSet]) { if (v <= t) flashSet.delete(k); }
        paint();
      }, ms + 40);
    }
    async function markChanged(idx, quiet) {   // 外部（保存行/划词替换）改行后：失效该块并就地刷新
      const k = kOfIdx(Math.max(0, idx | 0));
      const b = Math.floor(k / BLOCK);
      for (const m of ['tr', 'src', 'refine']) cache.delete(`${m}:${b}`);
      // 立刻重取该块：只删不取的话，随后的 paint() 会把这块渲染成空洞
      // （整块 200 行消失 → 文档高度骤降 → 正文位置跳，用户实测「翻译替换后页面位置会变」）
      await load(b, b);
      if (quiet || window.__aaEditing) return;
      if (k >= k0 && k <= k1) paint();
    }
    async function markBatch(idxs, quiet) {    // 批量改行（翻译替换/台词批量）：一次失效 + 一次补齐
      const blocks = new Set();
      for (const ix of idxs) blocks.add(Math.floor(kOfIdx(Math.max(0, ix | 0)) / BLOCK));
      if (!blocks.size) return;
      for (const b of blocks) {
        for (const m of ['tr', 'src', 'refine']) cache.delete(`${m}:${b}`);
      }
      const lo = Math.min.apply(null, [...blocks]), hi = Math.max.apply(null, [...blocks]);
      try { await load(lo, hi); } catch (e) { toast('区间加载失败：' + e, false); }   // load 只拉缺块
      if (quiet || window.__aaEditing) return;
      paint();
    }
    async function repaint() {                 // 字号/行高/批量改行后重建窗口（先补窗口内的块，避免空洞）
      if (k1 >= k0 && N) {
        const [c0, c1] = winBlocks();
        if (!FULL && !cacheFull(c0, c1)) { try { await load(c0, c1); } catch (e) {} }
      }
      paint();
      sync();
    }

    function showFatal(msg) {                    // 初始化失败绝不留下空白正文（方案 §6 铁律）
      const tip = `区间接口加载失败：${msg}　` +
        `<a href="${location.pathname}${location.search.replace(/[?&]win=\d+/, '')}${location.search ? '&' : '?'}win=0">改用整页渲染</a>` +
        `　<a href="javascript:location.reload()">重试</a>`;
      if (preEl) preEl.innerHTML = `<span class="aa-line" style="color:#c00"> ${tip}</span>`;
      if (colEl) colEl.innerHTML = '';
      toast('正文加载失败（见页面提示）', false);
    }

    const ready = (async () => {
      if (!N) return;
      try {
        if (FULL) {                              // 逃生口：一次取完整章（旧的整章 DOM 行为）
          toast('整页渲染中…');
          for (let b = 0; b < nBlocks; b += 8) await load(b, Math.min(nBlocks - 1, b + 7));
          k0 = 0; k1 = N - 1;
        } else {
          const b1 = Math.min(nBlocks - 1, PAD);
          await load(0, b1);
          k0 = 0; k1 = Math.min(N - 1, (b1 + 1) * BLOCK - 1);
        }
        paint();
        for (const fn of modeFns) { try { fn(mode); } catch (e) {} }
      } catch (e) {
        showFatal(e && e.message ? e.message : e);
      }
    })();

    window.__aaLineChanged = markChanged;      // 供划词翻译等外部改行后调用（失效块缓存 + 就地刷新）
    window.__aaWin = { ready, sync, moveTo, ensureVisible, scrollToLine, lineAtScroll,
                       setEditDelta, markChanged, markBatch, repaint, setMode, cache, flashLine,
                       flashMany,
                       onPaint: fn => afterPaint.push(fn), onMode: fn => modeFns.push(fn),
                       get mode() { return mode; }, get n() { return N; },
                       get k0() { return k0; }, get k1() { return k1; },
                       kOfLine: lineNo => kOfIdx(Math.max(0, (lineNo | 0) - 1)),
                       lineOfK: k => idxOfK(Math.max(0, Math.min(Math.max(0, N - 1), k))) + 1,
                       lh: lhPx, contentTop, rowAt: k => rowAt(k) };
    return window.__aaWin;
  })();

  // 滚动/尺寸变化驱动窗口；rAF 节流（passive，不阻塞滚动）
  (() => {
    let raf = 0;
    const kick = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; WIN.sync(); });
    };
    window.addEventListener('scroll', kick, { passive: true });
    window.addEventListener('resize', kick);
  })();

  // ---- 汉化/原文/精译 切换（窗口化：切模式只重取当前窗口的块数据，不再下发整章 JSON） ----
  (() => {
    const seg = document.getElementById('src-toggle');
    const hint = document.getElementById('mode-hint');
    const refineToggle = document.getElementById('btn-refine-toggle');
    const hasTr = !!CFG.hasTr;
    const hasRefine = !!meta_has_refine();
    function meta_has_refine() {
      try {
        const d = JSON.parse(document.getElementById('win-data')?.textContent || '{}');
        return !!d.hasRefine;
      } catch (e) { return false; }
    }
    function applyModeUi(m) {
      if (seg) seg.querySelectorAll('.seg-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === m));
      if (refineToggle) {
        refineToggle.classList.toggle('active', m === 'tr' || m === 'refine');
        refineToggle.classList.toggle('refine', m === 'refine');
        refineToggle.textContent = m === 'refine' ? '精译' : '汉化';
        refineToggle.title = (m === 'refine' ? '精译' : '汉化') + '（快捷键 h 切换）';
      }
      if (hint) hint.textContent = m === 'src' ? '原文' : (m === 'refine' ? '精译' : '汉化');
    }
    // 汉/精 toggle：tr↔refine 切换；原文模式下点它回汉化
    function toggleRefine() {
      const cur = WIN.mode;
      let next;
      if (cur === 'src') next = hasTr ? 'tr' : (hasRefine ? 'refine' : 'src');
      else next = (cur === 'tr' && hasRefine) ? 'refine' : 'tr';
      if (!hasTr && next === 'tr') { toast('本章未汉化，仅原文可读', false); return; }
      WIN.setMode(next);
    }
    if (refineToggle) refineToggle.addEventListener('click', e => { e.stopPropagation(); toggleRefine(); });
    if (seg) seg.addEventListener('click', e => {
      const b = e.target.closest('.seg-btn');
      if (!b) return;
      // 汉/精 toggle 由 refineToggle 自身处理(stopPropagation)，这里只处理原文
      if (b.id === 'btn-refine-toggle') return;
      if (b.dataset.mode === 'src') WIN.setMode('src');
    });
    // 快捷键 h：汉化/精译切换（不介入输入框/正文）
    document.addEventListener('keydown', e => {
      if (e.key !== 'h' && e.key !== 'H') return;
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || tag === 'button') return;
      toggleRefine();
    });
    WIN.ready.then(() => applyModeUi(WIN.mode));
  })();

  // ---- 窗口化初始化：跳转 / 续读 / 候选高亮（都改由几何表定位，需等首窗渲染完） ----
  WIN.ready.then(async () => {
    // 候选确认高亮：窗口每次重建后重应用（候选 IIFE 稍后注册该函数，故用可选调用）
    WIN.onPaint(() => {
      if (typeof window.__candReapplyHighlight === 'function') {
        try { window.__candReapplyHighlight(); } catch (e) {}
      }
    });
    // 闪烁交给 WIN（按 k 记状态）：窗口/行号列随后任何重建都不会丢掉高亮
    const flashLine = lineNo => flashNo(WIN.lineOfK(WIN.kOfLine(lineNo)), 1800);
    if (CFG.jumpLine) {
      await WIN.scrollToLine(CFG.jumpLine, true);
      flashLine(CFG.jumpLine);
      // 跳转是一次性参数：用掉就从 URL 抹掉，否则刷新会再跳回这个旧位置
      // （用户实际读到的行才是续读点，见 saveAAResume）——2026-09-21 修「刷新后不在原位置」
      try {
        const u = new URL(location.href);
        if (u.searchParams.has('line')) {
          u.searchParams.delete('line');
          history.replaceState(null, '', u.pathname + u.search + u.hash);
        }
      } catch (e) {}
    } else if (CFG.resumeLine) {
      await WIN.scrollToLine(CFG.resumeLine, false);
      flashLine(CFG.resumeLine);
      setTimeout(() => toast(`已续读到第 ${CFG.curIdx + 1} 章 第 ${CFG.resumeLine} 行`), 150);
    }
  });
  // ---- 台词纠正模式（2026-08-29 第 2 步，gold_dialogue.json 回归集） ----
  const DLG_KEY = 'aa_dlg_mode';
  const goldBtn = document.getElementById('dlg-toggle');
  const goldData = (window.READER_CFG.gold = window.READER_CFG.gold || {});   // 活引用：窗口重建后标记仍准
  const goldBase = `/api/gold/${encodeURIComponent(CFG.work)}/${encodeURIComponent(CFG.base)}`;

  function setDlgMode(on) {
    window.__aaDlgMode = on;
    document.body.classList.toggle('dlg-on', on);
    if (goldBtn) goldBtn.classList.toggle('on', on);
    try { localStorage.setItem(DLG_KEY, on ? '1' : '0'); } catch (e) {}
    if (on) { closeEdit(); toast('台词纠正模式：点行号左侧◆切换是/否台词'); }
    else { toast('已退出台词纠正模式'); }
    resetAllMarks();
  }
  function refreshMark(no) {
    const mark = no.querySelector('.dlg-mark');
    if (!mark) return;
    const line = +no.dataset.line;
    if (goldData[String(line)] === true) { mark.textContent = '◆'; mark.className = 'dlg-mark yes'; }
    else if (goldData[String(line)] === false) { mark.textContent = '◇'; mark.className = 'dlg-mark no'; }
    else if (no.dataset.autoDlg === '1') { mark.textContent = '◆'; mark.className = 'dlg-mark yes'; }
    else { mark.textContent = '◇'; mark.className = 'dlg-mark no'; }
  }
  function resetAllMarks() {
    document.querySelectorAll('.lc-no').forEach(no => refreshMark(no));
  }
  // ---- 正文行事件委托（2026-08-30 台词纠正模式交互升级） ----
  // 台词纠正模式下: 悬浮正文行 → 加深高亮 + 可点击切换; 非台词模式正文行保持原行为(无点击编辑)。
  // 事件委托绑在 #chapter-pre 上, render() 重建 innerHTML 后仍有效。
  const preEl = document.getElementById('chapter-pre');
  if (preEl) {
    let dlgHoverLine = 0;  // 0-based idx
    function clearDlgHl() {
      if (dlgHoverLine !== 0) {
        const old = preEl.querySelector(`span.aa-line[data-idx="${dlgHoverLine}"]`);
        if (old) old.classList.remove('dlg-hl');
        dlgHoverLine = 0;
      }
    }
    preEl.addEventListener('mousemove', e => {
      if (!window.__aaDlgMode) return;
      const b = e.target.closest('.aa-line');
      if (!b) return;
      const idx = parseInt(b.dataset.idx, 10);
      if (idx === dlgHoverLine) return;
      clearDlgHl();
      dlgHoverLine = idx;
      b.classList.add('dlg-hl');
    });
    preEl.addEventListener('mouseleave', clearDlgHl);
    preEl.addEventListener('click', e => {
      if (!window.__aaDlgMode) return;
      const b = e.target.closest('.aa-line');
      if (!b) return;
      e.preventDefault(); e.stopPropagation();
      const line = parseInt(b.dataset.idx, 10) + 1;  // data-idx 0-based -> line 1-based
      const no = document.querySelector(`.lc-no[data-line="${line}"]`);
      const span = document.querySelector(`#chapter-pre span.aa-line[data-idx="${line - 1}"]`);
      if (!no || !span) return;
      // 直接基于当前 span 的 dlg-line 状态翻转(不依赖 goldData, 保证与视觉一致)
      const auto = no.dataset.autoDlg === '1';
      const cur = goldData[String(line)] !== undefined ? goldData[String(line)] : auto;
      const next = !cur;
      // 后端写入
      const goldBaseL = goldBase;
      (async () => {
        try {
          const r = await fetch(`${goldBaseL}/${line}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_dialogue: next })
          });
          const j = await r.json();
          if (!r.ok) { toast('台词标注失败：' + (j.detail || JSON.stringify(j)), false); return; }
          goldData[String(line)] = next;
          span.classList.toggle('dlg-line', next);
          // 行号上同步 ◆/◇
          const mark = no.querySelector('.dlg-mark');
          if (mark) {
            mark.textContent = next ? '◆' : '◇';
            mark.className = 'dlg-mark ' + (next ? 'yes' : 'no');
          }
          toast(`行 ${line} 标记为「${next ? '是' : '非'}」台词 ✓`);
        } catch (err) { toast('网络错误：' + err, false); }
      })();
    });
  }
  async function toggleGold(line, no) {
    // 当前实际状态：gold 字符串键优先，否则自动判定（autoDlg=1 是台词）
    const auto = no.dataset.autoDlg === '1';
    const cur = goldData[String(line)] !== undefined ? goldData[String(line)] : auto;
    const next = !cur;
    try {
      const r = await fetch(`${goldBase}/${line}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_dialogue: next })
      });
      const j = await r.json();
      if (!r.ok) { toast('台词标注失败：' + (j.detail || JSON.stringify(j)), false); return; }
      goldData[String(line)] = next;
      refreshMark(no);
      const span = document.querySelector(`#chapter-pre span.aa-line[data-idx="${line - 1}"]`);
      if (span) span.classList.toggle('dlg-line', next);
      toast(`行 ${line} 标记为「${next ? '是' : '非'}」台词 ✓`);
    } catch (err) { toast('网络错误：' + err, false); }
  }
  // 窗口化：行号按钮由窗口重建（data-autoDlg 已在行 HTML 里带上）→ 只需每次 paint 后重算标记
  if (window.__aaWin) window.__aaWin.onPaint(() => { if (window.__aaDlgMode) resetAllMarks(); });
  if (goldBtn) goldBtn.addEventListener('click', () => setDlgMode(!window.__aaDlgMode));
  let savedDlg = 0;
  try { savedDlg = parseInt(localStorage.getItem(DLG_KEY) || '0', 10); } catch (e) {}
  if (savedDlg) setDlgMode(true);

  // ---- 翻译替换（2026-09-09）：手动替换对 → 本作品全部 汉化.txt（art 行跳过、SRC 宽度校正、先备份） ----
  // 后端 POST /api/work/{work}/replace-preview|apply（body {pairs}）；预览不自动刷新。
  const trBtn = document.getElementById('tr-replace-toggle');
  const trModal = document.getElementById('tr-replace-modal');
  const trPairsBox = document.getElementById('tr-pairs');
  const trPrev = document.getElementById('tr-preview');
  if (trBtn && trModal && trPairsBox && trPrev) {
    // 范围（2026-09-16；2026-09-17 默认 = 当前话和后续话）
    const trScope = () => {
      const r = trModal.querySelector('input[name="tr-scope"]:checked');
      return r ? r.value : 'from';
    };
    const trScopeCn = () => (trScope() === 'from' ? '当前话和后续话' : '全部章节');
    const trScopeNote = document.getElementById('tr-scope-note');
    if (trScopeNote && CFG.curLabel) trScopeNote.textContent = '（当前：' + CFG.curLabel + '）';
    // ---- 常用正则示例（2026-10-05，用户要求）：点条目即填入一对；表在下方 TR_EXAMPLES ----
    // 引擎口径 = logic._apply_pair：查找串以 re: 开头按 Python 正则（re.compile + m.expand），
    // 替换串里 \1 / \g<1> 取第 1 组；替换串留空 = 删除该串。替换后按各章 SRC 宽度补位、AA 艺术行整行跳过。
    // 收录铁律（真机实测踩过）：只放**碰不到 ASCII 笔画**的通用式。这套语料里 . ( ) 片假名连串在 art 行里
    // 到处都是（半角括号 → 命中 21 行全是画、列位右移；省略号 → 命中 304 行、画里的 :::.... 全中），
    // 所以那类规则一律不进示例表。作品专属的词/句式进作品术语表或补充术语.json。
    const TR_EXAMPLES = [
      { name: 'さん→桑',
        find: 're:([^ 　「」、。！？]{1,8})さん',
        repl: '\\1桑',
        desc: '敬语统一（项目铁律）：「さん」译「桑」，名字用括号保住（\\1）。' +
              '\n帖头行（名無しさん）由引擎整行跳过，所以这条只对台词里的 さん 生效。' },
      { name: '行尾残留日文',
        find: 're:[ぁ-ゖ]{4,}[ 　]*$',
        repl: '',
        desc: '行尾挂着整段没译的平假名（漏译残留）→ 删掉（替换为留空）。' +
              '\n只收平假名：片假名连串（ニニニ/ﾆﾆﾆ）多是 AA 画里的字，收进来会打到 art 行（实测 13 行全是画）。' +
              '\n先「刷新预览」逐行看过再应用——删的是真漏译，不是人名/拟声词。' },
      { name: '引号内首尾空格',
        find: 're:「[ 　]+(.+?)[ 　]+」',
        repl: '「\\1」',
        desc: '「…」内的首尾空格/全角空格清理，内容保留（\\1）。' +
              '\n要求至少 1 个空格才算命中 → 本来正常的台词不动（零命中也正常）。' },
      { name: '重复标点压缩',
        find: 're:([！？])\\1{1,}',
        repl: '\\1',
        desc: '「！！！」「？？」收成 1 个。查找串里的 \\1 是正则反向引用（同一字符连续），' +
              '替换串里的 \\1 取第 1 组——同一条规则里两侧都用上了。' +
              '\n只碰 ！？，不碰 ASCII 点线 → 不会打到 art 行（实测 57 行全是台词）。' },
      { name: '同词多译统一',
        find: 're:(?:身体|身子|躯体|身躯)',
        repl: '身体',
        desc: '同一个日文词被模型译成多种写法时统一成一种（把候选写成 (?:…) 非捕获组，不需要 \\1）。' +
              '\n替换前后同宽，不会挤动 art 列位。' },
      { name: '整句模板（\\1）',
        find: 're:相手の「(.+?)」を下げた',
        repl: '降低了对手的「\\1」',
        desc: '整句骨架替换：括号捕到的内容原样搬进译文（\\1）。' +
              '\n注意是原样透传——捕到日文就还是日文；要中文再补一对（如 re:こうげき → 攻击），或把该词收进作品术语表。' +
              '\n想让新章翻译时自动生效：宝可梦链写进 补充术语.json 的「模板」段（那里用 {名} 写法）。' },
    ];
    const trRow = (findVal, replVal, sfxVal) => {
      const row = document.createElement('div');
      row.className = 'tr-pair';
      row.innerHTML = '<input type="text" class="tr-find mono" placeholder="查找（残留日文 / 错译词…）" spellcheck="false">' +
        '<span class="tr-arrow">→</span>' +
        '<input type="text" class="tr-repl mono" placeholder="替换为（留空 = 删除该串）" spellcheck="false">' +
        '<label class="tr-sfxbox" title="sfx 样式（拟声词/效果音）：这一对命中的片段替换后一并打 sfx 标记 → 阅读器渲染成楷体+斜体+小一点（span.sfx）。人工逐对选择，不影响其它对。">' +
        '<input type="checkbox" class="tr-sfx"> sfx</label>' +
        '<button type="button" class="tr-del btn tiny ghost" title="删除该对">✕</button>';
      row.querySelector('.tr-find').value = findVal || '';
      row.querySelector('.tr-repl').value = replVal || '';
      row.querySelector('.tr-sfx').checked = !!sfxVal;
      row.querySelector('.tr-del').addEventListener('click', () => row.remove());
      return row;
    };
    // 历史替换记录（2026-09-13）：逐条勾选是否启用；勾选的与手工对合并后一起预览/应用
    const trHistBox = document.getElementById('tr-hist');
    const trHistInfo = document.getElementById('tr-hist-info');
    const trHistQ = document.getElementById('tr-hist-q');     // 搜索框（2026-10-01）
    let trHistQuery = '';
    let trHistRows = [];                     // 后端返回的记录（新在前）
    let trGlossMap = new Map();              // 本作品术语表现有词条 jp → {zh, cat}（用于「✓ 已加入」置灰）
    const trHistEnabled = () => trHistRows.filter(r => r.on).map(r => [r.find, r.repl]);
    const trHistMatch = (it) => {
      const q = trHistQuery.trim().toLowerCase();
      if (!q) return true;
      return String(it.find || '').toLowerCase().includes(q) ||
             String(it.repl || '').toLowerCase().includes(q) ||
             String(it.ts || '').toLowerCase().includes(q);
    };
    const trHistVisible = () => trHistRows.filter(trHistMatch);
    // 时间简略显示（2026-10-01 用户要求）：「2026-10-01 14:32」→「10-01 14:32」；完整信息在行的 title 里
    const trShortTs = (ts) => String(ts || '').replace(/^\d{4}-/, '');
    const trCollect = () => {
      const out = [];
      trPairsBox.querySelectorAll('.tr-pair').forEach(r => {
        const f = r.querySelector('.tr-find').value;
        if (!f || !f.trim()) return;
        // 第 3 位 = 该对是否打 sfx 标记（人工逐对勾选）
        out.push([f, r.querySelector('.tr-repl').value, !!r.querySelector('.tr-sfx').checked]);
      });
      // 手工对在前（去重由后端做），历史启用对在后（历史无 sfx 位 → 后端按 False）
      trHistEnabled().forEach(p => out.push(p));
      return out;
    };
    const trHistRender = () => {
      if (!trHistBox) return;
      if (!trHistRows.length) {
        trHistBox.innerHTML = '<p class="muted small" style="margin:2px 0">' +
          '暂无记录——每次「应用」成功后自动记一条（新在前，最多 ' + (trHistMax || 100) + ' 条）。</p>';
        trHistSyncInfo();
        return;
      }
      const rows = trHistVisible();          // 搜索过滤（2026-10-01）
      if (!rows.length) {
        trHistBox.innerHTML = '<p class="muted small" style="margin:2px 0">没有匹配「' +
          escapeHtml(trHistQuery) + '」的记录（共 ' + trHistRows.length + ' 条）。</p>';
        trHistSyncInfo();
        return;
      }
      trHistBox.innerHTML = '';
      rows.forEach((it) => {
        const row = document.createElement('label');
        row.className = 'tr-hist-row' + (it.on ? ' on' : '');
        row.title = (it.ts ? it.ts + ' 应用' : '') + (it.n > 1 ? '（累计 ' + it.n + ' 次）' : '') +
          (it.applied ? '，最近影响 ' + it.applied + ' 行 / ' + it.files + ' 文件' : '');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = !!it.on;
        cb.addEventListener('change', () => { it.on = cb.checked; trHistRender(); });
        const pair = document.createElement('span');
        pair.className = 'th-pair';
        pair.innerHTML = '<span class="th-find">' + escapeHtml(it.find) + '</span> → <span class="th-repl">' +
          (it.repl ? escapeHtml(it.repl) : '（删除）') + '</span>';
        const meta = document.createElement('span');
        meta.className = 'th-meta';
        meta.textContent = trShortTs(it.ts) + (it.n > 1 ? ' ×' + it.n : '');   // 简略（2026-10-01）；详情看 title
        row.appendChild(cb); row.appendChild(pair); row.appendChild(meta);
        // ＋术语表（2026-09-14）：单条把该对 (find→repl) 手动加进本作品术语表，只影响以后的翻译/校对。
        // 已在术语表里的条目直接显示「✓ 已加入」并禁用（不依赖本次会话是否点过）
        const glossHas = trGlossMap.has(it.find);
        const glossZh = glossHas ? (trGlossMap.get(it.find).zh || '') : '';
        const glossCat = glossHas ? (trGlossMap.get(it.find).cat || '') : '';
        const glossSame = glossHas && glossZh === it.repl;
        const addBtn = document.createElement('button');
        addBtn.type = 'button';
        addBtn.className = 'btn tiny ghost th-add';
        if (!it.repl) {
          addBtn.textContent = '＋术语表';
          addBtn.disabled = true;
          addBtn.title = '「替换为」为空的记录是「删除该串」，不是译名，不能加入术语表';
        } else if (glossSame) {
          addBtn.textContent = '✓ 已加入';
          addBtn.disabled = true;
          addBtn.title = '本作品术语表已有这一条：' + it.find + ' → ' + it.repl +
            (glossCat ? '（类别「' + glossCat + '」）' : '');
        } else if (glossHas) {
          addBtn.textContent = '✓ 已有';
          addBtn.disabled = true;
          addBtn.title = '本作品术语表已有「' + it.find + '」→「' + glossZh + '」，不覆盖（要改去术语表页改）';
        } else {
          addBtn.textContent = '＋术语表';
          addBtn.disabled = false;
          addBtn.title = '把这一对加入本作品术语表：' + it.find + ' → ' + it.repl +
            '（类别「专名」、来源 manual）。只影响以后的翻译/校对 prompt，不改现有文本。';
        }
        addBtn.addEventListener('click', async (ev) => {
          ev.preventDefault(); ev.stopPropagation();     // 行是 <label>，别让点击连带勾选框
          addBtn.disabled = true;
          addBtn.textContent = '加入中';
          try {
            const r = await fetch(`/api/glossary/${encodeURIComponent(CFG.work)}/add-pair`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ jp: it.find, zh: it.repl, cat: '专名' })
            });
            const j = await r.json().catch(() => ({}));
            if (j.added) {
              trGlossMap.set(it.find, { zh: it.repl, cat: '专名' });   // 记住：重渲染（勾选/刷新）后仍是「已加入」
              addBtn.textContent = '✓ 已加入';
              toast('已加入本作品术语表：' + it.find + ' → ' + it.repl + '（专名）✓', true);
              return;
            }
            if (j.reason === 'exists') {
              trGlossMap.set(it.find, { zh: it.repl, cat: '专名' });
              addBtn.textContent = '✓ 已加入';
              toast('术语表里已有这一条，无需重复添加（' + it.find + ' → ' + it.repl + '）', true);
              return;
            }
            if (j.reason === 'conflict') {
              trGlossMap.set(it.find, { zh: j.current_zh || '', cat: '' });
              addBtn.textContent = '✓ 已有';
              addBtn.title = '本作品术语表已有「' + it.find + '」→「' + (j.current_zh || '') + '」，不覆盖（要改去术语表页改）';
              toast('术语表已有「' + it.find + '」→「' + j.current_zh + '」，未覆盖（要改去术语表页改）', false);
            } else {
              toast('加入术语表失败：' + (j.detail || j.error || JSON.stringify(j)), false);
            }
          } catch (e) {
            toast('网络错误：' + e, false);
          }
          if (!trGlossMap.has(it.find)) {          // 失败才允许再点；成功/冲突已置灰
            addBtn.textContent = '＋术语表';
            addBtn.disabled = !it.repl;
          }
        });
        row.appendChild(addBtn);
        trHistBox.appendChild(row);
      });
      trHistSyncInfo();
    };
    const trHistSyncInfo = () => {
      if (!trHistInfo) return;
      if (!trHistRows.length) {
        trHistInfo.textContent = '每次「应用」成功后自动记一条（仅本作品）';
        return;
      }
      const on = trHistRows.filter(r => r.on).length;
      const vis = trHistVisible().length;
      trHistInfo.textContent = '已勾 ' + on + ' / ' + trHistRows.length + ' 条（仅本作品）' +
        (trHistQuery.trim() && vis !== trHistRows.length ? '；搜索命中 ' + vis + ' 条' : '');
    };
    let trHistMax = 100;
    async function trHistLoad() {
      if (!trHistBox) return;
      try {
        // 历史记录 + 本作品术语表现有词条（并发；术语表读失败不影响按钮可用）
        const [r, gr] = await Promise.all([
          fetch(`/api/work/${encodeURIComponent(CFG.work)}/replace-history`),
          fetch(`/api/glossary/${encodeURIComponent(CFG.work)}`).catch(() => null)
        ]);
        const j = await r.json();
        const items = (r.ok && j.items) ? j.items : [];
        trHistMax = j.max || 100;
        try {
          const gj = gr ? await gr.json() : {};
          trGlossMap = new Map((gj.glossary || []).filter(e => e && e.jp)
            .map(e => [e.jp, { zh: e.zh || '', cat: e.cat || '' }]));
        } catch (e) { trGlossMap = new Map(); }
        // 保留已勾选状态（同 find+repl 的沿用），避免刷新后选择丢失
        const prev = new Map(trHistRows.map(x => [x.find + '\u0000' + x.repl, x.on]));
        trHistRows = items.map(it => Object.assign({}, it, {
          on: prev.get(it.find + '\u0000' + it.repl) || false
        }));
        trHistRender();
      } catch (e) {
        trHistBox.innerHTML = '<p class="muted small" style="margin:2px 0">历史记录读取失败（不影响替换）。</p>';
      }
    }
    // 请求指纹 = 替换对 + 范围（改任一都算「未预览」，防按 A 预览按 B 应用）
    const trPairsKey = pairs => JSON.stringify([pairs, trScope()]);
    let trLastPairs = '';        // 上次预览用的「替换对 + 范围」指纹
    let trPrevData = null;       // 上次预览的原始结果（章节勾选变化时重渲染用）
    let trChapters = [];         // 本次预览**有命中**的章（规范名）
    let trChOn = new Set();      // 其中勾选要改的章
    const trEmptyPrev = () => {
      trPrev.innerHTML = '<p class="muted small" style="margin:2px 0">尚未预览——点击「刷新预览」查看受影响行（点行号跳转对应章节）。</p>';
    };
    const trPrevHint = document.getElementById('tr-prev-hint');
    const trInvalidate = () => {          // 替换对/范围一变 → 上次预览与章节条一起作废
      trLastPairs = '';
      trPrevData = null;
      trChapters = [];
      trChOn = new Set();
      trChRender();
      trEmptyPrev();
      if (trPrevHint) trPrevHint.classList.remove('hidden');
    };

    // ---- 章节勾选（2026-10-01，用户要求）----------------------------------
    // 章节条**在刷新预览之后才出现**，且只列**真有替换命中的章**（零命中 → 整条不显示）；
    // 默认全选；取消勾选的章不写入（应用 = 预览命中章 ∩ 勾选章）。
    // 注：章名靠阅读器目录（tr-chapters-data）映射显示，仅用于显示、不参与判定。
    const trChBox = document.getElementById('tr-chapters');
    const trChTools = document.getElementById('tr-ch-tools');
    const trChSum = document.getElementById('tr-ch-sum');
    const trChLabel = new Map();     // 汉化章名 → {num, label}
    try {
      (JSON.parse(document.getElementById('tr-chapters-data')?.textContent || '[]') || [])
        .forEach(c => { if (c && c.file) trChLabel.set(c.file, c); });
    } catch (e) {}
    const trChChecked = () => trChapters.filter(f => trChOn.has(f));
    const trChName = (f) => {
      const c = trChLabel.get(f);
      return c ? (c.num + '. ' + (c.label || f)) : f;
    };
    const trChSyncSum = () => {
      if (!trChSum) return;
      const on = trChChecked().length;
      trChSum.textContent = '已勾 ' + on + ' / ' + trChapters.length + ' 章' +
        (on ? '' : '（0 章——应用不会改动任何章）');
      trChSum.classList.toggle('bad', trChapters.length > 0 && !on);
    };
    const trChRender = () => {
      if (!trChBox) return;
      const has = trChapters.length > 0;
      trChBox.classList.toggle('hidden', !has);
      if (trChTools) trChTools.classList.toggle('hidden', !has);
      trChBox.innerHTML = '';
      trChapters.forEach(f => {
        const lab = document.createElement('label');
        lab.className = 'tr-ch' + (trChOn.has(f) ? ' on' : '');
        lab.title = trChName(f) + '（' + f + '）——取消勾选 = 这一章不改';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = trChOn.has(f);
        const sp = document.createElement('span');
        sp.textContent = trChName(f);
        cb.addEventListener('change', () => {
          if (cb.checked) trChOn.add(f); else trChOn.delete(f);
          lab.classList.toggle('on', cb.checked);
          trChSyncSum();
          trRenderPreview();               // 预览里把该章整组变淡划掉
        });
        lab.appendChild(cb); lab.appendChild(sp);
        trChBox.appendChild(lab);
      });
      trChSyncSum();
    };
    const trChSetAll = (on) => {
      trChapters.forEach(f => { if (on) trChOn.add(f); else trChOn.delete(f); });
      trChRender(); trRenderPreview();
    };
    const TR_PREV_MAX_LINES = 300;
    const trRenderPreview = () => {
      const j = trPrevData;
      if (!j) return;
      let shown = 0, out = '';
      for (const f of (j.files || [])) {
        if (shown >= TR_PREV_MAX_LINES) break;
        const off = !trChOn.has(f.file);
        const gname = trChLabel.has(f.file) ? trChName(f.file) : f.file;
        out += '<div class="gfile' + (off ? ' off' : '') + '"><b title="' + escapeHtml(f.file) + '">' + escapeHtml(gname) +
          '</b>（' + f.changed + ' 行' + (off ? '，已取消勾选 → 不改' : '') + '）<ul>';
        for (const l of f.lines) {
          if (shown >= TR_PREV_MAX_LINES) break;
          const n = l[0], b = l[1], a = l[2], ow = l[3];
          out += '<li><a class="mono small" target="_blank" title="新标签跳转该行" href="/work/' +
            encodeURIComponent(CFG.work) + '/read?f=' + encodeURIComponent(f.file) + '&line=' + n + '">' + n + ':</a> ' +
            (ow ? '<span class="ovw" title="替换后比 SRC 宽（text/mixed 行允许，如不需要可改短）">⚠超宽</span> ' : '') +
            escapeHtml(b) + ' → <b>' + escapeHtml(a) + '</b></li>';
          shown++;
        }
        out += '</ul></div>';
      }
      trPrev.innerHTML = '<p class="muted small" style="margin:2px 0">范围 <b>' + trScopeCn() + '</b>；实扫 <b>' +
        j.chapters + '</b> 章，命中 <b>' + (j.files || []).length + '</b> 章，共 <b>' + j.total + '</b> 行受影响' +
        '（要写入 <b>' + trChChecked().length + '</b> 章' +
        (j.art_skipped ? '；命中行里 AA 艺术行跳过 ' + j.art_skipped + ' 行' : '') + '）' +
        (j.sfx_total ? '；其中 <b>' + j.sfx_total + '</b> 处将标 sfx 样式（楷体斜体）' : '') +
        (j.total > TR_PREV_MAX_LINES ? '；只显示前 ' + TR_PREV_MAX_LINES + ' 行，应用会全部生效' : '') + '</p>' +
        (out || '<p class="muted">无受影响行（当前范围里没有命中）</p>');
    };
    async function trRunPreview() {
      const pairs = trCollect();
      if (!pairs.length) { toast('请先输入至少一对「查找 → 替换为」', false); return; }
      const btn = document.getElementById('tr-recalc');
      btn.disabled = true; btn.textContent = '刷新预览…';
      trPrev.innerHTML = '<p class="muted small" style="margin:2px 0">计算中…</p>';
      try {
        const r = await fetch(`/api/work/${encodeURIComponent(CFG.work)}/replace-preview`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pairs, scope: trScope(), start: CFG.fname })
        });
        const j = await r.json();
        if (!r.ok) { trPrev.innerHTML = ''; toast('预览失败：' + (j.detail || JSON.stringify(j)), false); return; }
        trLastPairs = trPairsKey(pairs);
        trPrevData = j;
        // 章节条（2026-10-01）：只列**有命中的章**、默认全选；零命中 → 整条不显示
        trChapters = (j.files || []).map(f => f.file);
        trChOn = new Set(trChapters);
        trChRender();
        trRenderPreview();
        if (trPrevHint) trPrevHint.classList.add('hidden');
      } catch (e) {
        trPrev.innerHTML = '';
        toast('网络错误：' + e, false);
      } finally {
        btn.disabled = false; btn.textContent = '刷新预览';
      }
    }
    async function trApply() {
      const pairs = trCollect();
      if (!pairs.length) { toast('请先输入至少一对「查找 → 替换为」', false); return; }
      const checked = trChChecked();
      if (!checked.length) {
        toast('请至少勾选一章（右下预览下方章节条里取消的章不会被改）', false); return;
      }
      // 「已预览」判据：替换对+范围与上次预览一致，且勾选章都在上次预览里（取消勾选是允许的）
      const prevFiles = new Set(((trPrevData || {}).files || []).map(f => f.file));
      const stale = trLastPairs !== trPairsKey(pairs) || !checked.every(f => prevFiles.has(f));
      if (stale &&
          !window.confirm('替换对/范围尚未预览（或已修改）。直接应用会改动「' + trScopeCn() +
            '」中勾选的 ' + checked.length + ' 章的 汉化.txt，确认继续？')) return;
      const btn = document.getElementById('tr-apply');
      btn.disabled = true; btn.textContent = '应用中…';
      try {
        const r = await fetch(`/api/work/${encodeURIComponent(CFG.work)}/replace-apply`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pairs, scope: trScope(), start: CFG.fname, chapters: checked })
        });
        const j = await r.json();
        if (!r.ok || !j.ok) { toast('应用失败：' + (!r.ok && j.detail ? j.detail : JSON.stringify(j)), false); return; }
        trLastPairs = trPairsKey(pairs);
        if (!j.applied) { toast('无受影响行（查找串在所选范围内的 汉化.txt 均未命中）'); return; }
        toast(`已应用 ${j.applied} 行替换（${(j.files || []).length} 个文件 / 范围：${trScopeCn()}，勾选 ${j.picked != null ? j.picked : trChChecked().length} 章，已自动备份）` +
          (j.sfx_marks ? `；另标 ${j.sfx_marks} 处 sfx 样式` : '') + ' ✓');
        if ((j.sfx_failed || []).length) {
          toast('sfx 标记写入失败：' + j.sfx_failed.map(x => x.file).join('、'), false);
        }
        trHistLoad();   // 本次应用已记入历史（新记录在顶部，默认不勾）
        // 当前章有改动且不多 → 就地重渲染保留阅读位置；否则整页刷新（改动在别章/太多）
        const curFile = (j.files || []).find(f => f.file === CFG.fname);
        const pre = document.getElementById('chapter-pre');
        const idxs = curFile ? (curFile.idxs || []).filter(ix => Number.isInteger(ix)) : [];
        if (curFile && pre && pre.dataset.mode === 'tr' && idxs.length && idxs.length <= 80) {
          if (window.__aaWin) {
            // 窗口化：一次失效受影响块 + 补齐（markBatch），再重建当前窗口
            await window.__aaWin.markBatch(idxs, true);
            await window.__aaWin.repaint();
            window.__aaWin.flashMany(idxs.map(ix => ix + 1), 1500);
          } else {
            await Promise.all(idxs.map(async ix => {
              try {
                const fr = await (await fetch(`${base}/line/${ix}/html`)).json();
                const span = document.querySelector(`#chapter-pre span.aa-line[data-idx="${ix}"]`);
                if (span) span.innerHTML = fr.html || ' ';
                const no = document.querySelector(`.lc-no[data-line="${ix + 1}"]`);
                if (no) { no.classList.add('flash'); setTimeout(() => no.classList.remove('flash'), 1500); }
              } catch (e) { /* 单行刷新失败忽略——数据已落盘，整页刷新可见 */ }
            }));
          }
        } else {
          // 先落盘阅读位置再做整页重载：否则重载后续读用的是旧位置（表现为「替换后位置变了」）
          saveAAResume();
          location.reload();   // 改动在别的章 / 太多 / 原文或精译模式 → 整页刷新
          return;
        }
      } catch (e) {
        toast('网络错误：' + e, false);
      } finally {
        btn.disabled = false; btn.textContent = '应用';
      }
    }
    trBtn.addEventListener('click', () => {
      if (window.__aaEditing) closeEdit();          // 行内编辑开着会遮住弹窗交互，先收起
      if (trPairsBox.querySelectorAll('.tr-pair').length === 0) trPairsBox.appendChild(trRow());
      trInvalidate();                               // 章节条要等「刷新预览」才有（2026-10-01）
      trModal.classList.remove('hidden');
      trHistLoad();
    });
    if (trHistBox) {
      document.getElementById('tr-hist-all').addEventListener('click', () => {
        trHistVisible().forEach(it => { it.on = true; }); trHistRender();
      });
      document.getElementById('tr-hist-none').addEventListener('click', () => {
        trHistVisible().forEach(it => { it.on = false; }); trHistRender();
      });
    }
    if (trHistQ) trHistQ.addEventListener('input', () => {
      trHistQuery = trHistQ.value || '';
      if (trHistRender) trHistRender();
    });
    // 同列新对（点示例 / 点「+ 添加替换对」都走这里）：新对 = 上次预览作废（与改范围同口径）
    const trAddPair = (findVal, replVal, sfxVal) => {
      const row = trRow(findVal, replVal, sfxVal);
      trPairsBox.appendChild(row);
      trInvalidate();
      trPairsBox.scrollTop = trPairsBox.scrollHeight;
      const inp = row.querySelector('.tr-find');
      if (inp) { try { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); } catch (e) {} }
      return row;
    };
    // 常用正则示例（2026-10-05）：点一下填入一对；说明进 title（悬停看用途与注意事项）
    const trExList = document.getElementById('tr-ex-list');
    if (trExList) {
      TR_EXAMPLES.forEach(ex => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'tr-ex';
        b.title = ex.name + '\n\n' + ex.desc + '\n\n查找：' + ex.find +
                  '\n替换为：' + (ex.repl === '' ? '（留空 = 删除该串）' : ex.repl);
        b.innerHTML = '<span class="tr-ex-name">' + escapeHtml(ex.name) + '</span>' +
                      '<span class="tr-ex-pat">' + escapeHtml(ex.find) + '</span>';
        b.addEventListener('click', () => trAddPair(ex.find, ex.repl, !!ex.sfx));
        trExList.appendChild(b);
      });
    }
    document.getElementById('tr-add-pair').addEventListener('click', () => trAddPair());
    // 章节勾选条按钮（2026-10-01）
    document.getElementById('tr-ch-all').addEventListener('click', () => trChSetAll(true));
    document.getElementById('tr-ch-none').addEventListener('click', () => trChSetAll(false));
    // 改范围 = 之前那次预览（含章节条）不再有效（应用时要求重新预览）
    trModal.querySelectorAll('input[name="tr-scope"]').forEach(r =>
      r.addEventListener('change', trInvalidate));
    document.getElementById('tr-recalc').addEventListener('click', trRunPreview);
    document.getElementById('tr-apply').addEventListener('click', trApply);
    document.getElementById('tr-cancel').addEventListener('click', () => trModal.classList.add('hidden'));
    let _trDown = null;
    trModal.addEventListener('mousedown', e => { _trDown = e.target; });
    trModal.addEventListener('click', e => { if (e.target === trModal && _trDown === trModal) trModal.classList.add('hidden'); });
  }

})();

/* ================= 候选确认悬浮窗（?cand=1 时启用；单页模式） =================
 *
 * 设计要点（2026-08-28 三问题修复）:
 * 1. 多出处: 悬浮窗列出候选全部来源章(可点击)，当前章高亮，点别的出处才跳（带 &ci=<idx> 还原）
 * 2. 高亮不丢失: 注册 window.__candReapplyHighlight，主 IIFE render() 重建后调用重应用
 * 3. 不自动跳章/不重置: show() 不再自动 location 跳转（那是"先跳最新章节后跳回候选1"根因）；
 *    只在当前章高亮，跳章由用户点出处触发，且用 &ci=<idx> 还原候选进度
 */
(() => {
  const CAND = window.CAND_DATA;
  if (!CAND || !CAND.candidates || !CAND.candidates.length) return;
  const panel = document.getElementById('cand-panel');
  if (!panel) return;
  const body = document.getElementById('cand-body');
  const progress = document.getElementById('cand-progress');
  const CFG = window.READER_CFG;
  const work = CFG.work;
  let list = CAND.candidates.slice();
  // 初始索引: CAND.init_idx（服务端从 &ci= 解析）— 跳章还原进度
  let idx = Math.min(CAND.init_idx || 0, list.length - 1);
  let curTerm = null, curHits = [];   // 当前候选 term + 命中行 idx（重应用中）
  let skipped = [];
  try { skipped = JSON.parse(sessionStorage.getItem('cand-skip-' + work) || '[]'); } catch (e) {}
  list = list.filter(c => !skipped.includes(c.term));

  // 可拖动
  let drag = false, ox = 0, oy = 0;
  panel.querySelector('.cand-head').addEventListener('mousedown', e => {
    drag = true; ox = e.clientX - panel.offsetLeft; oy = e.clientY - panel.offsetTop;
    e.preventDefault();
  });
  document.addEventListener('mousemove', e => {
    if (!drag) return;
    panel.style.left = (e.clientX - ox) + 'px';
    panel.style.top = (e.clientY - oy) + 'px';
  });
  document.addEventListener('mouseup', () => drag = false);

  function toast(msg, ok) {
    const t = document.createElement('div');
    t.className = 'toast' + (ok ? '' : ' bad');
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2000);
  }

  // 高亮当前候选 term 在当前渲染的行（原文含 term 的行 idx）
  function highlightInCurrent() {
    const pre = document.getElementById('chapter-pre');
    if (!pre) return;
    pre.querySelectorAll('.cand-highlight').forEach(el => el.classList.remove('cand-highlight'));
    if (!curTerm) return;
    let first = null, n = 0;
    pre.querySelectorAll('span.aa-line').forEach(sp => {
      if (curHits.includes(parseInt(sp.dataset.idx, 10))) {
        sp.classList.add('cand-highlight');
        if (!first) first = sp;
        n++;
      }
    });
    return first;
  }

  // 重应用高亮（切换汉化/原文后由主 IIFE render() 调用）；仅当当前渲染其数据含 term 时应用
  window.__candReapplyHighlight = function () {
    // term 仅在原文: 若当前汉化渲染且命中 idx 非当前模式行，提示已切原文更佳
    highlightInCurrent();
  };

  function jumpToCand(ci) {
    // 点某出处章跳转，带 &ci=<当前候选idx> 还原进度（不丢候选）
    location.href = '/work/' + work + '/read?cand=1&ci=' + ci;
  }

  function show() {
    if (idx >= list.length || idx < 0) { idx = 0; }
    if (list.length === 0) {
      body.innerHTML = '<div class="muted">候选已全部处理（含本轮跳过）。<br>可<code>--promote</code>落地知识库，或重启确认模式继续。</div>';
      progress.textContent = '';
      return;
    }
    if (idx >= list.length) idx = 0;
    const c = list[idx];
    curTerm = c.term;
    const hits = c['命中'] || {};
    // 所有来源章（有命中的优先）
    const srcSet = Object.keys(hits).length ? Object.keys(hits) : (c['来源'] || '').split(',').map(x=>x.trim()).filter(Boolean);
    // 当前章命中行：service 端已算 c['命中'][<当前base>]（若有）
    curHits = (hits[CFG.base] || []).slice();
    // 若当前章无命中但有来源章，扣"当前章命中为0"提示
    const curHas = curHits.length > 0;
    progress.textContent = (idx + 1) + ' / ' + list.length;
    body.innerHTML = `
      <div class="cand-item">
        <div class="cand-type"><span class="muted small">类型:</span> <select id="cand-type">
          ${['角色','术语','专有名词'].map(t => `<option value="${t}" ${(c.type||'专有名词')===t?'selected':''}>${t}</option>`).join('')}
        </select> <span class="cand-type-term">${c.term}</span></div>
        <div class="cand-trans">→ <input id="cand-trans" value="${(c.translation || '').replace(/"/g, '&quot;')}" placeholder="译名"></div>
        ${(c.type||'')==='角色' ? `<div class="cand-alias">别名_of: <input id="cand-aliasof" value="${(c['别名_of'] || '').replace(/"/g, '&quot;')}" placeholder="若为某角色别名, 填主角色名(可空)"></div>` : ''}
        <div class="cand-w">作品: <input id="cand-w" value="${(c['作品'] || '').replace(/"/g, '&quot;')}" placeholder="外部IP出处(可空)"></div>
        <div class="cand-src muted small">出处(${srcSet.length}): ${srcSet.map((s, i) =>
          `<a href="#" class="cand-src-link" data-src="${s}" data-term="${encodeURIComponent(c.term)}" data-idx="${i}">${s}</a>`).join(', ')}
          ${curHas ? '' : '<span class="warn small">（当前章无此词）</span>'}
        </div>
        ${c.note ? `<div class="cand-note warn small">${c.note}</div>` : ''}
      </div>`;
    // type 下拉: 切到角色时显示别名_of 输入, 否则隐藏
    const typeSel = document.getElementById('cand-type');
    if (typeSel) typeSel.addEventListener('change', () => {
      const isRole = typeSel.value === '角色';
      let alRow = document.querySelector('.cand-item .cand-alias');
      if (isRole && !alRow) {
        const al = document.createElement('div');
        al.className = 'cand-alias';
        al.innerHTML = '别名_of: <input id="cand-aliasof" placeholder="若为某角色别名, 填主角色名(可空)">';
        const wRow = document.querySelector('.cand-item .cand-w');
        wRow?.parentNode?.insertBefore(al, wRow);
      } else if (!isRole && alRow) alRow.remove();
    });
    // 出处链接点击 → 跳该章原文(base 归一, 阅读器不认 _台词.txt 章节) 还原候选 idx
    body.querySelectorAll('.cand-src-link').forEach(a => {
      a.addEventListener('click', ev => {
        ev.preventDefault();
        const src = a.dataset.src;
        const term = decodeURIComponent(a.dataset.term);
        // _台词.txt → 原文 base: entry_N_台词.txt → entry_N
        const base = src.endsWith('_台词.txt') ? src.slice(0, -'_台词.txt'.length) : src.replace(/\.txt(\.gz)?$/, '');
        location.href = '/work/' + work + '/read?f=' + encodeURIComponent(base + '.txt') + '&cand=1&ci=' + idx;
      });
    });
    // 当前章高亮（若有命中）；无则提示去别的出处看
    if (curHas) {
      const first = highlightInCurrent();
      if (first) { first.scrollIntoView({ block: 'center' }); }
      // 若 term 只在原文，自动切原文模式让高亮可见
      const pre = document.getElementById('chapter-pre');
      // 窗口化：不再内联整章原文（旧实现扫 __candSrcArr）→ 用服务端候选命中行判断本章原文是否含该词
      const srcHas = curHits.length > 0;
      if (srcHas && pre && pre.dataset.mode !== 'src') {
        const seg = document.getElementById('src-toggle');
        const srcBtn = seg?.querySelector('.seg-btn[data-mode="src"]');
        if (srcBtn) srcBtn.click();
      }
      toast('原文高亮 ' + curHits.length + ' 处: ' + c.term, true);
    } else {
      toast('当前章无此词「' + c.term + '」，点击出处章查看', false);
    }
  }

  // （窗口化 2026-09-21：原「记录原文数据供自动切模式判断」的 __candSrcArr 已删——整章原文不再下发）

  async function api(action, term, payload) {
    const url = '/api/work/' + encodeURIComponent(work) + '/candidates/' + encodeURIComponent(term) + '/' + action;
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: payload ? JSON.stringify(payload) : undefined });
    return r.json();
  }

  document.getElementById('cand-next').addEventListener('click', () => { idx = Math.min(idx + 1, list.length - 1); show(); });
  document.getElementById('cand-prev').addEventListener('click', () => { if (idx > 0) { idx--; show(); } });
  document.getElementById('cand-close').addEventListener('click', () => panel.classList.add('hidden'));
  document.getElementById('cand-skip').addEventListener('click', async () => {
    const c = list[idx];
    skipped.push(c.term);
    try { sessionStorage.setItem('cand-skip-' + work, JSON.stringify(skipped)); } catch (e) {}
    list = list.filter(x => x.term !== c.term);
    toast('已跳过: ' + c.term);
    show();
  });
  document.getElementById('cand-del').addEventListener('click', async () => {
    const c = list[idx];
    const r = await api('delete', c.term);
    if (r.ok) {
      list = list.filter(x => x.term !== c.term);
      toast('已删除: ' + c.term);
      show();
    } else toast(r.error || '删除失败', false);
  });
  document.getElementById('cand-confirm').addEventListener('click', async () => {
    const c = list[idx];
    const payload = {
      translation: document.getElementById('cand-trans')?.value || '',
      作品: document.getElementById('cand-w')?.value || '',
    };
    const tp = document.getElementById('cand-type');
    if (tp) payload.type = tp.value;
    const al = document.getElementById('cand-aliasof');
    if (al) payload['别名_of'] = al.value;
    const r = await api('confirm', c.term, payload);
    if (r.ok) {
      list = list.filter(x => x.term !== c.term);
      toast('已确认: ' + c.term + ' → ' + (payload.translation || ''));
      show();
    } else toast(r.error || '确认失败', false);
  });

  // 2026-09-20: 白名单（确认右侧，蓝色）—— 词入全局 glossary.json known_names（不再进候选池；
  // 残留检测白名单 aa_translate.KNOWN_NAMES 进程启动时加载 → 需重启工作台/翻译进程才吃到），本条候选移除
  const wlBtn = document.getElementById('cand-whitelist');
  if (wlBtn) {
    wlBtn.addEventListener('click', async () => {
      const c = list[idx];
      const r = await api('whitelist', c.term);
      if (r.ok) {
        list = list.filter(x => x.term !== c.term);
        toast('已记入本作品白名单: ' + c.term + (r.added ? '' : '（原本已在白名单）')
              + '（本表 ' + r.names_total + ' 条）· 要跨作品生效请在作品页「检查可反哺项」勾选写入全局', true);
        show();
      } else toast(r.error || '白名单写入失败', false);
    });
  }

  // 2026-09-06 grilling ②: 批量把已确认候选写入作品术语表(角色同进知识库人物)
  const promoteBtn = document.getElementById('cand-promote');
  if (promoteBtn) {
    promoteBtn.addEventListener('click', async () => {
      promoteBtn.disabled = true;
      const orig = promoteBtn.textContent;
      promoteBtn.textContent = '写入中…';
      try {
        const r = await fetch('/api/work/' + encodeURIComponent(work) + '/candidates/promote-work-glossary', { method: 'POST' });
        const j = await r.json();
        if (j.ok) {
          const skipN = (j.skipped || []).length;
          toast(`已写入作品表 ${j.written} 条` + (skipN ? `（跳过 ${skipN} 条）` : ''), true);
          if (j.written === 0 && j.skipped === 0) {
            body.innerHTML = '<div class="muted">无已确认候选。先逐条「确认」再写入。</div>';
          }
        } else toast(j.error || '写入失败', false);
      } catch (e) { toast('写入失败: ' + e, false); }
      promoteBtn.disabled = false;
      promoteBtn.textContent = orig;
    });
  }

  panel.classList.remove('hidden');
  show();
})();

/* ---- 全库搜索（2026-10-07，方案 doc/方案归档/工作台阅读器搜索与官方译名地区名方案_2026-10-07.md）----
   右上角「🔍 搜索」→ 弹窗；默认中日文都搜（可切只中文/只日文）；只搜 text/mixed 行（服务端按
   line_meta_v2 的 kind 过滤，AA 艺术行/帖头/空行不参与）；点结果跳到该章该行并闪烁行号；
   关键词 + 结果 + 列表滚动位置存 sessionStorage（阅读器翻章是整页跳转，靠它保留结果）。
   本段自带 IIFE：只用 window.READER_CFG / window.__aaWin（同章跳转走它，省一次整页刷新）。 */
(function () {
  const modal = document.getElementById('search-modal');
  const btn = document.getElementById('search-toggle');
  if (!modal || !btn) return;
  const CFG = window.READER_CFG || {};
  const qEl = document.getElementById('sp-q');
  const statEl = document.getElementById('sp-stat');
  const listEl = document.getElementById('sp-list');
  const badgeEl = document.getElementById('sp-badge');
  const scopeEl = document.getElementById('sp-scope');
  const KEY = 'aa_reader_search_' + (CFG.work || '');

  let st = { q: '', scope: 'both', total: 0, truncated: false, ms: 0, scanned: 0, items: [], open: false, scroll: 0 };
  try { Object.assign(st, JSON.parse(sessionStorage.getItem(KEY) || '{}')); } catch (e) {}
  const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify(st)); } catch (e) {} };
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function hl(text, q) {                     // 命中片段包 <mark>（先转义再拼，避免 XSS）
    const t = String(text == null ? '' : text);
    if (!q) return esc(t);
    const i = t.toLowerCase().indexOf(q.toLowerCase());
    if (i < 0) return esc(t);
    return esc(t.slice(0, i)) + '<mark>' + esc(t.slice(i, i + q.length)) + '</mark>' + esc(t.slice(i + q.length));
  }

  function setScope(v) {
    st.scope = v;
    [...scopeEl.querySelectorAll('button')].forEach(b => b.classList.toggle('on', b.dataset.scope === v));
    save();
  }

  function render() {
    const n = st.items.length;
    badgeEl.textContent = st.total ? String(st.total) : '';
    badgeEl.classList.toggle('on', !!st.total);
    if (!st.q) { statEl.textContent = ''; listEl.innerHTML = '<div class="sp-empty">输入关键词后回车：全库搜索文本/混排行（中日文都可）。</div>'; return; }
    const here = st.cur && st.cur.startsWith(CFG.base + ':');
    statEl.textContent = `关键词「${st.q}」· 命中 ${st.total} 行 · 扫 ${st.scanned} 章 · ${(st.ms / 1000).toFixed(1)}s`
      + (st.truncated ? `（只显示前 ${n} 条，请换更具体的关键词）` : '')
      + (here ? ` · 已跳到${st.curLabel || ''}（点结果可继续跳，关闭弹窗读正文）` : '');
    if (!n) { listEl.innerHTML = '<div class="sp-empty">没有命中（艺术行/帖头/空行不参与搜索）。</div>'; return; }
    let out = '', last = null;
    for (const it of st.items) {             // 结果已按章节顺序送回，这里只做分组标题
      if (it.base !== last) {
        last = it.base;
        out += `<div class="sp-ch">${it.chapter_no}. ${esc(it.label)} <span class="cnt">· ${esc(it.base)}</span></div>`;
      }
      const zh = it.zh ? `<span class="zh">${hl(it.zh, st.q)}</span>` : '';
      const jp = it.jp ? `<span class="jp">${hl(it.jp, st.q)}</span>` : '';
      out += `<button type="button" class="sp-hit${st.cur === it.base + ':' + it.idx ? ' cur' : ''}" data-base="${esc(it.base)}" data-idx="${it.idx}"`
        + ` data-file="${esc(it.tr_file || it.file)}" title="跳到本章第 ${it.idx} 行">`
        + `<span class="no">${it.idx}</span>${zh}${jp}</button>`;
    }
    listEl.innerHTML = out;
  }

  function open() {
    st.open = true; save();
    modal.classList.remove('hidden');
    qEl.value = st.q || '';
    setScope(st.scope || 'both');
    render();
    listEl.scrollTop = st.scroll || 0;
    qEl.focus(); qEl.select();
  }
  function close() { st.open = false; save(); modal.classList.add('hidden'); }

  function jump(it) {
    st.scroll = listEl.scrollTop;
    const idx = parseInt(it.idx, 10) || 0;
    st.cur = it.base + ':' + idx;
    st.curLabel = '第 ' + idx + ' 行';
    save();
    const win = window.__aaWin;
    if (it.base === CFG.base && win && win.scrollToLine) {   // 同章：不刷新页面
      close();
      win.scrollToLine(idx, true);
      win.flashLine(idx, 2600);
      return;
    }
    // 跨章：整页跳转（URL line= 由服务端交给渲染器定位）。结果留在 sessionStorage，
    // 到新章后弹窗自动恢复且列表里标出当前命中——「跳转不同章节保留搜索结果」。
    location.href = '/work/' + encodeURIComponent(CFG.work) + '/read?f='
      + encodeURIComponent(it.tr_file || it.file) + '&line=' + idx;
  }

  async function run() {
    const q = (qEl.value || '').trim();
    if (!q) { statEl.textContent = '请输入关键词'; qEl.focus(); return; }
    if (q !== st.q) { st.cur = ''; st.curLabel = ''; }   // 换关键词 → 上一次的「已跳到」标记作废
    st.q = q; st.open = true; save();
    statEl.textContent = '搜索中…（大作品首次建索引约 10 秒，之后同一作品 1~3 秒）';
    listEl.innerHTML = '';
    const url = '/api/work/' + encodeURIComponent(CFG.work) + '/find?q=' + encodeURIComponent(q)
      + '&scope=' + encodeURIComponent(st.scope || 'both');
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const j = await r.json();
      st.items = j.items || []; st.total = j.total || 0; st.truncated = !!j.truncated;
      st.ms = j.ms || 0; st.scanned = j.scanned || 0; st.scroll = 0;
      render(); save();
    } catch (e) {
      statEl.textContent = '搜索失败：' + e;
    }
  }

  btn.addEventListener('click', () => (st.open ? close() : open()));
  document.getElementById('sp-go').addEventListener('click', run);
  document.getElementById('sp-close').addEventListener('click', close);
  qEl.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); run(); } });
  scopeEl.addEventListener('click', e => {
    const b = e.target.closest('button[data-scope]');
    if (!b) return;
    setScope(b.dataset.scope);
    if (st.q && qEl.value.trim() === st.q) run();      // 已有结果 → 换范围立刻重搜
  });
  listEl.addEventListener('click', e => {
    const b = e.target.closest('.sp-hit');
    if (b) jump(b.dataset);
  });
  modal.addEventListener('click', e => { if (e.target === modal) close(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) { close(); return; }
    if (e.key === '/' && modal.classList.contains('hidden')
        && !e.target.closest('input, textarea, [contenteditable]')) { e.preventDefault(); open(); }
  });

  if (st.q) render();                                  // 翻章回来：徽章/状态先显示，点按钮即见结果
  if (st.q && st.open) open();                         // 上次是开着弹窗跳转的 → 自动恢复
})();
