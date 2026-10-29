# -*- coding: utf-8 -*-
"""
彩票预估网站后端（零第三方依赖）
--------------------------------------------------------------
标准库 http.server + sqlite3 + Cookie 会话，提供：
  · 静态资源（web/ 新站；/legacy 原版完整数据站）
  · 数据接口（开奖、奖级明细、统计分析、下期预测、实盘盈亏台账）
  · 注册 / 登录 / 登出 / 当前用户
  · 模拟收益：策略 CRUD、策略模拟运行（simulate.py）、资金池设置

启动：
    python server.py                # http://127.0.0.1:8123
    python server.py --port 8000
"""
import argparse
import csv
import hashlib
import html
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import simulate

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
WEB = os.path.join(BASE, "web")
DB_PATH = os.path.join(DATA, "app.db")

SESSION_DAYS = 7
PBKDF2_ROUNDS = 120000
MAX_STRATEGIES = 30
MAX_BODY = 1 << 20            # 请求体上限 1MB，防止内存被撑爆
MAX_CONN = 64                 # 并发连接上限，超出直接 503，避免线程被耗尽

# 登录 / 注册失败节流：同一来源 IP 连续失败达到阈值后锁定一段时间
LOGIN_MAX_FAILS = 8
LOGIN_WINDOW = 300
LOGIN_LOCK = 600
REGISTER_MAX = 20
REGISTER_WINDOW = 3600
_attempts = {}
_attempts_lock = threading.Lock()

# 安全响应头
CSP_MAIN = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
# /legacy 原版站点内联脚本较多，单独放宽 script-src
CSP_LEGACY = CSP_MAIN.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'")
SEC_HEADERS = [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "geolocation=(), microphone=(), camera=()"),
]


def _rate_wait(key):
    """该来源当前是否被锁定，返回剩余秒数（0 表示未锁定）"""
    now = time.time()
    with _attempts_lock:
        rec = _attempts.get(key)
        if rec and rec["until"] > now:
            return int(rec["until"] - now) + 1
        return 0


def _rate_fail(key, limit, window, lock_secs):
    """记一次失败；窗口内达到 limit 次则锁定 lock_secs 秒"""
    now = time.time()
    with _attempts_lock:
        rec = _attempts.setdefault(key, {"hits": [], "until": 0.0})
        rec["hits"] = [t for t in rec["hits"] if now - t < window]
        rec["hits"].append(now)
        if len(rec["hits"]) >= limit:
            rec["until"] = now + lock_secs
            rec["hits"] = []
        if len(_attempts) > 4096:
            for k in [k for k, v in _attempts.items() if v["until"] < now and not v["hits"]]:
                _attempts.pop(k, None)


def _rate_ok(key):
    """成功后清除该来源的失败计数"""
    with _attempts_lock:
        _attempts.pop(key, None)

# 访问统计：明细写 data/access.log，页面访问汇总写 data/visits.json
VISIT_LOG = os.path.join(DATA, "access.log")
VISIT_STAT = os.path.join(DATA, "visits.json")
VISIT_LOCK = threading.Lock()
VISIT_KEEP_DAYS = 90
VISIT_SKIP_EXT = (".css", ".js", ".png", ".ico", ".svg", ".jpg", ".jpeg", ".map",
                  ".txt", ".xml")
VISIT_PAGES = ("/", "/index.html", "/legacy")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
}

_full_draws = None
_full_draws_mt = None
_prize_index = None
_prize_index_mt = None


