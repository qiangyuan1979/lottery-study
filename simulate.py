# -*- coding: utf-8 -*-
"""
策略模拟引擎
--------------------------------------------------------------
用户给定：彩种、选号方式（自选 / 随机 / 系统预测）、每期注数、是否加买复式、
起始日期、期数、初始资金池。引擎在 draws.csv 的历史期内逐期"先选号、后开奖"，
用当期真实开奖号码与官方单注奖金结算，得到投入 / 奖金 / 净盈亏。

关键约束
  · 系统预测模式每期只用"该期之前"的历史数据打分选号，不含未来数据。
  · 资金池不足当期投入时跳过该期（不赊账），并计入 skipped。
  · 单式每注 2 元；复式按方案注数 × 2 元。
  · 浮动奖级无人中出时回退该奖级最近一次公布值（与 ledger/backtest 一致）。
"""
import math
import os
import random
from collections import Counter

import backtest
from analyze import load_draws, analyze_lotto, analyze_digit
from predict import score_lotto, score_special, score_digit, weighted_sample

PRICE = 2
MAX_PERIODS = 60
MAX_PERIODS_SYSTEM = 30
MAX_TICKETS = 10
MAX_TICKETS_SYSTEM = 5
MIN_HISTORY = 60          # 系统预测模式至少需要的观察期数

META = {
    "ssq": dict(name="双色球", type="lotto", front_name="红球", front_pool=33, front_pick=6,
                back_name="蓝球", back_pool=16, back_pick=1,
                desc="红球 33 选 6 + 蓝球 16 选 1", draw_days="周二 / 周四 / 周日"),
    "dlt": dict(name="超级大乐透", type="lotto", front_name="前区", front_pool=35, front_pick=5,
                back_name="后区", back_pool=12, back_pick=2,
                desc="前区 35 选 5 + 后区 12 选 2", draw_days="周一 / 周三 / 周六"),
    "qlc": dict(name="七乐彩", type="lotto", front_name="基本号", front_pool=30, front_pick=7,
                back_name="", back_pool=0, back_pick=0,
                desc="基本号 30 选 7（特别号由开奖产生）", draw_days="周一 / 周三 / 周五"),
    "kl8": dict(name="快乐8", type="lotto", front_name="选号", front_pool=80, front_pick=10,
                back_name="", back_pool=0, back_pick=0,
                desc="80 个号中选 10 个（开奖 20 个）", draw_days="每日"),
    "3d":  dict(name="福彩3D", type="digit", positions=[10, 10, 10],
                desc="百 / 十 / 个位各 0-9 选 1", draw_days="每日"),
    "qxc": dict(name="七星彩", type="digit", positions=[10, 10, 10, 10, 10, 10, 15],
                desc="前 6 位 0-9，第 7 位 0-14", draw_days="周二 / 周五 / 周日"),
}

_draws = None
_draws_mt = None
_pmap = None
DRAWS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "draws.csv")


def draws():
    """开奖数据；draws.csv 更新后自动重载，服务端无需重启"""
    global _draws, _draws_mt
    try:
        mt = os.path.getmtime(DRAWS_CSV)
    except OSError:
        mt = None
    if _draws is None or mt != _draws_mt:
        _draws = load_draws()
        _draws_mt = mt
    return _draws


def prize_map():
    global _pmap
    if _pmap is None:
        _pmap = backtest.load_prize_table()
    return _pmap


# ---------------------------------------------------------------- 号码规范化
def normalize_custom(code, raw):
    """把前端传入的自选号码规范化，非法输入抛 ValueError"""
    m = META[code]
    raw = raw or {}
    if m["type"] == "digit":
        nums = [int(x) for x in (raw.get("nums") or [])]
        if len(nums) != len(m["positions"]):
            raise ValueError("%s 需要 %d 位号码" % (m["name"], len(m["positions"])))
        for v, k in zip(nums, m["positions"]):
            if not 0 <= v < k:
                raise ValueError("%s 的某一位超出取值范围" % m["name"])
        return dict(nums=nums, sp=[])

    front = sorted({int(x) for x in (raw.get("front") or [])})
    if len(front) != m["front_pick"]:
        raise ValueError("%s 需要选 %d 个%s" % (m["name"], m["front_pick"], m["front_name"]))
    if any(n < 1 or n > m["front_pool"] for n in front):
        raise ValueError("%s 号码超出 1-%d" % (m["name"], m["front_pool"]))
    back = sorted({int(x) for x in (raw.get("back") or [])})
    if m["back_pick"]:
        if len(back) != m["back_pick"]:
            raise ValueError("%s 需要选 %d 个%s" % (m["name"], m["back_pick"], m["back_name"]))
        if any(n < 1 or n > m["back_pool"] for n in back):
            raise ValueError("%s %s超出 1-%d" % (m["name"], m["back_name"], m["back_pool"]))
    else:
        back = []
    return dict(nums=front, sp=back)


