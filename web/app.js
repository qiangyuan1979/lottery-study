/* 彩票预估 · 前端应用
 * 原生 JS 单页应用：哈希路由 + 无构建 + 无外部依赖
 * 页面：首页 / 下期预测 / 统计分析 / 开奖数据 / 预测盈亏 / 模拟收益 / 理论方法
 */
'use strict';

/* ============================================================
 * 常量
 * ============================================================ */
const CODES = ['ssq', 'dlt', 'qlc', 'kl8', '3d', 'qxc'];
const NAME = { ssq: '双色球', dlt: '超级大乐透', qlc: '七乐彩', kl8: '快乐8', '3d': '福彩3D', qxc: '七星彩' };
// 复式注数（仅用于前端投入估算；真实注数以服务端 parlay.bets 为准）
const PAR = { ssq: 14, dlt: 18, qlc: 8, kl8: 11, '3d': 8, qxc: 64 };
const SIDE = { ssq: ['红球', '蓝球'], dlt: ['前区', '后区'], qlc: ['基本号', ''], kl8: ['选号', ''] };
const POS = {
  '3d': ['百位', '十位', '个位'],
  qxc: ['第1位', '第2位', '第3位', '第4位', '第5位', '第6位', '第7位'],
};
const MODE_LABEL = { random: '随机选号', system: '系统预测', custom: '自选号码' };
const NAV = [
  ['home', '首页'],
  ['predict', '下期预测'],
  ['analysis', '统计分析'],
  ['draws', '开奖数据'],
  ['pnl', '预测盈亏'],
  ['sim', '模拟收益'],
  ['theory', '理论方法'],
];
const PICKABLE = { predict: 1, analysis: 1, draws: 1 };
const LOT = {}; // code -> meta

/* ============================================================
 * 基础工具
 * ============================================================ */
const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

function esc(v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
function isDigit(code) {
  const m = LOT[code];
  return m ? m.type === 'digit' : (code === '3d' || code === 'qxc');
}
function isNum(v) { return v != null && v !== '' && isFinite(Number(v)); }
function num(v, d) {
  if (!isNum(v)) return '—';
  const n = Number(v);
  return n.toLocaleString('zh-CN', { minimumFractionDigits: d == null ? 2 : d, maximumFractionDigits: d == null ? 2 : d });
}
function money(v) {
  if (!isNum(v)) return '0.00';
  return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function signed(v) {
  if (!isNum(v)) return '—';
  const n = Number(v);
  return (n > 0 ? '+' : n < 0 ? '−' : '') + money(Math.abs(n));
}
function signedMoney(v) {
  if (!isNum(v)) return '—';
  const n = Number(v);
  return (n > 0 ? '+¥ ' : n < 0 ? '−¥ ' : '¥ ') + money(Math.abs(n));
}
function pct(v, d) {
  if (!isNum(v)) return '—';
  return (Number(v) * 100).toFixed(d == null ? 1 : d) + '%';
}
function prob(v) {
  if (!isNum(v)) return '—';
  const n = Number(v);
  if (n === 0) return '0';
  if (n >= 0.01) return (n * 100).toFixed(2) + '%';
  if (n >= 1e-6) return (n * 100).toFixed(4) + '%';
  return n.toExponential(2);
}
function rng(pair) {
  if (!Array.isArray(pair) || pair.length < 2) return '—';
  return num(pair[0], 1) + ' – ' + num(pair[1], 1);
}
function shortNum(v) {
  if (!isNum(v)) return '—';
  const n = Number(v);
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(1) + '亿';
  if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + '万';
  return num(n, 0);
}
function fmtAxis(v) {
  const n = Number(v);
  if (!isFinite(n)) return '';
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(1) + '亿';
  if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + '万';
  if (Math.abs(n) >= 100) return String(Math.round(n));
  if (Math.abs(n) >= 1) return n.toFixed(0);
  return n.toFixed(2);
}
function signCls(v) {
  if (!isNum(v)) return '';
  const n = Number(v);
  return n > 0 ? 'pos' : n < 0 ? 'neg' : '';
}
function weekdayOf(d) {
  if (!d) return '';
  const t = new Date(d + 'T00:00:00');
  return isNaN(t) ? '' : '日一二三四五六'[t.getDay()];
}

/* ============================================================
 * 术语提示词典（悬停/点击显示）
 * ============================================================ */
const TIPS = {
  z: 'z 分数：把观测频数与理论期望的偏差除以标准差，|z|>2 约为 5% 显著性水平。',
  chi2: '卡方统计量：衡量各号码实际出现次数与理论期望次数的偏离程度，越大越偏离均匀。',
  p: 'p 值：在原假设（完全随机）成立时，出现当前或更极端结果的概率。p<0.05 才称“显著”。',
  fdr: 'BH-FDR：Benjamini–Hochberg 多重检验校正。同时检 80 个号码时，约 4 个会“假显著”，校正后控制误判率。',
  ewma: '指数加权移动平均：越近的期数权重越大，用于捕捉近期热度变化。',
  dirichlet: 'Dirichlet–多项分布：给每个号码一个先验强度，把历史频率向理论概率收缩，避免小样本极端。',
  mc: '蒙特卡洛：按“完全随机”规则模拟数万次，得到和值、跨度等指标的理论零分布。',
  boot: 'Bootstrap：从历史数据有放回重采样，估计统计量的抽样波动与置信区间。',
  hyper: '超几何分布：给定选号个数与开奖个数，命中 m 个号码的理论概率。',
  omit: '遗漏：某号码连续未开出的期数。当前遗漏大不代表“该出了”（赌徒谬误）。',
  kelly: '凯利公式：f* = (bp − q)/b。负期望赌局的最优下注比例为 0。',
  ret: '返奖率：奖金总额 ÷ 销售额。我国乐透型彩票通常在 50% 上下。',
  parlay: '复式投注：在同一票上多选号码，按组合展开为多注，投入与命中机会同时放大。',
  trans: '转移概率：上一期出现/未出现，到下一期出现/未出现的条件概率。',
  entropy: '信息熵：衡量号码分布的不确定性，越接近最大熵越像均匀随机。',
  kl: 'KL 散度：实测分布与均匀分布之间的信息差，值越小越接近随机。',
  shrink: '收缩估计：把样本频率与理论概率加权融合，降低小样本噪声。',
  lift: '提升倍数：某号码的融合概率 ÷ 均匀基线概率。1.08 表示比随机高 8%。',
  poisson: 'Poisson–Gamma 经验贝叶斯：把出现次数视为泊松计数，用 Gamma 先验做收缩。',
  geom: '几何分布：描述“首次出现所需期数”，遗漏间隔若服从几何分布，说明无记忆性。',
  run: '游程检验：检验序列的随机性，看连续相同符号是否过多或过少。',
  odds: '理论中奖概率：按组合数学计算的头奖概率，与选号方法无关。',
  roi: '投资回报率 ROI = 净盈亏 ÷ 投入。',
  ev: '期望值 EV：单注长期平均回报 = 奖金 × 中奖概率。低于成本即为负期望。',
  markov: '马尔可夫链：假设下一期状态只与本期有关，用于检验“记忆性”。',
  fusion: '融合模型：把历史频率、EWMA、贝叶斯后验、马尔可夫转移按 log-odds 加权，再归一化。',
};

function T(label, tip) {
  const t = tip || TIPS[label];
  if (!t) return esc(label);
  return '<span class="tip-term" data-tip="' + esc(t) + '">' + esc(label) + '</span>';
}

/* ============================================================
 * 通用组件
 * ============================================================ */
function kpi(k, v, x, cls) {
  return '<div class="kpi"><div class="k">' + k + '</div><div class="v ' + (cls || '') + '">' + v + '</div>' +
    (x ? '<div class="x">' + x + '</div>' : '') + '</div>';
}
function card(title, sub, body, extra) {
  return '<div class="card">' +
    (title ? '<div class="card-h"><h3>' + title + '</h3>' +
      (sub ? '<span class="sub">' + sub + '</span>' : '') + (extra || '') + '</div>' : '') +
    body + '</div>';
}
function gcard(title, sub, body) {
  return '<div>' + card(title, sub, body) + '</div>';
}
function tag(text, cls) {
  return '<span class="tag ' + (cls || '') + '">' + esc(text) + '</span>';
}
function kv(rows) {
  return '<dl class="kv">' + rows.map((r) =>
    '<dt>' + r[0] + '</dt><dd>' + r[1] + '</dd>').join('') + '</dl>';
}
function acc(title, body, open) {
  return '<details class="accordion"' + (open ? ' open' : '') + '><summary>' + title + '</summary>' +
    '<div class="body">' + body + '</div></details>';
}
function empty(msg) {
  return '<div class="empty">' + esc(msg) + '</div>';
}
function noteBox(text, cls) {
  return '<div class="note ' + (cls || '') + '">' + text + '</div>';
}
function field(label, inner, hint) {
  return '<label class="field"><span class="lab">' + label + '</span>' + inner +
    (hint ? '<div class="tiny muted mt8">' + hint + '</div>' : '') + '</label>';
}
/**
 * cols: [{t, num}]
 * cells: string | {h, num, cls}
 * rowAttr: (row, i) => ' data-act="..."'
 */
function table(cols, rows, rowAttr) {
  const th = cols.map((c) => '<th' + (c.num ? ' class="num"' : '') + '>' + (c.t || '') + '</th>').join('');
  const tr = (rows || []).map((r, i) => {
    const attr = rowAttr ? (rowAttr(r, i) || '') : '';
    const tds = cols.map((c, j) => {
      const cell = r[j];
      if (cell && typeof cell === 'object' && !(cell instanceof Date)) {
        const cls = [cell.num ? 'num' : '', cell.cls || ''].filter(Boolean).join(' ');
        return '<td' + (cls ? ' class="' + cls + '"' : '') + '>' + (cell.h == null ? '' : cell.h) + '</td>';
      }
      return '<td' + (c.num ? ' class="num"' : '') + '>' + (cell == null ? '' : cell) + '</td>';
    }).join('');
    return '<tr' + attr + '>' + tds + '</tr>';
  }).join('');
  return '<div class="tbl-wrap"><table class="tbl"><thead><tr>' + th + '</tr></thead><tbody>' +
    (tr || '<tr><td colspan="' + cols.length + '" class="center muted">暂无数据</td></tr>') +
    '</tbody></table></div>';
}
function chips(view, code) {
  return CODES.map((c) =>
    '<button class="chip ' + (c === code ? 'on' : '') + '" data-act="pick" data-view="' + view +
    '" data-code="' + c + '"><span class="dot"></span>' + NAME[c] + '</button>').join('');
}
function balls(list, opt) {
  const o = opt || {};
  return '<span class="balls">' + (list || []).map((n) => {
    const hit = o.hit && o.hit.has ? o.hit.has(Number(n)) : false;
    const txt = o.pad ? String(n).padStart(2, '0') : String(n);
    return '<span class="ball ' + (o.blue ? 'blue ' : '') + (o.sm ? 'sm ' : '') +
      (hit ? 'hit ' : '') + (o.dim ? 'dim' : '') + '">' + esc(txt) + '</span>';
  }).join('') + '</span>';
}
function drawBalls(code, nums, sp, sm) {
  const digit = isDigit(code);
  const list = (Array.isArray(sp) ? sp : (sp == null || sp === '' ? [] : [sp]))
    .filter((v) => v != null && v !== '');
  if (!list.length) return balls(nums, { sm: sm, pad: !digit });
  return '<span class="balls">' + (nums || []).map((n) => {
    const txt = digit ? String(n) : String(n).padStart(2, '0');
    return '<span class="ball ' + (sm ? 'sm' : '') + '">' + esc(txt) + '</span>';
  }).join('') + list.map((v) =>
    '<span class="ball blue ' + (sm ? 'sm' : '') + '">' + esc(String(v).padStart(2, '0')) + '</span>'
  ).join('') + '</span>';
}
function freqGrid(list, opt) {
  const o = opt || {};
  const items = list || [];
  if (!items.length) return empty('暂无数据');
  const max = Math.max(1, ...items.map((x) => Number(x.cnt) || 0));
  return '<div class="freq-grid">' + items.map((x) => {
    const w = Math.round((Number(x.cnt) || 0) / max * 100);
    const hot = o.hot ? o.hot(x) : (isNum(x.z) && Number(x.z) >= 2);
    const label = x.n != null ? x.n : x.d;
    return '<div class="freq-cell">' +
      '<div class="row" style="gap:4px"><span class="n">' + esc(label) + '</span>' +
      '<span class="c" style="margin-left:auto">' + (x.cnt == null ? '—' : x.cnt) + '</span></div>' +
      '<div class="z ' + signCls(x.z) + '">z ' + num(x.z, 2) + '</div>' +
      '<div class="bar"><i class="' + (hot ? 'hot' : '') + '" style="width:' + w + '%"></i></div>' +
      '</div>';
  }).join('') + '</div>';
}

/* ============================================================
 * 图表（内联 SVG，按容器宽度生成 viewBox）
 * ============================================================ */
function chartHost(spec) {
  return '<div class="chart" data-chart="' + encodeURIComponent(JSON.stringify(spec)) + '"></div>';
}
function mountCharts(root) {
  $$('[data-chart]', root || document).forEach((el) => {
    let spec;
    try { spec = JSON.parse(decodeURIComponent(el.dataset.chart)); } catch (e) { return; }
    const w = Math.max(320, Math.round(el.clientWidth || 360));
    const key = JSON.stringify(spec) + '|' + w;
    if (el.dataset.key === key) return;
    el.dataset.key = key;
    el.innerHTML = spec.type === 'bar' ? barSVG(spec, w, 220) : lineSVG(spec, w, 220);
  });
}
function lineSVG(spec, w, h) {
  const padL = 46, padR = 18, padT = 14, padB = 26;
  const iw = Math.max(20, w - padL - padR);
  const ih = Math.max(20, h - padT - padB);
  const series = spec.series || [];
  const vals = [];
  series.forEach((s) => (s.values || []).forEach((v) => { if (isNum(v)) vals.push(Number(v)); }));
  if (!vals.length) return '<div class="empty">暂无数据</div>';
  let mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals);
  if (spec.zero) { mn = Math.min(mn, 0); mx = Math.max(mx, 0); }
  if (mn === mx) { mn -= 1; mx += 1; }
  const padv = (mx - mn) * 0.08;
  mn -= padv; mx += padv;
  if (spec.zero && mn > 0) mn = 0;
  const n = Math.max(1, ...series.map((s) => (s.values || []).length));
  const X = (i) => (n <= 1 ? padL + iw / 2 : padL + iw * i / (n - 1));
  const Y = (v) => padT + ih - (v - mn) / (mx - mn) * ih;
  let grid = '';
  for (let i = 0; i < 3; i++) {
    const v = mn + (mx - mn) * i / 2, y = Y(v);
    grid += '<line x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (padL + iw) + '" y2="' + y.toFixed(1) + '" stroke="#eef1f7"/>';
    grid += '<text x="' + (padL - 6) + '" y="' + (y + 3).toFixed(1) + '" text-anchor="end">' + fmtAxis(v) + '</text>';
  }
  if (spec.zero && mn < 0 && mx > 0) {
    const y = Y(0);
    grid += '<line x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (padL + iw) + '" y2="' + y.toFixed(1) + '" stroke="#b9c3d6" stroke-dasharray="3 3"/>';
  }
  let paths = '';
  const palette = ['#2b6cf6', '#0f9d58', '#e5484d', '#c2760a'];
  series.forEach((s, si) => {
    const color = s.color || palette[si % palette.length];
    const arr = s.values || [];
    let d = '', started = false;
    arr.forEach((v, i) => {
      if (!isNum(v)) { started = false; return; }
      d += (started ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(Number(v)).toFixed(1) + ' ';
      started = true;
    });
    if (d) paths += '<path d="' + d.trim() + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>';
    for (let i = arr.length - 1; i >= 0; i--) {
      if (isNum(arr[i])) {
        paths += '<circle cx="' + X(i).toFixed(1) + '" cy="' + Y(Number(arr[i])).toFixed(1) + '" r="3" fill="' + color + '"/>';
        break;
      }
    }
  });
  let xl = '';
  const labs = spec.labels || [];
  if (labs.length) {
    const step = Math.max(1, Math.ceil(labs.length / 4));
    const idx = new Set();
    for (let i = 0; i < labs.length; i += step) idx.add(i);
    idx.add(labs.length - 1);
    Array.from(idx).sort((a, b) => a - b).forEach((i) => {
      const anchor = i === 0 ? 'start' : (i === labs.length - 1 ? 'end' : 'middle');
      xl += '<text x="' + X(i).toFixed(1) + '" y="' + (h - 8) + '" text-anchor="' + anchor + '">' + esc(labs[i]) + '</text>';
    });
  }
  return '<svg width="100%" height="100%" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="xMidYMid meet">' +
    grid + paths + xl + '</svg>';
}
function barSVG(spec, w, h) {
  const padL = 46, padR = 16, padT = 14, padB = 26;
  const iw = Math.max(20, w - padL - padR);
  const ih = Math.max(20, h - padT - padB);
  const vals = (spec.values || []).map((v) => (isNum(v) ? Number(v) : 0));
  if (!vals.length) return '<div class="empty">暂无数据</div>';
  const mx = Math.max(1, ...vals);
  const n = vals.length;
  const bw = iw / n;
  const Y = (v) => padT + ih - v / mx * ih;
  let grid = '';
  for (let i = 0; i < 3; i++) {
    const v = mx * i / 2, y = Y(v);
    grid += '<line x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (padL + iw) + '" y2="' + y.toFixed(1) + '" stroke="#eef1f7"/>';
    grid += '<text x="' + (padL - 6) + '" y="' + (y + 3).toFixed(1) + '" text-anchor="end">' + fmtAxis(v) + '</text>';
  }
  let bars = '';
  vals.forEach((v, i) => {
    const x = padL + bw * i + bw * 0.14;
    const bwd = Math.max(1.5, bw * 0.72);
    const y = Y(v);
    const hh = Math.max(1, padT + ih - y);
    bars += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bwd.toFixed(1) +
      '" height="' + hh.toFixed(1) + '" rx="2" fill="' + (spec.color || '#2b6cf6') + '" opacity=".82"/>';
  });
  let xl = '';
  const labs = spec.labels || [];
  const step = Math.max(1, Math.ceil(n / 8));
  labs.forEach((l, i) => {
    if (i % step === 0 || i === n - 1) {
      xl += '<text x="' + (padL + bw * i + bw / 2).toFixed(1) + '" y="' + (h - 8) + '" text-anchor="middle">' + esc(l) + '</text>';
    }
  });
  if (n <= 20) {
    vals.forEach((v, i) => {
      if (v > 0) {
        bars += '<text x="' + (padL + bw * i + bw / 2).toFixed(1) + '" y="' + (Y(v) - 4).toFixed(1) + '" text-anchor="middle">' + v + '</text>';
      }
    });
  }
  return '<svg width="100%" height="100%" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="xMidYMid meet">' +
    grid + bars + xl + '</svg>';
}

/* ============================================================
 * 全局状态
 * ============================================================ */
const state = {
  meta: null,
  preds: null,
  ana: null,
  pnl: null,
  me: null,
  authChecked: false,
  ready: false,
  pick: { predict: 'ssq', analysis: 'ssq', draws: 'ssq' },
  drawLimit: 30,
  sim: null,          // 当前查看的模拟结果 {id,name,result,spec}
  authTab: 'login',
  seq: 0,
};

/* ============================================================
 * 请求封装
 * ============================================================ */
async function api(path, opt) {
  const o = Object.assign({ credentials: 'same-origin', headers: {} }, opt || {});
  if (o.body && typeof o.body !== 'string') {
    o.body = JSON.stringify(o.body);
    o.headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(path, o);
  let data = null;
  try { data = await res.json(); } catch (e) { /* 非 JSON 响应 */ }
  if (!res.ok || (data && data.ok === false)) {
    const err = new Error((data && data.error) || ('请求失败 (' + res.status + ')'));
    err.status = res.status;
    throw err;
  }
  return data;
}

async function refreshMe(silent) {
  try {
    const r = await api('/api/me');
    state.me = { user: r.user, strategies: r.strategies || [] };
  } catch (e) {
    if (e.status === 401) state.me = null;
    else if (!silent) toast(e.message, true);
  }
  state.authChecked = true;
  paintChrome();
}

/* ============================================================
 * 路由（History API 路径路由，每个栏目可被单独收录）
 * ============================================================ */
const ROUTE_PATH = {
  home: '/', predict: '/predict', analysis: '/analysis', draws: '/draws',
  pnl: '/pnl', sim: '/sim', theory: '/theory',
};
function parseRoute() {
  let h = String(location.hash || '').replace(/^#\/?/, '');
  let parts;
  if (h) parts = h.split('/').filter(Boolean);          // 兼容旧的 #/draws/ssq 链接
  else parts = location.pathname.split('/').filter(Boolean);
  let route = parts[0] || 'home';
  let arg = parts[1] || null;
  if (!NAV.some((x) => x[0] === route)) { route = 'home'; arg = null; }
  return { route: route, arg: arg };
}
function routePath(route, arg) {
  const base = ROUTE_PATH[route] || '/';
  return arg ? base.replace(/\/$/, '') + '/' + encodeURIComponent(arg) : base;
}
function navigate(route, arg, replace) {
  const path = routePath(route, arg);
  if (location.pathname === path && !location.hash) { onRoute(); return; }
  if (replace) history.replaceState({}, '', path);
  else history.pushState({}, '', path);
  onRoute();
}
function paintMeta(route) {
  const el = document.getElementById('seo-pages');
  if (!el) return;
  let map;
  try { map = JSON.parse(el.textContent || '{}'); } catch (e) { return; }
  const m = map[ROUTE_PATH[route] || '/'];
  if (!m) return;
  document.title = m.title;
  const d = document.querySelector('meta[name="description"]');
  if (d) d.setAttribute('content', m.desc);
}
function onRoute() {
  const p = parseRoute();
  if (PICKABLE[p.route] && p.arg && LOT[p.arg]) state.pick[p.route] = p.arg;
  paintMeta(p.route);
  render();
}
function render() {
  const p = parseRoute();
  const route = p.route;
  if (!state.ready) return;
  let html = '';
  try {
    if (route === 'home') html = viewHome();
    else if (route === 'predict') html = viewPredict();
    else if (route === 'analysis') html = viewAnalysis();
    else if (route === 'draws') html = viewDraws();
    else if (route === 'pnl') html = viewPnl();
    else if (route === 'sim') html = viewSim();
    else if (route === 'theory') html = viewTheory();
    else html = viewHome();
  } catch (e) {
    console.error(e);
    html = card('页面渲染出错', '', noteBox(esc(e.message || String(e))) +
      '<div class="row mt12"><button class="btn" data-act="reload">重新加载</button></div>');
  }
  const app = $('#app');
  if (app) app.innerHTML = html;
  paintChrome();
  mountCharts(app);
  if (route === 'draws') loadDraws();
  window.scrollTo(0, 0);
}

/* ============================================================
 * 顶栏
 * ============================================================ */
function paintChrome() {
  const nav = $('#nav');
  const acc = $('#account');
  if (!nav || !acc) return;
  const cur = parseRoute().route;
  nav.innerHTML = NAV.map((n) =>
    '<a href="' + routePath(n[0]) + '" class="' + (cur === n[0] ? 'on' : '') + '">' + n[1] + '</a>').join('');
  if (!state.authChecked) { acc.innerHTML = ''; return; }
  if (state.me && state.me.user) {
    const u = state.me.user;
    acc.innerHTML =
      '<span class="avatar">' + esc(String(u.username || '?').slice(0, 1)) + '</span>' +
      '<span class="tiny">' + esc(u.username) + '</span>' +
      '<button class="btn sm ghost" data-act="wallet" title="设置初始资金池">' +
      '<span class="mono">¥ ' + money(u.wallet) + '</span></button>' +
      '<button class="btn sm ghost" data-act="logout">退出</button>';
  } else {
    acc.innerHTML =
      '<button class="btn sm" data-act="auth" data-tab="login">登录</button>' +
      '<button class="btn sm primary" data-act="auth" data-tab="register">注册</button>';
  }
}

/* ============================================================
 * 弹窗
 * ============================================================ */
const modalStack = [];
function modal(opt) {
  const root = $('#modal-root');
  const bd = document.createElement('div');
  bd.className = 'backdrop';
  bd.innerHTML = '<div class="modal ' + (opt.wide ? 'lg' : '') + '">' +
    '<div class="modal-h"><h3>' + (opt.title || '') + '</h3><button class="x" data-close>✕</button></div>' +
    '<div class="modal-b">' + (opt.body || '') + '</div>' +
    '<div class="modal-f">' + (opt.footer || '<button class="btn" data-close>关闭</button>') + '</div>' +
    '</div>';
  root.appendChild(bd);
  modalStack.push(bd);
  document.body.style.overflow = 'hidden';
  return bd;
}
function closeModal(bd) {
  if (!bd) return;
  const i = modalStack.indexOf(bd);
  if (i >= 0) modalStack.splice(i, 1);
  bd.remove();
  if (!modalStack.length) document.body.style.overflow = '';
}
function closeTop() { if (modalStack.length) closeModal(modalStack[modalStack.length - 1]); }
function closeAll() { while (modalStack.length) closeModal(modalStack[modalStack.length - 1]); }
function topModal() { return modalStack.length ? modalStack[modalStack.length - 1] : null; }
function setModalBody(bd, html) {
  const b = bd && bd.querySelector('.modal-b');
  if (b) b.innerHTML = html;
}

/* ============================================================
 * Toast / 悬停提示
 * ============================================================ */
let toastTimer = 0;
function toast(msg, bad) {
  const t = $('#toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.toggle('bad', !!bad);
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), 2400);
}
function initTip() {
  const tip = $('#tip');
  if (!tip) return;
  const move = (e) => {
    const pad = 14;
    const r = tip.getBoundingClientRect();
    let x = e.clientX + pad;
    let y = e.clientY + pad;
    if (x + r.width > window.innerWidth - 8) x = e.clientX - r.width - pad;
    if (y + r.height > window.innerHeight - 8) y = e.clientY - r.height - pad;
    tip.style.left = Math.max(6, x) + 'px';
    tip.style.top = Math.max(6, y) + 'px';
  };
  document.addEventListener('mouseover', (e) => {
    const el = e.target.closest && e.target.closest('[data-tip]');
    if (!el) return;
    tip.textContent = el.dataset.tip;
    tip.classList.add('on');
    move(e);
  });
  document.addEventListener('mousemove', (e) => {
    if (tip.classList.contains('on')) move(e);
  });
  document.addEventListener('mouseout', (e) => {
    const el = e.target.closest && e.target.closest('[data-tip]');
    if (el && !el.contains(e.relatedTarget)) tip.classList.remove('on');
  });
}

/* ============================================================
 * 登录 / 注册 / 资金池
 * ============================================================ */
function openAuth(tab) {
  state.authTab = tab === 'register' ? 'register' : 'login';
  const bd = modal({ title: '账户', body: '', footer: '' });
  bd.dataset.kind = 'auth';
  paintAuth(bd);
  bd.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.tagName === 'INPUT') submitAuth(bd);
  });
}
function paintAuth(bd) {
  const t = state.authTab;
  const isReg = t === 'register';
  setModalBody(bd,
    '<div class="seg" style="margin-bottom:16px">' +
    '<button data-act="auth-tab" data-tab="login" class="' + (isReg ? '' : 'on') + '">登录</button>' +
    '<button data-act="auth-tab" data-tab="register" class="' + (isReg ? 'on' : '') + '">注册</button>' +
    '</div>' +
    '<label class="field"><span class="lab">用户名</span>' +
    '<input class="inp" id="au-name" maxlength="20" autocomplete="username" placeholder="3-20 位中文 / 字母 / 数字 / 下划线"></label>' +
    '<label class="field"><span class="lab">密码</span>' +
    '<input class="inp" id="au-pwd" type="password" maxlength="64" autocomplete="' + (isReg ? 'new-password' : 'current-password') + '" placeholder="至少 6 位"></label>' +
    (isReg
      ? '<div class="note blue tiny">注册即获得 1000 元模拟资金池。资金池仅用于模拟，不涉及任何真实支付。</div>'
      : '<div class="note blue tiny">登录后可创建多个模拟策略，按真实开奖结果逐期结算损益。</div>'));
  const f = bd.querySelector('.modal-f');
  if (f) {
    f.innerHTML = '<button class="btn ghost" data-close>取消</button>' +
      '<button class="btn primary" data-act="auth-submit">' + (isReg ? '注册并登录' : '登录') + '</button>';
  }
  const inp = $('#au-name');
  if (inp) setTimeout(() => inp.focus(), 30);
}
async function submitAuth(bd) {
  const name = ($('#au-name') || {}).value || '';
  const pwd = ($('#au-pwd') || {}).value || '';
  if (!name.trim()) return toast('请输入用户名', true);
  if (!pwd) return toast('请输入密码', true);
  const isReg = state.authTab === 'register';
  const btn = $('[data-act="auth-submit"]', bd);
  if (btn) { btn.disabled = true; btn.textContent = isReg ? '注册中…' : '登录中…'; }
  try {
    await api(isReg ? '/api/register' : '/api/login', { method: 'POST', body: { username: name.trim(), password: pwd } });
    await refreshMe(true);
    closeAll();
    toast(isReg ? '注册成功，已登录' : '登录成功');
    render();
  } catch (e) {
    toast(e.message, true);
    if (btn) { btn.disabled = false; btn.textContent = isReg ? '注册并登录' : '登录'; }
  }
}
function openWallet() {
  if (!state.me) return openAuth('login');
  const bd = modal({
    title: '初始资金池',
    body: field('资金池金额（元）',
      '<input class="inp" id="w-amt" type="number" min="1" step="100" value="' + esc(state.me.user.wallet) + '">',
      '每次新建策略时作为初始资金写入账户，并作为模拟的起始余额。模拟不涉及真实资金。'),
    footer: '<button class="btn ghost" data-close>取消</button><button class="btn primary" data-act="w-save">保存</button>',
  });
  bd.dataset.kind = 'wallet';
  bd.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') saveWallet(bd);
  });
}
async function saveWallet(bd) {
  const v = Number(($('#w-amt') || {}).value);
  if (!isFinite(v) || v <= 0) return toast('请输入大于 0 的金额', true);
  try {
    const r = await api('/api/wallet', { method: 'PUT', body: { wallet: v } });
    state.me.user.wallet = r.wallet;
    closeAll();
    paintChrome();
    toast('资金池已更新为 ¥ ' + money(r.wallet));
  } catch (e) {
    toast(e.message, true);
  }
}
async function doLogout() {
  try { await api('/api/logout', { method: 'POST', body: {} }); } catch (e) { /* 忽略 */ }
  state.me = null;
  state.sim = null;
  paintChrome();
  render();
  toast('已退出登录');
}