# ---------------------------------------------------------------- 数据库
def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                pwd TEXT NOT NULL,
                salt TEXT NOT NULL,
                wallet REAL NOT NULL DEFAULT 1000,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions(
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS strategies(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                spec TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs(
                strategy_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                result TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def hash_pwd(pwd, salt=None, rounds=PBKDF2_ROUNDS):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), bytes.fromhex(salt), rounds)
    return dk.hex(), salt


# ---------------------------------------------------------------- 访问统计
def visits_state():
    try:
        with open(VISIT_STAT, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    st.setdefault("total", 0)
    st.setdefault("days", {})
    st.setdefault("first", now_str())
    return st


def record_visit(path, ip, status, ua, ref):
    """明细逐条写入 access.log；页面访问另计入 visits.json 汇总"""
    if path == "/api/visits" or path.lower().endswith(VISIT_SKIP_EXT):
        return
    ts = datetime.now()
    line = json.dumps(dict(t=ts.strftime("%Y-%m-%d %H:%M:%S"), ip=ip, path=path,
                           status=status, ua=ua, ref=ref), ensure_ascii=False)
    with VISIT_LOCK:
        with open(VISIT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if path not in VISIT_PAGES:
            return
        st = visits_state()
        d = st["days"].setdefault(ts.strftime("%Y-%m-%d"), dict(pv=0, uv=[]))
        d["pv"] += 1
        if ip not in d["uv"]:
            d["uv"].append(ip)
        st["total"] = st.get("total", 0) + 1
        st["days"] = {k: st["days"][k] for k in sorted(st["days"])[-VISIT_KEEP_DAYS:]}
        tmp = VISIT_STAT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, VISIT_STAT)


def visits_payload():
    st = visits_state()
    d = st["days"].get(datetime.now().strftime("%Y-%m-%d")) or {}
    days = [dict(day=k, pv=v.get("pv", 0), uv=len(v.get("uv") or []))
            for k, v in sorted(st["days"].items())[-14:]]
    return dict(total=st.get("total", 0), today=d.get("pv", 0),
                uv_today=len(d.get("uv") or []), first=st.get("first"),
                days=days)


# ---------------------------------------------------------------- SEO / 结构化内容
SEO_HEAD_A = "<!--SEO-HEAD-->"
SEO_HEAD_B = "<!--/SEO-HEAD-->"
SEO_A = "<!--SEO-SSR-->"
SEO_B = "<!--/SEO-SSR-->"

SITE_NAME = "彩票预估"
SITE_DESC = ("收录双色球、超级大乐透、快乐8、福彩3D、七乐彩、七星彩共 1.7 万余期官方开奖数据，"
             "提供卡方均匀性检验、遗漏与和值分布分析、下期号码参考、策略回测与实盘台账。"
             "仅供统计学习与模拟研究，不销售彩票。")

# 每个路由对应一个可被单独收录的页面
PAGES = {
    "/": dict(
        nav="首页",
        lead="各彩种最近一期开奖、下期参考号码与核心指标。",
        h1="彩票开奖数据分析与预测研究：双色球、超级大乐透、快乐8、福彩3D、七乐彩、七星彩",
        title="彩票预估 · 双色球/大乐透/快乐8 开奖数据统计与预测研究",
        desc=SITE_DESC,
        keywords="双色球,超级大乐透,快乐8,福彩3D,七乐彩,七星彩,开奖数据,开奖号码,统计分析,"
                 "卡方检验,遗漏分析,和值分布,号码频率,预测模型,策略回测"),
    "/predict": dict(
        nav="下期预测",
        lead="多模型打分生成的下期单式参考号码、核心号、杀号与小复式方案。",
        h1="下期号码统计参考：双色球、大乐透、快乐8 等六个彩种",
        title="下期号码统计参考 · 双色球/大乐透/快乐8 等六彩种 | 彩票预估",
        desc="按号码频率、遗漏期数、和值区间、跨度、奇偶比与大小比打分，生成双色球、超级大乐透、"
             "快乐8、福彩3D、七乐彩、七星彩的下期单式参考号码、核心号、备份号、杀号与小复式方案。",
        keywords="下期预测,双色球预测,大乐透预测,快乐8预测,福彩3D预测,七乐彩预测,七星彩预测,"
                 "参考号码,复式方案,杀号"),
    "/analysis": dict(
        nav="统计分析",
        lead="卡方均匀性检验、号码频率与遗漏、和值分布与区间估计。",
        h1="开奖数据统计分析：卡方均匀性检验、遗漏与和值分布",
        title="开奖统计分析 · 卡方检验/遗漏/和值分布 | 彩票预估",
        desc="对双色球、超级大乐透、快乐8、福彩3D、七乐彩、七星彩的官方开奖做卡方均匀性检验，"
             "统计号码频率、冷热号、当前遗漏、和值区间、跨度、奇偶比与大小比分布。",
        keywords="卡方检验,均匀性检验,号码频率,冷热号,遗漏分析,和值分布,跨度,奇偶比,大小比,开奖统计"),
    "/draws": dict(
        nav="开奖数据",
        lead="逐期开奖号码、开奖日期与期号，可追溯到 1.7 万余期。",
        h1="历史开奖数据：六个彩种最近开奖记录",
        title="历史开奖数据 · 六个彩种最近开奖 | 彩票预估",
        desc="按彩种查看双色球、超级大乐透、快乐8、福彩3D、七乐彩、七星彩的最近开奖期号、开奖日期与开奖号码，"
             "可追溯到 1.7 万余期历史记录。",
        keywords="开奖数据,开奖号码,开奖结果,双色球开奖,大乐透开奖,快乐8开奖,福彩3D开奖,七乐彩开奖,七星彩开奖"),
    "/pnl": dict(
        nav="预测盈亏",
        lead="统一投入口径下的模拟台账、命中与盈亏结算。",
        h1="预测台账与盈亏结算",
        title="预测台账与盈亏结算 · 逐期记录 | 彩票预估",
        desc="按统一投入口径记录每期预测号码，待开奖后自动结算单式与复式的投入、奖金、净收益与命中情况，"
             "形成可逐期核对的实盘台账。",
        keywords="预测台账,盈亏统计,命中率,模拟投注,结算,回报率"),
    "/sim": dict(
        nav="模拟收益",
        lead="单式与复式的逐期滚动回测、回报率与随机基准对比。",
        h1="策略回测：逐期滚动的模拟收益",
        title="策略回测与模拟收益 · 逐期滚动验证 | 彩票预估",
        desc="逐期滚动回测：每一期只用此前数据重算预测，再用当期真实开奖结算，对比单式与复式的投入、奖金、"
             "净收益与回报率，并给出随机选号基准。",
        keywords="策略回测,模拟收益,回报率,随机基准,复式,单式,逐期滚动"),
    "/theory": dict(
        nav="理论方法",
        lead="统计指标定义、模型假设与结论局限。",
        h1="统计方法与模型说明",
        title="统计方法与模型说明 · 指标定义与局限 | 彩票预估",
        desc="说明站内各模型的统计指标定义：卡方均匀性检验、遗漏与回补、频率 z 分数、和值区间、跨度、"
             "奇偶比、大小比、连号重号、独立性与游程检验，以及结论的局限。",
        keywords="统计方法,模型说明,卡方检验,遗漏,和值区间,游程检验,独立性检验,概率,随机性"),
}

SEO_FAQ = [
    ("彩票开奖号码是随机的吗？",
     "本站对每个彩种的全部历史开奖做卡方均匀性检验。多数彩种的 p 值大于 0.05，与随机抽取无显著差异；"
     "个别彩种偶尔出现 p<0.05，属于多重检验下的常见假阳性，不足以证明开奖存在可利用的规律。"),
    ("站内的下期参考号码是怎么算出来的？",
     "综合近期热号与冷号、遗漏期数、和值 80% 区间、跨度、奇偶比与大小比、重号与连号等维度打分，"
     "并从大量随机组合中筛选形态接近历史最频的组合，属于统计参考，不构成投注建议。"),
    ("本站销售或代购彩票吗？",
     "不销售、不代购，也不提供任何支付渠道。站内展示的均为公开开奖数据与统计结果，"
     "页面中出现的资金池与收益都是模拟回测。"),
    ("数据来源和更新频率是什么？",
     "数据取自中国福利彩票与中国体育彩票官方开奖公告，每晚自动更新一次，"
     "可逐期核对期号、开奖日期与官方派奖明细。"),
]


def _esc(x):
    return html.escape(str(x), quote=True)


def _num(x):
    try:
        return "{:,}".format(int(round(float(x))))
    except (TypeError, ValueError):
        return "—"


def _pct(x):
    try:
        return "%.1f%%" % (float(x) * 100)
    except (TypeError, ValueError):
        return "—"


def _money(x):
    try:
        return "{:,.2f}".format(float(x))
    except (TypeError, ValueError):
        return "—"


def _rng(r):
    try:
        return "%g ~ %g" % (float(r[0]), float(r[1]))
    except (TypeError, ValueError, IndexError):
        return "—"


def _status(s):
    return {"pending": "待开奖", "settled": "已结算"}.get(str(s or ""), str(s or "—"))


def seo_balls(nums, sp):
    head = " ".join("%02d" % int(n) for n in nums or [])
    tail = " ".join("%02d" % int(n) for n in sp or [])
    return head + (" + " + tail if tail else "")


def _seo_parlay(front, back):
    """复式号码：数字型彩种（3D/七星彩）每个位次是一组候选数字，按位连写"""
    def grp(g):
        if isinstance(g, (list, tuple)):
            return "".join(str(int(x)) for x in g)
        return "%02d" % int(g)
    head = " ".join(grp(g) for g in front or [])
    tail = " ".join(grp(g) for g in back or [])
    return head + (" + " + tail if tail else "")


def _seo_nav_list(cur):
    out = ["<h2>站内栏目</h2>", "<ul>"]
    for p, m in PAGES.items():
        if p == cur:
            continue
        out.append('<li><a href="%s">%s</a>：%s</li>' % (p, m["nav"], m["lead"]))
    out.append("</ul>")
    return "".join(out)


def seo_head(route, base):
    """按路由生成 title / description / canonical / Open Graph / JSON-LD"""
    m = PAGES[route]
    url = base + route
    graph = [
        {"@type": "WebSite", "@id": base + "/#website", "url": base + "/",
         "name": SITE_NAME, "alternateName": "彩票开奖数据统计与预测研究",
         "description": SITE_DESC, "inLanguage": "zh-CN"},
        {"@type": "WebPage", "@id": url + "#webpage", "url": url,
         "name": m["title"], "description": m["desc"],
         "isPartOf": {"@id": base + "/#website"}, "inLanguage": "zh-CN"},
    ]
    crumbs = [{"@type": "ListItem", "position": 1, "name": "首页", "item": base + "/"}]
    if route != "/":
        crumbs.append({"@type": "ListItem", "position": 2, "name": m["nav"], "item": url})
    graph.append({"@type": "BreadcrumbList", "itemListElement": crumbs})
    if route == "/":
        graph.append({"@type": "FAQPage", "@id": base + "/#faq", "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in SEO_FAQ]})
    ld = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)
    return "".join([
        "<title>%s</title>\n" % _esc(m["title"]),
        '<meta name="description" content="%s">\n' % _esc(m["desc"]),
        '<meta name="keywords" content="%s">\n' % _esc(m["keywords"]),
        '<meta name="robots" content="index,follow,max-image-preview:large">\n',
        '<meta name="author" content="%s">\n' % SITE_NAME,
        '<link rel="canonical" href="%s">\n' % _esc(url),
        '<meta property="og:type" content="website">\n',
        '<meta property="og:site_name" content="%s">\n' % SITE_NAME,
        '<meta property="og:title" content="%s">\n' % _esc(m["title"]),
        '<meta property="og:description" content="%s">\n' % _esc(m["desc"]),
        '<meta property="og:url" content="%s">\n' % _esc(url),
        '<meta property="og:locale" content="zh_CN">\n',
        '<meta name="twitter:card" content="summary">\n',
        '<meta name="twitter:title" content="%s">\n' % _esc(m["title"]),
        '<meta name="twitter:description" content="%s">\n' % _esc(m["desc"]),
        '<script type="application/ld+json">\n%s\n</script>\n' % ld,
        '<script id="seo-pages" type="application/json">%s</script>\n' % json.dumps(
            {p: {"title": v["title"], "desc": v["desc"]} for p, v in PAGES.items()},
            ensure_ascii=False),
    ])


def _seo_open(route):
    return ['<section class="wrap seo">', "<h1>%s</h1>" % _esc(PAGES[route]["h1"])]


def _seo_note(text):
    out = ["<h2>说明与局限</h2>"]
    if text:
        out.append("<p>%s</p>" % text)
    out.append("<h2>数据来源与免责声明</h2>"
               "<p>开奖号码、销售额与派奖明细取自中国福利彩票、中国体育彩票官方公告，每晚自动更新。"
               "本站不销售、不代购彩票，页面中的资金池与收益均为模拟回测，不构成任何投注建议。</p>")
    return "".join(out)


METHOD_HTML = ("各彩种独立建模：前区/后区号码各自做卡方均匀性检验判断是否偏离均匀分布；"
               "统计每个号码的遗漏期数、出现频率、冷热属性；对和值、跨度、奇偶比、大小比、连号与重号做分布刻画；"
               "再用独立性检验、游程检验判断期与期之间是否存在可利用的相关性。"
               "开奖本质是独立随机抽样，统计只能描述历史形态，无法提高中奖概率，请理性看待。")


def _frag_home():
    preds = read_json("predictions.json")
    ana = read_json("analysis.json")
    A = {x.get("code"): x for x in ana.get("lotteries") or []}
    out = _seo_open("/")
    out.append("<p>本站汇总中国福利彩票与中国体育彩票的官方开奖公告，对每个彩种做卡方均匀性检验、"
               "号码频率与遗漏分析、和值/跨度/奇偶/大小分布统计，并给出下期号码参考、复式方案与策略回测结果。"
               "所有内容仅供统计学习与模拟研究，不销售彩票。数据更新于 %s。</p>"
               % _esc(preds.get("generated_at") or ""))
    out.append(_seo_nav_list("/"))
    for it in preds.get("lotteries") or []:
        name = _esc(it.get("name") or it.get("code") or "")
        last = it.get("last") or {}
        nxt = it.get("next") or {}
        tk = (it.get("tickets") or [{}])[0]
        an = A.get(it.get("code")) or {}
        out.append("<h2>%s开奖结果与下期统计参考</h2>" % name)
        if last:
            out.append("<p>最近一期：第 %s 期（%s），开奖号码 %s。</p>" % (
                _esc(last.get("issue") or ""), _esc(last.get("date") or ""),
                seo_balls(last.get("nums"), last.get("sp"))))
        if nxt and tk:
            out.append("<p>下一期：第 %s 期（%s），统计参考号码 %s。</p>" % (
                _esc(nxt.get("issue") or ""), _esc(nxt.get("date") or ""),
                seo_balls(tk.get("nums"), tk.get("sp"))))
        u = an.get("uniformity") or {}
        s = an.get("sum") or {}
        out.append("<p>建模样本 %s 期；卡方均匀性检验 χ²=%s，df=%s，p=%s，%s；和值 80%% 区间 %s。</p>" % (
            an.get("draws") or 0, u.get("chi2") or "—", u.get("df") or "—", u.get("p") or "—",
            _esc(u.get("verdict") or ""), _rng(s.get("int80"))))
        win = it.get("window")
        if isinstance(win, dict) and win.get("reason"):
            out.append("<p>取样窗口：自 %s 起，原因：%s。</p>" % (
                _esc(win.get("start") or ""), _esc(win.get("reason"))))
    out.append(_seo_note(METHOD_HTML))
    out.append("</section>")
    return "".join(out)


def _frag_predict():
    preds = read_json("predictions.json")
    out = _seo_open("/predict")
    out.append("<p>下列号码由多模型打分生成：综合历史频率、近期冷热、遗漏期数、和值 80%% 区间、跨度、"
               "奇偶比与大小比等维度，从候选组合中筛选形态接近历史最频的组合。数据更新于 %s。</p>"
               % _esc(preds.get("generated_at") or ""))
    for it in preds.get("lotteries") or []:
        name = _esc(it.get("name") or it.get("code") or "")
        nxt = it.get("next") or {}
        out.append("<h2>%s下期参考号码</h2>" % name)
        if nxt:
            out.append("<p>目标期：第 %s 期，开奖日期 %s。</p>" % (
                _esc(nxt.get("issue") or ""), _esc(nxt.get("date") or "")))
        for i, t in enumerate((it.get("tickets") or [])[:5], 1):
            nums = t.get("nums") or []
            odd = t.get("odd")
            if odd is None or not nums:
                shape = ""
            else:
                shape = "，和值 %s，跨度 %s，奇偶比 %s:%s，大小比 %s:%s" % (
                    t.get("sum"), t.get("span"), odd, len(nums) - int(odd),
                    t.get("big"), len(nums) - int(t.get("big") or 0))
            out.append("<p>单式参考 %d：%s%s。</p>" % (
                i, seo_balls(nums, t.get("sp")), shape))
        par = it.get("parlay") or {}
        if par.get("front"):
            out.append("<p>复式方案：%s，号码 %s，共 %s 注，投入 %s 元。</p>" % (
                _esc(par.get("label") or "复式"),
                _seo_parlay(par.get("front"), par.get("back")),
                _num(par.get("bets")), _num(par.get("cost"))))
        core = [c for c in (it.get("core") or []) if c.get("n") is not None]
        if core:
            out.append("<p>核心号：%s。</p>" % "、".join(
                "%02d" % int(c["n"]) for c in core))
        kill = [c for c in (it.get("kill") or []) if c.get("n") is not None]
        if kill:
            out.append("<p>杀号参考：%s。</p>" % "、".join(
                "%02d" % int(c["n"]) for c in kill))
        note = it.get("sample_note")
        if isinstance(note, str) and note.strip():
            out.append("<p>样本说明：%s。</p>" % _esc(note))
    out.append(_seo_nav_list("/predict"))
    out.append(_seo_note("参考号码由统计打分生成，不含任何未来信息，也不改变各号码的理论出现概率。"))
    out.append("</section>")
    return "".join(out)


def _frag_analysis():
    ana = read_json("analysis.json")
    lots = ana.get("lotteries") or []
    d0 = min([x.get("date_from") for x in lots if x.get("date_from")] or [""])
    d1 = max([x.get("date_to") for x in lots if x.get("date_to")] or [""])
    out = _seo_open("/analysis")
    out.append("<p>每个彩种用全部可用历史开奖做统计：卡方均匀性检验判断号码是否偏离均匀抽取；"
               "统计每个号码的出现频率与当前遗漏；刻画和值、跨度、奇偶比、大小比的分布。"
               "统计区间 %s 至 %s。</p>" % (_esc(d0), _esc(d1)))
    for it in lots:
        name = _esc(it.get("name") or it.get("code") or "")
        u = it.get("uniformity") or {}
        s = it.get("sum") or {}
        out.append("<h2>%s：卡方检验与分布统计</h2>" % name)
        out.append("<p>建模样本 %s 期（%s 至 %s）。卡方均匀性检验 χ²=%s，df=%s，p=%s，结论：%s。</p>" % (
            _num(it.get("draws")), _esc(it.get("date_from") or ""), _esc(it.get("date_to") or ""),
            _esc(u.get("chi2") or "—"), _esc(u.get("df") or "—"), _esc(u.get("p") or "—"),
            _esc(u.get("verdict") or "—")))
        hot = [h for h in (it.get("hot") or [])[:5] if h.get("n") is not None]
        if hot:
            out.append("<p>高频号：%s。</p>" % "、".join(
                "%02d（%s 次，z=%s）" % (int(h["n"]), _num(h.get("cnt")), _esc(h.get("z")))
                for h in hot))
        cold = [h for h in (it.get("cold") or [])[:5] if h.get("n") is not None]
        if cold:
            out.append("<p>低频号：%s。</p>" % "、".join(
                "%02d（%s 次，z=%s）" % (int(h["n"]), _num(h.get("cnt")), _esc(h.get("z")))
                for h in cold))
        om = sorted([x for x in (it.get("omission") or []) if x.get("n") is not None],
                    key=lambda x: -(x.get("cur") or 0))[:5]
        if om:
            out.append("<p>当前遗漏最长：%s。</p>" % "、".join(
                "%02d（遗漏 %s 期，平均 %s 期）" % (int(x["n"]), _num(x.get("cur")), _esc(x.get("avg")))
                for x in om))
        if s:
            out.append("<p>和值分布：均值 %s，标准差 %s，中位数 %s，最小 %s，最大 %s，"
                       "80%% 区间 %s，90%% 区间 %s，KS 检验 p=%s。</p>" % (
                           _esc(s.get("mean")), _esc(s.get("std")), _esc(s.get("median")),
                           _esc(s.get("min")), _esc(s.get("max")),
                           _rng(s.get("int80")), _rng(s.get("int90")), _esc(s.get("ks_p"))))
    out.append(_seo_nav_list("/analysis"))
    out.append(_seo_note(METHOD_HTML))
    out.append("</section>")
    return "".join(out)


def _frag_draws():
    D = full_draws()
    out = _seo_open("/draws")
    out.append("<p>下表列出各彩种最近 8 期开奖的期号、开奖日期与开奖号码，完整记录可逐期追溯至 1.7 万余期，"
               "数据取自官方开奖公告。</p>")
    for code in ("ssq", "dlt", "qlc", "kl8", "3d", "qxc"):
        rows = (D.get(code) or [])[-8:][::-1]
        if not rows:
            continue
        name = _esc((simulate.META.get(code) or {}).get("name") or code)
        out.append("<h2>%s最近开奖记录</h2>" % name)
        out.append('<table class="seo-table"><caption>%s最近 %d 期</caption>'
                   "<thead><tr><th>期号</th><th>开奖日期</th><th>开奖号码</th><th>销售额（元）</th>"
                   "<th>奖池（元）</th></tr></thead><tbody>" % (name, len(rows)))
        for r in rows:
            out.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                _esc(r.get("issue")), _esc(r.get("date")),
                seo_balls(r.get("nums"), r.get("sp")),
                _num(r.get("sales")), _num(r.get("pool"))))
        out.append("</tbody></table>")
    out.append(_seo_nav_list("/draws"))
    out.append(_seo_note("开奖号码为官方公告结果，本站仅做整理与展示。"))
    out.append("</section>")
    return "".join(out)


def _frag_pnl():
    d = read_json("pnl.json")
    t = d.get("totals") or {}
    out = _seo_open("/pnl")
    out.append("<p>台账起点 %s，单注价格 %s 元。每期开奖前记录预测号码，开奖后自动结算单式与复式方案的"
               "投入、奖金与净收益，形成可逐期核对的模拟台账。数据更新于 %s。</p>" % (
                   _esc(d.get("first_at") or ""), _esc(d.get("price")), _esc(d.get("generated_at") or "")))
    out.append("<p>全站汇总：已结算 %s 期，待开奖 %s 期，累计投入 %s 元，累计奖金 %s 元，"
               "净收益 %s 元，回报率 %s。</p>" % (
                   _num(t.get("settled")), _num(t.get("pending")), _money(t.get("cost")),
                   _money(t.get("prize")), _money(t.get("net")), _pct(t.get("roi"))))
    for it in d.get("lotteries") or []:
        name = _esc(it.get("name") or it.get("code") or "")
        s = it.get("summary") or {}
        out.append("<h2>%s台账</h2>" % name)
        out.append("<p>已结算 %s 期，待开奖 %s 期，命中 %s 期；投入 %s 元，奖金 %s 元，"
                   "净收益 %s 元，回报率 %s。</p>" % (
                       _num(s.get("periods")), _num(s.get("pending")), _num(s.get("hit_periods")),
                       _money(s.get("cost")), _money(s.get("prize")), _money(s.get("net")),
                       _pct(s.get("roi"))))
        entries = (it.get("entries") or [])[-8:][::-1]
        if entries:
            out.append('<table class="seo-table"><caption>%s最近 %d 期台账</caption>'
                       "<thead><tr><th>期号</th><th>开奖日期</th><th>状态</th><th>投入（元）</th>"
                       "<th>奖金（元）</th><th>净收益（元）</th></tr></thead><tbody>" % (name, len(entries)))
            for e in entries:
                out.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                    _esc(e.get("issue")), _esc(e.get("date")), _status(e.get("status")),
                    _money(e.get("cost_total")), _money(e.get("prize_total")), _money(e.get("net"))))
            out.append("</tbody></table>")
    out.append(_seo_nav_list("/pnl"))
    out.append(_seo_note("台账按统一投入口径模拟，不含真实资金往来，也未考虑派奖封顶与税费细节。"))
    out.append("</section>")
    return "".join(out)


