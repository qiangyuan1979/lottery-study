# -*- coding: utf-8 -*-
"""
下期推荐与组合生成
--------------------------------------------------------------
读取 data/draws.csv + data/analysis.json，按多因子打分给出：
  1. 下期高概率区间（复用分位数区间，附形态与分区）
  2. 号码推荐：核心号 / 备选号 / 低分号（打分为启发式，不代表概率优势）
  3. 组合推荐：5 注单式 + 复式方案（注数与金额）
  4. 数字彩按位推荐（福彩3D / 七星彩）

打分因子（乐透型）：
  长期频数 z（0.30）+ 近 50 期热度 z（0.30）+ 遗漏回补比（0.25）+ 与上期共现 z（0.15）
输出 data/predictions.json
"""
import json
import math
import os
import random
from collections import Counter
from datetime import date, timedelta

import numpy as np

from analyze import SPEC, ORDER, load_draws, r
from models import lotto_models, digit_models, posterior_terms

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(DATA, "predictions.json")
ANALYSIS = os.path.join(DATA, "analysis.json")

WIN = 50          # 近期窗口
CO_WIN = 200      # 共现统计窗口
TRIES = 40000     # 组合抽样次数
PRICE = 2         # 每注 2 元

# 开奖周期（Python weekday：周一=0）
WEEKDAYS = {
    "ssq": [1, 3, 6],   # 周二 / 周四 / 周日
    "dlt": [0, 2, 5],   # 周一 / 周三 / 周六
    "qlc": [0, 2, 4],   # 周一 / 周三 / 周五
    "qxc": [1, 4, 6],   # 周二 / 周五 / 周日
    "3d":  list(range(7)),
    "kl8": list(range(7)),
}
WNAME = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 单注约束（来自历史最频形态；快乐8按“选十”口径，和值区间为理论 80% 覆盖）
RULES = {
    "ssq": dict(size=6, sum=(74, 128), span=(17, 30), odd=(2, 3, 4), big=(2, 3, 4), maxrun=2, zones=2),
    "qlc": dict(size=7, sum=(82, 135), span=(18, 28), odd=(3, 4, 5), big=(3, 4, 5), maxrun=2, zones=2),
    "dlt": dict(size=5, sum=(62, 120), span=(15, 31), odd=(2, 3, 4), big=(2, 3, 4), maxrun=2, zones=2),
    "kl8": dict(size=10, sum=(320, 490), span=(45, 79), odd=(4, 5, 6, 7), big=(4, 5, 6, 7), maxrun=3, zones=0),
}

NOTE = ("本页推荐由历史统计的启发式打分生成（长期频数、近 50 期热度、遗漏回补、与上期共现），"
        "并叠加 EWMA、经验贝叶斯后验、一阶马尔可夫、蒙特卡洛、Bootstrap、超几何等概率模型的校准结果；"
        "学术层（Dirichlet–Multinomial、Poisson–Gamma 经验贝叶斯、BH-FDR、游程/NIST/Ljung–Box/马尔可夫 χ²/"
        "Anderson–Darling 随机性检验、Kelly 与轮转覆盖）其中两项进入打分：收缩后验 z×0.16、马尔可夫条件 lift z×0.18。"
        "这些检验的作用是验证“是否真的存在可被利用的规律”，而不是保证命中。"
        "高胜率区间是历史经验分位数覆盖范围，属于描述性统计，不改变每期独立随机的本质。"
        "任何一注号码的中奖概率相同，请理性购彩、量力而行。")


# ---------------------------------------------------------------- 工具
def standardize(d):
    """把 {k: v} 标准化为 z 分数"""
    vals = list(d.values())
    if not vals:
        return {}
    mu = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals)) or 1e-9
    return {k: (v - mu) / sd for k, v in d.items()}


def weekday_cn(d):
    return WNAME[d.weekday()]


def next_draw_date(last_date, code):
    try:
        d = date.fromisoformat(str(last_date))
    except ValueError:
        return None
    wds = WEEKDAYS.get(code, [])
    for i in range(1, 16):
        nd = d + timedelta(days=i)
        if nd.weekday() in wds:
            return nd.isoformat()
    return None


def next_issue(issue):
    s = str(issue)
    if len(s) >= 4:
        return s[:-3] + str(int(s[-3:]) + 1).zfill(3)
    return str(int(s) + 1)


