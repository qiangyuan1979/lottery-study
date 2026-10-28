# -*- coding: utf-8 -*-
"""
逐期滚动回测与盈亏统计
--------------------------------------------------------------
对每个彩种取最近 N 期，逐期执行“只用当期之前的历史数据”的预测流程：
    历史切片 -> analyze_lotto / analyze_digit 重算 rec
             -> score_lotto / score_digit 打分
             -> gen_tickets 生成 5 注单式 -> build_parlay 生成复式
再用当期真实开奖号码 + 官方公布的单注奖金结算，得到每期投入 / 奖金 / 净盈亏。

结算口径
  · 单式：5 注 × 2 元
  · 复式：按推荐方案的注数 × 2 元
  · 浮动奖级（一等奖、二等奖等）无人中出时官方单注奖金为 0/空，
    回退到该奖级最近一次公布的非零单注奖金；3D 奖级明细缺失，按固定直选 1040 元。
  · 同时用同一套结算函数模拟“完全随机选号”，作为对照组。

输出 data/backtest.json
"""
import argparse
import json
import math
import os
import random
from collections import Counter
from datetime import datetime

import pandas as pd

from analyze import SPEC, ORDER, load_draws, analyze_lotto, analyze_digit
import predict
from predict import (score_lotto, score_special, score_digit,
                     gen_tickets, gen_digit_tickets, build_special_tickets, build_parlay)

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
PRIZES = os.path.join(DATA, "prizes.csv")
OUT = os.path.join(DATA, "backtest.json")

PRICE = 2

# 官方未公布 / 无人中出时的回退单注奖金（元）
DEFAULT_PRIZE = {
    ("ssq", "一等奖"): 5000000, ("ssq", "二等奖"): 100000,
    ("dlt", "一等奖"): 5000000, ("dlt", "二等奖"): 100000,
    ("qlc", "一等奖"): 500000, ("qlc", "二等奖"): 20000,
    ("qxc", "一等奖"): 5000000, ("qxc", "二等奖"): 30000,
    ("kl8", "选十中十"): 5000000,
    ("3d", "单选"): 1040, ("3d", "组三"): 346, ("3d", "组六"): 173,
}

KL8_LEVEL = {10: "选十中十", 9: "选十中九", 8: "选十中八", 7: "选十中七",
             6: "选十中六", 5: "选十中五", 0: "选十中零"}

# load_prize_table() 填充：各彩种各奖级最近一次公布的非零单注奖金
LAST_PRIZE = {}

NOTE = ("本页为逐期滚动回测：每一期只用该期之前的历史数据重新跑预测流程，再用当期真实开奖号码结算，"
        "因此不含未来数据。奖金取官方公布的当期单注奖金；浮动奖级无人中出时回退到最近一次公布值。"
        "彩票返奖率约 50%，长期期望必然亏损，回测结果只用于检验预测方法，不构成投注建议。")


# ---------------------------------------------------------------- 奖金表
def load_prize_table():
    """{(code, issue): {level: unit_prize}}，无人中出的奖级用此前最近非零值回退"""
    df = pd.read_csv(PRIZES, dtype=str)
    df["unit"] = pd.to_numeric(df["unit_prize"], errors="coerce")
    df["lv"] = df["prize_level"].fillna("")
    # 只保留基本投注奖级，剔除追加 / 派奖变体，避免重复计奖
    df = df[~df["lv"].str.contains("追加|派奖", regex=True)]
    df = df.sort_values(["lottery_code", "draw_date"])

    table, last = {}, {}
    for (code, issue), g in df.groupby(["lottery_code", "issue"], sort=False):
        if code == "3d":       # 3D 奖级数据止于 2021 年，统一用固定奖金
            continue
        m = {}
        for _, x in g.iterrows():
            lv = x["lv"]
            u = x["unit"]
            if pd.notna(u) and u > 0:
                last[(code, lv)] = float(u)
                m[lv] = float(u)
            else:
                v = last.get((code, lv)) or DEFAULT_PRIZE.get((code, lv))
                if v:
                    m[lv] = float(v)
        if m:
            table[(code, issue)] = m
    LAST_PRIZE.clear()
    LAST_PRIZE.update(last)
    return table


