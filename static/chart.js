/* 财富终端 · 基金 — 自研 SVG 折线图（零依赖，完全离线）
 * 用法：LineChart.render(el, data, opts)
 *   data: [{date: 'YYYY-MM-DD', value: number}, ...]
 *   opts: { valueFmt?: (v:number)=>string }
 */
(function () {
  'use strict';

  var H = 320; // 固定高度
  var PAD = { top: 22, right: 62, bottom: 34, left: 62 };

  // 取“整齐”步长（1/2/5 × 10^k）
  function niceNum(range, round) {
    if (!(range > 0) || !isFinite(range)) return 1;
    var exp = Math.floor(Math.log(range) / Math.LN10);
    var frac = range / Math.pow(10, exp);
    var nf;
    if (round) nf = frac < 1.5 ? 1 : frac < 3 ? 2 : frac < 7 ? 5 : 10;
    else nf = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10;
    return nf * Math.pow(10, exp);
  }

  // 计算 Y 轴范围：保证恰好 5 条网格线（4 个间隔）
  function computeScale(minV, maxV) {
    var lo = minV;
    var hi = maxV;
    if (!(hi > lo)) {
      var d = Math.abs(lo) * 0.05;
      if (!(d > 0)) d = 0.5;
      lo -= d;
      hi += d;
    }
    var step = niceNum((hi - lo) / 4, true);
    for (var guard = 0; guard < 12; guard++) {
      // 相对容差，避免大指数下的浮点误差导致步长偏大
      var qLo = lo / step;
      var qHi = hi / step;
      var bIdx = Math.floor(qLo + 1e-7 * (Math.abs(qLo) + 1));
      var tIdx = Math.ceil(qHi - 1e-7 * (Math.abs(qHi) + 1));
      if (tIdx - bIdx <= 4) {
        return { min: bIdx * step, max: bIdx * step + 4 * step, step: step };
      }
      step = niceNum(step * 1.5, true);
    }
    return { min: lo, max: hi, step: (hi - lo) / 4 };
  }

  function stepDecimals(step) {
    if (step >= 1) return 0;
    return Math.min(4, Math.max(1, Math.ceil(-Math.log(step) / Math.LN10 - 1e-9)));
  }

  // X 轴约 6 个日期标签：n<=6 全取，否则取首尾 + 中间均匀
  function xLabelIndices(n) {
    if (n <= 6) {
      var all = [];
      for (var i = 0; i < n; i++) all.push(i);
      return all;
    }
    var idx = [];
    for (var k = 0; k < 6; k++) {
      var v = Math.round((k * (n - 1)) / 5);
      if (idx.indexOf(v) === -1) idx.push(v);
    }
    return idx;
  }

  var uidCounter = 0;

  function render(el, data, opts) {
    opts = opts || {};
    var points = (data || []).filter(function (d) {
      return d && d.date != null && isFinite(Number(d.value));
    }).map(function (d) {
      return { date: String(d.date), value: Number(d.value) };
    });

    // 解除旧 resize 监听（同一容器重复 render）
    if (el.__lcResize) {
      window.removeEventListener('resize', el.__lcResize);
      el.__lcResize = null;
    }
    el.innerHTML = '';

    if (!points.length) {
      var empty = document.createElement('div');
      empty.className = 'lc-empty';
      empty.textContent = '暂无数据';
      el.appendChild(empty);
      return;
    }

    el.__lcPoints = points;
    el.__lcOpts = opts;
    draw(el, points, opts);

    var raf = 0;
    el.__lcResize = function () {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(function () {
        raf = 0;
        if (el.__lcPoints) draw(el, el.__lcPoints, el.__lcOpts || {});
      });
    };
    window.addEventListener('resize', el.__lcResize);
  }

  function draw(el, points, opts) {
    var W = el.clientWidth;
    if (!(W > 0)) W = 640; // 容器隐藏时兜底
    W = Math.max(W, 360);
    var n = points.length;

    var minV = Infinity;
    var maxV = -Infinity;
    for (var i = 0; i < n; i++) {
      if (points[i].value < minV) minV = points[i].value;
      if (points[i].value > maxV) maxV = points[i].value;
    }
    var scale = computeScale(minV, maxV);
    var min = scale.min;
    var max = scale.max;
    var span = max - min;
    var plotW = W - PAD.left - PAD.right;
    var plotH = H - PAD.top - PAD.bottom;

    function xAt(i) {
      return n === 1 ? PAD.left + plotW / 2 : PAD.left + (plotW * i) / (n - 1);
    }
    function yAt(v) {
      return PAD.top + (1 - (v - min) / span) * plotH;
    }

    var fmt = opts.valueFmt || function (v) { return v.toFixed(2); };
    var dec = stepDecimals(scale.step);
    var gid = 'lc-grad-' + (++uidCounter);

    var svg = '';
    svg += '<svg class="lc-svg" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg">';
    svg += '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="rgba(37,99,235,0.22)"></stop>' +
      '<stop offset="1" stop-color="rgba(37,99,235,0)"></stop>' +
      '</linearGradient></defs>';

    // Y 轴：5 条网格线 + 左右刻度标签
    for (var t = 0; t < 5; t++) {
      var tv = min + (span * t) / 4;
      var ty = yAt(tv);
      svg += '<line x1="' + PAD.left + '" y1="' + ty + '" x2="' + (W - PAD.right) + '" y2="' + ty + '" stroke="#e5e7eb" stroke-width="1"></line>';
      svg += '<text x="' + (PAD.left - 8) + '" y="' + (ty + 4) + '" text-anchor="end" class="lc-ylabel">' + tv.toFixed(dec) + '</text>';
      svg += '<text x="' + (W - PAD.right + 8) + '" y="' + (ty + 4) + '" text-anchor="start" class="lc-ylabel">' + tv.toFixed(dec) + '</text>';
    }

    // X 轴：约 6 个日期标签
    var labels = xLabelIndices(n);
    for (var li = 0; li < labels.length; li++) {
      var pi = labels[li];
      svg += '<text x="' + xAt(pi) + '" y="' + (H - PAD.bottom + 22) + '" text-anchor="middle" class="lc-xlabel">' + points[pi].date + '</text>';
    }

    // 折线 + 渐变填充
    var pts = [];
    for (var j = 0; j < n; j++) pts.push(xAt(j).toFixed(2) + ',' + yAt(points[j].value).toFixed(2));
    var first = pts[0].split(',');
    var last = pts[n - 1].split(',');
    svg += '<path d="M ' + pts.join(' L ') + ' L ' + last[0] + ' ' + (H - PAD.bottom) + ' L ' + first[0] + ' ' + (H - PAD.bottom) + ' Z" fill="url(#' + gid + ')" stroke="none"></path>';
    svg += '<polyline points="' + pts.join(' ') + '" fill="none" stroke="#2563eb" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></polyline>';

    // 末端点 + 当前值标注
    var lx = xAt(n - 1);
    var ly = yAt(points[n - 1].value);
    svg += '<circle cx="' + lx + '" cy="' + ly + '" r="3.5" fill="#2563eb"></circle>';
    var anchor = lx > W - PAD.right - 70 ? 'end' : 'start';
    var tx = anchor === 'end' ? lx - 8 : lx + 8;
    var tyy = (ly - 10 < PAD.top + 12) ? ly + 18 : ly - 10;
    svg += '<text x="' + tx + '" y="' + tyy + '" text-anchor="' + anchor + '" class="lc-endval">' + fmt(points[n - 1].value) + '</text>';

    // hover 用元素（十字线 + 高亮点）
    svg += '<line id="lc-cross" x1="0" x2="0" y1="' + PAD.top + '" y2="' + (H - PAD.bottom) + '" stroke="#94a3b8" stroke-width="1" stroke-dasharray="4 3" style="display:none"></line>';
    svg += '<circle id="lc-dot" r="4" fill="#ffffff" stroke="#2563eb" stroke-width="2" style="display:none"></circle>';
    svg += '</svg>';
    svg += '<div class="lc-tip" style="display:none"></div>';

    el.innerHTML = svg;

    var svgEl = el.querySelector('svg');
    var cross = el.querySelector('#lc-cross');
    var dot = el.querySelector('#lc-dot');
    var tipEl = el.querySelector('.lc-tip');

    function showAt(i) {
      var px = xAt(i);
      var py = yAt(points[i].value);
      cross.setAttribute('x1', px);
      cross.setAttribute('x2', px);
      cross.style.display = '';
      dot.setAttribute('cx', px);
      dot.setAttribute('cy', py);
      dot.style.display = '';
      tipEl.textContent = points[i].date + '　' + fmt(points[i].value);
      tipEl.style.display = 'block';
      // 定位：不溢出容器
      var tw = tipEl.offsetWidth;
      var th = tipEl.offsetHeight;
      var txp = px + 14;
      if (txp + tw > W - 4) txp = px - 14 - tw;
      if (txp < 4) txp = 4;
      var typ = py - th - 12;
      if (typ < 4) typ = py + 12;
      if (typ + th > H - 4) typ = H - th - 4;
      tipEl.style.left = txp + 'px';
      tipEl.style.top = typ + 'px';
    }
    function hide() {
      cross.style.display = 'none';
      dot.style.display = 'none';
      tipEl.style.display = 'none';
    }

    svgEl.addEventListener('mousemove', function (e) {
      var rect = svgEl.getBoundingClientRect();
      if (!(rect.width > 0)) return;
      var mx = e.clientX - rect.left;
      var i;
      if (n === 1) {
        i = 0;
      } else {
        i = Math.round((mx - PAD.left) / (plotW / (n - 1)));
        if (i < 0) i = 0;
        if (i > n - 1) i = n - 1;
      }
      showAt(i);
    });
    svgEl.addEventListener('mouseleave', hide);
  }

  window.LineChart = { render: render };
})();
