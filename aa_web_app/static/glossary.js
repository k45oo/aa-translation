/* 术语表编辑器：搜索 / 增删改 / 保存（作品块只读，服务端强制） */
(() => {
  const D = window.GLOSSARY_DATA;
  const R = window.ROLES_DATA || { '角色': [] };   // 2026-09-01: 角色块迁出 roles.json
  const state = {
    glossary: D.glossary.map(p => Array.isArray(p) ? { jp: p[0], zh: p[1], cat: '' } : { ...p }),
    roles: R['角色'].map(r => ({ ...r })),
    known: [...D.known_names],
  };

  const $ = s => document.querySelector(s);
  const esc = escapeHtml;

  function setCount() {
    $('#cnt-glossary').textContent = state.glossary.length;
    $('#cnt-roles').textContent = state.roles.length;
    $('#cnt-known').textContent = state.known.length;
  }

  function render() {
    const q = ($('#g-search').value || '').trim();
    const match = v => !q || String(v).includes(q);

    const tgl = $('#tbl-glossary tbody');
    tgl.innerHTML = '';
    state.glossary.forEach((pair, i) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td><input data-i="${i}" data-f="jp" value="${esc(pair.jp)}"></td>
                      <td><input data-i="${i}" data-f="zh" value="${esc(pair.zh)}"></td>
                      <td><input data-i="${i}" data-f="cat" value="${esc(pair.cat || '')}" placeholder="常用-梗/特殊-麻将"></td>
                      <td><button class="btn tiny ghost" data-del="${i}">删</button></td>`;
      tgl.appendChild(tr);
    });
    setCount();
  }

  $('#tbl-glossary tbody').addEventListener('input', e => {
    const t = e.target.dataset;
    if (!t.f) return;
    state.glossary[+t.i][t.f] = e.target.value;
  });
  $('#tbl-glossary tbody').addEventListener('click', e => {
    if (e.target.dataset.del === undefined) return;
    state.glossary.splice(+e.target.dataset.del, 1);
    render();
  });

  $('#add-glossary').addEventListener('click', () => { state.glossary.push({ jp: '', zh: '', cat: '' }); render(); });
  $('#g-search').addEventListener('input', render);

  $('#g-save').addEventListener('click', async () => {
    const btn = $('#g-save');
    btn.disabled = true;
    const msg = $('#g-msg');
    msg.textContent = '保存中…';
    try {
      const r = await fetch('/api/glossary', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ known_names: state.known, glossary: state.glossary, roles: state.roles })
      });
      const j = await r.json();
      if (!r.ok) {
        msg.textContent = '';
        toast('保存失败：' + (j.detail ? JSON.stringify(j.detail) : JSON.stringify(j)), false);
        return;
      }
      msg.textContent = '';
      toast(`已保存 ✓（角色 ${j['角色']} / 替换 ${j['glossary']} / 白名单 ${j['known_names']}，备份 ${j.backup}）`);
    } catch (e) {
      toast('网络错误：' + e, false);
    } finally {
      btn.disabled = false;
    }
  });

  $('#g-backups').addEventListener('click', async () => {
    const box = $('#g-backups-box');
    if (!box.classList.contains('hidden')) { box.classList.add('hidden'); return; }
    const r = await fetch('/api/glossary/backups');
    const j = await r.json();
    if (!j.backups.length) { box.innerHTML = '<p class="muted">暂无备份</p>'; }
    else {
      box.innerHTML = '<h3>术语表备份（保留最近 10 份）</h3><ul>' + j.backups.map(b =>
        `<li><span class="mono">${b.ts}</span> · ${b.files.length} 个文件 · ${(b.size / 1024).toFixed(1)} KB
         <button class="btn tiny ghost" data-rt="${b.ts}">恢复</button></li>`).join('') + '</ul>';
    }
    box.classList.remove('hidden');
  });
  $('#g-backups-box').addEventListener('click', async e => {
    const ts = e.target.dataset.rt;
    if (!ts) return;
    if (!confirm(`恢复 ${ts} 的术语表？将覆盖当前文件`)) return;
    const r = await fetch('/api/glossary/backups/' + ts + '/restore', { method: 'POST' });
    const j = await r.json();
    toast(j.ok ? '已恢复 ✓ 刷新页面生效' : '失败: ' + JSON.stringify(j), j.ok);
    if (j.ok) setTimeout(() => location.reload(), 900);
  });

  render();

  // ---- 作品角色管理弹窗（2026-08-26：全局角色/白名单表隐藏，改为按作品管理） ----
  const rolesModal = $('#roles-modal');
  const rm = { work: '', rows: [] };   // rows: {_idx, _new, 日文, 中文, 作品[], 分类, 备注}

  function rmRender() {
    const tb = $('#tbl-modal-roles tbody');
    tb.innerHTML = '';
    rm.rows.forEach((r, i) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td><input data-i="${i}" data-f="日文" value="${esc(r['日文'] || '')}"></td>
        <td><input data-i="${i}" data-f="中文" value="${esc(r['中文'] || '')}"></td>
        <td><input data-i="${i}" data-f="作品" value="${esc((r['作品'] || []).join(','))}"></td>
        <td><input data-i="${i}" data-f="分类" value="${esc(r['分类'] || '')}"></td>
        <td><input data-i="${i}" data-f="备注" value="${esc(r['备注'] || '')}"></td>
        <td><button class="btn tiny ghost" data-delmr="${i}">删</button></td>`;
      tb.appendChild(tr);
    });
  }

  document.querySelectorAll('button[data-roles]').forEach(btn => {
    btn.addEventListener('click', () => {
      rm.work = btn.dataset.roles;
      $('#roles-modal-title').textContent = `「${rm.work}」`;
      rm.rows = [];
      state.roles.forEach((r, i) => {
        const ws = r['作品'] || [];
        if (ws.includes(rm.work)) rm.rows.push({ _idx: i, ...r });
      });
      rmRender();
      rolesModal.classList.remove('hidden');
      $('#roles-modal-msg').textContent = '';
    });
  });

  $('#tbl-modal-roles tbody').addEventListener('input', e => {
    const t = e.target.dataset;
    if (!t.f) return;
    const r = rm.rows[+t.i];
    if (t.f === '作品') r['作品'] = e.target.value.split(',').map(s => s.trim()).filter(Boolean);
    else r[t.f] = e.target.value;
  });
  $('#tbl-modal-roles tbody').addEventListener('click', e => {
    if (e.target.dataset.delmr === undefined) return;
    rm.rows.splice(+e.target.dataset.delmr, 1);
    rmRender();
  });
  $('#add-modal-role').addEventListener('click', () => {
    rm.rows.push({ _idx: -1, '日文': '', '中文': '', '作品': [rm.work], '分类': '', '备注': '' });
    rmRender();
  });
  $('#roles-modal-close').addEventListener('click', () => rolesModal.classList.add('hidden'));
  let _rmDown = null;
  rolesModal.addEventListener('mousedown', e => { _rmDown = e.target; });
  rolesModal.addEventListener('click', e => { if (e.target === rolesModal && _rmDown === rolesModal) rolesModal.classList.add('hidden'); });

  $('#roles-modal-save').addEventListener('click', async () => {
    const btn = $('#roles-modal-save');
    const msg = $('#roles-modal-msg');
    // 合并回 state.roles：按作品 id 整体替换（旧条目全移除，弹窗内容全量写回）
    // 2026-08-26: 原按 _idx 移除会漏掉"上次保存已入库的新增角色"（_idx=-1 无索引标记）
    state.roles = state.roles.filter(r => !(r['作品'] || []).includes(rm.work));
    const cleaned = rm.rows.map(r => ({ '日文': (r['日文'] || '').trim(), '中文': (r['中文'] || '').trim(),
      '作品': (r['作品'] || []).filter(Boolean), '分类': (r['分类'] || '').trim(), '备注': (r['备注'] || '').trim() }));
    // 过滤空行（日文/中文都空）
    cleaned.forEach((c, i) => { if (!c['日文'] && !c['中文']) return; state.roles.push(c); });
    btn.disabled = true;
    msg.textContent = '保存中…';
    try {
      const r = await fetch('/api/glossary', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ known_names: state.known, glossary: state.glossary, roles: state.roles })
      });
      const j = await r.json();
      if (!r.ok) {
        btn.disabled = false;
        msg.textContent = '保存失败: ' + (j.detail && j.detail.errors ? j.detail.errors.join('；') : JSON.stringify(j));
        return;
      }
      msg.textContent = `已保存 ${j['角色']} 角色 ✓`;
      setCount();
      btn.disabled = false;
      setTimeout(() => { rolesModal.classList.add('hidden'); toast('角色已保存 ✓'); }, 800);
    } catch (e) { msg.textContent = '网络错误: ' + e; btn.disabled = false; }
  });
})();
