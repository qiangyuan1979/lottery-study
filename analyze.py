# -*- coding: utf-8 -*-
"""
彩票分布统计建模分析
--------------------------------------------------------------
对 6 个乐透/数字彩彩种做多重统计分析，输出 data/analysis.json 供网站展示。

分析项目
  1. 号码频数 + 卡方均匀性检验（是否为均匀随机）
  2. 和值分布：均值/标准差/中心 80%·90% 区间、直方图、正态拟合
  3. 跨度分布：中心 80% 区间
  4. 形态分布：奇偶比、大小比、连号、与上期重号
  5. 遗漏分析：当前遗漏、平均间隔、最大遗漏
  6. 独立性检验：和值自相关（lag1~5）、游程检验
  7. 号码对共现：高频/低频组合
  8. 数字彩位次分布（福彩3D、七星彩）
结论口径：区间来自历史经验分布，属于“描述性覆盖区间”，
不改变单注中奖概率（各期独立同分布）。
"""
import json
import math
import os
from collections import Counter

import numpy as np
import pandas as pd
from scipy import stats

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(DATA, "analysis.json")

SPEC = {
    "ssq": dict(name="双色球", pool=33, pick=6, special=(16, 1), digit=False),
    "kl8": dict(name="快乐8", pool=80, pick=20, special=None, digit=False),
    "3d": dict(name="福彩3D", pool=10, pick=3, special=None, digit=True, positions=3),
    "qlc": dict(name="七乐彩", pool=30, pick=7, special=(30, 1), digit=False),
    "dlt": dict(name="超级大乐透", pool=35, pick=5, special=(12, 2), digit=False),
    "qxc": dict(name="七星彩", pool=10, pick=7, special=None, digit=True, positions=7),
}
ORDER = ["ssq", "kl8", "3d", "qlc", "dlt", "qxc"]

# 位次规则例外：七星彩自 2020-10-16（第 20101 期）起，第 7 位取值 0-14
POS_RULE = {
    "qxc": {7: dict(start="2020-10-16", support=15,
                    note="2020-10-16 起后区取值 0-14，按新规则区间统计")},
}

# 建模窗口：仅对"规则变更"或"官方存档系统性异常"的彩种截取同质样本。
# draws.csv 与网站开奖页仍保留全量数据，截取只影响统计建模。
WINDOW = {
    "dlt": dict(start="2014-01-01",
                reason="官方存档 2007–2013 前区号码分布显著偏斜（χ²≈234，df=34，"
                       "小号 16 仅出现 95 次、大号 33 出现 224 次，理论各 145 次），"
                       "2014 年起与均匀分布吻合（χ²≈34，df=34），疑为早期存档口径不同"),
    "qxc": dict(start="2020-10-16",
                reason="2020-10-16 起第 7 位取值域由 0-9 扩为 0-14，规则前后不可混池"),
}


def window_of(code):
    w = WINDOW.get(code)
    if not w:
        return None
    return dict(start=w["start"], reason=w["reason"])



def r(x, n=4):
    if x is None:
        return None
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        if not math.isfinite(float(x)):
            return None
        return round(float(x), n)
    return x


def pct(a, q):
    return float(np.percentile(a, q))


def runs_test(x, med):
    """游程检验：序列在自身中位数上下方的游程个数是否随机"""
    s = ["+" if v > med else "-" for v in x]
    n1 = s.count("+")
    n2 = s.count("-")
    if n1 < 2 or n2 < 2:
        return None
    runs = 1
    for i in range(1, len(s)):
        if s[i] != s[i - 1]:
            runs += 1
    n = n1 + n2
    mu = 2 * n1 * n2 / n + 1
    var = 2 * n1 * n2 * (2 * n1 * n2 - n) / (n * n * (n - 1))
    if var <= 0:
        return None
    z = (runs - mu) / math.sqrt(var)
    return dict(runs=runs, expect=r(mu, 2), z=r(z, 3), p=r(2 * stats.norm.sf(abs(z))), verdict=verdict_p(2 * stats.norm.sf(abs(z))))