/* ============================================================
 * 事件绑定
 * ============================================================ */
function bind() {
  document.addEventListener('click', (e) => {
    const closeEl = e.target.closest && e.target.closest('[data-close]');
    if (closeEl) {
      e.preventDefault();
      const bd = closeEl.closest('.backdrop');
      if (bd) closeModal(bd);
      return;
    }
    if (e.target.classList && e.target.classList.contains('backdrop')) {
      closeModal(e.target);
      return;
    }
    const el = e.target.closest && e.target.closest('[data-act]');
    if (!el) return;
    const act = el.dataset.act;
    const bd = el.closest('.backdrop');
    switch (act) {
      case 'auth': openAuth(el.dataset.tab); break;
      case 'auth-tab':
        state.authTab = el.dataset.tab;
        if (bd) paintAuth(bd);
        break;
      case 'auth-submit':
        submitAuth(bd || topModal());
        break;
      case 'logout': doLogout(); break;
      case 'wallet': openWallet(); break;
      case 'w-save': saveWallet(bd || topModal()); break;
      case 'pick': pickLottery(el.dataset.view, el.dataset.code); break;
      case 'draw-limit':
        state.drawLimit = Number(el.dataset.limit) || 30;
        render();
        break;
      case 'reload': location.reload(); break;
      case 'entry':
        e.preventDefault();
        openEntryModal(el.dataset.code, el.dataset.issue);
        break;
      case 'pentry':
        e.preventDefault();
        openEntryModal(el.dataset.code, el.dataset.issue);
        break;
      case 'simview': loadStrategy(el.dataset.id); break;
      case 'simrun': runStrategy(el.dataset.id); break;
      case 'simdel': deleteStrategy(el.dataset.id); break;
      case 'simclose':
        state.sim = null;
        render();
        break;
      case 'simrow':
        e.preventDefault();
        openRowModal(el.dataset.idx);
        break;
      case 's-code': onStratCode(el.dataset.code); break;
      case 's-mode': onStratMode(el.dataset.mode); break;
      case 's-parlay': onStratParlay(); break;
      case 's-num': onStratNum(el); break;
      case 's-open': openStrategyModal(); break;
      case 's-submit': submitStrategy(); break;
      case 's-reset': resetCustom(SIMD.sd, SIMD.sd.codes[0]); paintStrategy(); break;
      case 's-suggest': suggestStart(SIMD.sd, true); break;
      default: break;
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modalStack.length) { closeTop(); }
  });

  window.addEventListener('popstate', onRoute);
  window.addEventListener('hashchange', onRoute);

  // 站内链接走 History API，避免整页刷新
  document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!a || a.target || a.hasAttribute('download')) return;
    const href = a.getAttribute('href') || '';
    if (href.charAt(0) !== '/' || href.indexOf('//') === 0) return;
    const path = href.split(/[?#]/)[0];
    const seg = path.replace(/^\//, '').split('/')[0];
    if (seg && !NAV.some((n) => n[0] === seg)) return;
    e.preventDefault();
    if (path === location.pathname && !location.hash) { onRoute(); return; }
    history.pushState({}, '', path);
    onRoute();
  });

  let rzTimer = 0;
  window.addEventListener('resize', () => {
    clearTimeout(rzTimer);
    rzTimer = setTimeout(() => mountCharts(document), 160);
  });
}
function pickLottery(view, code) {
  if (!view || !code) return;
  state.pick[view] = code;
  navigate(view, code);
}

