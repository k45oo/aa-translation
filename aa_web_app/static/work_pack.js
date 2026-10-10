/* 作品导出对话框（2026-10-01）
   一处实现，两个入口共用：作品页动作条「导出作品」+ 作品列表「更多」菜单「导出」。
   用法：aaExportDialog(workId, title)   —— 依赖 app.js 的 toast / escapeHtml。
   后端：GET /api/work/<id>/export-preview · POST /api/work/<id>/export ·
        GET /api/work/<id>/export/status · GET /api/work/<id>/export/download?f= */
(function () {
  let modal = null, els = null, timer = null, preview = null, curWork = null;

  const fmt = n => (n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : (n / 1024).toFixed(0) + ' KB');

  function build() {
    modal = document.createElement('div');
    modal.className = 'modal hidden';
    modal.innerHTML = `
      <div class="modal-box" style="width:720px;max-width:94vw">
        <h3>导出作品 — <span id="aexp-title"></span></h3>
        <div id="aexp-preview" class="muted small">读取打包清单…</div>
        <div class="dm-sec" style="margin:8px 0">
          <label style="display:flex;gap:6px;align-items:center">
            <input type="checkbox" id="aexp-raw">
            <span>包含原文 HTML（<code>raw/</code>，可从原站重下；勾选后包会大很多）</span>
          </label>
        </div>
        <p class="muted small">不含：work 过程文件与缓存（ckpt / 术语候选中间态 / 行类型缓存 / 统计）、
          日志与记录（runner_log、edits.jsonl、替换历史、width_report）、备份（<code>*.bak*</code>、<code>backup_*</code>）。</p>
        <div class="actions">
          <button class="btn" id="aexp-start">开始导出</button>
          <button class="btn ghost" id="aexp-close">关闭</button>
        </div>
        <div id="aexp-status" class="muted small" style="margin-top:6px"></div>
        <div id="aexp-done"></div>
      </div>`;
    document.body.appendChild(modal);
    const $ = id => modal.querySelector('#' + id);
    els = { title: $('aexp-title'), preview: $('aexp-preview'), raw: $('aexp-raw'),
            status: $('aexp-status'), done: $('aexp-done'), start: $('aexp-start'), close: $('aexp-close') };
    modal.addEventListener('mousedown', e => { modal._d = e.target; });
    modal.addEventListener('click', e => { if (e.target === modal && modal._d === modal) close(); });
    els.close.addEventListener('click', close);
    els.raw.addEventListener('change', render);
    els.start.addEventListener('click', start);
  }

  function stopPoll() { if (timer) { clearInterval(timer); timer = null; } }
  function close() { stopPoll(); if (modal) modal.classList.add('hidden'); }

  function render() {
    if (!preview) return;
    const withRaw = els.raw.checked;
    const files = withRaw ? preview.with_raw_files : preview.files;
    const bytes = withRaw ? preview.with_raw_bytes : preview.bytes;
    let h = `<div class="del-row">作品根：<code>${escapeHtml(preview.root || '—')}</code></div>`;
    h += `<div class="del-row">本次打包：<b>${files}</b> 个文件 / <b>${fmt(bytes)}</b>（rar 常规压缩）</div>`;
    if (preview.raw_files) {
      h += `<div class="del-row muted small">raw/ 原文 HTML：${preview.raw_files} 个 / ${fmt(preview.raw_bytes)}${withRaw ? '（已含在本次包里）' : '（未含，可勾选上面的选项带上）'}</div>`;
    }
    h += `<div class="del-row">work 人工资产 ${preview.assets.length} 项：<span class="mono">${escapeHtml(preview.assets.slice(0, 12).join(', ')) || '（无）'}</span>${preview.assets.length > 12 ? ' …' : ''}</div>`;
    h += `<div class="del-row muted small">work 过程/记录/备份文件已排除：${preview.skipped.length} 项</div>`;
    if ((preview.html || []).length) h += `<div class="del-row">交付合集：<span class="mono">${escapeHtml(preview.html.join(', '))}</span></div>`;
    h += `<div class="del-row muted small">导出目录：<code>${escapeHtml(preview.export_dir)}</code></div>`;
    if (!preview.rar_ok) h += `<div class="del-row warn">${escapeHtml(preview.error || '未找到 Rar.exe')}</div>`;
    if (preview.busy) h += `<div class="del-row warn">该作品有运行中的翻译/校对任务，等它结束再导出（防止打包到写了一半的文件）</div>`;
    els.preview.innerHTML = h;
    els.start.disabled = !preview.rar_ok || preview.busy;
  }

  async function fetchPreview() {
    try {
      const r = await fetch('/api/work/' + encodeURIComponent(curWork) + '/export-preview');
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || '预览失败');
      preview = j; render();
    } catch (e) { els.preview.innerHTML = `<span class="warn">${escapeHtml(e.message)}</span>`; }
  }

  function loadPreview() {          // 打开弹窗：清掉上次状态再拉清单
    els.preview.textContent = '读取打包清单…';
    els.done.innerHTML = ''; els.status.textContent = '';
    els.start.textContent = '开始导出'; els.start.disabled = false;
    fetchPreview();
  }

  function poll() {
    stopPoll();
    timer = setInterval(async () => {
      try {
        const r = await fetch('/api/work/' + encodeURIComponent(curWork) + '/export/status');
        const j = await r.json();
        if (j.state === 'running') {
          els.status.textContent = '打包中… ' + ((j.log || []).slice(-2).join(' / ') || '');
        } else if (j.state === 'done') {
          stopPoll(); els.start.disabled = false; els.start.textContent = '重新导出';
          els.status.textContent = `完成：${j.files} 个文件 → ${fmt(j.size)}（${j.secs}s）`;
          els.done.innerHTML = `<div class="actions"><a class="btn" href="/api/work/${encodeURIComponent(curWork)}/export/download?f=${encodeURIComponent(j.name)}">下载 ${escapeHtml(j.name)}</a></div>`
            + `<div class="muted small">也可直接在 <code>${escapeHtml(j.export_dir || '')}</code> 里取。</div>`;
          toast('导出完成 ✓');
          fetchPreview();     // 只刷新清单体积，别把下载链接/状态文字清掉
        } else if (j.state === 'error') {
          stopPoll(); els.start.disabled = false; els.start.textContent = '重试导出';
          els.status.innerHTML = `<span class="warn">导出失败：${escapeHtml(j.error || '')}</span>`;
        }
      } catch (e) { /* 轮询失败下次再试 */ }
    }, 1200);
  }

  async function start() {
    els.start.disabled = true; els.start.textContent = '导出中…';
    els.done.innerHTML = ''; els.status.textContent = '启动中…';
    try {
      const r = await fetch('/api/work/' + encodeURIComponent(curWork) + '/export', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ include_raw: els.raw.checked })
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || '启动失败');
      poll();
    } catch (e) {
      els.start.disabled = false; els.start.textContent = '开始导出';
      els.status.innerHTML = `<span class="warn">${escapeHtml(e.message)}</span>`;
    }
  }

  window.aaExportDialog = function (work, title) {
    if (!modal) build();
    curWork = work;
    els.title.textContent = title || work;
    modal.classList.remove('hidden');
    loadPreview();
  };
})();