def prize_of(pmap, code, issue, level):
    """当期已有奖级明细优先；缺该期（如奖金未公布）时回退该奖级最近一次公布值"""
    if not level:
        return 0.0
    v = (pmap.get((code, issue)) or {}).get(level)
    if v is None:
        v = LAST_PRIZE.get((code, level))
    if v is None:
        v = DEFAULT_PRIZE.get((code, level), 0.0)
    return float(v)


# ---------------------------------------------------------------- 中奖判定
def level_ssq(t, draw):
    red = len(set(t["nums"]) & set(draw["nums"]))
    blue = bool(t.get("sp")) and t["sp"][0] in draw["sp"]
    if red == 6 and blue:
        return "一等奖"
    if red == 6:
        return "二等奖"
    if red == 5 and blue:
        return "三等奖"
    if red == 5 or (red == 4 and blue):
        return "四等奖"
    if red == 4 or (red == 3 and blue):
        return "五等奖"
    if blue:
        return "六等奖"
    return None


def level_dlt(t, draw):
    f = len(set(t["nums"]) & set(draw["nums"]))
    b = len(set(t.get("sp") or []) & set(draw["sp"]))
    if f == 5 and b == 2:
        return "一等奖"
    if f == 5 and b == 1:
        return "二等奖"
    if f == 5:
        return "三等奖"
    if f == 4 and b == 2:
        return "四等奖"
    if f == 4 and b == 1:
        return "五等奖"
    if f == 3 and b == 2:
        return "六等奖"
    if f == 4:
        return "七等奖"
    if (f == 3 and b == 1) or (f == 2 and b == 2):
        return "八等奖"
    if (f == 3 and b == 0) or (f == 2 and b == 1) or (f == 1 and b == 2) or (f == 0 and b == 2):
        return "九等奖"
    return None


def level_qlc(t, draw):
    """七乐彩：7 个基本号 + 1 个特别号，特别号需落在自己选的 7 个号中"""
    m = len(set(t["nums"]) & set(draw["nums"]))
    sp = draw["sp"][0] in set(t["nums"]) if draw.get("sp") else False
    if m == 7:
        return "一等奖"
    if m == 6 and sp:
        return "二等奖"
    if m == 6:
        return "三等奖"
    if m == 5 and sp:
        return "四等奖"
    if m == 5:
        return "五等奖"
    if m == 4 and sp:
        return "六等奖"
    if m == 4:
        return "七等奖"
    return None


def level_kl8(t, draw):
    h = len(set(t["nums"]) & set(draw["nums"]))
    return KL8_LEVEL.get(h)


def level_3d(t, draw):
    return "单选" if list(t["nums"]) == list(draw["nums"]) else None


def level_qxc(t, draw):
    """7星彩（2020-10 新规）：先判前 6 位，再看最后一位"""
    a, b = list(t["nums"]), list(draw["nums"])
    m6 = sum(1 for x, y in zip(a[:6], b[:6]) if x == y)
    last = len(a) > 6 and a[6] == b[6]
    total = m6 + (1 if last else 0)
    if total == 7:
        return "一等奖"
    if m6 == 6:
        return "二等奖"
    if m6 == 5 and last:
        return "三等奖"
    if total >= 5:
        return "四等奖"
    if total == 4:
        return "五等奖"
    if total == 3:
        return "六等奖"
    if last and m6 <= 1:
        return "六等奖"
    return None


LEVEL_FN = dict(ssq=level_ssq, dlt=level_dlt, qlc=level_qlc,
                kl8=level_kl8, **{"3d": level_3d, "qxc": level_qxc})


def settle_single(code, t, draw, pmap):
    lv = LEVEL_FN[code](t, draw)
    return lv, (prize_of(pmap, code, draw["issue"], lv) if lv else 0.0)


# ---------------------------------------------------------------- 复式结算
def settle_parlay(code, par, draw, pmap):
    """展开复式方案逐注结算，返回 (奖金, 注数, 奖级命中 Counter)"""
    from itertools import combinations, product
    hits, prize, bets = Counter(), 0.0, 0

    def add(nums, sp=None):
        nonlocal prize, bets
        t = dict(nums=list(nums), sp=list(sp) if sp else [])
        lv, amt = settle_single(code, t, draw, pmap)
        bets += 1
        prize += amt
        if lv:
            hits[lv] += 1

    if code == "ssq":
        for c in combinations(par["front"], 6):
            for b in par["back"]:
                add(c, [b])
    elif code == "dlt":
        for c in combinations(par["front"], 5):
            for b in combinations(par["back"], 2):
                add(c, list(b))
    elif code == "qlc":
        for c in combinations(par["front"], 7):
            add(c)
    elif code == "kl8":
        for c in combinations(par["front"], 10):
            add(c)
    elif code == "3d":
        for p in product(*par["front"]):
            add(list(p))
    elif code == "qxc":
        groups = list(par["front"]) + list(par["back"])
        for p in product(*groups):
            add(list(p))
    return prize, bets, hits


