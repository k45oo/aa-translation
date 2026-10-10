/* 通用：toast + 转义 */
function toast(msg, ok = true) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (ok ? '' : ' err');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('show'), 2600);
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

/* ===================== 通用分页（2026-09-13） =====================
   章节表（作品页/翻译任务/校对任务）与阅读页目录共用：每页 10/50/100 切换。
   - **只改 display，不动 DOM、不碰勾选状态** → 「全选」天然跨页生效（选择状态跟行绑定）
   - 每页大小按 key 记 localStorage（各页/各作品各自记）
   - activeOf 命中时自动跳到该行所在页（阅读页目录 = 当前章，切换章节后不会被翻页挡住）
   用法: aaPager({rows:'#tbl tbody tr', controls:'#pager', key:'work:'+workId,
                  activeOf: el => el.classList.contains('active'),
                  symbols: true,     // 翻页按钮只显示符号 ‹ ›（文字进 title 提示）
                  sizeLast: true})   // 「每页 N 章」排到页数后面（配 flex-direction:column 即显示在下方）
*/
function aaPager(opts) {
  const rows = (typeof opts.rows === 'string'
    ? [...document.querySelectorAll(opts.rows)] : (opts.rows || []));
  const box = (typeof opts.controls === 'string'
    ? document.querySelector(opts.controls) : opts.controls);
  if (!box || !rows.length) return null;
  const SIZES = opts.sizes || [10, 50, 100];
  const SMALL = Math.min(...SIZES);
  if (rows.length <= SMALL) { box.innerHTML = ''; return null; }   // 少于一页 → 不显示分页条
  const KEY = 'aa-page-size:' + (opts.key || location.pathname);
  let size = parseInt(localStorage.getItem(KEY) || '', 10);
  if (!SIZES.includes(size)) size = opts.defaultSize || SIZES[Math.min(1, SIZES.length - 1)];
  let page = 0;
  const pages = () => Math.max(1, Math.ceil(rows.length / size));

  box.classList.add('pager');
  // symbols: 阅读页窄侧栏只留 ‹ ›（文字放 title）；sizeLast: 「每页 N 章」排到页数之后
  const SYM = !!opts.symbols;
  const sizeHTML =
    '<span class="pager-size muted small">每页 <select class="pager-sel">' +
      SIZES.map(s => '<option value="' + s + '"' + (s === size ? ' selected' : '') + '>' + s + '</option>').join('') +
    '</select> 章</span>';
  const navHTML =
    '<span class="pager-nav">' +
      '<button type="button" class="btn tiny pager-prev"' + (SYM ? ' title="上一页"' : '') + '>' + (SYM ? '‹' : '‹ 上一页') + '</button>' +
      '<span class="pager-info muted small"></span>' +
      '<button type="button" class="btn tiny pager-next"' + (SYM ? ' title="下一页"' : '') + '>' + (SYM ? '›' : '下一页 ›') + '</button>' +
    '</span>';
  box.innerHTML = opts.sizeLast ? (navHTML + sizeHTML) : (sizeHTML + navHTML);
  const sel = box.querySelector('.pager-sel');
  const info = box.querySelector('.pager-info');
  const prev = box.querySelector('.pager-prev');
  const next = box.querySelector('.pager-next');

  function render() {
    const np = pages();
    if (page > np - 1) page = np - 1;
    if (page < 0) page = 0;
    const from = page * size, to = from + size;
    rows.forEach((r, i) => { r.style.display = (i >= from && i < to) ? '' : 'none'; });
    info.textContent = '第 ' + (page + 1) + ' / ' + np + ' 页（共 ' + rows.length + ' 章）';
    prev.disabled = page === 0;
    next.disabled = page >= np - 1;
    if (opts.onRender) opts.onRender();
  }
  const activeIdx = () => rows.findIndex(r => opts.activeOf && opts.activeOf(r));
  function go(i) { page = Math.max(0, Math.min(i, pages() - 1)); render(); }

  sel.addEventListener('change', () => {
    size = parseInt(sel.value, 10) || size;
    localStorage.setItem(KEY, String(size));
    const a = activeIdx();
    page = a >= 0 ? Math.floor(a / size) : 0;
    render();
  });
  prev.addEventListener('click', () => go(page - 1));
  next.addEventListener('click', () => go(page + 1));

  const ai = activeIdx();
  if (ai >= 0) page = Math.floor(ai / size);
  render();
  // visible() = 当前页可见行（「本页全选/全不选」用；只读 display，不改选择状态）
  return {go: go, pageOf: () => page, size: () => size, rows: rows,
          visible: () => rows.filter(r => r.style.display !== 'none')};
}