def validate(spec):
    """校验并补全策略参数，返回规范化后的 spec"""
    if not isinstance(spec, dict):
        raise ValueError("策略参数格式错误")
    codes = [c for c in (spec.get("codes") or []) if c in META]
    codes = [c for c in ("ssq", "dlt", "qlc", "kl8", "3d", "qxc") if c in codes]
    if not codes:
        raise ValueError("请至少选择一个彩种")
    if len(codes) > 6:
        raise ValueError("彩种过多")

    mode = spec.get("mode")
    if mode not in ("custom", "random", "system"):
        raise ValueError("选号方式只能是 自选 / 随机 / 系统预测")
    if mode == "custom" and len(codes) != 1:
        raise ValueError("自选模式一次只支持一个彩种（如需多彩种请分别建立策略）")

    try:
        tickets = int(spec.get("tickets") or 5)
    except (TypeError, ValueError):
        raise ValueError("每期注数必须是整数")
    cap = MAX_TICKETS_SYSTEM if mode == "system" else MAX_TICKETS
    tickets = max(1, min(cap, tickets))

    try:
        periods = int(spec.get("periods") or 30)
    except (TypeError, ValueError):
        raise ValueError("期数必须是整数")
    cap_p = MAX_PERIODS_SYSTEM if mode == "system" else MAX_PERIODS
    periods = max(1, min(cap_p, periods))

    try:
        initial = float(spec.get("initial") or 0)
    except (TypeError, ValueError):
        raise ValueError("初始资金池必须是数字")
    if initial <= 0:
        raise ValueError("初始资金池必须大于 0")
    initial = min(initial, 1e9)

    start_date = str(spec.get("start_date") or "").strip()
    if len(start_date) != 10:
        raise ValueError("请选择起始日期（YYYY-MM-DD）")

    custom = {}
    if mode == "custom":
        raw = spec.get("custom") or {}
        if isinstance(raw, dict) and isinstance(raw.get(codes[0]), dict):
            # 兼容已规范化的存档形式 {code: {nums, sp}}
            saved = raw[codes[0]]
            raw = (dict(nums=saved.get("nums") or [])
                   if META[codes[0]]["type"] == "digit" else
                   dict(front=saved.get("nums") or [], back=saved.get("sp") or []))
        custom = {codes[0]: normalize_custom(codes[0], raw)}

    name = str(spec.get("name") or "").strip()[:40] or "策略 %s" % " / ".join(META[c]["name"] for c in codes)

    return dict(
        name=name, codes=codes, mode=mode, tickets=tickets, periods=periods,
        start_date=start_date, initial=round(initial, 2),
        parlay=bool(spec.get("parlay")), custom=custom,
    )


# ---------------------------------------------------------------- 选号
def _sample_front(rnd, m):
    return sorted(rnd.sample(range(1, m["front_pool"] + 1), m["front_pick"]))


def _sample_back(rnd, m):
    if not m["back_pick"]:
        return []
    return sorted(rnd.sample(range(1, m["back_pool"] + 1), m["back_pick"]))


def gen_random_tickets(code, rnd, n):
    m = META[code]
    if m["type"] == "digit":
        return [dict(nums=[rnd.randrange(k) for k in m["positions"]], sp=[]) for _ in range(n)]
    return [dict(nums=_sample_front(rnd, m), sp=_sample_back(rnd, m)) for _ in range(n)]