/* ============================================================
 * 公共小工具
 * ============================================================ */
function lotOf(code) {
  const L = (state.meta && state.meta.lotteries) || [];
  return L.find((x) => x.code === code) || null;
}
function lotName(code) {
  const m = lotOf(code);
  return m ? m.name : (NAME[code] || code);
}
function pageH(title, sub, right) {
  return '<div class="page-h"><div><h1>' + title + '</h1>' + (sub ? '<p>' + sub + '</p>' : '') + '</div>' +
    (right ? '<div class="row">' + right + '</div>' : '') + '</div>';
}
function verdictTag(v) {
  if (!v) return '';
  const s = String(v);
  const cls = /显著|偏离|拒绝|异常/.test(s) ? 'red' : (/接近|均匀|随机|正常|不显著/.test(s) ? 'green' : '');
  return tag(s, cls);
}
function tileList(cards) {
  return '<div class="grid g4">' + (cards || []).map((c) =>
    '<div class="kpi" data-tip="' + esc(c.hint || '') + '">' +
    '<div class="k">' + esc(c.name) + '</div>' +
    '<div class="v" style="font-size:16px">' + esc(c.value) + '</div></div>').join('') + '</div>';
}

/* ============================================================
 * 首页
 * ============================================================ */
function viewHome() {
  const L = (state.meta && state.meta.lotteries) || [];
  const total = L.reduce((s, x) => s + (x.count || 0), 0);
  const dates = L.map((x) => x.first_date).filter(Boolean).sort();
  const lastDates = L.map((x) => x.last_date).filter(Boolean).sort();
  const pr = (state.pnl && state.pnl.totals) || {};
  const hero =
    '<section class="hero">' +
    '<h1>看得懂的开奖数据 · 说得清的概率模型</h1>' +
    '<p>六种彩票的开奖数据、统计检验、下期预测与策略模拟放在同一个站点。术语都有悬停提示，结论都能回到原始开奖记录核对。</p>' +
    '<div class="hero-stats">' +
    '<div><b>' + L.length + '</b><span>彩种</span></div>' +
    '<div><b>' + shortNum(total) + '</b><span>累计开奖期数</span></div>' +
    '<div><b>' + esc(dates[0] || '—') + '</b><span>数据起始</span></div>' +
    '<div><b>' + esc(lastDates[lastDates.length - 1] || '—') + '</b><span>最近开奖</span></div>' +
    '<div><b>' + num(pr.pending, 0) + '</b><span>待开奖预测</span></div>' +
    '</div>' +
    '<div class="row"><a class="btn light" href="/predict">看下期预测</a>' +
    '<a class="btn" href="/sim">开始模拟收益</a>' +
    '<a class="btn" href="/theory">了解模型</a></div>' +
    '</section>';

  const lotCards = '<div class="grid g3 mt16">' + L.map((m) => {
    const pd = ((state.preds && state.preds.lotteries) || []).find((x) => x.code === m.code);
    const next = pd && pd.next;
    const last = m.last;
    return gcard(esc(m.name), esc(m.desc),
      '<div class="row" style="justify-content:space-between;align-items:flex-start">' +
      '<div><div class="tiny muted">最近一期</div>' +
      '<div class="mt8">' + (last ? drawBalls(m.code, last.nums, last.sp, true) : '<span class="muted">暂无</span>') + '</div>' +
      '<div class="tiny muted mt8">第 ' + esc(last ? last.issue : '—') + ' 期 · ' + esc(last ? last.date : '—') + '</div></div>' +
      '<div class="right" style="margin-left:auto"><div class="tiny muted">累计</div><div class="mono">' + num(m.count, 0) + ' 期</div>' +
      '<div class="tiny muted mt8">' + esc(m.draw_days || '') + '</div></div>' +
      '</div>' +
      (next ? '<hr class="sep"><div class="row" style="justify-content:space-between">' +
        '<div><div class="tiny muted">下期</div><div class="mono mt8">第 ' + esc(next.issue) + ' 期 · ' + esc(next.date) + '</div></div>' +
        '<a class="btn sm" href="/predict/' + m.code + '">预测</a></div>' : ''));
  }).join('') + '</div>';

  const feats = '<div class="grid g3 mt16">' + [
    ['下期预测', '核心号码、区间参考、复式方案，以及每个模型的原始输出。', '/predict'],
    ['统计分析', '卡方均匀性、遗漏、和值分布、独立性与游程检验。', '/analysis'],
    ['开奖数据', '逐期开奖号码与官方派奖明细，可点开查看。', '/draws'],
    ['预测盈亏', '每日预测按真实开奖结果结算的实盘台账。', '/pnl'],
    ['模拟收益', '自选 / 随机 / 系统预测三种选号方式的策略回测。', '/sim'],
    ['理论方法', '所有模型的定义、公式、假设与局限。', '/theory'],
  ].map((x) => gcard(x[0], '', '<p class="tiny muted" style="margin:0 0 12px">' + x[1] + '</p>' +
    '<a class="btn sm" href="' + x[2] + '">进入</a>')).join('') + '</div>';

  const disclaim = noteBox('<b>理性提示</b>：彩票每期开奖相互独立，任何模型都无法提高单注的理论中奖概率。' +
    '本站的统计分析与预测用于展示数据特征与建模方法，不构成投注建议。请勿将模拟结果等同于真实收益。');

  return hero +
    '<div class="mt24"></div>' +
    '<div class="page-h"><div><h1 style="font-size:16px">彩种总览</h1><p>点击彩种查看下期预测</p></div></div>' +
    lotCards + feats +
    '<div class="mt16">' + disclaim + '</div>';
}

/* ============================================================
 * 下期预测
 * ============================================================ */
function viewPredict() {
  const code = state.pick.predict;
  const P = state.preds || {};
  const it = (P.lotteries || []).find((x) => x.code === code);
  const chipsHtml = '<div class="chips">' + chips('predict', code) + '</div>';
  if (!it) {
    return pageH('下期预测', '', chipsHtml) + '<div class="mt16">' + card('', '', empty('暂无该彩种的预测数据，请先运行数据更新脚本')) + '</div>';
  }
  const ms = it.models || {};
  const next = it.next || {};
  const last = it.last;
  const odds = (it.ranges && it.ranges.odds) || '—';
  let maxLift = null;
  (ms.calibration || []).forEach((c) => { if (isNum(c.lift) && (maxLift == null || c.lift > maxLift)) maxLift = c.lift; });
  const retRate = ms.academic ? ms.academic.ret_rate : null;

  const head = pageH('下期预测', '生成于 ' + esc(P.generated_at || '—'), chipsHtml);

  const target = card('目标期与上期开奖', '',
    '<div class="row" style="justify-content:space-between;align-items:flex-start;gap:20px">' +
    '<div>' +
    '<div class="tiny muted">推荐用于</div>' +
    '<div class="mono" style="font-size:20px;font-weight:700">第 ' + esc(next.issue || '—') + ' 期</div>' +
    '<div class="tiny muted mt8">开奖日期 ' + esc(next.date || '—') +
    (next.weekday ? ' · 周' + esc(next.weekday) : '') +
    (next.basis ? ' · 依据第 ' + esc(next.basis) + ' 期开奖' : '') + '</div>' +
    '<div class="tiny muted mt8">建模样本 ' + num(it.draws, 0) + ' 期' +
    (it.window ? ' · 窗口自 ' + esc(it.window.start || '') + '（' + esc(it.window.reason || '') + '）' : '') + '</div>' +
    '</div>' +
    '<div class="right"><div class="tiny muted">上期开奖</div>' +
    '<div class="mt8">' + (last ? drawBalls(code, last.nums, last.sp) : '—') + '</div>' +
    '<div class="tiny muted mt8">第 ' + esc(last ? last.issue : '—') + ' 期 · ' + esc(last ? last.date : '—') + '</div></div>' +
    '</div>');

  const kpis = '<div class="grid g4 mt16">' +
    kpi('核心推荐', num((it.core || []).length, 0) + ' 个', isDigit(code) ? '按位置给出首选' : '按融合分排序') +
    kpi(T('头奖理论概率', TIPS.odds), '<span style="font-size:15px">' + esc(odds) + '</span>',
      '与选号方式无关') +
    kpi(T('融合提升', TIPS.lift), maxLift == null ? '—' : '× ' + num(maxLift, 2),
      maxLift == null ? '仅数字彩无此模型' : '相对均匀基线') +
    kpi(T('返奖率', TIPS.ret), retRate == null ? '—' : pct(retRate), retRate == null ? '数字彩不适用' : '奖金 ÷ 销售额') +
    '</div>';

  const coreCard = card(T('核心推荐') + '号码', isDigit(code) ? '按位置的首选数字与该位候选' : '融合历史、EWMA、贝叶斯与马尔可夫', coreBlock(code, it));
  const rangeCard = card('区间与结构参考', '和值 / 跨度 / 奇偶 / 热区', rangesBlock(code, it));

  const parlayCard = card(T('复式', TIPS.parlay) + '方案', '多选号码展开为多注', parlayBlock(code, it));
  const ticketsCard = card('单式推荐票', '每注 2 元，' + num((it.tickets || []).length, 0) + ' 注', ticketsBlock(code, it));

  const models = modelsBlock(code, it);
  const academic = academicBlock(code, it);

  return head + target + kpis +
    '<div class="grid g2 mt16">' + coreCard.replace(/^<div class="card">/, '<div class="card" style="margin-top:0">') + rangeCard.replace(/^<div class="card">/, '<div class="card" style="margin-top:0">') + '</div>' +
    '<div class="grid g2 mt16">' + parlayCard.replace(/^<div class="card">/, '<div class="card" style="margin-top:0">') + ticketsCard.replace(/^<div class="card">/, '<div class="card" style="margin-top:0">') + '</div>' +
    models + academic +
    '<div class="mt16">' + noteBox('以上号码由统计模型生成，<b>不改变理论中奖概率</b>。' +
      '“提升倍数”只表示该号码在历史样本中的相对频率偏差，不代表未来更容易开出。') + '</div>';
}

function coreBlock(code, it) {
  const core = it.core || [];
  if (!core.length) return empty('暂无核心推荐');
  if (isDigit(code)) {
    const pos = it.positions || [];
    const rows = core.map((c) => {
      const label = (POS[code] || [])[c.pos] || ('第' + (c.pos + 1) + '位');
      const detail = pos.find((p) => p.pos === c.pos);
      const top = (detail && detail.top) || [];
      return [
        esc(label),
        balls([c.d], { sm: true }),
        { h: num(c.score, 3), num: true },
        { h: top.map((t) => '<span class="mono">' + t.d + '</span> <span class="muted tiny">' + num(t.score, 2) + '</span>').join(' · '), cls: 'tiny' },
      ];
    });
    return table([{ t: '位置' }, { t: '首选' }, { t: '打分', num: true }, { t: '该位候选（打分）' }], rows);
  }
  const rows = core.map((c) => [
    balls([c.n], { sm: true, pad: true }),
    tag(c.tag || '—', 'blue'),
    { h: num(c.score, 3), num: true },
    { h: num(c.z_long, 2), num: true, cls: signCls(c.z_long) },
    { h: num(c.z_recent, 2), num: true, cls: signCls(c.z_recent) },
    { h: num(c.gap_ratio, 2), num: true },
  ]);
  return '<div class="row" style="margin-bottom:14px">' + balls(core.map((c) => c.n), { pad: true }) + '</div>' +
    table([{ t: '号码' }, { t: '标签' }, { t: '融合分', num: true },
      { t: T('长期 z', TIPS.z), num: true }, { t: '近期 z', num: true }, { t: '遗漏比', num: true }], rows);
}

function rangesBlock(code, it) {
  const r = it.ranges || {};
  const rows = [
    ['和值 80% 区间', rng(r.sum80)],
    ['和值 90% 区间', rng(r.sum90)],
    ['跨度 80% 区间', rng(r.span80)],
  ];
  if (isDigit(code)) {
    rows.push(['重号均值', num(r.repeat_mean, 2)]);
    rows.push(['奇数占比前三', (r.odd_top || []).map((x) => x.k + ' 个 ' + pct(x.share)).join('、') || '—']);
    rows.push(['大号占比前三', (r.big_top || []).map((x) => x.k + ' 个 ' + pct(x.share)).join('、') || '—']);
    if (r.play) rows.push(['玩法说明', esc(r.play)]);
  } else {
    rows.push(['重号', '常见 ' + num(r.repeat_mode, 0) + ' 个 · 均值 ' + num(r.repeat_mean, 2)]);
    rows.push(['奇数个数', (r.odd_top || []).map((x) => x.k + ' 个 ' + pct(x.share)).join('、') || '—']);
    rows.push(['大号个数', (r.big_top || []).map((x) => x.k + ' 个 ' + pct(x.share)).join('、') || '—']);
    rows.push(['连号出现率', pct(r.consec_rate)]);
    rows.push(['热区', esc(r.zone || '—') + (isNum(r.zone_share) ? ' · 占比 ' + pct(r.zone_share) : '')]);
    if (r.play) rows.push(['玩法说明', esc(r.play)]);
  }
  rows.push([T('头奖概率', TIPS.odds), esc(r.odds || '—')]);
  return kv(rows);
}

function parlayBlock(code, it) {
  const p = it.parlay;
  if (!p) return empty('暂无复式方案');
  let nums;
  if (isDigit(code)) {
    nums = '<span class="mono" style="font-size:15px;letter-spacing:1px">' +
      esc((p.front || []).map((a) => '[' + (a || []).join('') + ']').join(' ')) + '</span>';
  } else {
    nums = balls(p.front || [], { pad: true }) +
      ((p.back || []).length ? '<span style="width:8px"></span>' + balls(p.back, { blue: true, pad: true }) : '');
  }
  return '<div class="row" style="justify-content:space-between">' +
    '<div class="tiny muted">' + esc(p.label || '复式方案') + '</div>' +
    '<div class="tiny muted">' + num(p.bets, 0) + ' 注 · 投入 ¥ ' + money((p.bets || 0) * 2) + '</div></div>' +
    '<div class="mt12">' + nums + '</div>' +
    '<div class="tiny muted mt8">' + T('复式', TIPS.parlay) + '把多选号码按组合展开为多注，单注 2 元。</div>';
}

function ticketsBlock(code, it) {
  const ts = it.tickets || [];
  if (!ts.length) return empty('暂无单式推荐');
  const digit = isDigit(code);
  const cols = digit
    ? [{ t: '号码' }, { t: '和值', num: true }, { t: '跨度', num: true }, { t: '奇数个数', num: true },
      { t: '打分', num: true }, { t: '类型' }]
    : [{ t: '号码' }, { t: '和值', num: true }, { t: '跨度', num: true }, { t: '奇偶' }, { t: '大小' },
      { t: '打分', num: true }, { t: '类型' }];
  const rows = ts.map((t) => {
    const n = (t.nums || []).length;
    if (digit) {
      return [drawBalls(code, t.nums, null, true), { h: num(t.sum, 0), num: true }, { h: num(t.span, 0), num: true },
        { h: num(t.odd, 0), num: true }, { h: num(t.score, 3), num: true }, tag(t.type || '—', '')];
    }
    return [drawBalls(code, t.nums, t.sp, true), { h: num(t.sum, 0), num: true }, { h: num(t.span, 0), num: true },
      { h: num(t.odd, 0) + ' : ' + (n - (t.odd || 0)), cls: 'tiny' },
      { h: num(t.big, 0) + ' : ' + (n - (t.big || 0)), cls: 'tiny' },
      { h: num(t.score, 3), num: true }, tag(t.type || '—', '')];
  });
  return table(cols, rows);
}