def _frag_sim():
    b = read_json("backtest.json")
    t = b.get("totals") or {}
    out = _seo_open("/sim")
    out.append("<p>逐期滚动回测：共 %s 期，每期只用当期之前的数据重算预测，再用当期真实开奖结算，"
               "因此不含未来信息。单注价格 %s 元。数据更新于 %s。</p>" % (
                   _num(b.get("periods")), _esc(b.get("price")), _esc(b.get("generated_at") or "")))
    out.append('<table class="seo-table"><caption>全部彩种回测汇总</caption>'
               "<thead><tr><th>方案</th><th>投入（元）</th><th>奖金（元）</th>"
               "<th>净收益（元）</th><th>回报率</th></tr></thead><tbody>")
    for label, key in (("单式", "single"), ("小复式", "parlay"), ("合计", "combined")):
        v = t.get(key) or {}
        out.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            label, _money(v.get("cost")), _money(v.get("prize")), _money(v.get("net")), _pct(v.get("roi"))))
    out.append("</tbody></table>")
    for it in b.get("lotteries") or []:
        name = _esc(it.get("name") or it.get("code") or "")
        out.append("<h2>%s回测结果</h2>" % name)
        out.append("<p>回测区间：第 %s 期至第 %s 期（%s 至 %s），共 %s 期，命中 %s 期。</p>" % (
            _esc(it.get("start_issue") or ""), _esc(it.get("end_issue") or ""),
            _esc(it.get("start_date") or ""), _esc(it.get("end_date") or ""),
            _num(it.get("periods")), _num(it.get("hit_periods"))))
        for label, key in (("单式", "single"), ("小复式", "parlay"), ("合计", "combined")):
            v = it.get(key) or {}
            out.append("<p>%s：投入 %s 元，奖金 %s 元，净收益 %s 元，回报率 %s。</p>" % (
                label, _money(v.get("cost")), _money(v.get("prize")), _money(v.get("net")), _pct(v.get("roi"))))
    out.append(_seo_nav_list("/sim"))
    out.append(_seo_note("回测反映的是历史形态下的模拟结果，负回报与彩票约 50%% 的返奖率一致，"
                         "不代表未来收益，也不构成任何投注建议。"))
    out.append("</section>")
    return "".join(out)