# ---------------------------------------------------------------- 逐期推荐
def recommend(code, rows, i):
    """用 rows[:i] 生成该期推荐（与线上预测同一套流程）"""
    hist = rows[:i]
    if SPEC[code]["digit"]:
        rec = analyze_digit(code, hist)
        rec["last"], rec["draws"] = hist[-1], len(hist)
        ps, pos_detail = score_digit(code, hist, rec)
        tickets = gen_digit_tickets(code, hist, rec, ps)
        if code == "3d":
            par = dict(label="直选复式：每位取打分前 3 码",
                       front=[[x["d"] for x in top] for top in pos_detail], back=[],
                       bets=3 ** 3, cost=3 ** 3 * PRICE)
        else:
            par = dict(label="复式：前 6 位各取前 2 码、第 7 位取首位",
                       front=[[x["d"] for x in top[:2]] for top in pos_detail[:6]],
                       back=[[pos_detail[6][0]["d"]]],
                       bets=2 ** 6, cost=2 ** 6 * PRICE)
        return tickets, par

    rec = analyze_lotto(code, hist)
    rec["last"], rec["draws"] = hist[-1], len(hist)
    score, detail = score_lotto(code, hist, rec)
    special = score_special(code, hist, rec)
    tickets, _ = gen_tickets(code, hist, rec, score, detail, rec["last"]["issue"])
    tickets = build_special_tickets(tickets, special, (special or {}).get("pick", 0))

    pool, pick = SPEC[code]["pool"], SPEC[code]["pick"]
    order = sorted(range(1, pool + 1), key=lambda n: -score[n])
    core_n = 14 if code == "kl8" else pick + 2
    core = [dict(n=n) for n in order[:core_n]]
    par = build_parlay(code, {**rec, "core": core}, special)
    return tickets, par


# ---------------------------------------------------------------- 随机基准
def _pick(rnd, pool, k, start=1):
    return sorted(rnd.sample(range(start, pool + 1), k))


def random_pack(code, rnd, n_single):
    """生成与预测方案同结构的随机方案（5 注单式 + 1 组同注数复式）"""
    sp = SPEC[code]
    if code == "ssq":
        blues = rnd.sample(range(1, 17), 2)
        tk = [dict(nums=_pick(rnd, 33, 6), sp=[blues[i % 2]]) for i in range(n_single)]
        par = dict(front=_pick(rnd, 33, 7), back=_pick(rnd, 16, 2))
    elif code == "dlt":
        backs = rnd.sample(range(1, 13), 3)
        pairs = [(backs[i], backs[j]) for i in range(3) for j in range(i + 1, 3)]
        tk = [dict(nums=_pick(rnd, 35, 5), sp=sorted(pairs[i % 3])) for i in range(n_single)]
        par = dict(front=_pick(rnd, 35, 6), back=_pick(rnd, 12, 3))
    elif code == "qlc":
        tk = [dict(nums=_pick(rnd, 30, 7), sp=[]) for _ in range(n_single)]
        par = dict(front=_pick(rnd, 30, 8), back=[])
    elif code == "kl8":
        tk = [dict(nums=_pick(rnd, 80, 10), sp=[]) for _ in range(n_single)]
        par = dict(front=_pick(rnd, 80, 11), back=[])
    elif code == "3d":
        cols = [rnd.sample(range(10), 3) for _ in range(3)]
        tk = [dict(nums=[c[rnd.randrange(3)] for c in cols], sp=[]) for _ in range(n_single)]
        par = dict(front=cols, back=[])
    else:  # qxc
        cols = [rnd.sample(range(10), 2) for _ in range(6)] + [rnd.sample(range(15), 1)]
        tk = [dict(nums=[c[rnd.randrange(len(c))] for c in cols], sp=[]) for _ in range(n_single)]
        par = dict(front=cols[:6], back=[cols[6]])
    return tk, par