function modelsBlock(code, it) {
  const ms = it.models || {};
  const ac = ms.academic || {};
  const out = [];
  if (Array.isArray(ms.cards) && ms.cards.length) {
    out.push(card('模型速览', '悬停查看指标含义', tileList(ms.cards)));
  }

  if (Array.isArray(ms.calibration) && ms.calibration.length) {
    const rows = ms.calibration.map((c) => [
      balls([c.n], { sm: true, pad: true }),
      { h: pct(c.p_hist, 2), num: true },
      { h: pct(c.p_ewma, 2), num: true },
      { h: pct(c.p_bayes, 2), num: true },
      { h: pct(c.p_markov, 2), num: true },
      { h: '<b>' + pct(c.p_fused, 2) + '</b>', num: true },
      { h: num(c.lift, 2), num: true },
      { h: '[' + pct((c.ci || [])[0], 1) + ', ' + pct((c.ci || [])[1], 1) + ']', cls: 'tiny muted' },
      { h: num(c.omit, 0) + (isNum(c.omit_pct) ? ' (' + pct(c.omit_pct) + ')' : ''), num: true, cls: 'tiny' },
    ]);
    const chart = chartHost({
      type: 'line',
      labels: ms.calibration.map((c) => c.n),
      series: [
        { values: ms.calibration.map((c) => (isNum(c.p_hist) ? c.p_hist * 100 : null)), color: '#c7d2e6' },
        { values: ms.calibration.map((c) => (isNum(c.p_fused) ? c.p_fused * 100 : null)) },
      ],
    });
    out.push(card('号码概率校准', '灰线为历史频率，蓝线为融合后概率（%）',
      table([{ t: '号码' }, { t: T('历史频率', TIPS.shrink), num: true }, { t: T('EWMA', TIPS.ewma), num: true },
        { t: '贝叶斯', num: true }, { t: '马尔可夫', num: true }, { t: '融合', num: true },
        { t: '提升', num: true }, { t: '95% CI' }, { t: '遗漏', num: true }], rows) +
      '<div class="mt16">' + chart + '</div>'));
  }

  const g = [];
  if (ms.fusion) {
    const w = ms.fusion.weights || {};
    g.push(gcard('融合权重', ms.fusion.method || '',
      kv([['历史频率', pct(w.hist)], ['EWMA', pct(w.ewma)], ['贝叶斯后验', pct(w.bayes)],
        ['马尔可夫', pct(w.markov)], ['基线概率', isNum(ms.fusion.base) ? pct(ms.fusion.base, 3) : '—']])));
  }
  if (ms.entropy) {
    const e = ms.entropy;
    g.push(gcard(T('信息熵', TIPS.entropy), '',
      isNum(e.h)
        ? kv([['实测熵 H', num(e.h, 3)], ['最大熵 Hmax', num(e.hmax, 3)], ['比值', pct(e.ratio)],
          [T('KL 散度', TIPS.kl), num(e.kl, 4)], ['结论', verdictTag(e.verdict)]])
        : kv([['平均熵比', num(e.mean_ratio, 4)], ['说明', esc(e.note || '—')]])));
  }
  if (ms.markov) {
    const m = ms.markov;
    g.push(gcard(T('马尔可夫链', TIPS.markov), '',
      isNum(m.p11)
        ? kv([['上期出现→本期出现', pct(m.p11)], ['上期未出→本期出现', pct(m.p01)],
          ['基线概率', pct(m.base, 2)], ['说明', esc(m.note || '—')]])
        : kv([['位间偏离', num(m.dev, 3)], ['说明', esc(m.note || '—')]])));
  }
  if (ms.montecarlo) {
    const m = ms.montecarlo;
    const s = m.sum || {}, sp = m.span || {};
    const rows = [
      [T('模拟次数', TIPS.mc), num(m.sims, 0)],
      ['和值 P5 / P50 / P95', num(s.p5, 1) + ' / ' + num(s.p50, 1) + ' / ' + num(s.p95, 1)],
      ['跨度 P5 / P95', num(sp.p5, 1) + ' / ' + num(sp.p95, 1)],
    ];
    if (isNum(m.odd_mean)) rows.push(['奇数个数均值', num(m.odd_mean, 2)]);
    if (isNum(m.big_mean)) rows.push(['大号个数均值', num(m.big_mean, 2)]);
    if (isNum(m.repeat_mean)) rows.push(['重号均值', num(m.repeat_mean, 2)]);
    g.push(gcard('蒙特卡洛零分布', esc(m.note || ''), kv(rows)));
  }
  if (ms.bootstrap) {
    const b = ms.bootstrap;
    g.push(gcard(T('Bootstrap', TIPS.boot), '',
      kv([['重采样次数', num(b.iters, 0)], ['观测均值', num(b.obs_mean, 2)],
        ['均值 ' + pct(b.level, 0) + ' CI', rng(b.mean_ci)],
        ['中位数 ' + pct(b.level, 0) + ' CI', rng(b.median_ci)]])));
  }
  if (Array.isArray(ms.hypergeom)) {
    g.push(gcard(T('超几何命中', TIPS.hyper), '选号命中 m 个号码的概率',
      table([{ t: '命中个数', num: true }, { t: '概率', num: true }],
        ms.hypergeom.map((h) => [{ h: num(h.match, 0), num: true }, { h: prob(h.prob), num: true }]))));
  }
  if (ms.special) {
    const sp = ms.special;
    const rows = [['号码池', num(sp.pool, 0) + ' 选 ' + num(sp.pick, 0)]];
    if (sp.entropy) rows.push([T('信息熵', TIPS.entropy), num(sp.entropy.ratio, 4) + ' × Hmax']);
    if (isNum(sp.p11)) rows.push(['出现→出现', pct(sp.p11)]);
    if (isNum(sp.p01)) rows.push(['未出→出现', pct(sp.p01)]);
    g.push(gcard(SIDE[code] ? SIDE[code][1] || '特别号' : '特别号', '独立建模', kv(rows)));
  }
  if (g.length) out.push('<div class="grid g2">' + g.join('') + '</div>');

  if (Array.isArray(ac.tests) && ac.tests.length) {
    const rows = ac.tests.map((t) => [
      esc(t.name), { h: '<span class="tiny muted">' + esc(t.scope || '') + '</span>' },
      { h: num(t.stat, 3), num: true }, { h: prob(t.p), num: true }, verdictTag(t.verdict),
    ]);
    out.push(card('统计检验汇总', 'p 值为原假设（随机）成立时的概率',
      table([{ t: '检验' }, { t: '范围' }, { t: '统计量', num: true }, { t: 'p 值', num: true }, { t: '结论' }], rows)));
  }
  return out.join('');
}

function academicBlock(code, it) {
  const ms = it.models || {};
  const ac = ms.academic;
  if (!ac) return '';
  const parts = [];
  if (Array.isArray(ac.cards) && ac.cards.length) {
    parts.push(acc('学术模型速览', tileList(ac.cards), true));
  }
  if (ac.fdr) {
    const f = ac.fdr;
    parts.push(acc(T('多重检验校正', TIPS.fdr) + '（BH-FDR）',
      kv([['检验个数 m', num(f.m, 0)], ['显著性水平 α', num(f.alpha, 3)],
        ['原始显著个数', num(f.raw, 0)], ['校正后显著个数', num(f.q_sig, 0)]]) +
      '<div class="mt12">' + table([{ t: '号码', num: true }, { t: 'p 值', num: true }, { t: 'q 值', num: true }, { t: '显著' }],
        (f.rows || []).map((r) => [{ h: num(r.n, 0), num: true }, { h: prob(r.p), num: true },
          { h: num(r.q, 4), num: true }, verdictTag(r.sig ? '显著' : '不显著')])) + '</div>' +
      (f.note ? '<div class="tiny muted mt8">' + esc(f.note) + '</div>' : '')));
  }
  if (ac.dirichlet) {
    const d = ac.dirichlet;
    parts.push(acc(T('Dirichlet 收缩', TIPS.dirichlet),
      kv([['号码池', num(d.pool, 0) + ' 选 ' + num(d.n, 0)], ['先验强度 s', num(d.s, 3)],
        ['收缩系数 ρ', num(d.rho, 3)], ['有效样本 n_eff', num(d.n_eff, 1)]]) +
      (d.note ? '<div class="tiny muted mt8">' + esc(d.note) + '</div>' : '') +
      '<div class="mt12">' + table([{ t: '号码', num: true }, { t: '后验概率', num: true }, { t: '提升', num: true }],
        (d.top || []).map((x) => [{ h: num(x.n, 0), num: true }, { h: pct(x.p, 3), num: true }, { h: num(x.lift, 3), num: true }])) + '</div>'));
  }
  if (ac.poisson) {
    const p = ac.poisson;
    parts.push(acc(T('Poisson–Gamma 经验贝叶斯', TIPS.poisson),
      kv([['先验 α', num(p.alpha, 3)], ['先验 β', num(p.beta, 5)], ['B', num(p.B, 0)]]) +
      '<div class="mt12">' + table([{ t: '号码', num: true }, { t: 'λ', num: true }, { t: '概率', num: true }, { t: '提升', num: true }],
        (p.top || []).map((x) => [{ h: num(x.n, 0), num: true }, { h: num(x.lam, 3), num: true },
          { h: pct(x.p, 3), num: true }, { h: num(x.lift, 3), num: true }])) + '</div>'));
  }
  if (ac.geom) {
    const g = ac.geom;
    parts.push(acc(T('几何分布拟合', TIPS.geom),
      kv([['样本期数', num(g.n, 0)], ['估计 p', num(g.phat, 4)], ['理论均值', num(g.mean, 2)],
        ['D 统计量', num(g.D, 4)], ['p 值', prob(g.p)], ['A²', num(g.A2, 3)], ['结论', verdictTag(g.verdict)]]) +
      (isNum(g.p_asym) ? '<div class="tiny muted mt8">渐近 p 值 ' + prob(g.p_asym) + '（B = ' + num(g.B, 0) + '）</div>' : '')));
  }
  if (ac.markov_chi2) {
    const m = ac.markov_chi2;
    const rows = [['卡方统计量', num(m.chi2, 3)], ['自由度', num(m.df, 0)], ['p 值', prob(m.p)]];
    if (isNum(m.n)) rows.push(['样本转移数', num(m.n, 0)]);
    if (isNum(m.n11)) rows.push(['出现→出现 / 未出→出现', num(m.n11, 0) + ' / ' + num(m.n01, 0)]);
    parts.push(acc(T('马尔可夫独立性检验', TIPS.markov), kv(rows)));
  }
  if (ac.kelly) {
    const k = ac.kelly;
    parts.push(acc(T('凯利公式', TIPS.kelly),
      kv([['单注中奖概率', prob(k.p_win)], ['头奖倍数', shortNum(k.mult)],
        ['凯利比例 f*', num(k.f_star, 4)], ['单注期望值', isNum(k.ev) ? signedMoney(k.ev) : '—'],
        ['建议等待期数', num(k.wait, 0)], ['已覆盖期数', num(k.hit, 0)]])));
  }
  if (ac.wheel) {
    const w = ac.wheel;
    parts.push(acc('轮转覆盖（Wheel）',
      kv([['号码池 v', num(w.v, 0)], ['开出个数', num(w.drawn, 0)], ['单张票选号', num(w.ticket, 0)],
        ['覆盖注数', num(w.bets, 0)], ['投入', '¥ ' + money(w.cost)]]) +
      '<div class="mt12">' + table([{ t: '命中' }, { t: '概率' }],
        Object.keys(w.p_ge || {}).map((k) => [tag('≥ ' + k + ' 个', ''), { h: prob(w.p_ge[k]), num: true }])) + '</div>'));
  }
  if (ac.ev) {
    const e = ac.ev;
    parts.push(acc(T('期望值', TIPS.ev),
      kv([['单注期望回报', isNum(e.per_ticket) ? signedMoney(e.per_ticket) : '—'],
        ['期望返奖率', isNum(e.rate) ? pct(e.rate) : '—'],
        ['期望回报期数', num(e.periods, 1)]]) +
      (e.note ? '<div class="tiny muted mt8">' + esc(e.note) + '</div>' : '')));
  }
  if (isNum(ac.ret_rate)) {
    parts.push(acc(T('返奖率', TIPS.ret), kv([['历史返奖率', pct(ac.ret_rate)],
      ['说明', '奖金总额 ÷ 销售额，长期低于 100% 即为负期望。']])));
  }
  if (Array.isArray(ac.refs) && ac.refs.length) {
    parts.push(acc('参考文献与依据', '<ol style="margin:0;padding-left:20px">' +
      ac.refs.map((r) => '<li>' + esc(r) + '</li>').join('') + '</ol>'));
  }
  if (!parts.length) return '';
  return '<div class="card mt16"><div class="card-h"><h3>学术模型</h3>' +
    '<span class="sub">' + esc((ac.cards || []).length) + ' 个模型 · 点击展开</span></div>' +
    parts.join('') + '</div>';
}

/* ============================================================
 * 统计分析
 * ============================================================ */