def verdict_p(p):
    if p is None:
        return "样本不足"
    if p < 0.01:
        return "极显著偏离随机（p<0.01）"
    if p < 0.05:
        return "显著偏离随机（p<0.05）"
    return "与随机无显著差异（p≥0.05）"


def load_draws():
    df = pd.read_csv(os.path.join(DATA, "draws.csv"), dtype=str)
    df = df.sort_values(["lottery_code", "draw_date"])
    out = {}
    for code, g in df.groupby("lottery_code"):
        win = WINDOW.get(code)
        rows = []
        for _, x in g.iterrows():
            if win and str(x["draw_date"]) < win["start"]:
                continue
            nums = [int(v) for v in str(x["front_numbers"]).split() if v.strip().lstrip("-").isdigit()]
            bks = []
            if isinstance(x["back_numbers"], str) and x["back_numbers"].strip():
                bks = [int(v) for v in x["back_numbers"].split() if v.strip().isdigit()]
            rows.append(dict(issue=x["issue"], date=x["draw_date"], nums=nums, sp=bks))
        out[code] = rows
    return out


def analyze_lotto(code, rows):
    sp = SPEC[code]
    pool, pick = sp["pool"], sp["pick"]
    N = len(rows)
    exp = N * pick / pool

    # ---------- 1. 频数 + 卡方 ----------
    obs = np.zeros(pool + 1)
    for d in rows:
        for n in d["nums"]:
            if 1 <= n <= pool:
                obs[n] += 1
    o = obs[1:]
    chi2 = float(((o - exp) ** 2 / exp).sum())
    p_uniform = float(stats.chi2.sf(chi2, pool - 1))
    zp = math.sqrt(exp * (1 - pick / pool))
    freq = [dict(n=i + 1, cnt=int(o[i]), exp=r(exp, 1), z=r((o[i] - exp) / zp, 2)) for i in range(pool)]
    freq_sorted = sorted(freq, key=lambda d: (-d["z"], d["n"]))
    hot = freq_sorted[:8]
    cold = sorted(freq, key=lambda d: (d["z"], d["n"]))[:8]

    # ---------- 2. 和值 ----------
    sums = np.array([sum(d["nums"]) for d in rows], dtype=float)
    mu, sd = float(sums.mean()), float(sums.std(ddof=1))
    bins = 40
    cnts, edges = np.histogram(sums, bins=bins)
    hist = [dict(x0=r(edges[i], 1), x1=r(edges[i + 1], 1), c=int(cnts[i])) for i in range(len(cnts))]
    ks = stats.kstest((sums - mu) / sd, "norm")
    sum_block = dict(
        mean=r(mu, 2), std=r(sd, 2), min=int(sums.min()), max=int(sums.max()),
        int80=[r(pct(sums, 10), 1), r(pct(sums, 90), 1)],
        int90=[r(pct(sums, 5), 1), r(pct(sums, 95), 1)],
        median=r(pct(sums, 50), 1),
        skew=r(stats.skew(sums), 3), kurt=r(stats.kurtosis(sums), 3),
        ks_p=r(ks.pvalue), hist=hist,
    )

    # ---------- 3. 跨度 ----------
    spans = np.array([max(d["nums"]) - min(d["nums"]) for d in rows], dtype=float)
    span_block = dict(
        mean=r(spans.mean(), 2), std=r(spans.std(ddof=1), 2),
        int80=[r(pct(spans, 10), 1), r(pct(spans, 90), 1)],
        int90=[r(pct(spans, 5), 1), r(pct(spans, 95), 1)],
        hist=[dict(x0=i, x1=i + 1, c=int((np.floor(spans) == i).sum())) for i in range(int(spans.max()) + 1)],
    )

    # ---------- 4. 形态 ----------
    odd = np.array([sum(1 for n in d["nums"] if n % 2) for d in rows])
    big = np.array([sum(1 for n in d["nums"] if n > pool / 2) for d in rows])
    consec = np.array([sum(1 for a, b in zip(sorted(d["nums"]), sorted(d["nums"])[1:]) if b - a == 1) for d in rows])
    rep = np.array([len(set(d["nums"]) & set(rows[i - 1]["nums"])) for i, d in enumerate(rows)][1:], dtype=float)

    def dist_block(arr, total):
        c = Counter(int(v) for v in arr)
        items = sorted(c.items())
        return dict(
            dist=[dict(k=k, c=int(v), share=r(v / total)) for k, v in items],
            mode=int(max(c.items(), key=lambda kv: kv[1])[0]),
            top=[dict(k=int(k), share=r(v / total)) for k, v in c.most_common(3)],
        )

    odd_b = dist_block(odd, N)
    big_b = dist_block(big, N)
    consec_b = dict(
        rate=r(float((consec > 0).mean())),
        dist=[dict(k=int(k), c=int(v), share=r(v / N)) for k, v in sorted(Counter(int(v) for v in consec).items())],
    )
    rep_b = dict(
        mean=r(float(rep.mean()), 3),
        expect=r(pick * pick / pool, 3),
        dist=[dict(k=int(k), c=int(v), share=r(v / len(rep))) for k, v in sorted(Counter(int(v) for v in rep).items())],
        mode=int(Counter(int(v) for v in rep).most_common(1)[0][0]),
    )

    # ---------- 5. 遗漏 ----------
    last_seen = {n: -1 for n in range(1, pool + 1)}
    gaps = {n: [] for n in range(1, pool + 1)}
    for i, d in enumerate(rows):
        for n in d["nums"]:
            if last_seen[n] >= 0:
                gaps[n].append(i - last_seen[n] - 1)
            last_seen[n] = i
    omit = []
    for n in range(1, pool + 1):
        cur = N - 1 - last_seen[n]
        gl = gaps[n][1:] if len(gaps[n]) > 1 else gaps[n]
        omit.append(dict(n=n, cur=int(cur), avg=r(float(np.mean(gl)) if gl else None, 2),
                         max=int(max(gl)) if gl else 0,
                         cnt=int(obs[n])))
    omit_top = sorted(omit, key=lambda d: -d["cur"])[:12]

    # ---------- 6. 独立性 ----------
    ac = []
    for lag in range(1, 6):
        if N - lag > 10:
            rr = float(np.corrcoef(sums[:-lag], sums[lag:])[0, 1])
            ac.append(dict(lag=lag, r=r(rr, 4),
                           p=r(2 * stats.norm.sf(abs(rr) * math.sqrt(N - lag)))))
    runs = runs_test(sums, float(np.median(sums)))

    # ---------- 7. 号码对 ----------
    pairs = Counter()
    for d in rows:
        s = sorted(d["nums"])
        for i in range(len(s)):
            for j in range(i + 1, len(s)):
                pairs[(s[i], s[j])] += 1
    pair_exp = N * (pick * (pick - 1) / 2) / (pool * (pool - 1) / 2)
    top_pairs = [dict(a=a, b=b, c=int(c), exp=r(pair_exp, 1)) for (a, b), c in pairs.most_common(10)]
    n_possible = pool * (pool - 1) // 2
    zero_pairs = n_possible - len(pairs)
    low_pairs = [dict(a=a, b=b, c=int(c)) for (a, b), c in sorted(pairs.items(), key=lambda kv: kv[1])[:10]]

    # ---------- 8. 特别号 ----------
    special = None
    if sp["special"]:
        spool, spick = sp["special"]
        sobs = np.zeros(spool + 1)
        for d in rows:
            for n in d["sp"]:
                if 1 <= n <= spool:
                    sobs[n] += 1
        sexp = N * spick / spool
        schi2 = float(((sobs[1:] - sexp) ** 2 / sexp).sum())
        sfreq = [dict(n=i + 1, cnt=int(sobs[i + 1]), z=r((sobs[i + 1] - sexp) / math.sqrt(sexp * (1 - spick / spool)), 2)) for i in range(spool)]
        special = dict(pool=spool, pick=spick, chi2=r(schi2), p=r(stats.chi2.sf(schi2, spool - 1)),
                       verdict=verdict_p(float(stats.chi2.sf(schi2, spool - 1))),
                       freq=sfreq,
                       hot=sorted(sfreq, key=lambda d: -d["z"])[:5],
                       cold=sorted(sfreq, key=lambda d: d["z"])[:5])

    # ---------- 9. 分区热度 ----------
    zones = []
    zsize = pool / 3
    for z in range(3):
        lo, hi = int(round(z * zsize)) + 1, int(round((z + 1) * zsize))
        c = sum(f["cnt"] for f in freq if lo <= f["n"] <= hi)
        zones.append(dict(zone="%02d-%02d" % (lo, hi), lo=lo, hi=hi,
                          share=r(c / (N * pick)), exp=r(1 / 3)))
    best_zone = max(zones, key=lambda d: d["share"])

    comb = math.comb(pool, pick)
    if code == "kl8":
        # 快乐8 最高奖为“选十中十”：自选 10 个号全部落入开出的 20 个号
        comb = math.comb(pool, 10) // math.comb(pick, 10)
    elif sp["special"]:
        comb *= math.comb(sp["special"][0], sp["special"][1])

    return dict(
        code=code, name=sp["name"], draws=N, pool=pool, pick=pick,
        date_from=rows[0]["date"], date_to=rows[-1]["date"],
        last=dict(issue=rows[-1]["issue"], date=rows[-1]["date"],
                  nums=rows[-1]["nums"], sp=rows[-1]["sp"]),
        uniformity=dict(chi2=r(chi2), df=pool - 1, p=r(p_uniform), verdict=verdict_p(p_uniform),
                        expect=r(exp, 1)),
        freq=freq, hot=hot, cold=cold,
        sum=sum_block, span=span_block,
        odd=odd_b, big=big_b, consec=consec_b, repeat=rep_b,
        omission=omit_top,
        independence=dict(autocorr=ac, runs=runs),
        pairs=dict(top=top_pairs, low=low_pairs, zero=zero_pairs, possible=n_possible, expect=r(pair_exp, 1)),
        special=special,
        zones=zones, best_zone=best_zone,
        forecast=dict(
            sum80=sum_block["int80"], sum90=sum_block["int90"],
            span80=span_block["int80"],
            repeat_mode=rep_b["mode"], repeat_mean=rep_b["mean"], repeat_expect=rep_b["expect"],
            odd_top=odd_b["top"], big_top=big_b["top"],
            consec_rate=consec_b["rate"],
            zone=best_zone["zone"], zone_share=best_zone["share"],
            odd_share=r(float(np.mean(odd)) / pick),
            combinations=comb,
            odds="1/%s" % format(comb, ","),
        ),
    )