def pct(vals, q):
    if not vals:
        return None
    a = sorted(vals)
    k = (len(a) - 1) * q
    lo = int(math.floor(k))
    hi = min(lo + 1, len(a) - 1)
    return a[lo] + (a[hi] - a[lo]) * (k - lo)


# ---------------------------------------------------------------- 汇总
def summarize(cost, prize, periods, levels):
    net = prize - cost
    return dict(
        cost=round(cost, 2), prize=round(prize, 2), net=round(net, 2),
        roi=round(net / cost, 4) if cost else None,
        periods=periods,
        levels=[dict(level=k, count=v["count"], amount=round(v["amount"], 2))
                for k, v in sorted(levels.items(), key=lambda kv: (-kv[1]["amount"], kv[0]))],
    )


def backtest_one(code, rows, pmap, periods, iters, seed=7):
    n = min(periods, len(rows) - 60)
    start = len(rows) - n
    per_period = []
    single_cost = single_prize = 0.0
    par_cost = par_prize = 0.0
    lv_single, lv_par = Counter(), Counter()
    amt_single, amt_par = Counter(), Counter()
    hit_periods = 0
    best = None
    cum = 0.0

    for i in range(start, len(rows)):
        draw = rows[i]
        tickets, par = recommend(code, rows, i)

        s_prize, s_hits, detail = 0.0, [], []
        for t in tickets:
            lv, amt = settle_single(code, t, draw, pmap)
            s_prize += amt
            if lv:
                lv_single[lv] += 1
                amt_single[lv] += amt
                s_hits.append(dict(level=lv, amount=round(amt, 2)))
                if best is None or amt > best["amount"]:
                    best = dict(issue=draw["issue"], date=draw["date"], level=lv, amount=round(amt, 2))
            detail.append(dict(nums=t["nums"], sp=t.get("sp") or [], level=lv, prize=round(amt, 2)))

        p_prize, p_bets, p_hits = settle_parlay(code, par, draw, pmap) if par else (0.0, 0, Counter())
        for lv, c in p_hits.items():
            lv_par[lv] += c
            amt_par[lv] += prize_of(pmap, code, draw["issue"], lv) * c
        if s_hits or p_hits:
            hit_periods += 1

        cost_s = len(tickets) * PRICE
        cost_p = par["cost"] if par else 0
        net = s_prize + p_prize - cost_s - cost_p
        cum += net

        single_cost += cost_s
        single_prize += s_prize
        par_cost += cost_p
        par_prize += p_prize

        per_period.append(dict(
            issue=draw["issue"], date=draw["date"], nums=draw["nums"], sp=draw["sp"],
            single=dict(cost=cost_s, prize=round(s_prize, 2), net=round(s_prize - cost_s, 2),
                        hits=s_hits),
            parlay=dict(cost=cost_p, prize=round(p_prize, 2), net=round(p_prize - cost_p, 2),
                        bets=p_bets, hits=[dict(level=k, count=v) for k, v in p_hits.items()]),
            net=round(net, 2), cum_net=round(cum, 2),
            tickets=detail,
            parlay_info=dict(label=par["label"], front=par["front"], back=par["back"]) if par else None,
        ))

    # ---- 随机基准：同一套结算，随机选号，跑 iters 条完整路径 ----
    rnd = random.Random(seed)
    r_single, r_all = [], []
    for _ in range(iters):
        acc_s = acc_all = 0.0
        for pp in per_period:
            draw = dict(issue=pp["issue"], nums=pp["nums"], sp=pp["sp"])
            tk, par = random_pack(code, rnd, max(1, len(pp["tickets"])))
            sp_prize = sum(settle_single(code, t, draw, pmap)[1] for t in tk)
            pp_prize, _, _ = settle_parlay(code, par, draw, pmap)
            acc_s += sp_prize - pp["single"]["cost"]
            acc_all += sp_prize + pp_prize - pp["single"]["cost"] - pp["parlay"]["cost"]
        r_single.append(acc_s)
        r_all.append(acc_all)

    actual_s = round(single_prize - single_cost, 2)
    actual_all = round(single_prize + par_prize - single_cost - par_cost, 2)

    def rand_block(vals):
        return dict(
            iters=iters,
            net_mean=round(sum(vals) / len(vals), 2),
            net_p05=round(pct(vals, 0.05), 2),
            net_p95=round(pct(vals, 0.95), 2),
            beat_rate=round(sum(1 for v in vals if v > actual_all) / len(vals), 3),
        )

    return dict(
        code=code, name=SPEC[code]["name"], periods=len(per_period),
        start_issue=per_period[0]["issue"], end_issue=per_period[-1]["issue"],
        start_date=per_period[0]["date"], end_date=per_period[-1]["date"],
        single=summarize(single_cost, single_prize, len(per_period),
                         {k: dict(count=lv_single[k], amount=amt_single[k]) for k in lv_single}),
        parlay=summarize(par_cost, par_prize, len(per_period),
                         {k: dict(count=lv_par[k], amount=amt_par[k]) for k in lv_par}),
        combined=summarize(single_cost + par_cost, single_prize + par_prize, len(per_period),
                           {k: dict(count=lv_single[k] + lv_par[k],
                                    amount=amt_single[k] + amt_par[k]) for k in set(lv_single) | set(lv_par)}),
        hit_periods=hit_periods,
        best=best,
        random=dict(single=rand_block(r_single), combined=rand_block(r_all)),
        per_period=per_period,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--periods", type=int, default=100, help="每个彩种回测期数")
    ap.add_argument("--iters", type=int, default=100, help="随机基准模拟次数")
    ap.add_argument("--only", default="", help="只跑某个彩种代码，如 ssq")
    ap.add_argument("--tries", type=int, default=40000, help="组合抽样次数（默认与线上一致）")
    args = ap.parse_args()

    predict.TRIES = args.tries
    draws = load_draws()
    pmap = load_prize_table()

    res = []
    for code in ORDER:
        if args.only and code != args.only:
            continue
        rows = draws.get(code)
        if not rows or len(rows) < 80:
            continue
        print("回测 %s ..." % SPEC[code]["name"], flush=True)
        res.append(backtest_one(code, rows, pmap, args.periods, args.iters))

    total_cost = sum(x["combined"]["cost"] for x in res)
    total_prize = sum(x["combined"]["prize"] for x in res)
    total_single_cost = sum(x["single"]["cost"] for x in res)
    total_single_prize = sum(x["single"]["prize"] for x in res)
    total_par_cost = sum(x["parlay"]["cost"] for x in res)
    total_par_prize = sum(x["parlay"]["prize"] for x in res)

    payload = dict(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        periods=args.periods, price=PRICE, note=NOTE,
        method=("逐期滚动：第 i 期仅用前 i-1 期数据重算频数 / 遗漏 / 特别号，再按线上同款打分与约束生成 "
                "5 注单式 + 1 组复式；用当期真实开奖与官方单注奖金结算。随机基准为同结构随机选号，"
                "重复 %d 次完整路径。" % args.iters),
        totals=dict(
            single=dict(cost=round(total_single_cost, 2), prize=round(total_single_prize, 2),
                        net=round(total_single_prize - total_single_cost, 2),
                        roi=round((total_single_prize - total_single_cost) / total_single_cost, 4)
                        if total_single_cost else None),
            parlay=dict(cost=round(total_par_cost, 2), prize=round(total_par_prize, 2),
                        net=round(total_par_prize - total_par_cost, 2),
                        roi=round((total_par_prize - total_par_cost) / total_par_cost, 4)
                        if total_par_cost else None),
            combined=dict(cost=round(total_cost, 2), prize=round(total_prize, 2),
                          net=round(total_prize - total_cost, 2),
                          roi=round((total_prize - total_cost) / total_cost, 4) if total_cost else None),
        ),
        lotteries=res,
    )
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, separators=(",", ":"))

    print("写入 %s %.1f KB" % (OUT, os.path.getsize(OUT) / 1024))
    for x in res:
        c, s, r = x["combined"], x["single"], x["random"]["combined"]
        print("  %-6s %3d期 合计投入%9.0f 奖金%9.0f 净盈亏%+10.0f 回报率%+7.1f%% | 单式%+8.0f | 随机净%+9.0f"
              % (x["name"], x["periods"], c["cost"], c["prize"], c["net"], (c["roi"] or 0) * 100,
                 s["net"], r["net_mean"]))


if __name__ == "__main__":
    main()