/* ===================== 任务消耗预估（2026-09-21） =====================
   翻译控制台 / 校对控制台：勾选章节后显示「大约烧多少 token、多少钱」。
   数据来自 GET /api/work/<work>/estimate（零 API；口径与实测系数见 aa_web_app/estimate.py）。
   用法: const est = aaEstimateBox({box:'#est-box', work: workId, type:'translate',
                                   eids: () => [...勾选的 eid...],
                                   force: () => checkbox, free: () => bool});
         est.refresh();   // 勾选/开关变化后调用（内部去抖 400ms + 丢弃过期响应）
*/
function aaFmtTok(n) {
  n = n || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return Math.round(n / 1e3) + 'K';
  return String(n);
}
function aaFmtYuan(v) { return (v >= 0.01 || v === 0) ? v.toFixed(2) : v.toFixed(4); }

function aaEstText(j) {
  if (!j || !j.ok) return '<span class="bad">预估消耗：失败</span> ' + escapeHtml((j && j.error) || '');
  if (j.matched === 0) return '<span class="warn">预估消耗：所选章节在本作品原文里找不到（eid 不匹配）</span>';
  if (!j.chapters) return '预估消耗：所选章节都已完成（增量跳过 ' + j.skipped + ' 章）→ 约 0 次调用';
  const bits = [];
  bits.push('处理 <b>' + j.chapters + '</b> 章' + (j.skipped ? '（跳过 ' + j.skipped + '）' : ''));
  bits.push('输入 ≈<b>' + aaFmtTok(j.prompt) + '</b> tok · 输出 ≈<b>' + aaFmtTok(j.completion) + '</b> tok');
  if (j.free) bits.push('免费模型 → <b>¥0</b>');
  else if (j.priced) {
    // 模型多选（2026-09-22）：金额按勾选集合的主力端点算；集合内价差大时给区间
    const n = (j.models || []).length;
    bits.push('≈<b>¥' + aaFmtYuan(j.cost) + '</b>（无缓存）· 命中缓存 ≈¥' + aaFmtYuan(j.cost_cached) +
      ' · 高峰 ≈¥' + aaFmtYuan(j.cost_peak));
    if (n > 1 && j.cost_max > j.cost_min) {
      bits.push('链内 <span class="muted">¥' + aaFmtYuan(j.cost_min) + '–¥' + aaFmtYuan(j.cost_max) + '</span>');
    }
    bits.push('<span class="muted">按 ' + escapeHtml(j.rate || '?') + '</span>');
  } else {
    bits.push('<span class="warn">未定价</span>（单价表缺该项，只计 token）');
  }
  if (j.peak_now) bits.push('<span class="warn">现在是官网高峰时段（单价 ×2）</span>');
  // 2026-09-29 折扣窗口（小米 token plan 每日 00:00-08:00 系数 0.8×）：定时时刻的金额 = cost_at
  const ow = j.off_windows || [];
  if (!j.free && ow.length && j.off_factor) {
    const win = ow.join('、');
    if (j.off_at) bits.push('<span class="good">定时落在折扣时段（' + win + '，×' + j.off_factor + '）→ ≈<b>¥' +
      aaFmtYuan(j.cost_at) + '</b></span>');
    else if (j.at_ts) bits.push('定时时刻不在折扣时段（' + win + '，×' + j.off_factor + '）→ ≈¥' + aaFmtYuan(j.cost_at));
    else if (j.off_now) bits.push('<span class="good">现在是折扣时段（' + win + '，×' + j.off_factor + '）</span>');
    else bits.push('<span class="muted">折扣时段 ' + win + '（×' + j.off_factor + '：定时启动可省 ' +
      Math.round((1 - j.off_factor) * 100) + '%）</span>');
  }
  return '预估消耗：' + bits.join(' · ') + ' <span class="muted">（粗估 ±40%）</span>';
}