function flat(h) {
  return String(h).replace(/^<div class="card">/, '<div class="card" style="margin-top:0">');
}
function distBlock(title, sub, dist, color) {
  const d = dist || [];
  if (!d.length) return '';
  return '<div><div class="row" style="justify-content:space-between">' +
    '<span class="tiny muted">' + esc(title) + '</span>' +
    '<span class="tiny muted">' + esc(sub || '') + '</span></div>' +
    chartHost({ type: 'bar', color: color || '#2b6cf6',
      values: d.map((x) => x.c), labels: d.map((x) => String(x.k)) }) + '</div>';
}
function viewAnalysis() {
  const code = state.pick.analysis;
  const it = ((state.ana && state.ana.lotteries) || []).find((x) => x.code === code);
  const chipsHtml = '<div class="chips">' + chips('analysis', code) + '</div>';
  if (!it) {
    return pageH('统计分析', '', chipsHtml) +
      '<div class="mt16">' + card('', '', empty('暂无该彩种的统计数据，请先运行数据更新脚本')) + '</div>';
  }
  const digit = isDigit(code);
  const u = it.uniformity || {};
  const sm = it.sum || {};
  const sp = it.span || {};
  const fc = it.forecast || {};

  const head = pageH('统计分析',
    '样本 ' + esc(it.date_from || '—') + ' 至 ' + esc(it.date_to || '—') + ' · 共 ' + num(it.draws, 0) + ' 期',
    chipsHtml);

  const kpis = '<div class="grid g4 mt16">' +
    kpi(T('均匀性检验', TIPS.chi2), num(u.chi2, 2),
      'df = ' + num(u.df, 0) + ' · ' + T('p', TIPS.p) + ' = ' + prob(u.p)) +
    kpi('检验结论', '<span style="font-size:14px">' + (verdictTag(u.verdict) || '—') + '</span>',
      'p<0.05 表示偏离均匀') +
    kpi('和值 80% 区间', '<span style="font-size:17px">' + rng(sm.int80) + '</span>',
      '均值 ' + num(sm.mean, 2) + ' · 标准差 ' + num(sm.std, 2)) +
    kpi('跨度 80% 区间', '<span style="font-size:17px">' + rng(sp.int80) + '</span>',
      '均值 ' + num(sp.mean, 2) + ' · 标准差 ' + num(sp.std, 2)) +
    '</div>';

  const uniCard = card(T('均匀性检验', TIPS.chi2),
    digit ? '各数字位分别做卡方检验' : '各号码实际出现次数 vs 理论期望',
    kv([['卡方统计量 χ²', num(u.chi2, 2)], ['自由度 df', num(u.df, 0)],
      ['p 值', prob(u.p)], ['单号期望次数', num(u.expect, 1)],
      ['结论', verdictTag(u.verdict)]]) +
    (digit ? '' : '<div class="mt16">' + freqGrid(it.freq || [], {}) + '</div>'));

  const om = it.omission || [];
  const omCard = card(T('遗漏', TIPS.omit), '当前连续未开出的期数',
    om.length
      ? table([{ t: '号码' }, { t: '当前遗漏', num: true }, { t: '平均遗漏', num: true },
        { t: '最大遗漏', num: true }, { t: '出现次数', num: true }],
        om.map((x) => [
          balls([x.n], { sm: true, pad: !digit }),
          { h: num(x.cur, 0), num: true, cls: isNum(x.max) && x.cur >= x.max * 0.8 ? 'neg' : '' },
          { h: num(x.avg, 2), num: true },
          { h: num(x.max, 0), num: true },
          { h: num(x.cnt, 0), num: true },
        ]))
      : empty('暂无数据'));

  const histChart = (hist) => chartHost({ type: 'bar', color: '#2b6cf6',
    values: (hist || []).map((x) => x.c), labels: (hist || []).map((x) => String(Math.round(x.x0))) });
  const sumSpan = '<div class="grid g2 mt16">' +
    gcard(T('和值', TIPS.mc) + '分布', '均值 ' + num(sm.mean, 2) + ' · 标准差 ' + num(sm.std, 2),
      kv([['80% 区间', rng(sm.int80)], ['90% 区间', rng(sm.int90)],
        ['中位数', num(sm.median, 1)], ['偏度 / 峰度', num(sm.skew, 2) + ' / ' + num(sm.kurt, 2)],
        ['KS 正态检验 p', prob(sm.ks_p)]]) + histChart(sm.hist)) +
    gcard('跨度分布', '均值 ' + num(sp.mean, 2) + ' · 标准差 ' + num(sp.std, 2),
      kv([['80% 区间', rng(sp.int80)], ['90% 区间', rng(sp.int90)]]) + histChart(sp.hist)) +
    '</div>';

  let posCard = '';
  if (digit) {
    const ps = it.positions || [];
    const posName = (p) => (POS[code] || [])[p - 1] || ('第 ' + p + ' 位');
    posCard = card('各位置分布', '每个位置独立做卡方检验',
      table([{ t: '位置' }, { t: '样本', num: true }, { t: 'χ²', num: true }, { t: 'p 值', num: true },
        { t: '结论' }, { t: '最常见' }, { t: '占比', num: true }],
        ps.map((p) => [
          esc(posName(p.pos)),
          { h: num(p.n, 0), num: true },
          { h: num(p.chi2, 2), num: true },
          { h: prob(p.p), num: true },
          verdictTag(p.verdict),
          balls([p.top], { sm: true }),
          { h: pct(p.top_share), num: true },
        ])) +
      '<div class="mt16">' + ps.map((p) => acc(esc(posName(p.pos)) + ' 频数分布', freqGrid(p.freq, {}))).join('') + '</div>');
  }

  let hotCard = '', zoneCard = '', pairCard = '', specialCard = '';
  if (!digit) {
    if (Array.isArray(it.hot) && it.hot.length) {
      hotCard = card('热号 / 冷号', '按 z 分数排序',
        '<div class="tiny muted">热号（z 偏高）</div><div class="mt8">' + freqGrid(it.hot, {}) + '</div>' +
        '<div class="tiny muted mt16">冷号（z 偏低）</div><div class="mt8">' + freqGrid(it.cold, {}) + '</div>');
    }
    if (Array.isArray(it.zones) && it.zones.length) {
      zoneCard = card('三分区分布',
        it.best_zone ? '热区 ' + esc(it.best_zone.zone) + ' · 占比 ' + pct(it.best_zone.share) : '',
        table([{ t: '分区' }, { t: '实际占比', num: true }, { t: '理论占比', num: true }, { t: '偏差（百分点）', num: true }],
          it.zones.map((z) => [
            esc(z.zone),
            { h: pct(z.share), num: true },
            { h: pct(z.exp), num: true },
            { h: num((z.share - z.exp) * 100, 2), num: true, cls: signCls(z.share - z.exp) },
          ])));
    }
    if (it.pairs && Array.isArray(it.pairs.top) && it.pairs.top.length) {
      pairCard = card('号码共现', '出现次数最多的号码对 · 理论期望 ' + num(it.pairs.expect, 1),
        table([{ t: '号码对' }, { t: '出现次数', num: true }, { t: '理论期望', num: true }, { t: '差值', num: true }],
          it.pairs.top.map((p) => [
            balls([p.a], { sm: true, pad: true }) + balls([p.b], { sm: true, pad: true }),
            { h: num(p.c, 0), num: true },
            { h: num(p.exp, 1), num: true },
            { h: num(p.c - p.exp, 1), num: true, cls: signCls(p.c - p.exp) },
          ])));
    }
    if (it.special) {
      const s = it.special;
      const sname = ((SIDE[code] || [])[1] || '特别号');
      specialCard = card(esc(sname) + '分布', '池 ' + num(s.pool, 0) + ' 选 ' + num(s.pick, 0),
        kv([['卡方', num(s.chi2, 2)], ['p 值', prob(s.p)], ['结论', verdictTag(s.verdict)]]) +
        '<div class="mt16">' + freqGrid(s.freq, {}) + '</div>');
    }
  }

  const distBlocks = [];
  if (it.repeat && Array.isArray(it.repeat.dist)) {
    distBlocks.push(distBlock('重号个数', '均值 ' + num(it.repeat.mean, 2) + ' · 常见 ' + num(it.repeat.mode, 0) + ' 个', it.repeat.dist));
  }
  if (it.odd && Array.isArray(it.odd.dist)) {
    distBlocks.push(distBlock('奇数个数', '最常见 ' + num(it.odd.mode, 0) + ' 个', it.odd.dist, '#0f9d58'));
  }
  if (it.big && Array.isArray(it.big.dist)) {
    distBlocks.push(distBlock('大号个数', '最常见 ' + num(it.big.mode, 0) + ' 个', it.big.dist, '#c2760a'));
  }
  if (it.consec && Array.isArray(it.consec.dist)) {
    distBlocks.push(distBlock('连号组数', '出现率 ' + pct(it.consec.rate), it.consec.dist, '#e5484d'));
  }
  const distCard = distBlocks.length
    ? card('结构分布', '柱高为该取值的期数', '<div class="grid g2">' + distBlocks.join('') + '</div>')
    : '';

  const ind = it.independence || {};
  const acs = ind.autocorr || [];
  const runs = ind.runs || {};
  const indCard = card(T('独立性与随机性', TIPS.run), '自相关检验相邻期是否相关，游程检验整体随机性',
    '<div class="grid g2">' +
    '<div style="min-width:0">' +
    table([{ t: '滞后' }, { t: '自相关 r', num: true }, { t: 'p 值', num: true }, { t: '显著' }],
      acs.map((x) => [
        '第 ' + esc(x.lag) + ' 阶',
        { h: num(x.r, 4), num: true, cls: signCls(x.r) },
        { h: prob(x.p), num: true },
        Number(x.p) < 0.05 ? tag('显著', 'red') : tag('不显著', 'green'),
      ])) +
    '</div><div style="min-width:0">' +
    kv([['游程数', num(runs.runs, 0)], ['理论游程', num(runs.expect, 1)],
      [T('z 分数', TIPS.z), num(runs.z, 3)], ['p 值', prob(runs.p)],
      ['结论', verdictTag(runs.verdict)]]) +
    '</div></div>');

  const fcRows = [
    ['和值 80% / 90%', rng(fc.sum80) + ' / ' + rng(fc.sum90)],
    ['跨度 80%', rng(fc.span80)],
    ['重号', '常见 ' + num(fc.repeat_mode, 0) + ' 个 · 均值 ' + num(fc.repeat_mean, 2) + '（理论 ' + num(fc.repeat_expect, 2) + '）'],
    ['奇数个数前三', (fc.odd_top || []).map((x) => x.k + ' 个 ' + pct(x.share)).join('、') || '—'],
    ['大号个数前三', (fc.big_top || []).map((x) => x.k + ' 个 ' + pct(x.share)).join('、') || '—'],
    ['连号出现率', fc.consec_rate == null ? '数字彩不适用' : pct(fc.consec_rate)],
    ['热区', fc.zone ? esc(fc.zone) + ' · 占比 ' + pct(fc.zone_share) : '数字彩不适用'],
    ['奇数占比', pct(fc.odd_share)],
    ['组合总数', shortNum(fc.combinations)],
    ['头奖概率', esc(fc.odds || '—')],
  ];
  if (digit && Array.isArray(fc.positions)) {
    fcRows.push(['各位置最常见', fc.positions.map((p) =>
      esc((POS[code] || [])[p.pos - 1] || ('第 ' + p.pos + ' 位')) + ' ' + p.top).join(' · ')]);
  }
  const fcCard = card('下期结构参考', '由历史分布推算的常见区间，不构成预测承诺', kv(fcRows));

  let body = head + kpis;
  body += '<div class="grid g2 mt16">' + flat(uniCard) + flat(omCard) + '</div>';
  body += sumSpan;
  if (digit) {
    body += '<div class="mt16">' + posCard + '</div>';
    const extra = [];
    if (Array.isArray(it.digit_freq)) {
      extra.push(gcard('数字总频数', '各位合计后统计', freqGrid(it.digit_freq, {})));
    }
    if (Array.isArray(it.shapes)) {
      extra.push(gcard('形态分布', '按重号结构归类',
        table([{ t: '形态' }, { t: '期数', num: true }, { t: '占比', num: true }],
          it.shapes.map((x) => [esc(x.k), { h: num(x.c, 0), num: true }, { h: pct(x.share), num: true }]))));
    }
    if (extra.length) body += '<div class="grid g2 mt16">' + extra.join('') + '</div>';
  } else {
    if (hotCard && zoneCard) body += '<div class="grid g2 mt16">' + flat(hotCard) + flat(zoneCard) + '</div>';
    else if (hotCard || zoneCard) body += '<div class="mt16">' + (hotCard || zoneCard) + '</div>';
    if (pairCard && specialCard) body += '<div class="grid g2 mt16">' + flat(pairCard) + flat(specialCard) + '</div>';
    else if (pairCard || specialCard) body += '<div class="mt16">' + (pairCard || specialCard) + '</div>';
  }
  if (distCard) body += '<div class="mt16">' + distCard + '</div>';
  body += '<div class="mt16">' + indCard + '</div>';
  body += '<div class="mt16">' + fcCard + '</div>';
  body += '<div class="mt16">' + noteBox('统计结论只描述历史样本：即使卡方检验“显著”，也可能是多重比较带来的偶然偏差，' +
    '并且<b>不会改变未来开奖的独立性</b>。') + '</div>';
  return body;
}

/* ============================================================
 * 开奖数据
 * ============================================================ */
let drawsToken = 0;
const entryCache = {};

function viewDraws() {
  const code = state.pick.draws;
  const m = lotOf(code);
  const limits = [30, 60, 100, 200].map((n) =>
    '<button data-act="draw-limit" data-limit="' + n + '" class="' + (state.drawLimit === n ? 'on' : '') + '">近 ' + n + ' 期</button>').join('');
  const right = '<div class="chips">' + chips('draws', code) + '</div><div class="seg">' + limits + '</div>';
  const head = pageH('开奖数据',
    m ? esc(m.name) + ' · 累计 ' + num(m.count, 0) + ' 期 · ' + esc(m.draw_days || '') + ' · 每注 ' + money((state.meta && state.meta.price) || 2) + ' 元' : '',
    right);
  return head + '<div id="draws-body" class="mt16"><div class="loading"><span class="spin"></span> 正在加载开奖数据…</div></div>';
}

async function loadDraws() {
  const code = state.pick.draws;
  if (!$('#draws-body')) return;
  const token = ++drawsToken;
  try {
    const r = await api('/api/draws?code=' + encodeURIComponent(code) + '&limit=' + state.drawLimit);
    if (token !== drawsToken) return;
    const box = $('#draws-body');
    if (!box) return;
    box.innerHTML = drawTable(code, r);
  } catch (e) {
    if (token !== drawsToken) return;
    const box = $('#draws-body');
    if (box) box.innerHTML = noteBox('开奖数据读取失败：' + esc(e.message));
  }
}

function drawTable(code, r) {
  const rows = (r && r.rows) || [];
  rows.forEach((x) => { entryCache[code + '|' + x.issue] = x; });
  if (!rows.length) return card('', '', empty('暂无开奖记录'));
  const first = rows[0];
  const kpis = '<div class="grid g4">' +
    kpi('可查询期数', num(r.total, 0), '单次最多返回 200 期') +
    kpi('当前显示', num(rows.length, 0) + ' 期', '最新在上') +
    kpi('最近开奖', esc(first.issue), esc(first.date) + ' · 周' + weekdayOf(first.date)) +
    kpi('最近一期销售额', isNum(first.sales) ? shortNum(first.sales) : '—',
      isNum(first.pool) ? '奖池 ' + shortNum(first.pool) : '') +
    '</div>';
  const tb = table([{ t: '期号' }, { t: '开奖日期' }, { t: '开奖号码' },
    { t: '销售额', num: true }, { t: '奖池', num: true }, { t: '派奖明细' }],
    rows.map((x) => [
      { h: '<span class="mono">' + esc(x.issue) + '</span>' },
      { h: esc(x.date) + ' <span class="tiny muted">周' + weekdayOf(x.date) + '</span>' },
      { h: drawBalls(code, x.nums, x.sp, true) },
      { h: isNum(x.sales) ? shortNum(x.sales) : '—', num: true },
      { h: isNum(x.pool) ? shortNum(x.pool) : '—', num: true },
      { h: '<button class="btn sm ghost" data-act="entry" data-code="' + code +
        '" data-issue="' + esc(x.issue) + '">查看</button>', num: true },
    ]),
    (x) => ' data-act="entry" data-code="' + code + '" data-issue="' + esc(x.issue) + '" style="cursor:pointer"');
  return kpis + '<div class="mt16">' +
    card('近 ' + num(rows.length, 0) + ' 期开奖', '数据来自官方公开接口 · 点击任意行查看派奖明细', tb) +
    '</div>';
}

async function findDraw(code, issue) {
  const key = code + '|' + issue;
  if (entryCache[key]) return entryCache[key];
  const r = await api('/api/draws?code=' + encodeURIComponent(code) + '&limit=200');
  ((r && r.rows) || []).forEach((x) => { entryCache[code + '|' + x.issue] = x; });
  return entryCache[key] || null;
}

async function openEntryModal(code, issue) {
  if (!code || !issue) return;
  const bd = modal({
    wide: true,
    title: esc(lotName(code)) + ' · 第 ' + esc(issue) + ' 期',
    body: '<div class="loading"><span class="spin"></span> 正在读取派奖明细…</div>',
  });
  try {
    const r = await api('/api/prizes?code=' + encodeURIComponent(code) + '&issue=' + encodeURIComponent(issue));
    let d = entryCache[code + '|' + issue] || null;
    if (!d) {
      try { d = await findDraw(code, issue); } catch (e) { d = null; }
    }
    let html = '';
    if (d) {
      html += '<div class="row" style="justify-content:space-between;align-items:flex-start;gap:16px">' +
        '<div><div class="tiny muted">开奖号码</div><div class="mt8">' +
        drawBalls(code, d.nums, d.sp) + '</div></div>' +
        '<div class="right"><div class="tiny muted">开奖日期</div>' +
        '<div class="mono">' + esc(d.date) + ' · 周' + weekdayOf(d.date) + '</div></div></div>';
      html += '<div class="mt16">' + kv([
        ['销售额', isNum(d.sales) ? '¥ ' + money(d.sales) : '—'],
        ['开奖后奖池', isNum(d.pool) ? '¥ ' + money(d.pool) : '—'],
      ]) + '</div>';
    } else {
      html += noteBox('未找到该期的开奖号码记录，仅显示派奖明细。');
    }
    const prizes = (r.rows || []).map((x) => [
      esc(x.level || '—'),
      { h: isNum(x.winners) ? num(x.winners, 0) : '—', num: true },
      { h: isNum(x.unit) ? '¥ ' + money(x.unit) : '—', num: true },
      { h: isNum(x.total) ? '¥ ' + money(x.total) : '—', num: true },
      { h: '<span class="tiny muted">' + esc(x.note || '') + '</span>' },
    ]);
    html += '<div class="mt16">' + (prizes.length
      ? table([{ t: '奖级' }, { t: '中奖注数', num: true }, { t: '单注奖金', num: true },
        { t: '合计', num: true }, { t: '备注' }], prizes)
      : empty('官方未公布该期的派奖明细')) + '</div>';
    setModalBody(bd, html);
  } catch (e) {
    setModalBody(bd, noteBox('读取失败：' + esc(e.message)));
  }
}

/* ============================================================
 * 预测盈亏（实盘台账）
 * ============================================================ */
function pnlStatusTag(st) {
  return st === 'settled' ? tag('已结算', 'green') : tag('待开奖', 'amber');
}
/** 一注号码；已结算时按真实开奖高亮命中的球 */
function ticketBalls(code, t, draw) {
  const nums = t.nums || [];
  const sp = t.sp || [];
  if (isDigit(code)) {
    const dn = (draw && draw.nums) || [];
    return '<span class="balls">' + nums.map((n, i) =>
      '<span class="ball sm' + (dn[i] != null && Number(dn[i]) === Number(n) ? ' hit' : '') + '">' +
      esc(n) + '</span>').join('') + '</span>';
  }
  const dn = new Set(((draw && draw.nums) || []).map(Number));
  const dsp = new Set(((draw && draw.sp) || []).map(Number));
  let h = '<span class="balls">' + nums.map((n) =>
    '<span class="ball sm' + (dn.has(Number(n)) ? ' hit' : '') + '">' +
    esc(String(n).padStart(2, '0')) + '</span>').join('');
  h += sp.map((v) =>
    '<span class="ball blue sm' + (dsp.has(Number(v)) ? ' hit' : '') + '">' +
    esc(String(v).padStart(2, '0')) + '</span>').join('');
  return h + '</span>';
}
/** 复式方案（lotto：前区红球 + 后区蓝球；digit：按位号码组） */
function parlayBalls(code, par) {
  if (!par) return '';
  if (isDigit(code)) {
    return '<span class="balls">' + (par.front || []).map((g) =>
      '<span class="ball sm">' + esc(Array.isArray(g) ? g.join('') : g) + '</span>').join('') + '</span>';
  }
  let h = balls(par.front, { sm: true, pad: true });
  if ((par.back || []).length) h += balls(par.back, { sm: true, pad: true, blue: true });
  return h;
}