def _frag_theory():
    out = _seo_open("/theory")
    out.append("<h2>卡方均匀性检验</h2>"
               "<p>把每个号码的出现次数与理论期望次数比较，计算 χ² 统计量并换算 p 值。"
               "p 大于 0.05 表示与均匀抽取无显著差异；p 小于 0.05 只说明该样本下出现偏离，"
               "在多重检验下也常见假阳性。</p>")
    out.append("<h2>频率与 z 分数</h2>"
               "<p>用长期频率与近期频率对比，把出现次数标准化为 z 分数，衡量某号码偏离历史均值的程度。"
               "z 为正表示高于期望，为负表示低于期望。</p>")
    out.append("<h2>遗漏与回补</h2>"
               "<p>遗漏指某号码距离上次出现的期数。平均遗漏、最大遗漏与当前遗漏共同刻画冷热，"
               "但独立抽样下遗漏不会改变下一期的理论概率。</p>")
    out.append("<h2>和值、跨度、奇偶比与大小比</h2>"
               "<p>和值是当期号码之和，跨度为最大号与最小号之差，奇偶比与大小比描述结构形态。"
               "站内用 80% 与 90% 分位区间给出常见范围，并做 KS 检验判断分布是否接近正态。</p>")
    out.append("<h2>独立性与游程检验</h2>"
               "<p>用期与期之间的相关性检验、游程检验判断是否存在可利用的序列结构。"
               "多数彩种检验结果接近独立随机，说明历史号码无法用于提高中奖概率。</p>")
    out.append("<h2>回测与台账口径</h2>"
               "<p>回测每期只用此前数据重算预测，再用当期开奖结算，避免未来信息泄漏；"
               "台账按统一投入口径逐期记录投入、奖金与净收益，便于逐期核对。</p>")
    out.append(_seo_nav_list("/theory"))
    out.append(_seo_note(METHOD_HTML))
    out.append("</section>")
    return "".join(out)