def analyze_digit(code, rows):
    """数字型彩票：福彩3D / 七星彩（按位分析）"""
    sp = SPEC[code]
    P = sp["positions"]
    N = len(rows)
    sums = np.array([sum(d["nums"]) for d in rows], dtype=float)
    mu, sd = float(sums.mean()), float(sums.std(ddof=1))
    bins = 40
    cnts, edges = np.histogram(sums, bins=bins)
    hist = [dict(x0=r(edges[i], 1), x1=r(edges[i + 1], 1), c=int(cnts[i])) for i in range(len(cnts))]

    spans = np.array([max(d["nums"]) - min(d["nums"]) for d in rows], dtype=float)
    scnt, sedges = np.histogram(spans, bins=int(spans.max()) + 1, range=(0, spans.max() + 1))
    span_hist = [dict(x0=r(sedges[i], 1), x1=r(sedges[i + 1], 1), c=int(scnt[i])) for i in range(len(scnt))]

    # 按位频数 + 卡方（支持不同位数区间：七星彩 2020-10-16 起后区为 0-14）
    rules = POS_RULE.get(code, {})
    pos = []
    for p in range(P):
        rule = rules.get(p + 1)
        if rule:
            sub = [d for d in rows if d["date"] >= rule["start"]]
            K = rule["support"]
        else:
            sub = rows
            K = 10
        c = np.zeros(K)
        for d in sub:
            v = d["nums"][p]
            if 0 <= v < K:
                c[v] += 1
        n = float(c.sum())
        e = n / K
        chi2 = float(((c - e) ** 2 / e).sum())
        pv = float(stats.chi2.sf(chi2, K - 1))
        pos.append(dict(pos=p + 1, n=int(n), k=K, chi2=r(chi2), p=r(pv), verdict=verdict_p(pv),
                        freq=[dict(d=i, cnt=int(c[i]), z=r((c[i] - e) / math.sqrt(e * (1 - 1 / K)), 2))
                              for i in range(K)],
                        top=int(c.argmax()), top_share=r(float(c.max()) / n),
                        bottom=int(c.argmin()), note=rule["note"] if rule else None))

    # 全位合计频数（仅统计 0-9 的位次，后区 0-14 单独呈现）
    tot = np.zeros(10)
    n_tot = 0
    for q in pos:
        if q["k"] != 10:
            continue
        sub = [d for d in rows if d["date"] >= rules[q["pos"]]["start"]] if q["pos"] in rules else rows
        for d in sub:
            tot[d["nums"][q["pos"] - 1]] += 1
            n_tot += 1
    e_tot = n_tot / 10
    # 检验统计量取各 0-9 位次卡方之和（避免按位残差相抵），df 同量累加
    main_pos = [q for q in pos if q["k"] == 10]
    chi2_tot = float(sum(q["chi2"] for q in main_pos))
    df_tot = 9 * len(main_pos)
    p_tot = float(stats.chi2.sf(chi2_tot, df_tot))

    odd = np.array([sum(1 for v in d["nums"] if v % 2) for d in rows])
    big = np.array([sum(1 for v in d["nums"] if v >= 5) for d in rows])
    rep = np.array([sum(1 for a, b in zip(d["nums"], rows[i - 1]["nums"]) if a == b)
                    for i, d in enumerate(rows)][1:], dtype=float)

    def dist_block(arr, total):
        c = Counter(int(v) for v in arr)
        return dict(dist=[dict(k=k, c=int(v), share=r(v / total)) for k, v in sorted(c.items())],
                    mode=int(max(c.items(), key=lambda kv: kv[1])[0]),
                    top=[dict(k=int(k), share=r(v / total)) for k, v in c.most_common(3)])

    odd_b = dist_block(odd, N)
    big_b = dist_block(big, N)
    rep_b = dict(mean=r(float(rep.mean()), 3), expect=r(P / 10, 3), mode=int(Counter(int(v) for v in rep).most_common(1)[0][0]),
                 dist=[dict(k=int(k), c=int(v), share=r(v / len(rep))) for k, v in sorted(Counter(int(v) for v in rep).items())])

    # 形态（仅 3 位数字彩：组三/组六/豹子；7 位彩形态无固定含义，不输出）
    shapes = None
    if P == 3:
        shape = Counter()
        for d in rows:
            u = len(set(d["nums"]))
            shape[3 if u == 1 else (2 if u == 2 else 1)] += 1
        shape_name = {3: "豹子(三同)", 2: "组三(两同)", 1: "组六(全异)"}
        shapes = [dict(k=shape_name[k], c=shape[k], share=r(shape[k] / N)) for k in (3, 2, 1) if k in shape]

    omit = []
    for v in range(10):
        idx = [i for i, d in enumerate(rows) if v in d["nums"]]
        cur = N - 1 - idx[-1] if idx else N
        gl = [b - a - 1 for a, b in zip(idx, idx[1:])]
        omit.append(dict(n=v, cur=int(cur), avg=r(float(np.mean(gl)) if gl else None, 2),
                         max=int(max(gl)) if gl else 0, cnt=int(tot[v])))
    omit_top = sorted(omit, key=lambda d: -d["cur"])[:10]

    ac = []
    for lag in range(1, 6):
        if N - lag > 10:
            rr = float(np.corrcoef(sums[:-lag], sums[lag:])[0, 1])
            ac.append(dict(lag=lag, r=r(rr, 4),
                           p=r(2 * stats.norm.sf(abs(rr) * math.sqrt(N - lag)))))
    runs = runs_test(sums, float(np.median(sums)))

    return dict(
        code=code, name=sp["name"], draws=N, pool=10, pick=P, digit=True,
        date_from=rows[0]["date"], date_to=rows[-1]["date"],
        last=dict(issue=rows[-1]["issue"], date=rows[-1]["date"], nums=rows[-1]["nums"], sp=[]),
        uniformity=dict(chi2=r(chi2_tot), df=df_tot, p=r(p_tot), verdict=verdict_p(p_tot), expect=r(e_tot, 1)),
        positions=pos, digit_freq=[dict(d=i, cnt=int(tot[i]), z=r((tot[i] - e_tot) / math.sqrt(e_tot * 0.9), 2)) for i in range(10)],
        sum=dict(mean=r(mu, 2), std=r(sd, 2), min=int(sums.min()), max=int(sums.max()),
                 int80=[r(pct(sums, 10), 1), r(pct(sums, 90), 1)],
                 int90=[r(pct(sums, 5), 1), r(pct(sums, 95), 1)],
                 median=r(pct(sums, 50), 1), hist=hist,
                 ks_p=r(stats.kstest((sums - mu) / sd, "norm").pvalue)),
        span=dict(mean=r(float(spans.mean()), 2), std=r(float(spans.std(ddof=1)), 2),
                  int80=[r(pct(spans, 10), 1), r(pct(spans, 90), 1)],
                  int90=[r(pct(spans, 5), 1), r(pct(spans, 95), 1)], hist=span_hist),
        odd=odd_b, big=big_b, consec=None, repeat=rep_b,
        shapes=shapes, omission=omit_top,
        independence=dict(autocorr=ac, runs=runs),
        pairs=None, special=None, zones=None, best_zone=None,
        forecast=dict(sum80=[r(pct(sums, 10), 1), r(pct(sums, 90), 1)],
                      sum90=[r(pct(sums, 5), 1), r(pct(sums, 95), 1)],
                      span80=[r(pct(spans, 10), 1), r(pct(spans, 90), 1)],
                      repeat_mode=rep_b["mode"], repeat_mean=rep_b["mean"],
                      repeat_expect=rep_b["expect"], odd_top=odd_b["top"], big_top=big_b["top"],
                      consec_rate=None, zone=None, zone_share=None,
                      odd_share=r(float(np.mean(odd)) / P),
                      positions=[dict(pos=q["pos"], top=q["top"], share=q["top_share"], k=q["k"],
                                      hi=q["freq"][q["top"]]["d"]) for q in pos],
                      combinations=10 ** P, odds="1/%s" % format(10 ** P, ",")),
    )


def main():
    draws = load_draws()
    res = []
    for code in ORDER:
        if code not in draws:
            continue
        rows = draws[code]
        rows.sort(key=lambda d: (d["date"], d["issue"]))
        if SPEC[code]["digit"]:
            res.append(analyze_digit(code, rows))
        else:
            res.append(analyze_lotto(code, rows))
        res[-1]["window"] = window_of(code)

    payload = dict(
        generated_at=pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        note="区间由历史经验分布分位数给出，仅描述覆盖比例，不改变各期独立随机的中奖概率。",
        lotteries=res,
    )
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("写入", OUT, os.path.getsize(OUT), "字节")
    for x in res:
        u = x["uniformity"]
        print("  %-6s 期数=%-5d 卡方p=%.4f %s | 和值80%%=%s | 头奖概率 %s"
              % (x["name"], x["draws"], u["p"], u["verdict"], x["forecast"]["sum80"], x["forecast"]["odds"]))


if __name__ == "__main__":
    main()