function viewPnl() {
  const d = state.pnl;
  if (!d) return pageH('预测盈亏', '') + card('', '', empty('台账数据尚未生成'));
  const t = d.totals || {};
  const head = pageH('预测盈亏',
    '每期预测在下期开奖前登记，开奖后按真实号码与官方单注奖金结算，不做任何事后调整。',
    '<span class="tiny muted">台账起点 ' + esc(d.first_at || '—') + ' · 更新于 ' + esc(d.generated_at || '—') + '</span>');
  const kpis = '<div class="grid g3">' +
    kpi('已结算期数', num(t.settled, 0) + ' 期', '按真实开奖逐期结算') +
    kpi('待开奖期数', num(t.pending, 0) + ' 期', '已登记，等待官方开奖') +
    kpi(T('累计投入'), '¥ ' + money(t.cost), '5 注单式 + 1 组复式加买') +
    kpi('累计奖金', '¥ ' + money(t.prize), '按官方派奖单注奖金计算') +
    kpi('净盈亏', signedMoney(t.net), '奖金 − 投入', signCls(t.net)) +
    kpi(T('ROI'), isNum(t.roi) ? pct(t.roi) : '—', '净盈亏 ÷ 投入', signCls(t.roi)) +
    '</div>';
  const intro = '<div class="mt16">' + noteBox(
    esc(d.note || '') + '<br><span class="tiny">' + esc(d.method || '') + '</span>', 'blue') + '</div>';
  const lots = d.lotteries || [];
  const sec = lots.length
    ? '<div class="mt16">' + lots.map(pnlLotCard).join('') + '</div>'
    : '<div class="mt16">' + card('', '', empty('暂无登记记录')) + '</div>';
  return head + kpis + intro + sec;
}

function pnlLotCard(l) {
  const code = l.code;
  const c = l.current;
  const s = l.summary || {};
  const entries = l.entries || [];
  if (!c) return gcardWrapped(esc(l.name), '', empty('暂无登记记录'));

  const draw = c.draw;
  const headLine = '<div class="row" style="justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap">' +
    '<div><div class="tiny muted">当前登记期</div>' +
    '<div class="mono" style="font-size:15px;font-weight:600;margin-top:2px">第 ' + esc(c.issue) + ' 期 ' +
    '<span class="tiny muted" style="font-weight:400">' + esc(c.date || '') + ' · 周' + weekdayOf(c.date) + '</span> ' +
    pnlStatusTag(c.status) + '</div></div>' +
    (draw
      ? '<div class="right"><div class="tiny muted">真实开奖</div><div class="mt8">' +
        drawBalls(code, draw.nums, draw.sp, true) + '</div></div>'
      : '<div class="right tiny muted">开奖后自动结算</div>') +
    '</div>';

  const rows = [
    ['预测时间', esc(c.predicted_at || '—')],
    ['预测依据', '第 ' + esc(c.based_on_issue || '—') + ' 期开奖前的历史数据'],
    ['单式投入', '¥ ' + money(c.cost_single) + ' <span class="tiny muted">' +
      num((c.tickets || []).length, 0) + ' 注 × ' + money((state.pnl && state.pnl.price) || 2) + ' 元</span>'],
    ['复式加买', c.parlay
      ? '¥ ' + money(c.cost_parlay) + ' <span class="tiny muted">' + esc(c.parlay.label || '') +
        ' · ' + num(c.parlay.bets, 0) + ' 注</span>'
      : tag('未加买', '')],
    ['本期投入', '¥ ' + money(c.cost_total)],
    ['本期奖金', c.status === 'settled' ? '¥ ' + money(c.prize_total) : '—'],
    ['本期净盈亏', c.status === 'settled'
      ? '<span class="' + signCls(c.net) + '">' + signedMoney(c.net) + '</span>' : '—'],
    ['累计净盈亏', '<span class="' + signCls(c.cum_net) + '">' + signedMoney(c.cum_net) + '</span>'],
  ];

  const tk = (c.tickets || []).map((x, i) =>
    '<div class="ticket-line"><span class="tiny muted" style="width:48px">第 ' + (i + 1) + ' 注</span>' +
    ticketBalls(code, x, draw) + '</div>').join('') +
    (c.parlay
      ? '<div class="ticket-line"><span class="tiny muted" style="width:48px">复式</span>' +
        parlayBalls(code, c.parlay) + '<span class="spacer"></span>' +
        '<span class="tiny muted">' + esc(c.parlay.label || '') + ' · ' + num(c.parlay.bets, 0) + ' 注</span></div>'
      : '');

  const summed = kv([
    ['已结算 / 待开奖', num(s.periods, 0) + ' / ' + num(s.pending, 0) + ' 期'],
    ['累计投入 / 奖金', '¥ ' + money(s.cost) + ' / ¥ ' + money(s.prize)],
    ['净盈亏', '<span class="' + signCls(s.net) + '">' + signedMoney(s.net) + '</span>'],
    [T('ROI'), isNum(s.roi) ? pct(s.roi) : '—'],
    ['命中奖金的期数', num(s.hit_periods, 0) + ' 期'],
  ]);

  const eRows = entries.slice().reverse().map((e) => [
    { h: '<span class="mono">' + esc(e.issue) + '</span>' },
    { h: esc(e.date || '—') },
    { h: pnlStatusTag(e.status) },
    { h: e.draw ? drawBalls(code, e.draw.nums, e.draw.sp, true) : '<span class="tiny muted">—</span>' },
    { h: '¥ ' + money(e.cost_total), num: true },
    { h: e.status === 'settled' ? '¥ ' + money(e.prize_total) : '—', num: true },
    { h: e.status === 'settled' ? '<span class="' + signCls(e.net) + '">' + signedMoney(e.net) + '</span>' : '—', num: true },
    { h: '<span class="' + signCls(e.cum_net) + '">' + signedMoney(e.cum_net) + '</span>', num: true },
  ]);
  const eTbl = eRows.length
    ? table([{ t: '期号' }, { t: '开奖日期' }, { t: '状态' }, { t: '开奖号码' },
      { t: '投入', num: true }, { t: '奖金', num: true }, { t: '净盈亏', num: true }, { t: '累计', num: true }],
      eRows,
      (r) => ' data-act="pentry" data-code="' + code + '" style="cursor:pointer"')
    : empty('暂无历史期次');

  const lv = (s.levels || []).length
    ? table([{ t: '奖级' }, { t: '单式中奖注数', num: true }, { t: '复式中奖注数', num: true },
      { t: '合计金额', num: true }],
      s.levels.map((x) => [
        { h: tag(x.level, 'blue') },
        { h: num(x.single, 0), num: true },
        { h: num(x.parlay, 0), num: true },
        { h: '¥ ' + money(x.amount), num: true },
      ]))
    : empty('尚无中奖记录');

  const body = headLine +
    '<div class="mt16">' + kv(rows) + '</div>' +
    '<div class="mt16">' + summed + '</div>' +
    '<div class="mt16">' + acc('本期号码（' + num((c.tickets || []).length, 0) + ' 注单式' +
      (c.parlay ? ' + ' + num(c.parlay.bets, 0) + ' 注复式' : '') + '）', tk) + '</div>' +
    '<div class="mt16">' + acc('历史明细（' + num(entries.length, 0) + ' 期）', eTbl) + '</div>' +
    '<div class="mt16">' + acc('奖级命中汇总', lv) + '</div>';

  return gcardWrapped(esc(l.name) + ' ' + pnlStatusTag(c.status),
    '台账自 ' + esc(l.first_at || '—') + ' 起 · 已结算 ' + num(s.periods, 0) + ' 期', body);
}
function gcardWrapped(title, sub, body) {
  return '<div>' + card(title, sub, body) + '</div>';
}

/* ============================================================
 * 模拟收益（用户策略）
 * ============================================================ */
function specLine(sp) {
  const s = sp || {};
  const codes = (s.codes || []).map((c) => NAME[c] || c).join('、') || '—';
  return [
    MODE_LABEL[s.mode] || s.mode || '—',
    codes,
    '每期 ' + num(s.tickets, 0) + ' 注',
    s.parlay ? '含复式加买' : '仅单式',
    '起点 ' + esc(s.start_date || '—'),
    num(s.periods, 0) + ' 期',
  ].join(' · ');
}
function stratRow(sp) {
  const s = sp.summary;
  return '<div class="strat">' +
    '<div style="min-width:200px"><div class="nm">' + esc(sp.name) + '</div>' +
    '<div class="meta">' + specLine(sp.spec) + '</div></div>' +
    '<div class="nums">' +
    '<div><b class="' + (s ? signCls(s.net) : '') + '">' + (s ? signedMoney(s.net) : '—') + '</b><span>净盈亏</span></div>' +
    '<div><b>' + (s && isNum(s.roi) ? pct(s.roi) : '—') + '</b><span>ROI</span></div>' +
    '<div><b>' + (s ? num(s.periods, 0) : '—') + '</b><span>已购期数</span></div>' +
    '<div><b>' + (s ? '¥ ' + money(s.final) : '—') + '</b><span>期末资金</span></div>' +
    '</div>' +
    '<div class="row" style="gap:8px">' +
    '<button class="btn sm" data-act="simview" data-id="' + sp.id + '">查看</button>' +
    '<button class="btn sm primary" data-act="simrun" data-id="' + sp.id + '">运行</button>' +
    '<button class="btn sm danger" data-act="simdel" data-id="' + sp.id + '">删除</button>' +
    '</div></div>';
}

function viewSim() {
  const head = pageH('模拟收益',
    '用虚拟资金池按固定策略逐期购买，按真实开奖与官方奖金结算 —— 检验一套玩法能不能赚钱。');
  if (!state.authChecked) {
    return head + '<div class="loading"><span class="spin"></span> 正在校验登录状态…</div>';
  }
  const u = state.me && state.me.user;
  if (!u) {
    return head + card('登录后使用模拟收益', '',
      '<p class="muted" style="font-size:13px;line-height:1.9;margin:0 0 14px">' +
      '模拟收益会把你的策略、资金池与历史运行结果保存在账户下，随时可以重跑对比。' +
      '选号支持三种方式：' + esc(MODE_LABEL.custom) + '、' + esc(MODE_LABEL.random) + '、' +
      esc(MODE_LABEL.system) + '（只用每期之前的历史数据打分，不含未来信息）。</p>' +
      '<div class="row"><button class="btn primary" data-act="auth" data-tab="register">注册账户</button>' +
      '<button class="btn" data-act="auth" data-tab="login">已有账户，登录</button></div>' +
      '<div class="mt16">' + noteBox('彩票返奖率约 50%，任何策略的长期期望都为负。模拟结果只用于理解概率与波动，不构成投注建议。', 'blue') + '</div>');
  }
  const list = (state.me && state.me.strategies) || [];
  const kpis = '<div class="grid g3">' +
    kpi('当前资金池', '¥ ' + money(u.wallet), '顶栏可修改，作为新策略的初始资金') +
    kpi('已保存策略', num(list.length, 0) + ' 个', '每个策略独立资金池与期次') +
    kpi('单注价格', '¥ ' + money((state.meta && state.meta.price) || 2), '复式按方案注数 × 2 元') +
    '</div>';
  const listCard = card('我的策略', '策略可多个、购买方式可多个，互不影响',
    list.length ? list.map(stratRow).join('') : empty('还没有策略，点击右上角“新建策略”开始'),
    '<button class="btn sm primary" data-act="s-open">新建策略</button>');
  const sim = state.sim ? '<div class="mt16">' + simResult() + '</div>' : '';
  return head + kpis + '<div class="mt16">' + listCard + '</div>' + sim;
}

function simResult() {
  const S = state.sim;
  const r = S.result;
  const extra = '<button class="btn sm ghost" data-act="simclose">关闭结果</button>';
  if (!r) {
    return '<div id="sim-result">' + card(esc(S.name || '模拟结果'), '',
      S.pending
        ? '<div class="loading"><span class="spin"></span> 正在按真实开奖逐期结算…</div>'
        : empty('该策略还没有运行结果，点击“运行”开始结算'),
      extra) + '</div>';
  }
  const s = r.summary || {};
  const kpis = '<div class="grid g4">' +
    kpi('初始资金', '¥ ' + money(s.initial), '策略设定的资金池') +
    kpi('期末资金', '¥ ' + money(s.final), '初始 ' + signedMoney((s.final || 0) - (s.initial || 0)),
      signCls((s.final || 0) - (s.initial || 0))) +
    kpi('累计投入', '¥ ' + money(s.cost), '实际购买 ' + num(s.periods, 0) + ' 期') +
    kpi('累计奖金', '¥ ' + money(s.prize), '命中 ' + num(s.hit_periods, 0) + ' 期') +
    kpi('净盈亏', signedMoney(s.net), '奖金 − 投入', signCls(s.net)) +
    kpi(T('ROI'), isNum(s.roi) ? pct(s.roi) : '—', '净盈亏 ÷ 投入', signCls(s.roi)) +
    kpi('计划 / 实际期数', num(s.total_periods, 0) + ' / ' + num(s.periods, 0),
      '按起始日期与期数取真实开奖') +
    kpi('资金不足跳过', num(s.skipped, 0) + ' 期', '余额不足当期的投入时不购买') +
    '</div>';

  const ser = r.series || [];
  const chart = ser.length > 1
    ? chartHost({
      series: [{ values: ser.map((x) => x.cum_net), color: '#2b6cf6' }],
      labels: ser.map((x) => String(x.date || '').slice(5)),
      zero: true,
    })
    : empty('购买期数不足，暂不绘制曲线');

  const byCode = (s.by_code || []).length
    ? table([{ t: '彩种' }, { t: '购买期数', num: true }, { t: '投入', num: true },
      { t: '奖金', num: true }, { t: '净盈亏', num: true }, { t: 'ROI', num: true }],
      (s.by_code || []).map((x) => [
        { h: esc(x.name || NAME[x.code] || x.code) },
        { h: num(x.periods, 0), num: true },
        { h: '¥ ' + money(x.cost), num: true },
        { h: '¥ ' + money(x.prize), num: true },
        { h: '<span class="' + signCls(x.net) + '">' + signedMoney(x.net) + '</span>', num: true },
        { h: isNum(x.roi) ? pct(x.roi) : '—', num: true },
      ]))
    : empty('暂无分彩种数据');

  const lv = (s.levels || []).length
    ? table([{ t: '奖级' }, { t: '命中注数', num: true }, { t: '金额', num: true }],
      (s.levels || []).map((x) => [
        { h: tag(x.level, 'blue') },
        { h: num(x.count, 0), num: true },
        { h: '¥ ' + money(x.amount), num: true },
      ]))
    : empty('本次模拟没有中奖');

  const notes = '<ul style="margin:0;padding-left:18px;font-size:12.5px;line-height:1.9;color:var(--ink-2)">' +
    (r.notes || []).map((x) => '<li>' + esc(x) + '</li>').join('') + '</ul>';

  const all = r.rows || [];
  const MAX = 60;
  const keep = all.slice(Math.max(0, all.length - MAX));
  const disp = keep.map((x, i) => ({ x: x, idx: all.length - keep.length + i })).reverse();
  const rows = disp.map((o) => [
    { h: '<span class="mono">' + esc(o.x.issue) + '</span>' },
    { h: esc(o.x.date || '') },
    { h: esc(NAME[o.x.code] || o.x.code) },
    { h: drawBalls(o.x.code, o.x.draw.nums, o.x.draw.sp, true) },
    { h: '¥ ' + money(o.x.cost), num: true },
    { h: '¥ ' + money(o.x.prize), num: true },
    { h: '<span class="' + signCls(o.x.net) + '">' + signedMoney(o.x.net) + '</span>', num: true },
    { h: '¥ ' + money(o.x.balance), num: true },
  ]);
  const rowsTbl = rows.length
    ? table([{ t: '期号' }, { t: '开奖日期' }, { t: '彩种' }, { t: '开奖号码' },
      { t: '投入', num: true }, { t: '奖金', num: true }, { t: '净盈亏', num: true }, { t: '结算后资金', num: true }],
      rows,
      (rr, i) => ' data-act="simrow" data-idx="' + disp[i].idx + '" style="cursor:pointer"')
    : empty('没有实际购买的期次，可能起始日期晚于最新开奖');

  return '<div id="sim-result">' +
    card(esc(S.name || '模拟结果') + ' ' + tag(MODE_LABEL[(S.spec && S.spec.mode) || ''] || '—', 'blue'),
      specLine(S.spec), kpis, extra) +
    '<div class="grid g2 mt16">' +
    '<div>' + card('累计净盈亏曲线', '每购买一期后按真实开奖结算更新，0 线以上为盈利', chart) + '</div>' +
    '<div>' + card('分彩种表现', '同一策略内不同彩种的独立表现', byCode) + '</div>' +
    '</div>' +
    '<div class="grid g2 mt16">' +
    '<div>' + card('奖级命中', '单式与复式的合计命中', lv) + '</div>' +
    '<div>' + card('口径说明', '', notes) + '</div>' +
    '</div>' +
    '<div class="mt16">' + card('逐期明细' +
      (all.length > MAX ? '（最近 ' + MAX + ' 期）' : ''),
      '点击任意行查看该期的号码与命中明细', rowsTbl) + '</div>' +
    '</div>';
}