function aaEstimateBox(opts) {
  const box = typeof opts.box === 'string' ? document.querySelector(opts.box) : opts.box;
  if (!box) return null;
  let timer = null, seq = 0;
  async function run() {
    const eids = (opts.eids ? opts.eids() : []) || [];
    if (!eids.length) { box.textContent = '预估消耗：未选章节'; box.title = ''; return; }
    box.textContent = '预估消耗：计算中…';
    const my = ++seq;
    const q = new URLSearchParams({ type: opts.type || 'translate', eids: eids.join(',') });
    if (opts.force && opts.force()) q.set('force', '1');
    if (opts.free && opts.free()) q.set('free', '1');
    // 模型多选（2026-09-22）：勾选集 → 金额按勾选的主力端点算（空/未就绪 = 整条链）
    if (opts.models) { const m = opts.models() || []; if (m.length) q.set('models', m.join(',')); }
    // 定时启动（2026-09-29）：传定时时刻 → 后端按该时刻倍率算 cost_at（折扣窗口 0.8×）
    if (opts.at) { const a = opts.at(); if (a) q.set('at', a); }
    try {
      const r = await fetch('/api/work/' + encodeURIComponent(opts.work) + '/estimate?' + q.toString());
      const j = await r.json();
      if (my !== seq) return;                        // 勾选又变了 → 丢弃过期结果
      box.innerHTML = aaEstText(j);
      box.title = (j && j.ok)
        ? ((j.note || '') + '｜系数 ' + j.coef.in + '/' + j.coef.out + ' tok 每源字符｜端点 ' + j.rate
           + ((j.models || []).length ? '｜链（本次任务候选）: ' + j.models.join(' → ') : ''))
        : '';
    } catch (e) {
      if (my === seq) box.textContent = '预估消耗：计算失败';
    }
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(run, 400); }
  return { refresh: schedule, now: run };
}