FRAGMENTS = {
    "/": _frag_home,
    "/predict": _frag_predict,
    "/analysis": _frag_analysis,
    "/draws": _frag_draws,
    "/pnl": _frag_pnl,
    "/sim": _frag_sim,
    "/theory": _frag_theory,
}


def seo_fragment(route="/"):
    """把栏目对应的真实数据渲染成无 JS 也能读到的静态正文，供搜索引擎与大模型抓取"""
    return FRAGMENTS.get(route, _frag_home)()


def page_route(route):
    """把请求路径归一为 PAGES 中的栏目路由；非页面路径返回 None"""
    if route in ("", "/", "/index.html"):
        return "/"
    seg = route.strip("/").split("/")[0]
    p = "/" + seg
    return p if p in PAGES else None


def _replace_span(body, a, b, frag):
    A, B = a.encode("utf-8"), b.encode("utf-8")
    i, j = body.find(A), body.find(B)
    if i < 0 or j < i:
        return body
    return body[:i + len(A)] + frag.encode("utf-8") + body[j:]


def inject_seo(body, route="/", base=""):
    """按路由替换 head 中的 SEO 片段与 noscript 中的静态正文"""
    base = base or ""
    try:
        body = _replace_span(body, SEO_HEAD_A, SEO_HEAD_B, seo_head(route, base))
    except Exception:
        pass
    try:
        body = _replace_span(body, SEO_A, SEO_B, seo_fragment(route))
    except Exception:
        pass
    return body