async function loadStrategy(id) {
  if (!id) return;
  try {
    const r = await api('/api/strategies/' + encodeURIComponent(id));
    state.sim = { id: r.id, name: r.name, spec: r.spec, result: r.result || null };
    render();
    const el = document.getElementById('sim-result');
    if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (!r.result) toast('该策略尚未运行，点击“运行”开始结算');
  } catch (e) {
    toast(e.message, true);
  }
}

async function runStrategy(id) {
  if (!id) return;
  const btn = document.querySelector('[data-act="simrun"][data-id="' + id + '"]');
  if (btn) { btn.disabled = true; btn.textContent = '运行中…'; }
  try {
    const r = await api('/api/strategies/' + encodeURIComponent(id) + '/run', { method: 'POST' });
    await refreshMe(true);
    state.sim = {
      id: Number(id),
      name: (r.result && r.result.strategy_name) || '策略',
      spec: (r.result && r.result.spec) || null,
      result: r.result,
    };
    render();
    const el = document.getElementById('sim-result');
    if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    toast('结算完成');
  } catch (e) {
    toast(e.message, true);
    if (btn) { btn.disabled = false; btn.textContent = '运行'; }
  }
}

async function deleteStrategy(id) {
  if (!id) return;
  if (!window.confirm('删除策略会同时清除它的历史运行结果，确定删除？')) return;
  try {
    await api('/api/strategies/' + encodeURIComponent(id), { method: 'DELETE' });
    if (state.sim && String(state.sim.id) === String(id)) state.sim = null;
    await refreshMe(true);
    render();
    toast('策略已删除');
  } catch (e) {
    toast(e.message, true);
  }
}

function openRowModal(idx) {
  const S = state.sim;
  const r = S && S.result;
  const row = r && (r.rows || [])[Number(idx)];
  if (!row) return;
  const code = row.code;
  const draw = row.draw;

  const head = '<div class="row" style="justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap">' +
    '<div><div class="tiny muted">真实开奖</div><div class="mt8">' +
    drawBalls(code, draw.nums, draw.sp) + '</div></div>' +
    '<div class="right"><div class="tiny muted">开奖日期</div>' +
    '<div class="mono">' + esc(row.date) + ' · 周' + weekdayOf(row.date) + '</div></div></div>';

  const tk = (row.tickets || []).map((t, i) =>
    '<div class="ticket-line"><span class="tiny muted" style="width:48px">第 ' + (i + 1) + ' 注</span>' +
    ticketBalls(code, t, draw) + '<span class="spacer"></span>' +
    (t.level ? tag(t.level, 'green') : tag('未中奖', '')) +
    '<b class="mono">¥ ' + money(t.prize) + '</b></div>').join('');

  let par = '';
  if (row.parlay) {
    par = '<div class="ticket-line"><span class="tiny muted" style="width:48px">复式</span>' +
      parlayBalls(code, row.parlay) +
      '<span class="tiny muted">' + esc(row.parlay.label || '') + ' · ' + num(row.parlay.bets, 0) + ' 注</span>' +
      '<span class="spacer"></span><b class="mono">¥ ' + money(row.parlay_prize) + '</b></div>';
  }

  const kvRows = [
    ['本期投入', '¥ ' + money(row.cost) + ' <span class="tiny muted">单式 ¥ ' + money(row.cost_tickets) +
      (row.cost_parlay ? ' + 复式 ¥ ' + money(row.cost_parlay) : '') + '</span>'],
    ['本期奖金', '¥ ' + money(row.prize) +
      (row.parlay_prize ? ' <span class="tiny muted">含复式 ¥ ' + money(row.parlay_prize) + '</span>' : '')],
    ['本期净盈亏', '<span class="' + signCls(row.net) + '">' + signedMoney(row.net) + '</span>'],
    ['结算后资金', '¥ ' + money(row.balance)],
    ['累计净盈亏', '<span class="' + signCls(row.cum_net) + '">' + signedMoney(row.cum_net) + '</span>'],
  ];

  const lvKeys = Object.keys(row.level_count || {});
  const lv = lvKeys.length
    ? table([{ t: '奖级' }, { t: '注数', num: true }, { t: '金额', num: true }],
      lvKeys.map((k) => [
        { h: tag(k, 'blue') },
        { h: num(row.level_count[k], 0), num: true },
        { h: '¥ ' + money(row.level_amount && row.level_amount[k]), num: true },
      ]))
    : '';

  const html = head +
    '<div class="mt16">' + kv(kvRows) + '</div>' +
    '<div class="mt16">' + tk + par + '</div>' +
    (lv ? '<div class="mt16">' + lv + '</div>' : '');

  modal({
    wide: true,
    title: esc(NAME[code] || code) + ' · 第 ' + esc(row.issue) + ' 期 · 模拟明细',
    body: html,
  });
}

/* ============================================================
 * 新建策略（弹窗）
 * ============================================================ */
const SIMD = { bd: null, sd: null };
// 开奖星期（0=周日）；空数组表示每日开奖
const DRAW_WD = { ssq: [0, 2, 4], dlt: [1, 3, 6], qlc: [1, 3, 5], kl8: [], '3d': [], qxc: [0, 2, 5] };

function ymd(d) {
  const p = (n) => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}
function drawsPerWeek(code) {
  const w = DRAW_WD[code];
  return w && w.length ? w.length : 7;
}
/** 采样 n 个不重复整数（闭区间 lo..hi），升序 */
function sampleN(lo, hi, n) {
  const pool = [];
  for (let i = lo; i <= hi; i++) pool.push(i);
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    const t = pool[i]; pool[i] = pool[j]; pool[j] = t;
  }
  return pool.slice(0, n).sort((a, b) => a - b);
}

function newSpec() {
  const u = state.me && state.me.user;
  const sd = {
    name: '', codes: ['ssq'], mode: 'random', tickets: 5, periods: 30,
    parlay: true, start_date: '',
    initial: u && isNum(u.wallet) ? Number(u.wallet) : 1000,
    custom: {},
  };
  resetCustom(sd, 'ssq');
  suggestStart(sd, false);
  return sd;
}
/** 按“计划期数”反推一个能覆盖足够期次的起始日期 */
function suggestStart(sd, repaint) {
  if (!sd) return;
  const code = sd.codes[0];
  const lot = lotOf(code);
  const periods = Math.max(1, Math.min(200, Number(sd.periods) || 30));
  let d;
  if (lot && lot.last_date) {
    const weeks = (periods - 1) / drawsPerWeek(code);
    d = new Date(lot.last_date + 'T00:00:00');
    d.setDate(d.getDate() - Math.ceil(weeks * 7) - 1);
    if (lot.first_date && ymd(d) < lot.first_date) d = new Date(lot.first_date + 'T00:00:00');
  } else {
    d = new Date();
    d.setDate(d.getDate() - periods);
  }
  sd.start_date = ymd(d);
  if (repaint) paintStrategy();
}
function resetCustom(sd, code) {
  if (!sd) return;
  sd.custom[code || sd.codes[0]] = { front: [], back: [], nums: [] };
}
function customReady(sd) {
  const code = sd.codes[0];
  const lot = lotOf(code);
  if (!lot) return false;
  const cu = sd.custom[code] || {};
  if (lot.type === 'digit') {
    const pos = lot.positions || [];
    const nums = cu.nums || [];
    return pos.every((_, i) => nums[i] != null);
  }
  if ((cu.front || []).length !== lot.front_pick) return false;
  if (lot.back_pick && (cu.back || []).length !== lot.back_pick) return false;
  return true;
}
function randomCustom(sd) {
  const code = sd.codes[0];
  const lot = lotOf(code);
  if (!lot) return;
  if (!sd.custom[code]) resetCustom(sd, code);
  const cu = sd.custom[code];
  if (lot.type === 'digit') {
    cu.nums = (lot.positions || []).map((pool) => Math.floor(Math.random() * pool));
  } else {
    cu.front = sampleN(1, lot.front_pool, lot.front_pick);
    cu.back = lot.back_pick ? sampleN(1, lot.back_pool, lot.back_pick) : [];
  }
  repaintKeepScroll();
}
function repaintKeepScroll() {
  const body = SIMD.bd && SIMD.bd.querySelector('.modal-b');
  const sc = body ? body.scrollTop : 0;
  paintStrategy();
  const b2 = SIMD.bd && SIMD.bd.querySelector('.modal-b');
  if (b2) b2.scrollTop = sc;
}

/** 自选号码区 */
function customBlock(sd) {
  const code = sd.codes[0];
  const lot = lotOf(code);
  if (!lot) return '';
  if (!sd.custom[code]) resetCustom(sd, code);
  const cu = sd.custom[code];
  const side = SIDE[code] || ['号码', '后区'];
  let body = '';
  if (lot.type === 'digit') {
    body = (lot.positions || []).map((pool, i) => {
      const v = (cu.nums || [])[i];
      const nm = (POS[code] || [])[i] || ('第 ' + (i + 1) + ' 位');
      return '<div class="pick-block"><div class="t"><span>' + esc(nm) + '</span>' +
        '<span>' + (v == null ? '未选' : '已选 ' + v) + '</span></div>' +
        '<div class="pick-grid">' + Array.from({ length: pool }, (_, n) =>
          '<button class="pick ' + (v === n ? 'on' : '') + '" data-act="s-num" data-side="p' + i +
          '" data-n="' + n + '">' + n + '</button>').join('') + '</div></div>';
    }).join('');
  } else {
    body = '<div class="pick-block"><div class="t"><span>' + esc(side[0]) + ' · 选 ' + lot.front_pick + ' 个</span>' +
      '<span>' + (cu.front || []).length + ' / ' + lot.front_pick + '</span></div>' +
      '<div class="pick-grid">' + Array.from({ length: lot.front_pool }, (_, k) => k + 1).map((n) =>
        '<button class="pick ' + ((cu.front || []).indexOf(n) >= 0 ? 'on' : '') +
        '" data-act="s-num" data-side="front" data-n="' + n + '">' + n + '</button>').join('') +
      '</div></div>';
    if (lot.back_pick) {
      body += '<div class="pick-block"><div class="t"><span>' + esc(side[1] || '后区') + ' · 选 ' + lot.back_pick + ' 个</span>' +
        '<span>' + (cu.back || []).length + ' / ' + lot.back_pick + '</span></div>' +
        '<div class="pick-grid">' + Array.from({ length: lot.back_pool }, (_, k) => k + 1).map((n) =>
          '<button class="pick ' + ((cu.back || []).indexOf(n) >= 0 ? 'on blue' : '') +
          '" data-act="s-num" data-side="back" data-n="' + n + '">' + n + '</button>').join('') +
        '</div></div>';
    }
  }
  return '<div class="mt16"><div class="row"><span class="lab" style="margin:0">自选号码</span>' +
    '<span class="tiny muted">' + esc(lot.desc || '') + '</span><span class="spacer"></span>' +
    '<button class="btn sm ghost" data-act="s-num" data-side="rand">随机一注</button>' +
    '<button class="btn sm ghost" data-act="s-reset">清空</button></div>' + body +
    '<div class="tiny ' + (customReady(sd) ? 'muted' : '') + ' mt8">' +
    (customReady(sd) ? '号码已就绪：每期都用这一组号码购买' : '请按规则选满号码后才能创建策略') + '</div></div>';
}

function estCost(sd) {
  const price = Number((state.meta && state.meta.price) || 2);
  const tickets = Math.max(1, Number(sd.tickets) || 1);
  const parlayBets = sd.parlay ? sd.codes.reduce((s, c) => s + (PAR[c] || 0), 0) : 0;
  const per = (tickets + parlayBets) * price;
  const periods = Math.max(1, Math.round(Number(sd.periods) || 1));
  return { price, tickets, parlayBets, per, periods, total: per * periods, initial: Number(sd.initial) || 0 };
}
function paintEst() {
  const box = document.getElementById('st-est');
  const sd = SIMD.sd;
  if (!box || !sd) return;
  const e = estCost(sd);
  const cover = e.per > 0 ? Math.floor(e.initial / e.per) : 0;
  const enough = e.initial >= e.total;
  box.innerHTML = '<div class="grid g3">' +
    kpi('每期投入', '¥ ' + money(e.per), e.parlayBets
      ? '单式 ' + e.tickets + ' 注 + 复式 ' + e.parlayBets + ' 注'
      : '仅单式 ' + e.tickets + ' 注') +
    kpi('全程预算', '¥ ' + money(e.total), e.periods + ' 期 × ¥ ' + money(e.per)) +
    kpi('初始资金池', '¥ ' + money(e.initial),
      enough ? '预算内，资金充足' : '约可购买 ' + cover + ' 期，之后跳过', enough ? '' : 'neg') +
    '</div>' +
    (enough ? '' : noteBox('资金池不足以覆盖全部计划期数：资金不足的期次会被跳过（余额不会为负），不会被自动补钱。'));
}

function paintStrategy() {
  const bd = SIMD.bd;
  const sd = SIMD.sd;
  if (!bd || !sd) return;
  const code = sd.codes[0];
  const lim = (state.meta && state.meta.limits) || {};
  const sys = sd.mode === 'system';
  const capT = Math.max(1, Number(sys ? lim.tickets_system : lim.tickets) || (sys ? 5 : 10));
  const capP = Math.max(1, Number(sys ? lim.periods_system : lim.periods) || (sys ? 30 : 60));
  const price = money((state.meta && state.meta.price) || 2);
  const multi = sd.mode !== 'custom';

  const curT = Math.max(1, Number(sd.tickets) || 1);
  const presets = [1, 2, 3, 5, 10].filter((v) => v <= capT);
  if (presets.indexOf(curT) < 0) presets.push(curT);
  presets.sort((a, b) => a - b);

  const modeHint = {
    random: '每期由随机数生成号码，用于衡量“纯运气”的基准表现。',
    system: '每期只用该期之前的历史数据打分选号，绝不含未来信息；注数与期数上限更严（' +
      (Number(lim.tickets_system) || 5) + ' 注 / ' + (Number(lim.periods_system) || 30) + ' 期）。',
    custom: '固定使用你自己选定的一组号码，每期重复购买；只支持单彩种。',
  }[sd.mode] || '';

  let html = field('策略名称',
    '<input class="inp" id="st-name" data-f="name" maxlength="40" placeholder="留空则按彩种与方式自动命名" value="' + esc(sd.name) + '">',
    '策略保存在账户下，可创建多个并行对比');

  html += '<div class="field"><span class="lab">彩种 <span class="tiny muted">' +
    (multi ? '可多选，共用同一资金池' : '自选模式只支持一个彩种') + '</span></span>' +
    '<div class="chips">' + CODES.map((c) =>
      '<button class="chip ' + (sd.codes.indexOf(c) >= 0 ? 'on' : '') + '" data-act="s-code" data-code="' + c +
      '"><span class="dot"></span>' + NAME[c] + '</button>').join('') + '</div></div>';

  html += '<div class="field"><span class="lab">选号方式</span><div class="seg">' +
    ['random', 'system', 'custom'].map((m) =>
      '<button data-act="s-mode" data-mode="' + m + '" class="' + (sd.mode === m ? 'on' : '') + '">' +
      MODE_LABEL[m] + '</button>').join('') +
    '</div><div class="tiny muted mt8">' + esc(modeHint) + '</div></div>';

  html += '<div class="form-grid">' +
    field('每期注数（单式）',
      '<div class="seg">' + presets.map((v) =>
        '<button data-act="s-num" data-t="' + v + '" class="' + (curT === v ? 'on' : '') + '">' + v + ' 注</button>').join('') +
      '</div>', '每注 ' + price + ' 元，上限 ' + capT + ' 注') +
    field('计划期数',
      '<input class="inp" data-f="periods" type="number" min="1" max="' + capP + '" step="1" value="' + esc(sd.periods) + '">',
      '从起始日期起连续购买，上限 ' + capP + ' 期') +
    field('起始日期',
      '<input class="inp" id="st-start" data-f="start_date" type="date" value="' + esc(sd.start_date) + '">' +
      '<button class="btn sm ghost mt8" data-act="s-suggest">按当前期数建议</button>',
      '按该日期（含）之后的真实开奖逐期结算') +
    field('初始资金池',
      '<input class="inp" data-f="initial" type="number" min="1" step="100" value="' + esc(sd.initial) + '">',
      '保存策略时同步为账户资金池') +
    '</div>';

  html += '<div class="field"><span class="lab">购买方法</span><div class="row">' +
    '<button class="btn sm ' + (sd.parlay ? 'primary' : '') + '" data-act="s-parlay">' +
    (sd.parlay ? '单式 + 复式加买' : '仅单式') + '</button>' +
    '<span class="tiny muted">' + (sd.parlay
      ? '每期额外买 1 组复式，约 ' + sd.codes.reduce((s, c) => s + (PAR[c] || 0), 0) + ' 注（按方案注数 × ' + price + ' 元）'
      : '每期只买单式，投入最小') + '</span></div></div>';

  if (sd.mode === 'custom') html += customBlock(sd);

  html += '<div class="mt16" id="st-est"></div>';

  setModalBody(bd, html);
  paintEst();
  void code;
}