/* ===================== 付费链模型多选（2026-09-22） =====================
   翻译/校对控制台：模型选「付费链」时可勾选**本任务用哪些端点**（多选，链内仍按序降级）。
   数据来自 GET /api/work/<work>/models（零 API，读 model_config.toml 的链定义 + 单价表）。
   语义：勾选集 == 默认集 → 后端不写 env（= 整条链，含「兜底端点」，与旧行为一致）；
         勾选集不同 → 翻译写 AA_CHAIN / 校对写 AA_REFINE_BACKEND（保持链序）。
   用法: const mp = aaModelPicker({box:'#model-box', work: workId, type:'translate',
                                   visible: () => bool, onChange: fn});
         mp.names();          // → 勾选的**链条目 id**（backend|model；[] = 全部取消 → 整条链）
         mp.show(bool);       // 显示/隐藏（免费链时隐藏）
*/
function aaModelPicker(opts) {
  const box = typeof opts.box === 'string' ? document.querySelector(opts.box) : opts.box;
  if (!box) return null;
  let data = null;
  const chks = () => [...box.querySelectorAll('input.mchk')];

  function render(j) {
    const on = j.selected || j.default || [];      // 默认勾选 = 设置页保存的链（2026-10-10）
    const rows = (j.chain || []).map(function (it) {
      const r = it.rate || {};
      const pr = (r.miss != null && r.out != null)
        ? '¥' + r.miss + '/' + r.out + ' 每M' + (r.hit != null ? '（缓存 ¥' + r.hit + '）' : '')
        : '<span class="warn">未定价</span>';
      const tags = [];
      if (it.cache) tags.push('缓存');
      if (it.fallback_only) tags.push('兜底端点');
      if (!it.r18) tags.push('不接R18');
      if (!it.available) tags.push('缺key');
      return '<label class="mchk-row' + (it.available ? '' : ' muted') + '" title="' + escapeHtml(it.label) + '">' +
        '<input type="checkbox" class="mchk" data-name="' + escapeHtml(it.id || it.backend) + '"' +
        (on.indexOf(it.id || it.backend) >= 0 ? ' checked' : '') +
        (it.available ? '' : ' disabled') + '> ' +
        '<b>' + escapeHtml(it.model || it.backend) + '</b>' +
        ' <span class="muted small">' + escapeHtml(it.backend) + ' · ' + pr +
        (tags.length ? ' · ' + tags.join(' · ') : '') + '</span></label>';
    }).join('');
    const scope = (opts.type === 'proofread') ? '校对阶段' : '翻译+校对阶段';
    box.innerHTML = '<div class="muted small">本任务候选模型（付费链，多选；' + scope + '都用它；不勾 = 用整条链）：' +
      '主力 <b>' + escapeHtml(j.head || '?') + '</b>' +
      (j.r18 ? ' · <span class="bad">R18 作品 → r18 链</span>' : '') +
      (j.custom ? '' : ' · <span class="muted">未自定义 → 勾选 = 整条链</span>') + '</div>' +
      '<div class="mchk-list">' + rows + '</div>' +
      '<div class="muted small">默认勾选 = <a href="/settings">设置页</a>保存的链（改动可在此单任务覆盖）；' +
      '取消勾选 = 从候选链里摘掉（剩下的仍按链序降级）；全勾回默认。</div>';
    chks().forEach(function (c) {
      c.onchange = function () {
        const n = names();
        box.querySelector('.mchk-warn').innerHTML = (!n.length)
          ? '<span class="warn">一个都没勾 → 本次任务用整条链（默认）</span>' : '';
        if (opts.onChange) opts.onChange();
      };
    });
    if (!box.querySelector('.mchk-warn')) {
      box.insertAdjacentHTML('beforeend', '<div class="muted small mchk-warn"></div>');
    }
  }

  function names() { return chks().filter(c => c.checked).map(c => c.dataset.name); }

  const q = new URLSearchParams({ type: opts.type || 'translate' });
  fetch('/api/work/' + encodeURIComponent(opts.work) + '/models?' + q.toString())
    .then(r => r.json())
    .then(function (j) {
      if (!j || !j.ok) { box.innerHTML = '<span class="bad">模型清单加载失败</span> ' + escapeHtml((j && j.error) || ''); return; }
      data = j; render(j);
      if (opts.onChange) opts.onChange();
    })
    .catch(function () { box.innerHTML = '<span class="bad">模型清单加载失败（工作台不可达）</span>'; });

  return {
    names: names,
    head: () => (data ? data.head : ''),
    ready: () => !!data,
    show: (v) => { box.style.display = v ? '' : 'none'; },
  };
}

/* 右上角热加载：POST /api/reload → 成功后刷新当前页（新增作品/改代码后点，免重启） */
document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('hot-reload');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    if (btn.disabled) return;
    btn.disabled = true;
    btn.classList.add('loading');
    btn.textContent = '⟳ 加载中…';
    try {
      const r = await fetch('/api/reload', { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      if (r.ok && j.ok) {
        btn.classList.remove('loading');
        btn.classList.add('done');
        btn.textContent = `✓ 已加载 ${j.works} 作品`;
        toast(`重新加载成功（${j.works} 个作品）`);
        setTimeout(() => location.reload(), 600);
      } else {
        btn.disabled = false;
        btn.classList.remove('loading');
        btn.textContent = '⟳ 重新加载';
        toast('重新加载失败：' + (j.detail || r.status), false);
      }
    } catch (e) {
      btn.disabled = false;
      btn.classList.remove('loading');
      btn.textContent = '⟳ 重新加载';
      toast('重新加载失败：' + e.message, false);
    }
  });
});