def gen_system_tickets(code, hist, issue, n, sid):
    """只用 hist（当期之前）打分选号；返回 (tickets, parlay)"""
    m = META[code]
    rnd = random.Random("sys-%s-%s-%s" % (sid, code, issue))

    if m["type"] == "digit":
        rec = analyze_digit(code, hist)
        rec["last"], rec["draws"] = hist[-1], len(hist)
        pos_scores, pos_detail = score_digit(code, hist, rec)
        tickets = []
        for _ in range(n):
            nums = []
            for i, k in enumerate(m["positions"]):
                items = list(range(k))
                w = [math.exp(0.9 * pos_scores[i][d]) for d in items]
                nums.append(weighted_sample(rnd, items, w, 1)[0])
            tickets.append(dict(nums=nums, sp=[]))
        widths = [2] * len(m["positions"])
        if code == "qxc":
            widths[-1] = 1
        par = dict(label="按位取打分前 2 码复式" if code == "qxc" else "每位取打分前 2 码复式",
                   front=[[x["d"] for x in top[:w]] for top, w in zip(pos_detail, widths)], back=[])
        return tickets, par

    rec = analyze_lotto(code, hist)
    rec["last"], rec["draws"] = hist[-1], len(hist)
    score, _detail = score_lotto(code, hist, rec)
    special = score_special(code, hist, rec)

    pool, pick = m["front_pool"], m["front_pick"]
    order = sorted(range(1, pool + 1), key=lambda x: -score[x])
    cand = order[:max(pick * 2, pick + 5)]
    w = [math.exp(0.9 * score[x]) for x in cand]

    sp_nums = [x["n"] for x in ((special or {}).get("recommend") or [])]
    sp_pick = (special or {}).get("pick", 0)
    pairs = [(sp_nums[a], sp_nums[b]) for a in range(len(sp_nums)) for b in range(a + 1, len(sp_nums))]

    tickets = []
    for i in range(n):
        nums = sorted(weighted_sample(rnd, cand, w, pick))
        sp = []
        if sp_pick == 1 and sp_nums:
            sp = [sp_nums[i % len(sp_nums)]]
        elif sp_pick >= 2 and pairs:
            sp = sorted(pairs[i % len(pairs)])
        tickets.append(dict(nums=nums, sp=sp))

    par = None
    if code == "ssq":
        par = dict(label="红球 7 码 + 蓝球 2 码 小复式", front=sorted(order[:7]),
                   back=sorted(sp_nums[:2]) if len(sp_nums) >= 2 else [])
    elif code == "dlt":
        par = dict(label="前区 6 码 + 后区 3 码 复式", front=sorted(order[:6]),
                   back=sorted(sp_nums[:3]) if len(sp_nums) >= 3 else [])
    elif code == "qlc":
        par = dict(label="基本号 8 码复式", front=sorted(order[:8]), back=[])
    elif code == "kl8":
        par = dict(label="选十 11 码复式", front=sorted(order[:11]), back=[])
    return tickets, par


def _digit_widths(code):
    m = META[code]
    w = [2] * len(m["positions"])
    if code == "qxc":
        w[-1] = 1
    return w


def gen_custom_parlay(code, base, rnd):
    """在自选号码基础上各位置加 1 个号码组成复式"""
    m = META[code]
    if m["type"] == "digit":
        front = []
        for v, k, w in zip(base["nums"], m["positions"], _digit_widths(code)):
            extra = [d for d in range(k) if d != v]
            front.append(sorted([v] + (rnd.sample(extra, w - 1) if w > 1 else [])))
        return dict(label="按位加 1 码复式", front=front, back=[])

    front = list(base["nums"])
    if len(front) < m["front_pool"]:
        rest = [x for x in range(1, m["front_pool"] + 1) if x not in front]
        front.append(rnd.choice(rest))
    back = list(base["sp"])
    if m["back_pick"] and len(back) < m["back_pool"]:
        rest = [x for x in range(1, m["back_pool"] + 1) if x not in back]
        back.append(rnd.choice(rest))
    return dict(label="加 1 码复式", front=sorted(front), back=sorted(back))