function onStratCode(code) {
  const sd = SIMD.sd;
  if (!sd || !NAME[code]) return;
  if (sd.mode === 'custom') {
    sd.codes = [code];
  } else {
    const i = sd.codes.indexOf(code);
    if (i >= 0) {
      if (sd.codes.length === 1) { toast('至少保留一个彩种', true); return; }
      sd.codes.splice(i, 1);
    } else {
      sd.codes.push(code);
      sd.codes = CODES.filter((c) => sd.codes.indexOf(c) >= 0);
    }
  }
  if (!sd.custom[code]) resetCustom(sd, code);
  suggestStart(sd, false);
  paintStrategy();
}
function onStratMode(mode) {
  const sd = SIMD.sd;
  if (!sd || !MODE_LABEL[mode]) return;
  sd.mode = mode;
  const lim = (state.meta && state.meta.limits) || {};
  if (mode === 'custom') sd.codes = [sd.codes[0] || 'ssq'];
  if (mode === 'system') {
    const capT = Number(lim.tickets_system) || 5;
    const capP = Number(lim.periods_system) || 30;
    if (Number(sd.tickets) > capT) sd.tickets = capT;
    if (Number(sd.periods) > capP) sd.periods = capP;
  }
  if (!sd.custom[sd.codes[0]]) resetCustom(sd, sd.codes[0]);
  suggestStart(sd, false);
  paintStrategy();
}
function onStratParlay() {
  if (!SIMD.sd) return;
  SIMD.sd.parlay = !SIMD.sd.parlay;
  paintStrategy();
}
function toggleCustom(sd, side, n) {
  const code = sd.codes[0];
  const lot = lotOf(code);
  if (!lot || !isFinite(n)) return;
  if (!sd.custom[code]) resetCustom(sd, code);
  const cu = sd.custom[code];
  if (lot.type === 'digit') {
    const i = Number(String(side).slice(1));
    if (!isFinite(i)) return;
    if (!cu.nums) cu.nums = [];
    cu.nums[i] = cu.nums[i] === n ? null : n;
  } else {
    const key = side === 'back' ? 'back' : 'front';
    const cap = key === 'back' ? lot.back_pick : lot.front_pick;
    if (!cu[key]) cu[key] = [];
    const arr = cu[key];
    const i = arr.indexOf(n);
    if (i >= 0) arr.splice(i, 1);
    else if (arr.length >= cap) { toast('最多选 ' + cap + ' 个，先取消一个再选', true); return; }
    else { arr.push(n); arr.sort((a, b) => a - b); }
  }
  repaintKeepScroll();
}
function onStratNum(el) {
  const sd = SIMD.sd;
  if (!sd) return;
  const side = el.dataset.side;
  if (side === 'rand') return randomCustom(sd);
  if (side) return toggleCustom(sd, side, Number(el.dataset.n));
  if (el.dataset.t) {
    const lim = (state.meta && state.meta.limits) || {};
    const cap = Math.max(1, Number(sd.mode === 'system' ? lim.tickets_system : lim.tickets) || 10);
    sd.tickets = Math.max(1, Math.min(cap, Number(el.dataset.t) || 1));
    paintStrategy();
  }
}

function openStrategyModal() {
  if (!state.me || !state.me.user) { openAuth('login'); return; }
  const sd = newSpec();
  const bd = modal({
    wide: true,
    title: '新建模拟策略',
    body: '',
    footer: '<button class="btn ghost" data-close>取消</button>' +
      '<button class="btn primary" data-act="s-submit" id="st-submit">创建并运行</button>',
  });
  SIMD.bd = bd;
  SIMD.sd = sd;
  bd.addEventListener('input', (e) => {
    const f = e.target && e.target.dataset && e.target.dataset.f;
    if (!f) return;
    const v = e.target.value;
    if (f === 'periods' || f === 'initial') sd[f] = v === '' ? '' : v;
    else sd[f] = v;
    paintEst();
  });
  paintStrategy();
}

function customPayload(sd) {
  const code = sd.codes[0];
  const lot = lotOf(code);
  if (!lot) throw new Error('彩种信息缺失，请刷新页面');
  const cu = sd.custom[code] || {};
  if (lot.type === 'digit') {
    const pos = lot.positions || [];
    const nums = [];
    for (let i = 0; i < pos.length; i++) {
      const v = (cu.nums || [])[i];
      if (v == null) throw new Error('请先为' + ((POS[code] || [])[i] || ('第 ' + (i + 1) + ' 位')) + '选一个号码');
      nums.push(Number(v));
    }
    return { nums: nums };
  }
  const side = SIDE[code] || ['号码', '后区'];
  const front = (cu.front || []).slice();
  const back = (cu.back || []).slice();
  if (front.length !== lot.front_pick) throw new Error(side[0] + '需要选 ' + lot.front_pick + ' 个');
  if (lot.back_pick && back.length !== lot.back_pick) throw new Error(side[1] + '需要选 ' + lot.back_pick + ' 个');
  return { front: front, back: back };
}

async function submitStrategy() {
  const sd = SIMD.sd;
  const bd = SIMD.bd;
  if (!sd || !bd) return;
  const btn = bd.querySelector('#st-submit');
  let body;
  try {
    const periods = Number(sd.periods);
    const initial = Number(sd.initial);
    if (!isFinite(periods) || periods < 1) throw new Error('计划期数至少 1 期');
    if (!isFinite(initial) || initial <= 0) throw new Error('初始资金池必须大于 0');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(sd.start_date || ''))) throw new Error('请选择起始日期');
    if (sd.mode === 'custom' && !customReady(sd)) throw new Error('请先选满自选号码');
    body = {
      name: String(sd.name || '').trim(),
      codes: sd.codes.slice(),
      mode: sd.mode,
      tickets: Math.max(1, Number(sd.tickets) || 1),
      periods: Math.round(periods),
      parlay: !!sd.parlay,
      start_date: sd.start_date,
      initial: initial,
      custom: sd.mode === 'custom' ? customPayload(sd) : {},
    };
  } catch (e) {
    toast(e.message, true);
    return;
  }
  if (btn) { btn.disabled = true; btn.textContent = '创建中…'; }
  try {
    const r = await api('/api/strategies', { method: 'POST', body: body });
    closeModal(bd);
    SIMD.bd = null;
    SIMD.sd = null;
    await refreshMe(true);
    state.sim = { id: r.id, name: r.spec.name, spec: r.spec, result: null, pending: true };
    if (parseRoute().route !== 'sim') navigate('sim');
    else render();
    toast('策略已创建，正在按真实开奖结算');
    await runStrategy(r.id);
  } catch (e) {
    toast(e.message, true);
    if (btn) { btn.disabled = false; btn.textContent = '创建并运行'; }
  }
}

/* ============================================================
 * 理论方法
 * ============================================================ */
const TERM_CN = {
  z: 'z 分数', chi2: '卡方统计量', p: 'p 值', fdr: 'BH-FDR 校正', ewma: '指数加权平均 EWMA',
  dirichlet: 'Dirichlet–多项分布', mc: '蒙特卡洛', boot: 'Bootstrap 重采样', hyper: '超几何分布',
  omit: '遗漏', kelly: '凯利公式', ret: '返奖率', parlay: '复式投注', trans: '转移概率',
  entropy: '信息熵', kl: 'KL 散度', shrink: '收缩估计', lift: '提升倍数',
  poisson: 'Poisson–Gamma 经验贝叶斯', geom: '几何分布', run: '游程检验', odds: '理论中奖概率',
  roi: 'ROI 投资回报率', ev: '期望值 EV', markov: '马尔可夫链', fusion: '融合打分模型',
};

function viewTheory() {
  const groups = [
    {
      t: '随机性检验', s: '开奖结果是否真的接近均匀随机',
      items: [
        ['chi2', '卡方拟合优度', '逐彩种把每个号码的实际出现次数与理论期望对比，得到卡方统计量。'],
        ['p', 'p 值判定', '由卡方统计量换算 p 值；p<0.05 才标记为“显著偏离”，否则视为正常波动。'],
        ['fdr', '多重检验校正', '同时检验几十上百个号码时，用 BH 法控制假阳性，避免把随机波动当成规律。'],
        ['z', '单号码 z 分数', '单个号码的偏差程度，|z|>2 在“统计分析”页高亮，作为“偏热/偏冷”的量化口径。'],
        ['entropy', '信息熵', '把号码分布的熵与理论最大熵比较，衡量整体均匀性。'],
        ['kl', 'KL 散度', '量化实测分布与均匀分布之间的信息差，越接近 0 越像随机。'],
        ['run', '游程检验', '检验连续出现 / 连续遗漏的段长是否异常，判断序列是否存在记忆。'],
        ['markov', '马尔可夫检验', '假设下一期状态只与本期有关，用转移矩阵检验“记忆性”。'],
        ['trans', '转移概率', '由历史统计“上期出现→本期出现”等四种转移的条件概率。'],
      ],
    },
    {
      t: '号码热度与打分', s: '近期热度、遗漏与经验贝叶斯收缩',
      items: [
        ['ewma', '指数加权平均', '给近期期数更高权重，得到近期热度曲线，与长期频率并列展示。'],
        ['omit', '遗漏统计', '记录每个号码的当前遗漏与历史最大遗漏；遗漏大不等于“该出了”。'],
        ['geom', '几何分布', '若遗漏间隔近似几何分布，说明号码无记忆，等待不会提高中奖概率。'],
        ['poisson', 'Poisson–Gamma', '把出现次数视为泊松计数，用 Gamma 先验做经验贝叶斯收缩。'],
        ['dirichlet', 'Dirichlet–多项', '给每个号码一个先验强度，把历史频率向理论概率收缩，避免小样本极端值。'],
        ['shrink', '收缩估计', '样本频率与理论概率加权融合，得到更稳的概率估计。'],
        ['lift', '提升倍数', '融合概率 ÷ 均匀基线概率，>1 表示模型认为略高于随机水平。'],
        ['fusion', '融合打分', '把历史频率、EWMA、贝叶斯后验、马尔可夫转移按 log-odds 加权后归一化，作为“系统预测”的排序依据。'],
      ],
    },
    {
      t: '组合与概率计算', s: '奖级概率、模拟与波动范围',
      items: [
        ['hyper', '超几何分布', '给定选号个数与开奖个数，计算命中 m 个号码的理论概率，是各奖级概率的基础。'],
        ['odds', '理论中奖概率', '按组合数算出的头奖概率，与选号方式无关 —— 任何选号法都改变不了它。'],
        ['mc', '蒙特卡洛', '按“完全随机”规则模拟数万次，得到和值、跨度、奇偶比等指标的理论零分布。'],
        ['boot', 'Bootstrap', '从历史数据有放回重采样，估计统计量的抽样波动与置信区间，判断差异是否稳定。'],
      ],
    },
    {
      t: '资金与期望', s: '为什么长期一定是亏的',
      items: [
        ['ret', '返奖率', '奖金总额 ÷ 销售额，我国乐透型彩票通常在 50% 上下，这是负期望的根源。'],
        ['ev', '期望值', '单注长期平均回报 = 奖金 × 中奖概率，低于 2 元成本即为负期望。'],
        ['kelly', '凯利公式', 'f* = (bp − q)/b；负期望赌局的最优下注比例为 0。'],
        ['roi', 'ROI', '模拟收益页用净盈亏 ÷ 累计投入计算，用于横向比较不同策略。'],
        ['parlay', '复式投注', '同一票上多选号码按组合展开为多注，投入与命中机会同时放大，但期望不变。'],
      ],
    },
  ];

  const body = '<div class="grid g2">' + groups.map((g) =>
    gcardWrapped(g.t, g.s,
      kv(g.items.map((it) => [
        T(it[1], TIPS[it[0]]),
        '<span class="muted">' + esc(it[2]) + '</span>',
      ])))).join('') + '</div>';

  const glossary = card('术语速查',
    '页面中带虚线下划线的词都可以悬停查看定义',
    acc('展开全部 ' + Object.keys(TIPS).length + ' 个术语',
      kv(Object.keys(TIPS).map((k) => [
        T(TERM_CN[k] || k, TIPS[k]),
        '<span class="muted">' + esc(TIPS[k]) + '</span>',
      ]))));

  const rule = card('模拟与结算口径', '“预测盈亏”台账与“模拟收益”共用同一套规则',
    kv([
      ['单注价格', '每注 2 元；复式按方案注数 × 2 元'],
      ['购买顺序', '每期先选号、后开奖，选号只使用该期之前的数据'],
      ['结算依据', '真实开奖号码 + 官方单注奖金，逐期结算，不做任何事后调整'],
      ['资金不足', '当资金池不足以支付该期时整期跳过（余额不会为负）'],
      ['统计口径', '净盈亏 = 奖金 − 投入；ROI = 净盈亏 ÷ 投入'],
    ]));

  return pageH('理论方法',
    '站点里每个指标都能回到一个统计模型：术语悬停看定义，展开卡片看模型怎么被使用。') +
    body +
    '<div class="mt16">' + glossary + '</div>' +
    '<div class="mt16">' + rule + '</div>' +
    '<div class="mt16">' + noteBox(
      '彩票是负期望游戏：所有模型只能描述历史与量化不确定性，不能预测未来开奖。' +
      '本站在任何页面都不提供投注建议，模拟收益与预测盈亏都只用于统计学习。', 'blue') + '</div>' +
    '<div class="mt16 tiny muted">原版完整数据站仍可在 <a href="/legacy" style="color:var(--brand)">/legacy</a> 访问。</div>';
}

/* ============================================================
 * 启动
 * ============================================================ */
async function loadVisits() {
  const el = document.getElementById('visit-stat');
  if (!el) return;
  try {
    const v = await api('/api/visits');
    el.textContent = '累计访问 ' + num(v.total, 0) + ' 次 · 今日 ' + num(v.today, 0) +
      ' 次 · 今日访客 ' + num(v.uv_today, 0) + ' 人';
  } catch (e) { el.textContent = ''; }
}

async function boot() {
  initTip();
  bind();
  // 旧的 #/draws 链接归一成路径地址，便于分享与收录
  if (location.hash) {
    const p0 = parseRoute();
    history.replaceState({}, '', routePath(p0.route, p0.arg));
  }
  try {
    const [meta, preds, ana, pnl] = await Promise.all([
      api('/api/meta'), api('/api/predictions'), api('/api/analysis'), api('/api/pnl'),
    ]);
    state.meta = meta;
    (meta.lotteries || []).forEach((m) => { LOT[m.code] = m; });
    state.preds = preds;
    state.ana = ana;
    state.pnl = pnl;
  } catch (e) {
    const app = $('#app');
    if (app) {
      app.innerHTML = card('数据加载失败', '',
        noteBox(esc(e.message || String(e))) +
        '<div class="row mt12"><button class="btn" data-act="reload">重新加载</button></div>');
    }
    return;
  }
  await refreshMe(true);
  state.ready = true;
  onRoute();
  loadVisits();
}
boot();

