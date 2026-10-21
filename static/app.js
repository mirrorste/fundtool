/* 财富终端 · 基金 — 前端主逻辑（原生 JS，ES2019 兼容，零外部依赖）
 * 路由：#portfolio（默认） / #rank / #fund/{code}
 */
(function () {
  'use strict';

  // ================= 基础工具 =================
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // 数字格式：净值/百分比 2 位小数；金额千分位
  function fmtMoney(v) {
    if (v == null || isNaN(v)) return '—';
    return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function fmtNum(v, d) {
    if (v == null || isNaN(v)) return '—';
    return Number(v).toFixed(d == null ? 2 : d);
  }
  function fmtPct(v) {
    if (v == null || isNaN(v)) return '—';
    return Number(v).toFixed(2) + '%';
  }
  function pctClass(v) {
    if (v == null || isNaN(v)) return '';
    return Number(v) > 0 ? 'up' : (Number(v) < 0 ? 'down' : '');
  }
  function signed(v, fmt) {
    if (v == null || isNaN(v)) return '—';
    return (Number(v) > 0 ? '+' : '') + fmt(Number(v));
  }
  function pctCell(v) {
    return '<td class="num ' + pctClass(v) + '">' + fmtPct(v) + '</td>';
  }

  // ================= API 封装 =================
  // 非 2xx：解析 {"error"}（或 422 的 detail[].msg）并抛出带 status 的错误
  async function api(path, opts) {
    opts = opts || {};
    var init = { method: opts.method || 'GET', headers: {} };
    if (opts.body != null) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    var res = await fetch(path, init);
    var data = null;
    try { data = await res.json(); } catch (e) { /* 非 JSON 响应 */ }
    if (!res.ok) {
      var msg = null;
      if (data && typeof data === 'object') {
        if (data.error) msg = String(data.error);
        else if (Array.isArray(data.detail)) {
          msg = data.detail
            .map(function (d) { return d && d.msg ? String(d.msg) : ''; })
            .filter(Boolean)
            .join('；');
        }
      }
      var err = new Error(msg || ('HTTP ' + res.status));
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return { data: data, headers: res.headers, status: res.status };
  }

  // ================= 全局状态 =================
  var FUND_TYPES = ['全部', '股票型', '混合型', '债券型', '指数型', 'QDII', 'LOF', 'FOF'];
  var SORT_FIELDS = [
    ['daily', '日增长率'], ['1w', '近1周'], ['1m', '近1月'], ['3m', '近3月'], ['6m', '近6月'],
    ['1y', '近1年'], ['2y', '近2年'], ['3y', '近3年'], ['ytd', '今年来'], ['since', '成立来']
  ];

  var state = {
    health: null,
    portfolio: [],
    portfolioSort: { field: 'market_value', order: 'desc' },   // 持仓表排序（市值降序默认）
    rank: { type: '全部', sort: '1m', order: 'desc', kw: '', page: 1, size: 20 },
    fund: { code: null, range: '1y', normalized: false }
  };
  var navSeriesCache = [];
  var fundCodeLoaded = null;
  // 请求序号：live 模式下首屏大查询很慢，过期响应不得覆盖更新的结果
  var rankReqSeq = 0;
  var navReqSeq = 0;
  var infoReqSeq = 0;

  // ================= 顶栏状态 / 横幅 / toast =================
  function showBanner() { $('#error-banner').classList.remove('hidden'); }
  function hideBanner() { $('#error-banner').classList.add('hidden'); }

  async function loadHealth() {
    var el = $('#data-status');
    try {
      var r = await api('/api/health');
      state.health = r.data;
      el.innerHTML = r.data && r.data.mode === 'mock'
        ? '<span class="badge badge-mock">MOCK 演示数据</span>'
        : '<span class="badge badge-live">实时数据</span>';
      hideBanner();
    } catch (e) {
      el.innerHTML = '<span class="badge badge-error">数据源异常</span>';
      showBanner();
    }
  }

  function toast(msg, type) {
    var c = $('#toast-container');
    var t = document.createElement('div');
    t.className = 'toast' + (type ? ' toast-' + type : '');
    t.textContent = msg;
    c.appendChild(t);
    requestAnimationFrame(function () { t.classList.add('show'); });
    setTimeout(function () {
      t.classList.remove('show');
      setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 300);
    }, 3200);
  }

  // 错误占位（503 统一文案）
  function errorBoxHtml(e, retryKey) {
    var msg = (e && e.status === 503)
      ? '数据源暂不可达，请稍后重试。'
      : ('加载失败：' + (e && e.message ? e.message : '未知错误'));
    var html = '<div class="error-box"><span>' + esc(msg) + '</span>';
    if (retryKey) html += '<button class="btn btn-sm" type="button" data-retry="' + esc(retryKey) + '">重试</button>';
    return html + '</div>';
  }

  // 统一重试按钮（委托）
  function bindRetry() {
    document.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('button[data-retry]') : null;
      if (!b) return;
      var key = b.getAttribute('data-retry');
      if (key === 'portfolio') renderPortfolio();
      else if (key === 'rank') fetchRank();
      else if (key === 'fund-info' && state.fund.code) loadFundInfo(state.fund.code);
      else if (key === 'nav' && state.fund.code) loadNavSeries(state.fund.code);
    });
  }

  // ================= 路由 =================
  var TAB_IDS = { portfolio: 'tab-portfolio', rank: 'tab-rank', fund: 'tab-fund' };

  function parseRoute() {
    var h = location.hash || '';
    var m = h.match(/^#fund\/([0-9A-Za-z]+)$/);
    if (m) return { tab: 'fund', code: m[1] };
    if (h.indexOf('#rank') === 0) return { tab: 'rank', code: null };
    if (h.indexOf('#fund') === 0) return { tab: 'fund', code: null };
    return { tab: 'portfolio', code: null };
  }

  function showTab(tab) {
    Object.keys(TAB_IDS).forEach(function (k) {
      $('#' + TAB_IDS[k]).classList.toggle('hidden', k !== tab);
    });
    $all('.tab-link').forEach(function (a) {
      a.classList.toggle('active', a.getAttribute('data-tab') === tab);
    });
  }

  function onRoute() {
    var r = parseRoute();
    showTab(r.tab);
    if (r.tab === 'portfolio') renderPortfolio();
    else if (r.tab === 'rank') fetchRank();
    else renderFund(r.code);
  }

  // ================= 持仓页 =================
  function infoItem(label, valueHtml) {
    return '<div class="info-item"><div class="info-label">' + label + '</div><div class="info-value">' + valueHtml + '</div></div>';
  }

  async function renderPortfolio() {
    var tbody = $('#portfolio-tbody');
    tbody.innerHTML = '<tr><td colspan="9" class="loading">加载中…</td></tr>';
    ['stat-mv', 'stat-cost', 'stat-pnl', 'stat-pnl-pct'].forEach(function (id) {
      var el = document.getElementById(id);
      el.textContent = '…';
      el.className = 'stat-value';
    });
    $('#portfolio-updated').textContent = '';
    try {
      var r = await api('/api/portfolio');
      var d = r.data || {};
      state.portfolio = d.holdings || [];
      var s = d.summary || {};
      $('#stat-mv').textContent = fmtMoney(s.total_mv);
      $('#stat-cost').textContent = fmtMoney(s.total_cost);
      var pnlEl = $('#stat-pnl');
      pnlEl.textContent = signed(s.total_pnl, fmtMoney);
      pnlEl.className = 'stat-value ' + pctClass(s.total_pnl);
      var pctEl = $('#stat-pnl-pct');
      pctEl.textContent = signed(s.total_pnl_pct, fmtPct);
      pctEl.className = 'stat-value ' + pctClass(s.total_pnl_pct);
      $('#portfolio-updated').textContent = d.updated_at ? '更新：' + d.updated_at : '';

      if (!state.portfolio.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="empty">暂无持仓，点右上角「添加持仓」开始</td></tr>';
        updateSortIndicators();
        return;
      }
      state.portfolio.sort(function (a, b) {
        var f = state.portfolioSort.field;
        var av = a[f], bv = b[f];
        // null/stale 行沉底（无论升降序）
        var an = (av == null), bn = (bv == null);
        if (an && bn) return 0;
        if (an) return 1;
        if (bn) return -1;
        return (av < bv ? -1 : av > bv ? 1 : 0) * (state.portfolioSort.order === 'asc' ? 1 : -1);
      });
      updateSortIndicators();
      tbody.innerHTML = state.portfolio.map(portfolioRowHtml).join('');
    } catch (e) {
      if (e.status === 503) showBanner();
      tbody.innerHTML = '<tr><td colspan="9">' + errorBoxHtml(e, 'portfolio') + '</td></tr>';
    }
  }

  function portfolioRowHtml(h) {
    var stale = h.stale === true;
    return '<tr data-code="' + esc(h.code || '') + '" class="' + (stale ? 'stale-row' : '') + '">' +
      '<td>' + esc(h.code) + '</td>' +
      '<td class="rank-name">' + esc(h.name || '—') + '</td>' +
      '<td class="num">' + fmtMoney(h.shares) + '</td>' +
      '<td class="num">' + fmtNum(h.cost_nav, 4) + '</td>' +
      '<td class="num">' + (stale ? '<span class="stale-badge">旧</span>—' : fmtNum(h.nav, 4)) + '</td>' +
      '<td class="num">' + fmtMoney(h.market_value) + '</td>' +
      '<td class="num ' + pctClass(h.pnl) + '">' + signed(h.pnl, fmtMoney) + '</td>' +
      '<td class="num ' + pctClass(h.pnl_pct) + '">' + signed(h.pnl_pct, fmtPct) + '</td>' +
      '<td><div class="row-ops">' +
        '<button class="btn-icon" type="button" data-act="edit" data-id="' + esc(h.id) + '" title="编辑" aria-label="编辑">✎</button>' +
        '<button class="btn-icon btn-icon-danger" type="button" data-act="del" data-id="' + esc(h.id) + '" title="删除" aria-label="删除">🗑</button>' +
      '</div></td>' +
      '</tr>';
  }

  function bindPortfolioEvents() {
    $('#btn-add-holding').addEventListener('click', function () { openHoldingModal(null); });
    $('#portfolio-tbody').addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('button[data-act]') : null;
      if (btn) {
        var id = Number(btn.getAttribute('data-id'));
        if (btn.getAttribute('data-act') === 'edit') {
          var h = null;
          for (var i = 0; i < state.portfolio.length; i++) {
            if (state.portfolio[i].id === id) { h = state.portfolio[i]; break; }
          }
          openHoldingModal(h);
        } else if (btn.getAttribute('data-act') === 'del') {
          deleteHolding(id);
        }
        return;
      }
      // 点击行 → 跳转详情页（编辑/删除按钮以外）
      var tr = e.target.closest ? e.target.closest('tr[data-code]') : null;
      if (tr) {
        var code = tr.getAttribute('data-code');
        if (code) location.hash = '#fund/' + code;
      }
    });
    // 表头排序点击
    document.querySelectorAll('#tab-portfolio .sort-th').forEach(function (th) {
      th.addEventListener('click', function () {
        var f = th.getAttribute('data-sort');
        if (!f) return;
        if (state.portfolioSort.field === f) {
          state.portfolioSort.order = state.portfolioSort.order === 'desc' ? 'asc' : 'desc';
        } else {
          state.portfolioSort.field = f;
          state.portfolioSort.order = 'desc';
        }
        renderPortfolio();   // 重新 fetch + sort + 渲染（保留 sort 状态）
      });
    });
  }

  function updateSortIndicators() {
    document.querySelectorAll('.sort-th').forEach(function (th) {
      var f = th.getAttribute('data-sort');
      var ind = th.querySelector('.sort-ind');
      if (!ind) return;
      if (state.portfolioSort.field === f) {
        th.classList.add('sort-active');
        ind.textContent = state.portfolioSort.order === 'desc' ? '▾' : '▴';
      } else {
        th.classList.remove('sort-active');
        ind.textContent = '';
      }
    });
  }

  function deleteHolding(id) {
    var h = null;
    for (var i = 0; i < state.portfolio.length; i++) {
      if (state.portfolio[i].id === id) { h = state.portfolio[i]; break; }
    }
    var label = h ? (h.code + (h.name ? ' ' + h.name : '')) : ('ID ' + id);
    if (!confirm('确定删除持仓「' + label + '」吗？')) return;
    api('/api/portfolio/' + id, { method: 'DELETE' })
      .then(function () {
        toast('已删除持仓');
        renderPortfolio();
      })
      .catch(function (e) {
        toast('删除失败：' + e.message, 'error');
        if (e.status === 503) showBanner();
      });
  }

  // ================= 持仓弹窗 =================
  var modalMode = 'add';
  var modalEditId = null;

  function openHoldingModal(h) {
    modalMode = h ? 'edit' : 'add';
    modalEditId = h ? h.id : null;
    $('#modal-title').textContent = h ? '编辑持仓' : '添加持仓';
    $('#f-code').value = h ? h.code : '';
    $('#f-shares').value = h ? h.shares : '';
    $('#f-cost').value = h ? h.cost_nav : '';
    $('#f-name').value = h ? (h.name || '') : '';
    $('#f-note').value = h ? (h.note || '') : '';
    // 默认「按市值+盈亏」模式（新增和编辑均适用）
    $('#f-mode').value = 'value';
    $('#f-mode').disabled = false;
    $('#f-mv').value = h && h.market_value ? h.market_value : '';
    $('#f-pnl-type').value = 'pct';
    $('#f-pnl-pct').value = h && h.pnl_pct != null ? h.pnl_pct : '';
    $('#f-pnl-amt').value = '';
    syncModalMode();
    hideModalError();
    $('#modal-overlay').classList.remove('hidden');
    setTimeout(function () { $('#f-code').focus(); }, 30);
  }

  // 按录入方式/盈亏口径切换可见字段
  function syncModalMode() {
    hideModalError();
    var byValue = $('#f-mode').value === 'value';
    var pct = $('#f-pnl-type').value === 'pct';
    $('#row-shares').classList.toggle('hidden', byValue);
    $('#row-cost').classList.toggle('hidden', byValue);
    $('#row-mv').classList.toggle('hidden', !byValue);
    $('#row-pnl-type').classList.toggle('hidden', !byValue);
    $('#row-pnl-pct').classList.toggle('hidden', !byValue || !pct);
    $('#row-pnl-amt').classList.toggle('hidden', !byValue || pct);
  }

  function closeModal() { $('#modal-overlay').classList.add('hidden'); }

  function showModalError(msg) {
    var el = $('#modal-error');
    el.textContent = msg;
    el.classList.remove('hidden');
  }
  function hideModalError() { $('#modal-error').classList.add('hidden'); }

  function onSubmitHolding(e) {
    e.preventDefault();
    var code = $('#f-code').value.trim();
    var name = $('#f-name').value.trim();
    var note = $('#f-note').value.trim();
    var byValue = modalMode === 'add' && $('#f-mode').value === 'value';
    var body = { fund_code: code, fund_name: name, note: note };

    if (!/^\d{6}$/.test(code)) { showModalError('基金代码必须为 6 位数字'); return; }
    if (name.length > 50) { showModalError('基金名称过长（≤50 字符）'); return; }
    if (note.length > 50) { showModalError('备注过长（≤50 字符）'); return; }

    if (byValue) {
      var mvRaw = $('#f-mv').value.trim();
      var mv = Number(mvRaw);
      if (!mvRaw || !isFinite(mv) || mv <= 0) { showModalError('当前市值必须为正数'); return; }
      body.market_value = mv;
      if ($('#f-pnl-type').value === 'pct') {
        var pctRaw = $('#f-pnl-pct').value.trim();
        var pct = Number(pctRaw);
        if (!pctRaw || !isFinite(pct)) { showModalError('请填写持有盈亏率'); return; }
        if (pct <= -100) { showModalError('持有盈亏率必须大于 -100%'); return; }
        body.pnl_pct = pct;
      } else {
        var amtRaw = $('#f-pnl-amt').value.trim();
        var amt = Number(amtRaw);
        if (!amtRaw || !isFinite(amt)) { showModalError('请填写持有盈亏额'); return; }
        if (amt >= mv) { showModalError('持有盈亏额必须小于当前市值'); return; }
        body.pnl_amount = amt;
      }
    } else {
      var shares = Number($('#f-shares').value);
      var cost = Number($('#f-cost').value);
      if (!isFinite(shares) || shares <= 0) { showModalError('份额必须为正数'); return; }
      if (!isFinite(cost) || cost <= 0) { showModalError('成本净值必须为正数'); return; }
      body.shares = shares;
      body.cost_nav = cost;
    }

    var req = (modalMode === 'add')
      ? api('/api/portfolio', { method: 'POST', body: body })
      : api('/api/portfolio/' + modalEditId, { method: 'PUT', body: body });
    var msg = modalMode === 'add' ? '已添加持仓' : '已更新持仓';

    $('#modal-submit').disabled = true;
    req.then(function () {
      closeModal();
      toast(msg);
      renderPortfolio();
    }).catch(function (err) {
      if (err.status === 409) showModalError(err.message || '该基金已在持仓中');
      else if (err.status === 422) showModalError(err.message || '字段校验未通过，请检查输入');
      else if (err.status === 404) showModalError(err.message || '持仓不存在，请刷新页面');
      else if (err.status === 503) { showBanner(); showModalError('数据源暂不可达，请稍后重试'); }
      else showModalError('保存失败：' + err.message);
    }).then(function () {
      $('#modal-submit').disabled = false;
    });
  }

  function bindModalEvents() {
    $('#modal-close').addEventListener('click', closeModal);
    $('#modal-cancel').addEventListener('click', closeModal);
    $('#modal-overlay').addEventListener('click', function (e) {
      if (e.target === this) closeModal();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !$('#modal-overlay').classList.contains('hidden')) closeModal();
    });
    $('#holding-form').addEventListener('submit', onSubmitHolding);
    $('#f-mode').addEventListener('change', syncModalMode);
    $('#f-pnl-type').addEventListener('change', syncModalMode);
  }

  // ================= 排行页 =================
  function fillRankControls() {
    var typeSel = $('#rank-type');
    typeSel.innerHTML = FUND_TYPES.map(function (t) {
      return '<option value="' + esc(t) + '"' + (t === state.rank.type ? ' selected' : '') + '>' + esc(t) + '</option>';
    }).join('');
    var sortSel = $('#rank-sort');
    sortSel.innerHTML = SORT_FIELDS.map(function (f) {
      return '<option value="' + esc(f[0]) + '"' + (f[0] === state.rank.sort ? ' selected' : '') + '>' + esc(f[1]) + '</option>';
    }).join('');
  }

  async function fetchRank() {
    var seq = ++rankReqSeq;
    var s = state.rank;
    var tbody = $('#rank-tbody');
    tbody.innerHTML = '<tr><td colspan="15" class="loading">加载中…</td></tr>';
    $('#rank-fetched').textContent = '';
    var p = new URLSearchParams();
    p.set('type', s.type);
    p.set('sort', s.sort);
    p.set('order', s.order);
    if (s.kw) p.set('kw', s.kw);
    p.set('page', String(s.page));
    p.set('size', String(s.size));
    try {
      var r = await api('/api/rank?' + p.toString());
      if (seq !== rankReqSeq) return;  // 已有更新的查询，丢弃本次结果
      var d = r.data || {};
      var rows = Array.isArray(d.rows) ? d.rows : [];
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="15" class="empty">' +
          (s.kw ? '没有匹配「' + esc(s.kw) + '」的基金' : '暂无排行数据') + '</td></tr>';
      } else {
        tbody.innerHTML = rows.map(function (row, i) {
          return '<tr class="rank-row" data-code="' + esc(row.code) + '">' +
            '<td class="num muted">' + ((s.page - 1) * s.size + i + 1) + '</td>' +
            '<td>' + esc(row.code) + '</td>' +
            '<td class="rank-name">' + esc(row.name) + '</td>' +
            '<td class="muted">' + esc(row.date) + '</td>' +
            '<td class="num">' + fmtNum(row.nav, 4) + '</td>' +
            pctCell(row.daily_pct) +
            pctCell(row.w1) + pctCell(row.m1) + pctCell(row.m3) + pctCell(row.m6) +
            pctCell(row.y1) + pctCell(row.y2) + pctCell(row.y3) +
            pctCell(row.ytd) + pctCell(row.since) +
            '</tr>';
        }).join('');
      }
      var total = Number(d.total) || 0;
      var totalPages = Math.max(1, Math.ceil(total / s.size));
      if (s.page > totalPages) s.page = totalPages;
      $('#rank-page-info').textContent = '第 ' + s.page + ' / ' + totalPages + ' 页（共 ' + total + ' 只）';
      $('#rank-prev').disabled = s.page <= 1;
      $('#rank-next').disabled = s.page >= totalPages;
      $('#rank-fetched').textContent = d.fetched_at ? '数据时间：' + d.fetched_at : '';
    } catch (e) {
      if (seq !== rankReqSeq) return;
      if (e.status === 503) showBanner();
      tbody.innerHTML = '<tr><td colspan="15">' + errorBoxHtml(e, 'rank') + '</td></tr>';
    }
  }

  function bindRankEvents() {
    $('#rank-query').addEventListener('click', function () {
      state.rank.type = $('#rank-type').value;
      state.rank.sort = $('#rank-sort').value;
      state.rank.order = $('#rank-order').value;
      state.rank.kw = $('#rank-kw').value.trim();
      state.rank.page = 1;
      fetchRank();
    });
    $('#rank-kw').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); $('#rank-query').click(); }
    });
    $('#rank-prev').addEventListener('click', function () {
      if (state.rank.page > 1) { state.rank.page--; fetchRank(); }
    });
    $('#rank-next').addEventListener('click', function () {
      state.rank.page++;
      fetchRank();
    });
    $('#rank-tbody').addEventListener('click', function (e) {
      var tr = e.target.closest ? e.target.closest('tr[data-code]') : null;
      if (tr) location.hash = '#fund/' + tr.getAttribute('data-code');
    });
  }

  // ================= 详情页：搜索 =================
  var searchTimer = null;
  var searchResults = [];
  var searchActive = -1;

  function searchOpen() { return !$('#fund-suggestions').classList.contains('hidden'); }

  function closeSuggestions() {
    searchActive = -1;
    $('#fund-suggestions').classList.add('hidden');
    $('#fund-search').setAttribute('aria-expanded', 'false');
  }

  function renderSuggestions(results) {
    searchResults = results || [];
    searchActive = -1;
    var box = $('#fund-suggestions');
    if (!searchResults.length) {
      box.innerHTML = '<div class="suggestion-item muted">没有匹配的基金</div>';
    } else {
      box.innerHTML = searchResults.map(function (r, i) {
        return '<div class="suggestion-item" data-i="' + i + '">' +
          '<span class="s-code">' + esc(r.code) + '</span>' +
          '<span class="s-name">' + esc(r.name) + '</span>' +
          '<span class="s-type muted">' + esc(r.type || '') + '</span>' +
          '</div>';
      }).join('');
    }
    box.classList.remove('hidden');
    $('#fund-search').setAttribute('aria-expanded', 'true');
  }

  function updateActiveSuggestion() {
    $all('#fund-suggestions .suggestion-item').forEach(function (el, i) {
      el.classList.toggle('active', i === searchActive);
    });
  }

  function selectFund(code) {
    closeSuggestions();
    location.hash = '#fund/' + code;
  }

  function bindFundSearch() {
    var input = $('#fund-search');
    input.addEventListener('input', function () {
      var kw = input.value.trim();
      if (searchTimer) clearTimeout(searchTimer);
      if (!kw) { closeSuggestions(); return; }
      searchTimer = setTimeout(function () {
        api('/api/fund/search?kw=' + encodeURIComponent(kw))
          .then(function (r) { renderSuggestions((r.data && r.data.results) || []); })
          .catch(function (e) {
            closeSuggestions();
            if (e.status === 503) { showBanner(); toast('搜索暂不可用：数据源暂不可达', 'error'); }
          });
      }, 250);
    });

    input.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { closeSuggestions(); return; }
      if (!searchOpen() || !searchResults.length) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        var n = searchResults.length;
        searchActive = (searchActive + (e.key === 'ArrowDown' ? 1 : -1) + n) % n;
        updateActiveSuggestion();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        var r = searchResults[searchActive >= 0 ? searchActive : 0];
        if (r) selectFund(r.code);
      }
    });

    $('#fund-suggestions').addEventListener('click', function (e) {
      var item = e.target.closest ? e.target.closest('.suggestion-item[data-i]') : null;
      if (!item) return;
      var r = searchResults[Number(item.getAttribute('data-i'))];
      if (r) selectFund(r.code);
    });

    // 点击搜索框以外区域关闭建议
    document.addEventListener('click', function (e) {
      if (!searchOpen()) return;
      if (!$('#search-box').contains(e.target)) closeSuggestions();
    });
  }

  // ================= 详情页：渲染 =================
  function syncFundControls() {
    $all('#nav-range-group .seg-btn').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-range') === state.fund.range);
    });
    $('#nav-normalized').checked = state.fund.normalized;
  }

  function renderFund(code) {
    state.fund.code = code;
    if (code !== fundCodeLoaded) {
      fundCodeLoaded = code;
      state.fund.range = '1y';
      state.fund.normalized = false;
      navSeriesCache = [];
    }
    $('#fund-search').value = code || '';
    syncFundControls();
    closeSuggestions();

    // 详情页签链接指向当前基金，便于回跳
    var fundTab = $('.tab-link[data-tab="fund"]');
    if (fundTab) fundTab.setAttribute('href', code ? '#fund/' + code : '#fund');

    var wrap = $('#fund-info-wrap');
    var navCard = $('#nav-card');
    if (!code) {
      wrap.innerHTML = '<div class="card"><div class="empty">在上方搜索框输入基金代码或名称，查看基金详情</div></div>';
      navCard.classList.add('hidden');
      $('#nav-chart').innerHTML = '';
      return;
    }
    navCard.classList.remove('hidden');
    loadFundInfo(code);
    loadNavSeries(code);
  }

  async function loadFundInfo(code) {
    var seq = ++infoReqSeq;
    var wrap = $('#fund-info-wrap');
    wrap.innerHTML = '<div class="card"><div class="loading">加载基金信息…</div></div>';
    try {
      var r = await api('/api/fund/' + encodeURIComponent(code) + '/info');
      if (seq !== infoReqSeq) return;
      var d = r.data || {};
      var p = d.periods || {};
      var purchase = d.purchase || null;
      var feeHtml = purchase
        ? esc(purchase.fee_rate) + (purchase.discount ? ' <span class="muted" style="font-size:12px">（折扣 ' + esc(purchase.discount) + ' 折）</span>' : '')
        : '—';
      wrap.innerHTML =
        '<div class="card info-card">' +
        '<div class="info-title">' + esc(d.name || d.code) +
          '<span class="muted">' + esc(d.code) + (d.type ? ' · ' + esc(d.type) : '') + '</span></div>' +
        '<div class="info-grid">' +
        infoItem('净值日期', esc(d.date || '—')) +
        infoItem('单位净值', fmtNum(d.nav, 4)) +
        infoItem('累计净值', fmtNum(d.cum_nav, 4)) +
        infoItem('基金类型', esc(d.type || '—')) +
        infoItem('申购费率', feeHtml) +
        infoItem('数据来源', d.source === 'nav_hist' ? '净值历史' : '排行快照') +
        '</div>' +
        '<div class="period-table-wrap"><table class="data-table period-table"><thead><tr>' +
        '<th>日增</th><th>近1周</th><th>近1月</th><th>近3月</th><th>近6月</th>' +
        '<th>近1年</th><th>近2年</th><th>近3年</th><th>今年来</th><th>成立来</th>' +
        '</tr></thead><tbody><tr>' +
        pctCell(p.daily_pct) + pctCell(p.w1) + pctCell(p.m1) + pctCell(p.m3) + pctCell(p.m6) +
        pctCell(p.y1) + pctCell(p.y2) + pctCell(p.y3) + pctCell(p.ytd) + pctCell(p.since) +
        '</tr></tbody></table></div>' +
        '</div>';
    } catch (e) {
      if (seq !== infoReqSeq) return;
      if (e.status === 404) {
        wrap.innerHTML = '<div class="card"><div class="empty">未找到该基金：' + esc(code) +
          '<br><span class="muted" style="font-size:12px">请重新搜索确认基金代码</span></div></div>';
      } else {
        if (e.status === 503) showBanner();
        wrap.innerHTML = '<div class="card">' + errorBoxHtml(e, 'fund-info') + '</div>';
      }
    }
  }

  function drawNavChart() {
    var box = $('#nav-chart');
    var data = (navSeriesCache || []).map(function (s) {
      return { date: s.date, value: state.fund.normalized ? s.normalized : s.nav };
    });
    LineChart.render(box, data, {
      valueFmt: function (v) { return v.toFixed(2); }
    });
  }

  async function loadNavSeries(code) {
    var seq = ++navReqSeq;
    var box = $('#nav-chart');
    var staleNote = $('#nav-stale-note');
    box.innerHTML = '<div class="loading">加载净值走势…</div>';
    staleNote.classList.add('hidden');
    try {
      var r = await api('/api/fund/' + encodeURIComponent(code) + '/nav?range=' + encodeURIComponent(state.fund.range));
      if (seq !== navReqSeq) return;
      staleNote.classList.toggle('hidden', !(r.headers && r.headers.get('X-Nav-Stale') === '1'));
      navSeriesCache = (r.data && r.data.series) || [];
      drawNavChart();
    } catch (e) {
      if (seq !== navReqSeq) return;
      staleNote.classList.add('hidden');
      if (e.status === 404) {
        box.innerHTML = '<div class="empty">' + esc(e.message || '该基金暂无净值数据') + '</div>';
      } else {
        if (e.status === 503) showBanner();
        box.innerHTML = errorBoxHtml(e, 'nav');
      }
    }
  }

  function bindFundChartEvents() {
    $('#nav-range-group').addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('.seg-btn') : null;
      if (!btn || !state.fund.code) return;
      state.fund.range = btn.getAttribute('data-range');
      syncFundControls();
      loadNavSeries(state.fund.code);
    });
    $('#nav-normalized').addEventListener('change', function (e) {
      state.fund.normalized = e.target.checked;
      if (state.fund.code) drawNavChart(); // series 同时含 nav 与 normalized，本地重绘即可
    });
  }

  // ================= 初始化 =================
  function init() {
    fillRankControls();
    bindPortfolioEvents();
    bindRankEvents();
    bindFundSearch();
    bindFundChartEvents();
    bindModalEvents();
    bindRetry();
    window.addEventListener('hashchange', onRoute);
    loadHealth();
    onRoute();
  }

  init();
})();