def weighted_sample(rnd, items, weights, k):
    keys = []
    for it, w in zip(items, weights):
        u = rnd.random()
        keys.append((u ** (1.0 / max(w, 1e-9)), it))
    keys.sort(key=lambda x: -x[0])
    return sorted(it for _, it in keys[:k])


# ---------------------------------------------------------------- 乐透型打分
def score_lotto(code, rows, rec):
    sp = SPEC[code]
    pool, pick = sp["pool"], sp["pick"]
    zlong = {f["n"]: float(f["z"]) for f in rec["freq"]}

    win = rows[-WIN:]
    W = len(win)
    cnt = Counter()
    for d in win:
        cnt.update(d["nums"])
    p = pick / pool
    exp = W * p
    sd = math.sqrt(W * p * (1 - p)) or 1e-9
    zrec = {n: (cnt.get(n, 0) - exp) / sd for n in range(1, pool + 1)}

    om = {o["n"]: o for o in rec.get("omission") or []}
    gap = {}
    for n in range(1, pool + 1):
        o = om.get(n) or {}
        avg = float(o.get("avg") or 0) or max(1.0, len(rows) / max(1, o.get("cnt") or 1))
        gap[n] = float(o.get("cur") or 0) / max(1.0, avg)
    zgap = standardize(gap)

    last = set(rec["last"]["nums"])
    co = Counter()
    for d in rows[-CO_WIN:]:
        s = set(d["nums"])
        if s & last:
            co.update(s - last)
    zco = standardize({n: co.get(n, 0) for n in range(1, pool + 1)})

    score, detail = {}, {}
    # 学术分量：Poisson–Gamma 收缩后验 z 与一阶马尔可夫条件 lift z
    M = np.zeros((len(rows), pool), dtype=bool)
    for i, d in enumerate(rows):
        for v in d["nums"]:
            if 1 <= v <= pool:
                M[i, v - 1] = True
    zpost, zmarkov = posterior_terms(M, p)
    for n in range(1, pool + 1):
        s = (0.20 * zlong.get(n, 0) + 0.20 * zrec[n]
             + 0.16 * zgap.get(n, 0) + 0.10 * zco.get(n, 0)
             + 0.16 * float(zpost[n - 1]) + 0.18 * float(zmarkov[n - 1]))
        score[n] = s
        detail[n] = dict(z_long=r(zlong.get(n, 0), 2), z_recent=r(zrec[n], 2),
                         gap_ratio=r(gap[n], 2), z_co=r(zco.get(n, 0), 2),
                         z_post=r(float(zpost[n - 1]), 2), z_markov=r(float(zmarkov[n - 1]), 2))
    return score, detail


def score_special(code, rows, rec):
    sp = rec.get("special")
    if not sp:
        return None
    pool, pick = sp["pool"], sp["pick"]
    zlong = {f["n"]: float(f["z"]) for f in sp["freq"]}
    win = rows[-WIN:]
    W = len(win)
    cnt = Counter()
    for d in win:
        for v in d["sp"]:
            cnt[v] += 1
    p = pick / pool
    exp = W * pick / pool
    sd = math.sqrt(W * pick * p * (1 - p)) or 1e-9
    zrec = {n: (cnt.get(n, 0) - exp) / sd for n in range(1, pool + 1)}

    cur, tot = {}, Counter()
    for n in range(1, pool + 1):
        c = 0
        for d in reversed(rows):
            if n in d["sp"]:
                break
            c += 1
        cur[n] = c
        for d in rows:
            if n in d["sp"]:
                tot[n] += 1
    gap = {n: cur[n] / max(1.0, (len(rows) - tot[n]) / max(1, tot[n] + 1)) for n in range(1, pool + 1)}
    zgap = standardize(gap)

    score = {n: 0.40 * zlong.get(n, 0) + 0.35 * zrec[n] + 0.25 * zgap.get(n, 0)
             for n in range(1, pool + 1)}
    order = sorted(score, key=lambda n: -score[n])
    rec_n = 2 if pick == 1 else 3
    return dict(pool=pool, pick=pick, recommend=[
        dict(n=n, score=r(score[n], 2), z_long=r(zlong.get(n, 0), 2),
             z_recent=r(zrec[n], 2), gap_ratio=r(gap[n], 2)) for n in order[:rec_n]
    ])