# ---------------------------------------------------------------- 数据缓存
def _csv_mtime(name):
    """CSV 修改时间，用于判断缓存是否需要重建（每日更新后无需重启服务）"""
    try:
        return os.path.getmtime(os.path.join(DATA, name))
    except OSError:
        return None


def full_draws():
    """完整开奖（不做建模窗口裁剪），供开奖数据页使用"""
    global _full_draws, _full_draws_mt
    mt = _csv_mtime("draws.csv")
    if _full_draws is None or mt != _full_draws_mt:
        out = {}
        with open(os.path.join(DATA, "draws.csv"), encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                code = r["lottery_code"]
                front = [int(x) for x in str(r.get("front_numbers") or "").split() if x.strip().isdigit()]
                back = [int(x) for x in str(r.get("back_numbers") or "").split() if x.strip().isdigit()]
                if not front:
                    continue
                out.setdefault(code, []).append(dict(
                    issue=r["issue"], date=r["draw_date"], nums=front, sp=back,
                    sales=r.get("sales_amount") or "", pool=r.get("pool_after_draw") or "",
                ))
        for v in out.values():
            v.sort(key=lambda x: (x["date"], x["issue"]))
        _full_draws = out
        _full_draws_mt = mt
    return _full_draws


def prize_index():
    """{(code, issue): [{level, winners, unit, total}]}"""
    global _prize_index, _prize_index_mt
    mt = _csv_mtime("prizes.csv")
    if _prize_index is None or mt != _prize_index_mt:
        idx = {}
        with open(os.path.join(DATA, "prizes.csv"), encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                key = (r["lottery_code"], r["issue"])
                idx.setdefault(key, []).append(dict(
                    level=r.get("prize_level") or "",
                    winners=r.get("winner_count") or "",
                    unit=r.get("unit_prize") or "",
                    total=r.get("total_prize") or "",
                    note=r.get("note") or "",
                ))
        _prize_index = idx
        _prize_index_mt = mt
    return _prize_index


def read_json(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def meta_payload():
    D = simulate.draws()
    out = []
    for code in ("ssq", "dlt", "qlc", "kl8", "3d", "qxc"):
        m = simulate.META[code]
        rows = D.get(code) or []
        last = rows[-1] if rows else None
        item = dict(code=code, name=m["name"], type=m["type"], desc=m["desc"],
                    draw_days=m["draw_days"], front_pool=m.get("front_pool"),
                    front_pick=m.get("front_pick"), back_pool=m.get("back_pool"),
                    back_pick=m.get("back_pick"), positions=m.get("positions"),
                    count=len(rows), first_date=rows[0]["date"] if rows else None,
                    last_date=rows[-1]["date"] if rows else None,
                    last=last, price=simulate.PRICE)
        out.append(item)
    return dict(lotteries=out, price=simulate.PRICE,
                limits=dict(periods=simulate.MAX_PERIODS,
                            periods_system=simulate.MAX_PERIODS_SYSTEM,
                            tickets=simulate.MAX_TICKETS,
                            tickets_system=simulate.MAX_TICKETS_SYSTEM))


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "LotteryStudy"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def version_string(self):
        # 不回显 Python 版本等软件栈信息
        return self.server_version

    # ---------- 基础响应 ----------
    def _log_visit(self, status):
        try:
            record_visit(urlparse(self.path).path, self.client_address[0], status,
                         (self.headers.get("User-Agent") or "")[:200],
                         (self.headers.get("Referer") or "")[:300])
        except Exception:
            pass

    def _send(self, status, body=b"", ctype="application/json; charset=utf-8", headers=None,
              csp=None):
        self._log_visit(status)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", csp or CSP_MAIN)
        for k, v in SEC_HEADERS:
            self.send_header(k, v)
        for k, v in (headers or []):
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def json(self, obj, status=200):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def err(self, msg, status=400):
        self.json(dict(ok=False, error=msg), status)

    def body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        if n > MAX_BODY:
            self.close_connection = True
            raise ValueError("请求体超出大小限制")
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("请求体不是合法 JSON")

    def log_message(self, fmt, *args):
        pass

    # ---------- 会话 ----------
    def current_user(self, conn=None):
        raw = self.headers.get("Cookie") or ""
        c = SimpleCookie()
        try:
            c.load(raw)
        except Exception:
            return None
        m = c.get("sid")
        if not m:
            return None
        token = m.value
        close = conn is None
        conn = conn or db()
        try:
            row = conn.execute(
                "SELECT u.id,u.username,u.wallet,s.expires_at FROM sessions s "
                "JOIN users u ON u.id=s.user_id WHERE s.token=?", (token,)).fetchone()
        finally:
            if close:
                conn.close()
        if not row:
            return None
        if row["expires_at"] < now_str():
            return None
        return dict(id=row["id"], username=row["username"], wallet=row["wallet"])

    def require_user(self):
        u = self.current_user()
        if not u:
            self.err("请先登录", 401)
        return u

    def set_session(self, user_id):
        token = secrets.token_urlsafe(32)
        exp = (datetime.now() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M")
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_str(),))
            conn.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES(?,?,?)",
                         (token, user_id, exp))
        cookie = "sid=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d" % (
            token, SESSION_DAYS * 86400)
        self._pending_cookie = cookie
        return token

    # ---------- 静态资源 ----------
    def site_base(self):
        """按请求 Host 推导站点根地址，便于换域名后 canonical/sitemap 自动跟随"""
        host = (self.headers.get("Host") or "").strip()
        if re.match(r"^[A-Za-z0-9.\-]+(:\d{1,5})?$", host):
            return "http://" + host
        try:
            port = self.server.server_address[1]
        except Exception:
            port = 8123
        return "http://127.0.0.1:%d" % port

    def static(self, route):
        page = page_route(route)
        base = self.site_base()
        if page:
            # 栏目路径（含 /predict/ssq 这类深链）统一由单页应用渲染
            path = os.path.join(WEB, "index.html")
        elif route.startswith("/data/"):
            # 原版数据站用相对路径取 csv/json，这里限定目录与类型后放行
            path = os.path.normpath(os.path.join(DATA, route[len("/data/"):]))
            if not path.startswith(DATA + os.sep) or \
                    os.path.splitext(path)[1].lower() not in (".csv", ".json"):
                return self.err("not found", 404)
        elif route == "/legacy":
            path = os.path.join(BASE, "index.html")
        else:
            rel = route.lstrip("/")
            path = os.path.normpath(os.path.join(WEB, rel))
            if not path.startswith(WEB):
                return self.err("not found", 404)
        if not os.path.isfile(path):
            return self.err("not found", 404)
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            body = f.read()
        if page:
            body = inject_seo(body, page, base)
        elif route in ("/sitemap.xml", "/robots.txt"):
            body = body.replace(b"http://127.0.0.1:8123", base.encode("utf-8"))
            if route == "/sitemap.xml":
                today = datetime.now().strftime("%Y-%m-%d").encode("utf-8")
                body = re.sub(rb"<lastmod>[\d-]{10}</lastmod>",
                              b"<lastmod>" + today + b"</lastmod>", body)
        self._send(200, body, CONTENT_TYPES.get(ext, "application/octet-stream"),
                   csp=CSP_LEGACY if route == "/legacy" else None)

    # ---------- 路由 ----------
    def _same_origin(self):
        """状态变更接口的同源校验（CSRF 纵深防护）"""
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site == "cross-site":
            return False
        origin = (self.headers.get("Origin") or "").strip()
        if origin and origin != "null":
            host = (self.headers.get("Host") or "").strip().lower()
            return urlparse(origin).netloc.lower() == host
        return True

    def _drain(self):
        """丢弃未读取的请求体，避免 keep-alive 连接上的报文错位"""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if 0 < n <= MAX_BODY:
            self.rfile.read(n)
        elif n > MAX_BODY:
            self.close_connection = True

    def _guard(self):
        if self._same_origin():
            return False
        self._drain()
        self.err("跨站请求已拒绝", 403)
        return True

    def do_GET(self):
        u = urlparse(self.path)
        route, q = u.path, parse_qs(u.query)
        if route.startswith("/api/"):
            return self.api_get(route, q)
        return self.static(route)

    def do_POST(self):
        if self._guard():
            return
        u = urlparse(self.path)
        return self.api_post(u.path)

    def do_PUT(self):
        if self._guard():
            return
        u = urlparse(self.path)
        return self.api_put(u.path)

    def do_DELETE(self):
        if self._guard():
            return
        u = urlparse(self.path)
        return self.api_delete(u.path)

    def _finish(self, obj, status=200):
        cookie = getattr(self, "_pending_cookie", None)
        headers = [("Set-Cookie", cookie)] if cookie else []
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", headers)
        self._pending_cookie = None

    # ---------- GET ----------
    def api_get(self, route, q):
        try:
            if route == "/api/meta":
                return self.json(meta_payload())
            if route == "/api/predictions":
                return self.json(read_json("predictions.json"))
            if route == "/api/analysis":
                return self.json(read_json("analysis.json"))
            if route == "/api/pnl":
                return self.json(read_json("pnl.json"))
            if route == "/api/visits":
                return self.json(visits_payload())

            if route == "/api/draws":
                code = (q.get("code") or ["ssq"])[0]
                try:
                    limit = max(1, min(200, int((q.get("limit") or ["30"])[0])))
                except ValueError:
                    limit = 30
                rows = full_draws().get(code) or []
                return self.json(dict(code=code, total=len(rows), rows=rows[-limit:][::-1]))

            if route == "/api/prizes":
                code = (q.get("code") or [""])[0]
                issue = (q.get("issue") or [""])[0]
                return self.json(dict(code=code, issue=issue,
                                      rows=prize_index().get((code, issue)) or []))

            if route == "/api/me":
                u = self.require_user()
                if not u:
                    return
                with db() as conn:
                    rows = conn.execute(
                        "SELECT s.id,s.name,s.spec,s.created_at,r.result,r.created_at AS run_at "
                        "FROM strategies s LEFT JOIN runs r ON r.strategy_id=s.id "
                        "WHERE s.user_id=? ORDER BY s.id DESC", (u["id"],)).fetchall()
                out = []
                for r in rows:
                    result = json.loads(r["result"]) if r["result"] else None
                    out.append(dict(id=r["id"], name=r["name"], spec=json.loads(r["spec"]),
                                    created_at=r["created_at"], run_at=r["run_at"],
                                    summary=(result or {}).get("summary"),
                                    notes=(result or {}).get("notes")))
                return self.json(dict(ok=True, user=u, strategies=out))

            m = re.match(r"^/api/strategies/(\d+)$", route)
            if m:
                u = self.require_user()
                if not u:
                    return
                return self.strategy_detail(u, int(m.group(1)))

            return self.err("接口不存在", 404)
        except ValueError as e:
            return self.err(str(e), 400)
        except Exception:
            traceback.print_exc()
            return self.err("服务器内部错误，请稍后重试", 500)

    def strategy_detail(self, u, sid):
        with db() as conn:
            s = conn.execute("SELECT * FROM strategies WHERE id=? AND user_id=?",
                             (sid, u["id"])).fetchone()
            if not s:
                return self.err("策略不存在", 404)
            r = conn.execute("SELECT result,created_at FROM runs WHERE strategy_id=?",
                             (sid,)).fetchone()
        return self.json(dict(ok=True, id=s["id"], name=s["name"], spec=json.loads(s["spec"]),
                              created_at=s["created_at"],
                              result=json.loads(r["result"]) if r else None,
                              run_at=r["created_at"] if r else None))

    # ---------- POST ----------
    def api_post(self, route):
        try:
            if route == "/api/register":
                return self.register()
            if route == "/api/login":
                return self.login()
            if route == "/api/logout":
                return self.logout()
            if route == "/api/strategies":
                return self.create_strategy()

            m = re.match(r"^/api/strategies/(\d+)/run$", route)
            if m:
                return self.run_strategy(int(m.group(1)))

            return self.err("接口不存在", 404)
        except ValueError as e:
            return self.err(str(e), 400)
        except Exception:
            traceback.print_exc()
            return self.err("服务器内部错误，请稍后重试", 500)

    def register(self):
        key = "register:" + self.client_address[0]
        w = _rate_wait(key)
        if w:
            return self.err("注册过于频繁，请 %d 秒后重试" % w, 429)
        b = self.body()
        username = str(b.get("username") or "").strip()
        password = str(b.get("password") or "")
        if not re.fullmatch(r"[\w\u4e00-\u9fa5]{3,20}", username):
            return self.err("用户名需 3-20 位中文、字母、数字或下划线")
        if len(password) < 6:
            return self.err("密码至少 6 位")
        pwd, salt = hash_pwd(password)
        try:
            with db() as conn:
                cur = conn.execute(
                    "INSERT INTO users(username,pwd,salt,wallet,created_at) VALUES(?,?,?,?,?)",
                    (username, pwd, salt, 1000.0, now_str()))
                uid = cur.lastrowid
        except sqlite3.IntegrityError:
            _rate_fail(key, REGISTER_MAX, REGISTER_WINDOW, REGISTER_WINDOW)
            return self.err("该用户名已被注册")
        _rate_ok(key)
        self.set_session(uid)
        return self._finish(dict(ok=True, user=dict(id=uid, username=username, wallet=1000.0)))

    def login(self):
        key = "login:" + self.client_address[0]
        w = _rate_wait(key)
        if w:
            return self.err("尝试次数过多，请 %d 秒后重试" % w, 429)
        b = self.body()
        username = str(b.get("username") or "").strip()
        password = str(b.get("password") or "")
        with db() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            _rate_fail(key, LOGIN_MAX_FAILS, LOGIN_WINDOW, LOGIN_LOCK)
            return self.err("用户名或密码错误", 401)
        pwd, _ = hash_pwd(password, row["salt"])
        if not secrets.compare_digest(pwd, row["pwd"]):
            _rate_fail(key, LOGIN_MAX_FAILS, LOGIN_WINDOW, LOGIN_LOCK)
            return self.err("用户名或密码错误", 401)
        _rate_ok(key)
        self.set_session(row["id"])
        return self._finish(dict(ok=True, user=dict(id=row["id"], username=row["username"],
                                                    wallet=row["wallet"])))

    def logout(self):
        raw = self.headers.get("Cookie") or ""
        c = SimpleCookie()
        try:
            c.load(raw)
        except Exception:
            c = None
        if c and c.get("sid"):
            with db() as conn:
                conn.execute("DELETE FROM sessions WHERE token=?", (c["sid"].value,))
        self._pending_cookie = "sid=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
        return self._finish(dict(ok=True))

    def create_strategy(self):
        u = self.require_user()
        if not u:
            return
        b = self.body()
        spec = simulate.validate(dict(
            name=b.get("name"), codes=b.get("codes"), mode=b.get("mode"),
            tickets=b.get("tickets"), periods=b.get("periods"), parlay=b.get("parlay"),
            start_date=b.get("start_date"), initial=b.get("initial"), custom=b.get("custom"),
        ))
        with db() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM strategies WHERE user_id=?",
                             (u["id"],)).fetchone()["c"]
            if n >= MAX_STRATEGIES:
                return self.err("策略数量已达上限 %d 个" % MAX_STRATEGIES)
            cur = conn.execute(
                "INSERT INTO strategies(user_id,name,spec,created_at) VALUES(?,?,?,?)",
                (u["id"], spec["name"], json.dumps(spec, ensure_ascii=False), now_str()))
            sid = cur.lastrowid
            if b.get("initial") is not None:
                conn.execute("UPDATE users SET wallet=? WHERE id=?", (spec["initial"], u["id"]))
        return self.json(dict(ok=True, id=sid, spec=spec))

    def run_strategy(self, sid):
        u = self.require_user()
        if not u:
            return
        with db() as conn:
            s = conn.execute("SELECT * FROM strategies WHERE id=? AND user_id=?",
                             (sid, u["id"])).fetchone()
        if not s:
            return self.err("策略不存在", 404)
        spec = json.loads(s["spec"])
        result = simulate.run_strategy(spec, sid=sid)
        result["strategy_id"] = sid
        result["strategy_name"] = s["name"]
        with db() as conn:
            conn.execute("INSERT OR REPLACE INTO runs(strategy_id,user_id,result,created_at) "
                         "VALUES(?,?,?,?)",
                         (sid, u["id"], json.dumps(result, ensure_ascii=False), now_str()))
        return self.json(dict(ok=True, result=result))

    # ---------- PUT ----------
    def api_put(self, route):
        try:
            if route == "/api/wallet":
                u = self.require_user()
                if not u:
                    return
                b = self.body()
                try:
                    v = round(float(b.get("wallet")), 2)
                except (TypeError, ValueError):
                    return self.err("资金池金额必须是数字")
                if not (0 < v <= 1e9):
                    return self.err("资金池金额需大于 0")
                with db() as conn:
                    conn.execute("UPDATE users SET wallet=? WHERE id=?", (v, u["id"]))
                return self.json(dict(ok=True, wallet=v))
            return self.err("接口不存在", 404)
        except ValueError as e:
            return self.err(str(e), 400)
        except Exception:
            traceback.print_exc()
            return self.err("服务器内部错误，请稍后重试", 500)

    # ---------- DELETE ----------
    def api_delete(self, route):
        try:
            m = re.match(r"^/api/strategies/(\d+)$", route)
            if not m:
                return self.err("接口不存在", 404)
            u = self.require_user()
            if not u:
                return
            sid = int(m.group(1))
            with db() as conn:
                cur = conn.execute("DELETE FROM strategies WHERE id=? AND user_id=?",
                                   (sid, u["id"]))
                conn.execute("DELETE FROM runs WHERE strategy_id=?", (sid,))
            if cur.rowcount == 0:
                return self.err("策略不存在", 404)
            return self.json(dict(ok=True))
        except Exception:
            traceback.print_exc()
            return self.err("服务器内部错误，请稍后重试", 500)


class LimitedServer(ThreadingHTTPServer):
    """限制同时处理的连接数，超出时立即回 503，避免线程被慢连接耗尽"""
    daemon_threads = True

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._sem = threading.BoundedSemaphore(MAX_CONN)

    def process_request(self, request, client_address):
        if not self._sem.acquire(blocking=False):
            try:
                # 先取走已到达的请求字节，否则关闭时会触发 RST 把 503 冲掉
                request.settimeout(0.05)
                try:
                    request.recv(65536)
                except Exception:
                    pass
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\n"
                                b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            except Exception:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._sem.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._sem.release()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    if not os.path.isfile(os.path.join(WEB, "index.html")):
        raise SystemExit("缺少 web/index.html")
    init_db()
    srv = LimitedServer((args.host, args.port), Handler)
    print("彩票预估网站已启动： http://%s:%d" % (args.host, args.port))
    print("原版完整数据站：   http://%s:%d/legacy" % (args.host, args.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