def gen_random_parlay(code, rnd):
    m = META[code]
    if m["type"] == "digit":
        front = []
        for k, w in zip(m["positions"], _digit_widths(code)):
            front.append(sorted(rnd.sample(range(k), w)))
        return dict(label="按位随机复式", front=front, back=[])
    front_n = m["front_pick"] + 1
    front = sorted(rnd.sample(range(1, m["front_pool"] + 1), front_n))
    back = []
    if m["back_pick"]:
        back_n = m["back_pick"] + 1
        back = sorted(rnd.sample(range(1, m["back_pool"] + 1), min(back_n, m["back_pool"])))
    return dict(label="随机复式", front=front, back=back)


# ---------------------------------------------------------------- 注数 / 花费
def parlay_bets(code, par):
    if not par:
        return 0
    m = META[code]
    if m["type"] == "digit":
        bets = 1
        for g in par["front"]:
            bets *= max(1, len(g))
        return bets
    bets = math.comb(len(par["front"]), m["front_pick"])
    if m["back_pick"]:
        bets *= math.comb(len(par["back"]), m["back_pick"])
    return bets


# ---------------------------------------------------------------- 结算
def settle_period(code, tickets, par, draw, pmap):
    prize = 0.0
    level_count = Counter()
    level_amount = Counter()
    detail = []
    for t in tickets:
        lv, amt = backtest.settle_single(code, t, draw, pmap)
        prize += amt
        if lv:
            level_count[lv] += 1
            level_amount[lv] += amt
        detail.append(dict(nums=list(t["nums"]), sp=list(t.get("sp") or []),
                           level=lv, prize=round(amt, 2)))

    par_prize, par_bets, par_hits = 0.0, 0, Counter()
    if par:
        par_prize, par_bets, par_hits = backtest.settle_parlay(code, par, draw, pmap)
        for k, v in par_hits.items():
            level_count[k] += v
            level_amount[k] += backtest.prize_of(pmap, code, draw["issue"], k) * v
        prize += par_prize

    return dict(tickets=detail, level_count=dict(level_count),
                level_amount={k: round(v, 2) for k, v in level_amount.items()},
                parlay_prize=round(par_prize, 2),
                parlay_hits=dict(par_hits), parlay_bets=par_bets,
                prize=round(prize, 2))


