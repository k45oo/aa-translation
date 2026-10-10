/* 工作台「设置」页（方案 doc/方案归档/工作台设置页与配置文件方案_2026-10-08.md 阶段 6）
 * 四区块各自「保存」：密钥 / 模型（翻译·校对）/ 划词（开关+链）/ 代理。
 * 数据全部来自 GET /api/settings（密钥只回掩码），保存走 POST /api/settings。
 * 约定：按钮同行并排、无省略号、不自动展开/滚动。 */
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const S = { state: null, keyEdits: {}, keyClears: {} };

  function toast(msg, ok) {
    const el = $('toast');
    if (!el) return;
    el.textContent = msg;
    el.className = 'toast show' + (ok === false ? ' bad' : '');
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.className = 'toast'; }, 3200);
  }

  function api(path, body) {
    const opt = body ? { method: 'POST', headers: { 'Content-Type': 'application/json' },
                         body: JSON.stringify(body) } : {};
    return fetch(path, opt).then(r => r.json());
  }

  // ---------------- 渲染 ----------------

  function renderEnv(s) {
    $('s-envpath').textContent = s.env.path;
    $('s-envmeta').textContent = s.env.exists
      ? '（已在用；最近保存 ' + new Date(s.env.saved_at * 1000).toLocaleString() + '）'
      : '（还没这个文件，保存任一项会自动新建）';
  }

  function renderKeys(s) {
    const rows = s.keys.map(function (k) {
      const src = { 'env': '真实环境变量', '.env': '.env' }[k.source] || '';
      const state = k.configured
        ? '<span class="ok">已配置</span> <span class="mono">' + esc(k.masked) + '</span>'
        : '<span class="warn">未配置</span>';
      return '<label class="mchk-row" title="' + esc(k.purpose) + '">' +
        '<b>' + esc(k.name) + '</b> ' + state +
        ' <span class="muted small">' + esc(k.purpose) + (src ? ' · 来源 ' + src : '') + '</span>' +
        '<div class="s-keyline">' +
        '<input type="password" class="s-keyinput" data-key="' + esc(k.name) + '" ' +
        'placeholder="粘贴新的 key（留空 = 不改）" autocomplete="new-password" spellcheck="false">' +
        '<button class="btn tiny ghost" data-test="' + esc(k.name) + '">测试连接</button>' +
        '<button class="btn tiny ghost" data-clear="' + esc(k.name) + '">清除</button>' +
        '</div></label>';
    }).join('');
    $('keys-list').innerHTML = rows || '<span class="muted">没有可配置的 key（TOML 里没有 key_env 定义）</span>';
    $('keys-list').querySelectorAll('.s-keyinput').forEach(function (inp) {
      inp.oninput = function () { S.keyEdits[inp.dataset.key] = inp.value; };
    });
    $('keys-list').querySelectorAll('[data-clear]').forEach(function (b) {
      b.onclick = function () {
        const k = b.dataset.clear;
        if (!confirm('清除 ' + k + ' 在当前 .env 里的值？（其它层的值不受影响）')) return;
        api('/api/settings', { section: 'keys', clear: [k] }).then(function (j) {
          toast(j && j.ok ? '已清除 ' + k : '清除失败', !!(j && j.ok));
          if (j && j.ok) refresh();
        });
      };
    });
    $('keys-list').querySelectorAll('[data-test]').forEach(function (b) {
      b.onclick = function () {
        const k = b.dataset.test;
        b.disabled = true;
        const old = b.textContent;
        b.textContent = '测试中…';
        api('/api/settings/test-key', { name: k }).then(function (j) {
          b.disabled = false;
          b.textContent = old;
          toast(k + '：' + (j.ok ? '可用（' + j.backend + '/' + (j.model || '?') + ' → ' + (j.reply || '') + '）'
                                  : (j.error || '测试失败')), !!j.ok);
        }).catch(function () { b.disabled = false; b.textContent = old; toast('测试请求失败', false); });
      };
    });
  }

  function chainBlock(host, blk, kind) {
    const rows = (blk.entries || []).map(function (it) {
      const r = it.rate || {};
      const pr = (r.miss != null && r.out != null)
        ? '¥' + r.miss + '/' + r.out + ' 每M' : '<span class="warn">未定价</span>';
      const tags = [];
      if (it.cache) tags.push('缓存');
      if (it.fallback_only) tags.push('兜底端点');
      if (!it.r18) tags.push('不接R18');
      if (!it.available) tags.push('缺key');
      return '<label class="mchk-row' + (it.available ? '' : ' muted') + '" title="' + esc(it.label) + '">' +
        '<input type="checkbox" class="schk" data-kind="' + kind + '" data-id="' + esc(it.id) + '"' +
        (blk.selected.indexOf(it.id) >= 0 ? ' checked' : '') +
        (it.available ? '' : ' disabled') + '> ' +
        '<b>' + esc(it.model || it.backend) + '</b>' +
        ' <span class="muted small">' + esc(it.backend) + ' · ' + pr +
        (tags.length ? ' · ' + tags.join(' · ') : '') + '</span></label>';
    }).join('');
    const custom = blk.custom ? '<span class="bad">已自定义</span>' : '<span class="muted">默认</span>';
    host.innerHTML = '<div class="muted small">当前：' + custom +
      '（共 ' + (blk.entries || []).length + ' 项；不勾 = 用整条链）</div>' + rows;
  }

  function renderModels(s) {
    chainBlock($('m-translate'), s.models.translate, 'translate');
    chainBlock($('m-proofread'), s.models.proofread, 'proofread');
  }

  function renderSelection(s) {
    $('sel-enabled').checked = !!s.sel_enabled;
    chainBlock($('m-selection'), s.models.selection, 'selection');
  }

  function renderProxy(s) {
    const p = s.proxy;
    if (document.activeElement !== $('proxy-url')) $('proxy-url').value = p.url || '';
    $('proxy-mode').value = p.mode || 'auto';
    $('proxy-detail').innerHTML = 'TOML 默认：<code>' + esc(p.toml_url || '—') + '</code> · <code>' +
      esc(p.toml_mode || 'auto') + '</code> ｜ 当前生效：地址来自 <b>' + esc(p.url_source) +
      '</b>，策略来自 <b>' + esc(p.mode_source) + '</b>';
  }

  function render(s) {
    S.state = s;
    renderEnv(s); renderKeys(s); renderModels(s); renderSelection(s); renderProxy(s);
  }

  function refresh() {
    return api('/api/settings').then(function (j) {
      if (!j || !j.ok) { toast('设置加载失败', false); return; }
      render(j);
    }).catch(function () { toast('设置加载失败（工作台不可达）', false); });
  }

  // ---------------- 保存 ----------------

  function checked(kind) {
    return [...document.querySelectorAll('input.schk[data-kind="' + kind + '"]')]
      .filter(c => c.checked).map(c => c.dataset.id);
  }

  function save(path, body, msgId, done) {
    const el = $(msgId);
    if (el) el.textContent = '保存中…';
    api(path, body).then(function (j) {
      const ok = !!(j && j.ok);
      if (el) el.textContent = ok ? ('已保存并立即生效（在跑的任务不受影响）' + (j.changed && j.changed.length ? '：' + j.changed.join('、') : ''))
                                  : ('保存失败：' + ((j && j.error) || '未知错误'));
      toast(ok ? '已保存' : '保存失败', ok);
      if (ok) { refresh(); if (done) done(j); }
    }).catch(function (e) {
      if (el) el.textContent = '保存失败：' + e;
      toast('保存失败', false);
    });
  }

  // ---------------- 绑定 ----------------

  $('keys-save').onclick = function () {
    const set = {};
    Object.keys(S.keyEdits).forEach(function (k) {
      if (S.keyEdits[k] && S.keyEdits[k].trim()) set[k] = S.keyEdits[k].trim();
    });
    if (!Object.keys(set).length) { toast('没有改动（留空 = 不改）', false); return; }
    save('/api/settings', { section: 'keys', set: set }, 'keys-msg', function () {
      S.keyEdits = {};
      document.querySelectorAll('.s-keyinput').forEach(i => { i.value = ''; });
    });
  };

  $('models-save').onclick = function () {
    save('/api/settings', { section: 'models',
                            translate: checked('translate'),
                            proofread: checked('proofread') }, 'models-msg');
  };
  $('models-reset').onclick = function () {
    const s = S.state;
    document.querySelectorAll('input.schk[data-kind="translate"]').forEach(function (c) {
      c.checked = s.models.translate.default.indexOf(c.dataset.id) >= 0;
    });
    document.querySelectorAll('input.schk[data-kind="proofread"]').forEach(function (c) {
      c.checked = s.models.proofread.default.indexOf(c.dataset.id) >= 0;
    });
    $('models-msg').textContent = '已回到当前默认勾选（点「保存模型选择」生效）';
  };

  $('sel-save').onclick = function () {
    save('/api/settings', { section: 'selection', sel_enabled: $('sel-enabled').checked,
                            sel_chain: checked('selection') }, 'sel-msg');
  };
  $('sel-reset').onclick = function () {
    const s = S.state;
    document.querySelectorAll('input.schk[data-kind="selection"]').forEach(function (c) {
      c.checked = s.models.selection.default.indexOf(c.dataset.id) >= 0;
    });
    $('sel-msg').textContent = '已回到当前默认勾选（点「保存划词设置」生效）';
  };

  $('proxy-save').onclick = function () {
    save('/api/settings', { section: 'proxy', proxy_url: $('proxy-url').value,
                            proxy_mode: $('proxy-mode').value }, 'proxy-msg');
  };
  $('proxy-clear').onclick = function () {
    save('/api/settings', { section: 'proxy', clear_url: true, proxy_mode: $('proxy-mode').value },
         'proxy-msg');
  };
  $('proxy-test').onclick = function () {
    const b = $('proxy-test');
    b.disabled = true;
    const old = b.textContent;
    b.textContent = '测试中…';
    api('/api/settings/test-proxy', {}).then(function (j) {
      b.disabled = false;
      b.textContent = old;
      const reach = (ok, msg) => (ok ? '✓ 通了（' + msg + '；缺 key 时 401 属正常）' : '✗ 不通：' + msg);
      const lines = [
        '代理端口：' + (j.local ? '✓ ' : '✗ ') + j.local_msg,
        '不走代理到付费端点：' + (j.direct_ok ? reach(true, j.direct_msg) : reach(false, j.direct_msg)),
        '经代理到付费端点：' + (j.proxy_ok === null ? '— ' + j.proxy_msg
                                                    : (j.proxy_ok ? reach(true, j.proxy_msg) : reach(false, j.proxy_msg))),
      ];
      $('proxy-msg').innerHTML = lines.map(esc).join('<br>');
    }).catch(function (e) {
      b.disabled = false; b.textContent = old;
      $('proxy-msg').textContent = '测试失败：' + e;
    });
  };

  refresh();
})();