# ---------------------------------------------------------------- 组合生成
def check(nums, rule, pool):
    s = sum(nums)
    if not (rule["sum"][0] <= s <= rule["sum"][1]):
        return None
    span = max(nums) - min(nums)
    if not (rule["span"][0] <= span <= rule["span"][1]):
        return None
    odd = sum(1 for n in nums if n % 2)
    if odd not in rule["odd"]:
        return None
    big = sum(1 for n in nums if n > pool / 2)
    if big not in rule["big"]:
        return None
    run = mx = 1
    for a, b in zip(nums, nums[1:]):
        run = run + 1 if b - a == 1 else 1
        mx = max(mx, run)
    if mx > rule["maxrun"]:
        return None
    if rule["zones"]:
        z = len({min(2, (n - 1) * 3 // pool) for n in nums})
        if z < rule["zones"]:
            return None
    return dict(sum=s, span=span, odd=odd, big=big, run=mx)


def gen_tickets(code, rows, rec, score, detail, seed):
    sp = SPEC[code]
    pool = sp["pool"]
    rule = RULES[code]
    k = rule["size"]
    rnd = random.Random("%s-%s" % (code, rec["last"]["issue"]))
    items = list(range(1, pool + 1))
    weights = [math.exp(0.9 * score[n]) for n in items]

    cand = []
    for _ in range(TRIES):
        nums = weighted_sample(rnd, items, weights, k)
        info = check(nums, rule, pool)
        if not info:
            continue
        avg = sum(score[n] for n in nums) / k
        cand.append((avg, nums, info))
        if len(cand) >= 800:
            break

    cand.sort(key=lambda x: -x[0])
    picked, lim = [], max(2, k - 4)
    for avg, nums, info in cand:
        if any(len(set(nums) & set(p[1])) > lim for p in picked):
            continue
        picked.append((avg, nums, info))
        if len(picked) >= 5:
            break

    tickets = []
    for avg, nums, info in picked:
        zr = sum(detail[n]["z_recent"] for n in nums) / k
        gr = sum(detail[n]["gap_ratio"] for n in nums) / k
        if code == "kl8":
            ttype = "选十 · 近期热号" if zr > 0.4 else "选十 · 遗漏回补" if gr > 1.2 else "选十 · 均衡"
        else:
            ttype = "近期热号型" if zr > 0.4 else "遗漏回补型" if gr > 1.2 else "均衡型"
        tickets.append(dict(nums=nums, score=r(avg, 3), type=ttype, **info))

    return tickets, (len(cand) > 0)


def build_special_tickets(tickets, special, pick):
    """把特别号分配到各注"""
    if not special:
        return tickets
    nums = [x["n"] for x in special["recommend"]]
    if pick == 1:
        for i, t in enumerate(tickets):
            t["sp"] = [nums[i % len(nums)]]
    else:
        pairs = [(nums[i], nums[j]) for i in range(len(nums)) for j in range(i + 1, len(nums))]
        for i, t in enumerate(tickets):
            p = pairs[i % len(pairs)]
            t["sp"] = sorted(p)
    return tickets


def build_parlay(code, rec, special):
    sp = SPEC[code]
    pick = sp["pick"]
    core = rec["core"]
    front = [x["n"] for x in core]
    if code == "ssq":
        f7 = sorted(front[:7])
        b2 = [x["n"] for x in special["recommend"]][:2]
        bets = math.comb(7, 6) * len(b2)
        return dict(label="红球 7 码 + 蓝球 2 码 小复式", front=f7, back=sorted(b2),
                    bets=bets, cost=bets * PRICE)
    if code == "dlt":
        f6 = sorted(front[:6])
        b3 = [x["n"] for x in special["recommend"]][:3]
        bets = math.comb(6, 5) * math.comb(len(b3), 2)
        return dict(label="前区 6 码 + 后区 3 码 复式", front=f6, back=sorted(b3),
                    bets=bets, cost=bets * PRICE)
    if code == "qlc":
        f8 = sorted(front[:8])
        bets = math.comb(8, 7)
        return dict(label="基本号 8 码复式", front=f8, back=[], bets=bets, cost=bets * PRICE)
    if code == "kl8":
        f11 = sorted(front[:11])
        bets = math.comb(11, 10)
        return dict(label="选十 11 码复式", front=f11, back=[], bets=bets, cost=bets * PRICE)
    return None


def build_lotto(code, rows, rec, ana):
    score, detail = score_lotto(code, rows, rec)
    special = score_special(code, rows, rec)
    pool, pick = SPEC[code]["pool"], SPEC[code]["pick"]
    order = sorted(range(1, pool + 1), key=lambda n: -score[n])
    core_n = 14 if code == "kl8" else pick + 2
    backup_n = 6 if code == "kl8" else 5

    def pack(n):
        d = detail[n]
        if d["z_recent"] > 0.5:
            tag = "近期热"
        elif d["gap_ratio"] > 1.3:
            tag = "遗漏回补"
        elif d["z_long"] > 0.5:
            tag = "长期偏多"
        else:
            tag = "平稳"
        return dict(n=n, score=r(score[n], 3), tag=tag, **d)

    core = [pack(n) for n in order[:core_n]]
    backup = [pack(n) for n in order[core_n:core_n + backup_n]]
    kill = [pack(n) for n in order[-4:][::-1]]

    tickets, ok = gen_tickets(code, rows, rec, score, detail, rec["last"]["issue"])
    tickets = build_special_tickets(tickets, special, (special or {}).get("pick", 0))

    f = rec["forecast"]
    rule = RULES[code]
    ranges = dict(
        sum80=f.get("sum80"), sum90=f.get("sum90"), span80=f.get("span80"),
        repeat_mode=f.get("repeat_mode"), repeat_mean=f.get("repeat_mean"),
        odd_top=f.get("odd_top"), big_top=f.get("big_top"),
        consec_rate=f.get("consec_rate"),
        zone=f.get("zone"), zone_share=f.get("zone_share"),
        odds=f.get("odds"),
        play="选十（自选 10 个号全部落入开出的 20 个号）" if code == "kl8" else None,
    )

    parlay = build_parlay(code, {**rec, "core": core}, special)

    out = dict(
        code=code, name=rec["name"], pool=pool, pick=pick, digit=False,
        draws=rec["draws"],
        last=rec["last"], ranges=ranges,
        core=core, backup=backup, kill=kill,
        special=special, tickets=tickets, parlay=parlay,
        sample_note="组合在 %d 次抽样中按和历史值 %s~%s、跨度 %s~%s 等历史最频形态筛选"
                    % (TRIES, rule["sum"][0], rule["sum"][1], rule["span"][0], rule["span"][1]),
    )
    out["models"] = lotto_models(code, rows, rec, pool, pick)
    if not ok:
        out["tickets"] = []
        out["ticket_note"] = "约束过严，未生成符合条件的组合，请放宽条件或重新运行"
    return out


# ---------------------------------------------------------------- 数字彩
def digit_series(rows, pos):
    return [d["nums"][pos - 1] for d in rows if len(d["nums"]) >= pos]


def score_digit(code, rows, rec):
    win = rows[-WIN:]
    w = len(win)
    pos_scores, pos_detail = [], []
    for q in rec["positions"]:
        pos = q["pos"]
        k = q["k"]
        zlong = {f["d"]: float(f["z"]) for f in q["freq"]}
        cnt = Counter(digit_series(win, pos))
        exp = w / k
        sd = math.sqrt(w * (1 / k) * (1 - 1 / k)) or 1e-9
        zrec = {d: (cnt.get(d, 0) - exp) / sd for d in range(k)}
        seq = digit_series(rows, pos)
        gap = {}
        for d in range(k):
            cur = 0
            for v in reversed(seq):
                if v == d:
                    break
                cur += 1
            tot = seq.count(d)
            avg = (len(seq) - tot) / max(1, tot + 1)
            gap[d] = cur / max(1.0, avg)
        zgap = standardize(gap)
        Mp = np.zeros((len(seq), k), dtype=bool)
        Mp[np.arange(len(seq)), seq] = True
        zp, zm = posterior_terms(Mp, 1.0 / k)
        score = {d: 0.28 * zlong.get(d, 0) + 0.26 * zrec[d] + 0.20 * zgap.get(d, 0)
                 + 0.14 * float(zp[d]) + 0.12 * float(zm[d]) for d in range(k)}
        order = sorted(score, key=lambda d: -score[d])
        top = [dict(d=d, score=r(score[d], 2), z_long=r(zlong.get(d, 0), 2),
                    z_recent=r(zrec[d], 2), gap_ratio=r(gap[d], 2),
                    z_post=r(float(zp[d]), 2), z_markov=r(float(zm[d]), 2)) for d in order[:3]]
        pos_scores.append({d: score[d] for d in range(k)})
        pos_detail.append(top)
        q["_top"] = order[:3]
    return pos_scores, pos_detail


def gen_digit_tickets(code, rows, rec, pos_scores):
    rnd = random.Random("%s-%s" % (code, rec["last"]["issue"]))
    f = rec["forecast"]
    lo, hi = f["sum80"]
    slo, shi = f["span80"]
    ok_odd = {x["k"] for x in (f.get("odd_top") or [])[:2]}
    ok_big = {x["k"] for x in (f.get("big_top") or [])[:2]}
    positions = rec["positions"]
    cand, seen = [], set()
    for _ in range(TRIES):
        nums = []
        for i, q in enumerate(positions):
            k = q["k"]
            top = q["_top"]
            items = list(range(k))
            weights = [math.exp(0.9 * pos_scores[i][d]) if d in top else math.exp(0.35 * pos_scores[i][d])
                       for d in items]
            nums.append(weighted_sample(rnd, items, weights, 1)[0])
        s, span = sum(nums), max(nums) - min(nums)
        if not (lo <= s <= hi and slo <= span <= shi):
            continue
        odd = sum(1 for v in nums if v % 2)
        big = sum(1 for v in nums if v >= 5)
        if ok_odd and odd not in ok_odd:
            continue
        if ok_big and big not in ok_big:
            continue
        key = tuple(nums)
        if key in seen:
            continue
        seen.add(key)
        avg = sum(pos_scores[i][d] for i, d in enumerate(nums)) / len(nums)
        cand.append((avg, nums, s, span))
        if len(cand) >= 600:
            break
    cand.sort(key=lambda x: -x[0])
    picked = []
    for avg, nums, s, span in cand:
        if any(sum(1 for a, b in zip(nums, p[1]) if a == b) >= len(nums) - 2 for p in picked):
            continue
        picked.append((avg, nums, s, span))
        if len(picked) >= 5:
            break
    tickets = []
    for avg, nums, s, span in picked:
        t = dict(nums=nums, sum=s, span=span,
                 odd=sum(1 for v in nums if v % 2),
                 score=r(avg, 3), type="按位打分组合")
        if code == "3d":
            t["shape"] = "豹子" if len(set(nums)) == 1 else "组三" if len(set(nums)) == 2 else "组六"
        tickets.append(t)
    return tickets


def build_digit(code, rows, rec, ana):
    pos_scores, pos_detail = score_digit(code, rows, rec)
    tickets = gen_digit_tickets(code, rows, rec, pos_scores)
    f = rec["forecast"]
    positions = []
    for q, top in zip(rec["positions"], pos_detail):
        positions.append(dict(pos=q["pos"], k=q["k"], note=q.get("note"),
                              top=top, hist_top=q["top"], hist_share=q["top_share"]))
    ranges = dict(
        sum80=f.get("sum80"), sum90=f.get("sum90"), span80=f.get("span80"),
        repeat_mode=f.get("repeat_mode"), repeat_mean=f.get("repeat_mean"),
        odd_top=f.get("odd_top"), big_top=f.get("big_top"),
        consec_rate=f.get("consec_rate"), odds=f.get("odds"),
    )

    if code == "3d":
        digits = [x["top"][0]["d"] for x in positions]
        f2 = [x["top"][1]["d"] for x in positions]
        bets = 3 ** 3
        parlay = dict(label="直选复式：每位取打分前 3 码", front=[[x["d"] for x in p["top"]] for p in positions],
                      back=[], bets=bets, cost=bets * PRICE)
        core = [dict(pos=p["pos"], d=p["top"][0]["d"], score=p["top"][0]["score"], tag="按位首选")
                for p in positions]
        backup = [dict(pos=p["pos"], d=p["top"][1]["d"], score=p["top"][1]["score"], tag="按位次选")
                  for p in positions]
    else:
        bets = 2 ** 6
        parlay = dict(label="复式：前 6 位各取前 2 码、第 7 位取首位", front=[[x["d"] for x in p["top"][:2]] for p in positions[:6]],
                      back=[[p["top"][0]["d"]] for p in positions[6:]], bets=bets, cost=bets * PRICE)
        core = [dict(pos=p["pos"], d=p["top"][0]["d"], score=p["top"][0]["score"], tag="按位首选")
                for p in positions]
        backup = [dict(pos=p["pos"], d=p["top"][1]["d"], score=p["top"][1]["score"], tag="按位次选")
                  for p in positions]

    out = dict(
        code=code, name=rec["name"], pool=10, pick=len(rec["positions"]), digit=True,
        draws=rec["draws"], last=rec["last"], ranges=ranges,
        core=core, backup=backup, kill=[], special=None,
        positions=positions, tickets=tickets, parlay=parlay,
        sample_note="组合在 %d 次抽样中按和历史区间 %s~%s、跨度 %s~%s 筛选" % (TRIES, f["sum80"][0], f["sum80"][1], f["span80"][0], f["span80"][1]),
    )
    out["models"] = digit_models(code, rows, rec)
    return out


# ---------------------------------------------------------------- 主流程
def main():
    with open(ANALYSIS, encoding="utf-8") as fp:
        ana = json.load(fp)
    amap = {x["code"]: x for x in ana["lotteries"]}
    draws = load_draws()

    out = []
    for code in ORDER:
        rows, rec = draws.get(code), amap.get(code)
        if not rows or not rec:
            continue
        rec = dict(rec)
        rec["last"] = rows[-1]
        rec["draws"] = len(rows)
        if SPEC[code].get("digit"):
            item = build_digit(code, rows, rec, ana)
        else:
            item = build_lotto(code, rows, rec, ana)
        item["window"] = rec.get("window")
        out.append(item)

    for x in out:
        last_date = x["last"]["date"]
        nd = next_draw_date(last_date, x["code"])
        x["next"] = dict(
            issue=next_issue(x["last"]["issue"]),
            date=nd,
            weekday=weekday_cn(date.fromisoformat(nd)) if nd else None,
            basis="按最新期号 +1 与固定开奖周期估算，以官方公告为准",
        )

    payload = dict(
        generated_at=ana.get("generated_at"),
        based_on=dict(draws_csv_rows=sum(len(v) for v in draws.values())),
        method=("乐透型打分：长期频数 z×0.20 + 近 50 期热度 z×0.20 + 遗漏回补×0.16 + 与上期共现 z×0.10"
                " + Poisson–Gamma 收缩后验 z×0.16 + 马尔可夫条件 lift z×0.18；"
                "数字彩按位：0.28/0.26/0.20/0.14/0.12。"
                "组合先按和值/跨度/奇偶/大小/连号/分区等历史最频形态筛选，再按总分排序取 5 注。"
                "扩展模型：EWMA（半衰期 30 期）、经验贝叶斯 Beta-Binomial 后验、一阶马尔可夫转移、"
                "近 50 期二项检验、遗漏几何分布分位、log-odds 概率融合、熵/KL 散度、"
                "蒙特卡洛零分布、Bootstrap 重采样、超几何命中概率；"
                "学术层：Dirichlet–Multinomial（Minka 固定点）、Poisson–Gamma 经验贝叶斯（Efron–Morris 矩估计）、"
                "BH-FDR 多重检验、Wald–Wolfowitz 游程、NIST SP 800-22 频率/串行、Ljung–Box Q、"
                "Anderson–Goodman 马尔可夫 χ²、Anderson–Darling/KS 几何拟合、Kelly 准则、轮转覆盖设计、实测返奖率"),
        note=NOTE,
        lotteries=out,
    )
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, separators=(",", ":"))

    print("写入", OUT, os.path.getsize(OUT), "字节")
    for x in out:
        tk = x["tickets"][0]["nums"] if x["tickets"] else []
        spx = x["tickets"][0].get("sp") if x["tickets"] else None
        print("  %-6s 下期 %-8s %-8s 核心=%s 首注=%s%s" %
              (x["name"], x["next"]["issue"], x["next"]["date"], [c.get("n", c.get("d")) for c in x["core"][:6]],
               tk, ("+" + str(spx)) if spx else ""))


if __name__ == "__main__":
    main()