# ---------------------------------------------------------------- 主流程
def run_strategy(raw_spec, sid=0):
    spec = validate(raw_spec)
    pmap = prize_map()
    D = draws()

    events = []
    notes = []
    for code in spec["codes"]:
        rows = D.get(code) or []
        if not rows:
            notes.append("%s 暂无开奖数据，已跳过" % META[code]["name"])
            continue

        idx = next((i for i, r in enumerate(rows) if r["date"] >= spec["start_date"]), None)
        if idx is None:
            notes.append("%s 起始日期晚于最新开奖，已跳过" % META[code]["name"])
            continue
        need = MIN_HISTORY if spec["mode"] == "system" else 0
        if idx < need:
            idx = min(need, len(rows) - 1)
            notes.append("%s 起始日期过早，系统预测需至少 %d 期历史，已顺延至 %s"
                         % (META[code]["name"], need, rows[idx]["date"]))
        if idx >= len(rows) - 1:
            notes.append("%s 起点之后无可用开奖期，已跳过" % META[code]["name"])
            continue

        pool = rows[idx: idx + spec["periods"]]
        for j, draw in enumerate(pool):
            i = idx + j
            hist = rows[:i]
            cost_base = spec["tickets"] * PRICE

            if spec["mode"] == "custom":
                base = spec["custom"][code]
                tickets = [dict(nums=list(base["nums"]), sp=list(base["sp"])) for _ in range(spec["tickets"])]
                par = None
                if spec["parlay"]:
                    rnd = random.Random("cus-%s-%s-%s" % (sid, code, draw["issue"]))
                    par = gen_custom_parlay(code, base, rnd)
            elif spec["mode"] == "random":
                rnd = random.Random("rnd-%s-%s-%s" % (sid, code, draw["issue"]))
                tickets = gen_random_tickets(code, rnd, spec["tickets"])
                par = gen_random_parlay(code, rnd) if spec["parlay"] else None
            else:
                tickets, par = gen_system_tickets(code, hist, draw["issue"], spec["tickets"], sid)
                if not spec["parlay"]:
                    par = None

            bets = parlay_bets(code, par)
            cost = cost_base + bets * PRICE
            settled = settle_period(code, tickets, par, draw, pmap)
            events.append(dict(
                code=code, name=META[code]["name"], issue=draw["issue"], date=draw["date"],
                draw=dict(issue=draw["issue"], date=draw["date"], nums=list(draw["nums"]), sp=list(draw["sp"])),
                tickets=settled["tickets"],
                parlay=dict(label=par.get("label", ""), front=par.get("front"), back=par.get("back"),
                            bets=bets, cost=bets * PRICE) if par else None,
                cost=round(cost, 2), cost_tickets=round(cost_base, 2),
                cost_parlay=round(bets * PRICE, 2),
                prize=settled["prize"], net=round(settled["prize"] - cost, 2),
                level_count=settled["level_count"], level_amount=settled["level_amount"],
                parlay_prize=settled["parlay_prize"], parlay_hits=settled["parlay_hits"],
                parlay_bets=bets,
            ))

    events.sort(key=lambda e: (e["date"], e["code"]))

    balance = spec["initial"]
    bought, skipped = [], 0
    series = []
    level_total = Counter()
    level_amount_total = Counter()
    by_code = {}

    for ev in events:
        b = by_code.setdefault(ev["code"], dict(code=ev["code"], name=ev["name"], periods=0,
                                                cost=0.0, prize=0.0, net=0.0, hits=0))
        if balance + 1e-9 < ev["cost"]:
            skipped += 1
            continue
        balance = round(balance - ev["cost"] + ev["prize"], 2)
        ev["balance"] = balance
        ev["cum_net"] = round(balance - spec["initial"], 2)
        bought.append(ev)
        series.append(dict(date=ev["date"], issue=ev["issue"], code=ev["code"],
                           balance=balance, cum_net=ev["cum_net"], net=ev["net"]))
        b["periods"] += 1
        b["cost"] += ev["cost"]
        b["prize"] += ev["prize"]
        b["net"] += ev["net"]
        b["hits"] += 1 if ev["prize"] > 0 else 0
        for k, v in (ev["level_count"] or {}).items():
            level_total[k] += v
        for k, v in (ev["level_amount"] or {}).items():
            level_amount_total[k] += v

    cost = round(sum(e["cost"] for e in bought), 2)
    prize = round(sum(e["prize"] for e in bought), 2)
    net = round(prize - cost, 2)

    for b in by_code.values():
        b["cost"] = round(b["cost"], 2)
        b["prize"] = round(b["prize"], 2)
        b["net"] = round(b["net"], 2)
        b["roi"] = round(b["net"] / b["cost"], 4) if b["cost"] else None
        b["hit_periods"] = b.get("hits", 0)
        b.pop("hits", None)

    levels = [dict(level=k, count=level_total[k], amount=round(level_amount_total.get(k, 0.0), 2))
              for k in sorted(level_total, key=lambda x: (-level_amount_total.get(x, 0.0), x))]

    return dict(
        spec=spec,
        generated_at=_now(),
        summary=dict(initial=spec["initial"], final=round(balance, 2), periods=len(bought),
                     skipped=skipped, cost=cost, prize=prize, net=net,
                     roi=round(net / cost, 4) if cost else None,
                     hit_periods=sum(1 for e in bought if e["prize"] > 0),
                     total_periods=len(events), levels=levels,
                     by_code=sorted(by_code.values(), key=lambda x: -x["cost"])),
        series=series,
        rows=bought,
        notes=notes + [
            "系统预测模式每期只用该期之前的历史数据打分选号，不使用未来数据。",
            "手工核对时请注意：单式每注 2 元，复式按方案注数 × 2 元；资金池不足的期次不购买。",
        ],
    )


def _now():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- 自测
if __name__ == "__main__":
    demo = dict(name="自测-双色球系统预测", codes=["ssq"], mode="system", tickets=2,
                parlay=False, start_date="2026-04-01", periods=6, initial=500)
    r = run_strategy(demo)
    s = r["summary"]
    print("期数 %d 跳过 %d 投入 %.0f 奖金 %.0f 净 %.0f 收益率 %s" %
          (s["periods"], s["skipped"], s["cost"], s["prize"], s["net"], s["roi"]))
    for row in r["rows"][:3]:
        print(row["issue"], row["date"], [t["nums"] for t in row["tickets"]],
              row["draw"]["nums"], row["prize"])
