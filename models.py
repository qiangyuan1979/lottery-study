# -*- coding: utf-8 -*-
"""
扩展统计 / 概率模型
--------------------------------------------------------------
在 predict.py 的启发式打分之外，叠加一组可解释、可复算的概率模型：

号码级（乐透）
  1. 指数衰减频率 EWMA（半衰期 30 期）      —— 时间加权的近期出现率
  2. 经验贝叶斯 Beta-Binomial 后验（κ=50）  —— 向理论概率收缩，附 95% 可信区间
  3. 一阶马尔可夫转移概率                   —— P(出|上期出) / P(出|上期未出)
  4. 近 50 期二项检验                       —— 是否显著偏热 / 偏冷
  5. 遗漏的几何分布分位                     —— 当前遗漏在历史遗漏中的位置
  6. log-odds 加权融合（Benter 式集成）      —— 校准概率
  7. 号码分布熵 / KL 散度                   —— 随机性度量

组合级
  8. 蒙特卡洛模拟（20,000 次均匀抽样）       —— 和值/跨度/奇偶/大小/重号零分布
  9. Bootstrap 重采样（2,000 次）            —— 和值均值与中位数 95% 置信区间
 10. 超几何分布                             —— 命中 m 个号码的概率

数字彩按位版本：EWMA / 贝叶斯后验 / 马尔可夫转移矩阵 / 熵 + 组合级 MC、Bootstrap。

口径：全部为历史描述与理论零分布对照，不改变各期独立随机、单注概率相等的本质。
"""
import math
import os

import numpy as np
from scipy import special, stats

from analyze import r

HALF_LIFE = 30.0        # EWMA 半衰期（期）
KAPPA = 50.0            # 经验贝叶斯先验强度（等效伪样本数）
MC_SIMS = 20000         # 蒙特卡洛次数
BOOT_ITERS = 2000       # Bootstrap 次数
FUSION_W = dict(hist=0.20, ewma=0.30, bayes=0.25, markov=0.25)
EPS = 1e-6


# ---------------------------------------------------------------- 基础
def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


def _seed(code):
    return sum((i + 1) * ord(c) for i, c in enumerate(code)) * 7919 + 17


def _onehot(seq, k, offset=0):
    """把每期取值序列转成 (N, K) 0/1 矩阵"""
    n = len(seq)
    M = np.zeros((n, k), dtype=bool)
    for i, v in enumerate(seq):
        j = v - offset
        if 0 <= j < k:
            M[i, j] = True
    return M


def _entropy(p):
    p = np.asarray(p, dtype=float)
    p = p / p.sum()
    return float(-np.sum(p * np.log2(np.maximum(p, 1e-12))))


def _percentile_of(arr, v):
    if len(arr) == 0:
        return None
    return float((np.asarray(arr) <= v).mean())


# ---------------------------------------------------------------- 乐透号码级
def _number_models(M, pool, pick):
    """M：(N, pool) 出现矩阵，返回各模型的号码级概率"""
    N = M.shape[0]
    p0 = pick / pool
    counts = M.sum(0).astype(float)

    # 1) 指数衰减频率
    w = 0.5 ** ((N - 1 - np.arange(N)) / HALF_LIFE)
    ewma = (w @ M) / w.sum()

    # 2) 经验贝叶斯 Beta-Binomial 后验
    a = counts + KAPPA * p0
    b = (N - counts) + KAPPA * (1 - p0)
    p_bayes = a / (a + b)
    ci_lo = stats.beta.ppf(0.025, a, b)
    ci_hi = stats.beta.ppf(0.975, a, b)

    # 3) 一阶马尔可夫：给定上期状态的条件出现概率
    prev, cur = M[:-1], M[1:]
    hit_prev = prev.sum(0).astype(float)
    miss_prev = (N - 1) - hit_prev
    p11 = np.where(hit_prev > 0, (prev & cur).sum(0) / np.maximum(hit_prev, 1), p0)
    p01 = np.where(miss_prev > 0, ((~prev) & cur).sum(0) / np.maximum(miss_prev, 1), p0)
    p_markov = np.where(M[-1], p11, p01)

    # 4) 近 50 期二项检验
    Wn = min(50, N)
    recent = M[-Wn:].sum(0).astype(int)
    binom_p = np.array([stats.binomtest(int(k), Wn, p0).pvalue for k in recent])

    # 5) 遗漏与几何分布分位
    cur_omit = np.zeros(pool)
    omit_pct = np.zeros(pool)
    for j in range(pool):
        idx = np.flatnonzero(M[:, j])
        cur_omit[j] = N - 1 - idx[-1] if idx.size else N
        gaps = np.diff(idx) - 1 if idx.size > 1 else np.array([])
        pct = _percentile_of(gaps, cur_omit[j])
        omit_pct[j] = pct if pct is not None else 0.5

    # 6) log-odds 融合 → 归一化到 Σp = pick
    p_hist = counts / N
    fused = _sigmoid(
        FUSION_W["hist"] * _logit(p_hist)
        + FUSION_W["ewma"] * _logit(ewma)
        + FUSION_W["bayes"] * _logit(p_bayes)
        + FUSION_W["markov"] * _logit(p_markov)
    )
    fused = fused / fused.sum() * pick

    # 7) 熵 / KL
    q = counts / max(counts.sum(), 1)
    h = _entropy(q)
    hmax = math.log2(pool)
    kl = float(np.sum(q * np.log2(np.maximum(q, 1e-12) * pool)))

    return dict(
        counts=counts, p_hist=p_hist, ewma=ewma, p_bayes=p_bayes,
        ci_lo=ci_lo, ci_hi=ci_hi, p11=p11, p01=p01, p_markov=p_markov,
        binom_p=binom_p, recent=recent, recent_n=Wn,
        cur_omit=cur_omit, omit_pct=omit_pct, fused=fused,
        entropy=dict(h=r(h, 4), hmax=r(hmax, 4), ratio=r(h / hmax),
                     kl=r(kl, 6), verdict="越接近 1 越接近均匀随机"),
        p0=p0,
    )


# ---------------------------------------------------------------- 组合级
def _montecarlo_lotto(pool, pick, last_mask, seed):
    rng = np.random.default_rng(seed)
    keys = rng.random((MC_SIMS, pool))
    sel = np.argpartition(keys, pick, axis=1)[:, :pick]
    mask = np.zeros((MC_SIMS, pool), dtype=bool)
    np.put_along_axis(mask, sel, True, axis=1)
    nums = sel + 1
    s = nums.sum(1)
    span = nums.max(1) - nums.min(1)
    odd = (nums % 2 == 1).sum(1)
    big = (nums > pool / 2).sum(1)
    rep = (mask & last_mask).sum(1)
    return dict(
        sims=MC_SIMS,
        sum=dict(p5=r(np.percentile(s, 5), 1), p10=r(np.percentile(s, 10), 1),
                 p50=r(np.percentile(s, 50), 1), p90=r(np.percentile(s, 90), 1),
                 p95=r(np.percentile(s, 95), 1), mean=r(s.mean(), 2)),
        span=dict(p5=r(np.percentile(span, 5), 1), p10=r(np.percentile(span, 10), 1),
                  p90=r(np.percentile(span, 90), 1), p95=r(np.percentile(span, 95), 1)),
        odd_mean=r(odd.mean(), 2), big_mean=r(big.mean(), 2),
        repeat_mean=r(rep.mean(), 3),
        note="均匀随机抽样 %d 次得到的理论零分布，代表“完全随机下”应出现的区间" % MC_SIMS,
    )


def _bootstrap_lotto(sums, seed):
    rng = np.random.default_rng(seed + 1)
    n = len(sums)
    means = np.empty(BOOT_ITERS)
    meds = np.empty(BOOT_ITERS)
    for i in range(BOOT_ITERS):
        smp = rng.choice(sums, n, replace=True)
        means[i] = smp.mean()
        meds[i] = np.median(smp)
    return dict(iters=BOOT_ITERS, level=0.95,
                obs_mean=r(float(np.mean(sums)), 2), obs_median=r(float(np.median(sums)), 1),
                mean_ci=[r(np.percentile(means, 2.5), 2), r(np.percentile(means, 97.5), 2)],
                median_ci=[r(np.percentile(meds, 2.5), 1), r(np.percentile(meds, 97.5), 1)],
                note="对 %d 期和值做有放回重采样 %d 次，估计均值的抽样波动" % (n, BOOT_ITERS))


def _hypergeom(pool, pick, ticket):
    total = math.comb(pool, ticket)
    out = []
    for m in range(max(1, ticket - 3), ticket + 1):
        if ticket - m > pool - pick:
            continue
        prob = math.comb(pick, m) * math.comb(pool - pick, ticket - m) / total
        out.append(dict(match=m, prob=prob))
    return out


# ================================================================ 学术模型
# 以下模型取自博彩 / 统计学文献，除用于展示检验结论外，
# Dirichlet–Multinomial 后验与一阶马尔可夫条件概率还会以新分量的形式进入
# predict.py 的启发式打分（见 posterior_terms）。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CACHE = {}

# 头奖参数：单注奖金（元）与中头奖的组合数
TOP_PRIZE = {
    "ssq": dict(prize=5000000, den=math.comb(33, 6) * 16, name="一等奖"),
    "dlt": dict(prize=5000000, den=math.comb(35, 5) * math.comb(12, 2), name="一等奖"),
    "qlc": dict(prize=500000, den=math.comb(30, 7), name="一等奖"),
    "kl8": dict(prize=5000000, den=math.comb(80, 10), name="选十中十"),
    "3d": dict(prize=1040, den=1000, name="单选"),
    "qxc": dict(prize=5000000, den=10 ** 7, name="一等奖"),
}

REFS = [
    "Good, I. J. (1965). The Estimation of Probabilities: An Essay on Modern Bayesian Methods. MIT Press.",
    "Minka, T. P. (2000). Estimating a Dirichlet distribution. Technical report, MIT.",
    "Efron, B. & Morris, C. (1975). Data analysis using Stein's estimator and its generalizations. JASA 70, 311–319.",
    "Benjamini, Y. & Hochberg, Y. (1995). Controlling the false discovery rate. JRSS-B 57, 289–300.",
    "Wald, A. & Wolfowitz, J. (1940). On a test whether two samples are from the same population. Ann. Math. Stat. 11, 147–162.",
    "Ljung, G. M. & Box, G. E. P. (1978). On a measure of lack of fit in time series models. Biometrika 65, 297–303.",
    "Anderson, T. W. & Goodman, L. A. (1957). Statistical inference about Markov chains. Ann. Math. Stat. 28, 89–110.",
    "Anderson, T. W. & Darling, D. A. (1952). Asymptotic theory of certain goodness of fit criteria. Ann. Math. Stat. 23, 193–212.",
    "Kelly, J. L. (1956). A new interpretation of information rate. Bell System Technical Journal 35, 917–926.",
    "Li, P. C. & van Rees, G. H. J. (2002). Lotto design tables. Journal of Combinatorial Designs 10, 137–150.",
    "NIST SP 800-22 Rev.1a (2010). A Statistical Test Suite for Random and Pseudorandom Number Generators.",
]


def _csv(name):
    if name not in _CACHE:
        import pandas as pd
        _CACHE[name] = pd.read_csv(os.path.join(BASE_DIR, "data", name), dtype=str)
    return _CACHE[name]


def _return_rate(code, tail=150):
    """实测返奖率：最近 tail 期 Σ总奖金 / Σ销售额；无奖级明细（如 3D）时返回 None"""
    key = "ret:" + code
    if key in _CACHE:
        return _CACHE[key]
    out = None
    try:
        import pandas as pd
        d = _csv("draws.csv")
        d = d[d["lottery_code"] == code].copy()
        d["sales"] = pd.to_numeric(d["sales_amount"], errors="coerce")
        d = d[d["sales"] > 0].tail(tail * 3)

        p = _csv("prizes.csv")
        p = p[p["lottery_code"] == code].copy()
        p = p[~p["prize_level"].fillna("").str.contains("追加|派奖", regex=True)]
        p["tp"] = pd.to_numeric(p["total_prize"], errors="coerce")
        g = p.groupby("issue")["tp"].sum()

        d["prize"] = d["issue"].map(g)
        d = d[d["prize"].notna() & (d["prize"] > 0)].tail(tail)
        if len(d) >= 20:
            sales, prize = float(d["sales"].sum()), float(d["prize"].sum())
            if sales > 0:
                out = dict(rate=prize / sales, periods=int(len(d)), sales=sales, prize=prize,
                           start=d["issue"].iloc[0], end=d["issue"].iloc[-1])
    except Exception:
        out = None
    _CACHE[key] = out
    return out


# ---------------------------------------------------------------- 1. Dirichlet–Multinomial
def _dirichlet_minka(counts, pool, iters=150):
    """Minka 固定点估计 Dirichlet 集中参数 s，返回 (s, ρ, 后验均值, 有效样本量)

    s 越小 → 各号概率差异越大（过度离散）；s→∞ 退化为均匀多项式。
    ρ = 1/(1+s) 为组内相关系数，n_eff = n/(1+(n−1)ρ) 为有效样本量。
    """
    c = np.asarray(counts, dtype=float)
    tot = float(c.sum())
    m = np.full(pool, 1.0 / pool)
    s = 50.0
    for _ in range(iters):
        a = s * m
        num = float(np.sum(special.psi(c + a) - special.psi(a)))
        den = float(np.sum(special.psi(c + s) - special.psi(s)))
        if den <= 0 or num <= 0:
            break
        s_new = min(max(s * num / den, 0.5), 1e5)
        done = abs(s_new - s) < 1e-4 * max(1.0, s)
        s = s_new
        if done:
            break
    post = (c + s * m) / (tot + s)
    rho = 1.0 / (1.0 + s)
    n_eff = tot / (1.0 + (tot - 1.0) * rho) if tot > 1 else tot
    return s, rho, post, n_eff


# ---------------------------------------------------------------- 2. Poisson–Gamma
def _poisson_gamma(counts, N):
    """Efron–Morris 矩估计的 Gamma 先验，返回收缩强度 B 与后验强度"""
    c = np.asarray(counts, dtype=float)
    pool = c.size
    m = float(c.mean())
    v = float(c.var(ddof=1)) if pool > 1 else m
    if v > m * 1.0001:
        alpha, beta = m * m / (v - m), m / (v - m)
    else:                       # 无过度离散 → 取强先验，几乎全部收缩到均值
        alpha, beta = max(m, 1.0) * 1e3, 1e3
    post = (c + alpha) / (1.0 + beta)
    return dict(alpha=alpha, beta=beta, B=beta / (1.0 + beta), m=m,
                post=post, p=post / max(N, 1))


# ---------------------------------------------------------------- 3. BH-FDR
def _bh_fdr(pvals, alpha=0.05):
    """Benjamini–Hochberg 逐步上升法，返回 q 值与该水平下的拒绝集合"""
    p = np.asarray(pvals, dtype=float)
    m = p.size
    order = np.argsort(p)
    ranked = p[order] * m / np.arange(1, m + 1)
    qs = np.minimum.accumulate(ranked[::-1])[::-1]
    q = np.empty(m)
    q[order] = np.minimum(qs, 1.0)
    return q, q <= alpha


# ---------------------------------------------------------------- 4. 随机性套件
def _runs_test(bits):
    """Wald–Wolfowitz 游程检验（0/1 序列是否独立同分布）"""
    b = np.asarray(bits, dtype=bool)
    n = b.size
    n1 = int(b.sum())
    n0 = n - n1
    if n < 20 or n1 == 0 or n0 == 0:
        return None
    runs = 1 + int(np.sum(b[1:] != b[:-1]))
    mu = 2.0 * n1 * n0 / n + 1.0
    var = 2.0 * n1 * n0 * (2.0 * n1 * n0 - n) / (n * n * (n - 1.0))
    if var <= 0:
        return None
    z = (runs - mu) / math.sqrt(var)
    return dict(n=n, n1=n1, n0=n0, runs=runs, mu=mu, z=z,
                p=float(2 * stats.norm.sf(abs(z))))


def _psi(bits, m):
    if m <= 0:
        return 0.0
    b = np.asarray(bits, dtype=bool)
    n = b.size
    ext = np.concatenate([b, b[:m - 1]]) if m > 1 else b
    w = 1 << np.arange(m - 1, -1, -1)
    blocks = np.lib.stride_tricks.sliding_window_view(ext, m)[:n] @ w
    nu = np.bincount(blocks, minlength=2 ** m).astype(float)
    return (2.0 ** m / n) * float(np.sum(nu ** 2)) - n


def _nist_bits(bits):
    """NIST SP 800-22 的频率（单比特）与串行（m=2,3）检验"""
    b = np.asarray(bits, dtype=bool)
    n = b.size
    if n < 100:
        return None
    s_obs = abs(float((2.0 * b - 1.0).sum())) / math.sqrt(n)
    out = dict(n=n, p_monobit=float(special.erfc(s_obs / math.sqrt(2.0))))
    p1 = _psi(b, 1)
    for m in (2, 3):
        p2 = _psi(b, m)
        p0 = _psi(b, m - 2)
        d2 = max(p2 - 2.0 * p1 + p0, 0.0)
        out["p_serial%d" % m] = float(special.gammaincc(2.0 ** (m - 2), d2 / 2.0))
        p1 = p2
    return out


# ---------------------------------------------------------------- 5. Ljung–Box
def _ljung_box(x, h=10):
    x = np.asarray(x, dtype=float)
    n = x.size
    if n < h + 8:
        return None
    x = x - x.mean()
    den = float(np.sum(x * x)) or 1e-12
    Q = 0.0
    for k in range(1, h + 1):
        rk = float(np.sum(x[k:] * x[:-k])) / den
        Q += rk * rk / (n - k)
    Q *= n * (n + 2)
    return dict(n=n, h=h, Q=Q, p=float(stats.chi2.sf(Q, h)))


# ---------------------------------------------------------------- 6. 马尔可夫独立性检验
def _markov_chi2(M):
    """Anderson–Goodman：号码“出现/未出现”跨期的一阶独立性 χ² 检验（2×2，df=1）"""
    prev, cur = M[:-1], M[1:]
    n11 = float((prev & cur).sum())
    n10 = float((prev & ~cur).sum())
    n01 = float((~prev & cur).sum())
    n00 = float((~prev & ~cur).sum())
    O = np.array([[n11, n10], [n01, n00]])
    tot = float(O.sum())
    if tot <= 0:
        return None
    E = O.sum(1, keepdims=True) @ O.sum(0, keepdims=True) / tot
    ok = E > 0
    chi2 = float((((O - E) ** 2 / np.where(ok, E, 1.0))[ok]).sum())
    return dict(chi2=chi2, df=1, p=float(stats.chi2.sf(chi2, 1)), n=int(tot),
                n11=int(n11), n10=int(n10), n01=int(n01), n00=int(n00))


def _markov_chi2_k(seq, k):
    """数字彩按位：k×k 转移表的一阶独立性检验，df=(k−1)²（剔除空行空列）"""
    s = np.asarray(seq, dtype=int)
    if s.size < 40:
        return None
    T = np.zeros((k, k))
    for i in range(s.size - 1):
        T[s[i], s[i + 1]] += 1
    rs, cs = T.sum(1), T.sum(0)
    keep = (rs > 0) & (cs > 0)
    T2 = T[np.ix_(keep, keep)]
    if T2.shape[0] < 2:
        return None
    E = T2.sum(1, keepdims=True) @ T2.sum(0, keepdims=True) / T2.sum()
    chi2 = float((((T2 - E) ** 2 / np.maximum(E, 1e-9))).sum())
    df = (T2.shape[0] - 1) ** 2
    return dict(chi2=chi2, df=df, p=float(stats.chi2.sf(chi2, df)))


# ---------------------------------------------------------------- 7. 几何分布拟合
def _geom_fit(gaps):
    """遗漏间隔 vs 几何分布的 KS 与 Anderson–Darling 检验（参数由样本均值估计）"""
    g = np.asarray(gaps, dtype=float)
    g = g[g >= 1]
    if g.size < 60:
        return None
    ph = 1.0 / float(g.mean())
    ks = stats.kstest(g, "geom", args=(ph,))
    xs = np.sort(g)
    n = xs.size
    F = np.clip(stats.geom.cdf(xs, ph), 1e-12, 1 - 1e-12)
    A2 = -n - float(np.mean((2 * np.arange(1, n + 1) - 1) * (np.log(F) + np.log(1 - F[::-1]))))
    return dict(n=int(n), phat=ph, mean=float(g.mean()), D=float(ks.statistic),
                p=float(ks.pvalue), A2=A2,
                verdict="与几何分布无显著差异" if ks.pvalue >= 0.05 else "与几何分布有偏离（多重比较下常见）")


def _pooled_gaps(M):
    """把“号码/数字 × 出现位置”矩阵转成汇总遗漏间隔（期数−1）"""
    M = np.asarray(M, dtype=bool)
    if M.ndim != 2:
        return np.array([], dtype=float)
    out = []
    for j in range(M.shape[1]):
        idx = np.flatnonzero(M[:, j])
        if idx.size > 1:
            out.append(np.diff(idx) - 1)
    return np.concatenate(out).astype(float) if out else np.array([], dtype=float)


def _geom_A2(gaps):
    """只算 Anderson–Darling 统计量（参数由样本均值估计），供蒙特卡洛复用"""
    g = np.asarray(gaps, dtype=float)
    g = g[g >= 1]
    if g.size < 60:
        return None
    ph = 1.0 / float(g.mean())
    xs = np.sort(g)
    n = xs.size
    F = np.clip(stats.geom.cdf(xs, ph), 1e-12, 1 - 1e-12)
    return -n - float(np.mean((2 * np.arange(1, n + 1) - 1) * (np.log(F) + np.log(1 - F[::-1]))))


def _mc_geom(a2_obs, gen, B=120, seed=12345):
    """遗漏间隔检验的组合零分布校准

    汇总间隔在“每期固定开 pick 个号 / 每位固定 1 个数字”的约束下并非独立样本，
    渐近 p 值会被结构性依赖放大到 1e-200 量级。这里按真实零分布模拟 B 次，
    用“模拟 A² 不优于观测 A²”的比例给出经验 p 值。
    """
    if a2_obs is None:
        return None
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(B):
        a = _geom_A2(gen(rng))
        if a is not None:
            vals.append(a)
    if len(vals) < 20:
        return None
    arr = np.asarray(vals, dtype=float)
    return dict(p=float((1.0 + np.sum(arr >= a2_obs)) / (len(arr) + 1.0)),
                B=int(len(arr)), med=float(np.median(arr)))


def _gen_lotto_gaps(N, pool, pick):
    """零分布生成器：每期从 pool 个号中均匀不重复抽 pick 个"""
    def gen(rng):
        if N * pool <= 4e6:
            idx = np.argpartition(rng.random((N, pool)), pick - 1, axis=1)[:, :pick]
            M = np.zeros((N, pool), dtype=bool)
            M[np.arange(N)[:, None], idx] = True
        else:
            M = np.zeros((N, pool), dtype=bool)
            for i in range(N):
                M[i, rng.choice(pool, size=pick, replace=False)] = True
        return _pooled_gaps(M)
    return gen


def _gen_digit_gaps(sizes, ks):
    """零分布生成器：各位独立、均匀地取其取值域内的 1 个数字"""
    def gen(rng):
        gap_parts = []
        for N, k in zip(sizes, ks):
            s = rng.integers(0, k, size=N)
            for d in range(k):
                idx = np.flatnonzero(s == d)
                if idx.size > 1:
                    gap_parts.append(np.diff(idx) - 1)
        return np.concatenate(gap_parts).astype(float) if gap_parts else np.array([], dtype=float)
    return gen


# ---------------------------------------------------------------- 8. Kelly / 期望等待
def _kelly(p_win, price, top, ret, draws_in=1000):
    """Kelly 最优投注比例 f*；负期望时 f*=0。附期望等待期与 N 期内至少中一次的概率"""
    b = max(top / price - 1.0, 0.0)
    raw = (b * p_win - (1 - p_win)) / b if b > 0 else -1.0
    return dict(p_win=p_win, mult=b, raw=raw, f_star=max(raw, 0.0),
                ev=(price * ret if ret else None),
                wait=(1.0 / p_win if p_win > 0 else None),
                hit=1.0 - (1.0 - p_win) ** draws_in, draws_in=draws_in)


# ---------------------------------------------------------------- 9. 轮转矩阵保证
def _wheel(pool, drawn, ticket, v):
    """选 v 个号做全轮转：开奖落在所选号内的个数为 j 时，至少有一注命中 min(j, ticket) 个"""
    tot = math.comb(pool, drawn)
    rows, p_ge = [], {3: 0.0, 4: 0.0, 5: 0.0}
    for j in range(max(0, drawn - (pool - v)), min(drawn, v) + 1):
        pr = math.comb(v, j) * math.comb(pool - v, drawn - j) / tot
        best = min(j, ticket)
        rows.append(dict(j=j, p=pr, best=best))
        for g in p_ge:
            if best >= g:
                p_ge[g] += pr
    bets = math.comb(v, ticket)
    return dict(v=v, drawn=drawn, ticket=ticket, bets=bets, cost=bets * 2,
                rows=rows, p_ge=p_ge)


# ---------------------------------------------------------------- 统一文本
def _verdict(p):
    if p is None:
        return "样本不足"
    if p >= 0.05:
        return "不能拒绝“随机”的原假设"
    if p < 0.01:
        return "偏离零分布（在组合约束下较少见）"
    return "p<0.05，多重检验下通常仍属波动"


def _mc_bit_null(rows, pool, pick, obs, B=200, seed=12345):
    """组合零分布下的蒙特卡洛校准

    乐透每期恰好开出 pick 个号（不重复），位流因此不是 iid 伯努利序列，
    NIST / 游程的渐近 p 值会系统性偏小。这里按“每期从 pool 个号中均匀抽
    pick 个”的零分布模拟 B 次，用统计量（即 p 值）的经验分位给出校准 p 值。
    """
    n = len(rows)
    if n < 20 or pick < 2 or pick >= pool:
        return None
    ngood = pool - int(pool // 2)          # v > pool/2 的号码个数
    nbad = pool - ngood
    rng = np.random.default_rng(seed)
    keys = [("runs", "p"), ("monobit", "p_monobit"), ("serial2", "p_serial2"), ("serial3", "p_serial3")]
    sim = {k: [] for k, _ in keys}
    pos = np.arange(pick)[None, :]
    for _ in range(B):
        k = rng.hypergeometric(ngood, nbad, pick, size=n)
        bits = (pos >= (pick - k[:, None])).ravel()
        r = _runs_test(bits)
        ni = _nist_bits(bits)
        if r is None or ni is None:
            return None
        sim["runs"].append(r["p"])
        sim["monobit"].append(ni["p_monobit"])
        sim["serial2"].append(ni["p_serial2"])
        sim["serial3"].append(ni["p_serial3"])

    out = {}
    for key, fld in keys:
        p_obs = (obs.get("runs") or {}).get(fld) if key == "runs" else (obs.get("nist") or {}).get(fld)
        if p_obs is None:
            out[key] = None
            continue
        arr = np.asarray(sim[key], dtype=float)
        out[key] = dict(p=float((1.0 + np.sum(arr <= p_obs)) / (B + 1.0)),
                        p_asym=float(p_obs), B=B)
    return out


def _academic_common(code, rows, M, pool, pick, kpos=None):
    """乐透与数字彩共用的检验套件（游程 / NIST / Ljung–Box / Kelly / 返奖率）

    kpos 为数字彩各位取值域大小（七星彩末位为 0–14），用于按位中位数二值化。
    """
    bits = []
    if kpos:
        for d in rows:
            for i, v in enumerate(d["nums"]):
                if i < len(kpos):
                    bits.append(2 * v >= kpos[i])
    else:
        for d in rows:
            for v in d["nums"]:
                bits.append(v > pool / 2 if pool > 10 else v >= 5)
    bits = np.array(bits, dtype=bool) if bits else np.array([], dtype=bool)

    runs = _runs_test(bits)
    nist = _nist_bits(bits)

    # 乐透：位流受“每期固定 pick 个号”约束，用组合零分布做蒙特卡洛校准
    mcn = (_mc_bit_null(rows, pool, pick, dict(runs=runs, nist=nist), seed=_seed(code))
           if pool > 10 else None)
    if mcn:
        for key, obj, fld in (("runs", runs, "p"), ("monobit", nist, "p_monobit"),
                              ("serial2", nist, "p_serial2"), ("serial3", nist, "p_serial3")):
            if obj is None or mcn.get(key) is None:
                continue
            obj["p_asym"] = obj[fld]
            obj[fld] = mcn[key]["p"]
    sums = np.array([sum(d["nums"]) for d in rows], dtype=float)
    lb = _ljung_box(sums, 10)

    top = TOP_PRIZE.get(code) or dict(prize=5000000, den=math.comb(max(pool, pick), min(pool, pick)), name="头奖")
    ret = _return_rate(code)
    kel = _kelly(1.0 / top["den"], 2.0, top["prize"], (ret or {}).get("rate"))

    tail = "（组合零分布 MC 校准）" if mcn else ""
    tests = []
    if runs:
        tests.append(dict(name="Wald–Wolfowitz 游程检验", scope="大小单双位流" + tail,
                          stat="游程 %d / 期望 %.1f" % (runs["runs"], runs["mu"]),
                          p=runs["p"], verdict=_verdict(runs["p"]),
                          ref="Wald & Wolfowitz (1940)"))
    if nist:
        tests.append(dict(name="NIST 单比特频率检验", scope="位流均衡性" + tail,
                          stat="n=%d" % nist["n"], p=nist["p_monobit"],
                          verdict=_verdict(nist["p_monobit"]), ref="NIST SP 800-22"))
        for m in (2, 3):
            kk = "p_serial%d" % m
            tests.append(dict(name="NIST 串行检验 m=%d" % m, scope="相邻位组合独立性" + tail,
                              stat="—", p=nist[kk], verdict=_verdict(nist[kk]),
                              ref="NIST SP 800-22"))
    if lb:
        tests.append(dict(name="Ljung–Box Q 检验", scope="和值自相关（滞后 %d）" % lb["h"],
                          stat="Q=%.2f" % lb["Q"], p=lb["p"], verdict=_verdict(lb["p"]),
                          ref="Ljung & Box (1978)"))

    return dict(bits_bits=bits, runs=runs, nist=nist, lb=lb, kelly=kel,
                ret=ret, top=top, tests=tests)


def posterity_terms_note():
    return "后验分量 = Dirichlet–Multinomial / Poisson–Gamma 收缩后验的提升倍数；马尔可夫分量 = 条件出现概率提升"


# ---------------------------------------------------------------- 对外：打分分量
def posterior_terms(M, p0):
    """供 predict.py 打分使用的新分量（闭式估计，可快速复算）

    返回 (z_post, z_markov)：
      z_post   —— Poisson–Gamma 经验贝叶斯收缩后验的提升倍数 z 分数
      z_markov —— 一阶马尔可夫条件概率（按上期是否出现）的提升倍数 z 分数
    """
    M = np.asarray(M, dtype=bool)
    N, K = M.shape
    if N < 20 or K < 2:
        return np.zeros(K), np.zeros(K)
    counts = M.sum(0).astype(float)
    pg = _poisson_gamma(counts, N)
    lift = pg["p"] / max(p0, EPS)
    z_post = (lift - lift.mean()) / (lift.std(ddof=1) or 1e-9)

    prev, cur = M[:-1], M[1:]
    hit = prev.sum(0).astype(float)
    miss = (N - 1) - hit
    p11 = np.where(hit > 0, (prev & cur).sum(0) / np.maximum(hit, 1), p0)
    p01 = np.where(miss > 0, ((~prev) & cur).sum(0) / np.maximum(miss, 1), p0)
    pm = np.where(M[-1], p11, p01)
    lift2 = pm / max(p0, EPS)
    z_markov = (lift2 - lift2.mean()) / (lift2.std(ddof=1) or 1e-9)
    return z_post, z_markov


# ---------------------------------------------------------------- 对外：乐透
def _academic_lotto(code, rows, M, nm, pool, pick):
    N = M.shape[0]
    counts = nm["counts"]
    p0 = nm["p0"]
    com = _academic_common(code, rows, M, pool, pick)

    # 1) Dirichlet–Multinomial
    s, rho, post, n_eff = _dirichlet_minka(counts, pool)
    p_dir = post * pick
    dorder = np.argsort(-p_dir)[:8]
    dirichlet = dict(pool=pool, n=int(counts.sum()), s=s, rho=rho, n_eff=n_eff,
                     top=[dict(n=int(j) + 1, p=r(float(p_dir[j]), 4), lift=r(float(p_dir[j] / p0), 3))
                          for j in dorder],
                     note="集中参数 s=%s（越小越偏离均匀）；ρ=%s 为组内相关，有效样本量 %s"
                          % (r(s, 1), r(rho, 3), r(n_eff, 0)))

    # 2) Poisson–Gamma
    pg = _poisson_gamma(counts, N)
    gorder = np.argsort(-pg["post"])[:8]
    poisson = dict(alpha=r(pg["alpha"], 2), beta=r(pg["beta"], 3), B=r(pg["B"], 3),
                   top=[dict(n=int(j) + 1, lam=r(float(pg["post"][j]), 3),
                             p=r(float(pg["p"][j]), 4), lift=r(float(pg["p"][j] / p0), 3))
                        for j in gorder],
                   note="γ 先验矩估计 α=%s、β=%s；收缩强度 B=%s（越接近 1 越向理论值收缩）"
                        % (r(pg["alpha"], 2), r(pg["beta"], 3), r(pg["B"], 3)))

    # 3) BH-FDR
    q, rej = _bh_fdr(nm["binom_p"])
    order = np.argsort(nm["binom_p"])[:8]
    fdr = dict(m=pool, alpha=0.05, raw=int((nm["binom_p"] < 0.05).sum()), q_sig=int(rej.sum()),
               rows=[dict(n=int(j) + 1, p=r(float(nm["binom_p"][j]), 4), q=r(float(q[j]), 4),
                          sig=bool(q[j] <= 0.05)) for j in order],
               note="对 %d 个号码的二项 p 值做 Benjamini–Hochberg 校正；原始 p<0.05 有 %d 个，"
                    "校正后剩 %d 个（原假设下约 5%%×%d≈%.1f 个本就该显著）"
                    % (pool, int((nm["binom_p"] < 0.05).sum()), int(rej.sum()), pool, pool * 0.05))

    # 4) Markov χ²
    mchi = _markov_chi2(M)
    tests = list(com["tests"])
    if mchi:
        tests.append(dict(name="一阶马尔可夫独立性 χ²", scope="号码出现/未出现跨期",
                          stat="χ²=%.2f (df=1)" % mchi["chi2"], p=mchi["p"],
                          verdict=_verdict(mchi["p"]), ref="Anderson & Goodman (1957)"))

    # 5) 几何拟合（渐近 p 值按组合零分布做蒙特卡洛校准）
    geom = _geom_fit(_pooled_gaps(M))
    gmc = None
    if geom:
        gmc = _mc_geom(geom["A2"], _gen_lotto_gaps(N, pool, pick), B=120,
                       seed=_seed(code) + 31)
        if gmc:
            geom["p_asym"] = geom["p"]
            geom["p"] = gmc["p"]
            geom["B"] = gmc["B"]
            geom["verdict"] = _verdict(gmc["p"])
        tests.append(dict(name="遗漏间隔 vs 几何分布",
                          scope="Anderson–Darling（抽样零分布 MC 校准）" if gmc else "Anderson–Darling / KS",
                          stat="A²=%s · D=%s" % (r(geom["A2"], 2), r(geom["D"], 4)),
                          p=geom["p"], verdict=geom["verdict"],
                          ref="Anderson & Darling (1952)"))

    # 6) 轮转矩阵
    v = 12 if code == "kl8" else pick + 2
    wheel = _wheel(pool, pick, 10 if code == "kl8" else pick, v)

    kel = com["kelly"]
    ret = com["ret"]
    ev = dict(rate=(ret or {}).get("rate"), periods=(ret or {}).get("periods"),
              per_ticket=(2.0 * ret["rate"] if ret else None),
              note=("最近 %d 期 Σ奖金 / Σ销售额 = 实测返奖率；单注 2 元的长期期望回报 ≈ %.2f 元"
                    % (ret["periods"], 2.0 * ret["rate"])) if ret
                   else "该彩种奖级明细缺失，无法计算实测返奖率")

    cards = [
        dict(id="dirichlet", name="Dirichlet 后验集中度", value="s=%s · ρ=%s" % (r(s, 1), r(rho, 3)),
             hint="s 越小概率越不均；有效样本量 %s（远小于实际 %d，说明信息增益有限）" % (r(n_eff, 0), int(counts.sum()))),
        dict(id="pg", name="Poisson–Gamma 收缩", value="B=%s" % r(pg["B"], 3),
             hint="经验贝叶斯收缩强度，B→1 表示历史频率几乎全被理论值吸收"),
        dict(id="fdr", name="BH-FDR 校正", value="%d → %d 个" % (fdr["raw"], fdr["q_sig"]),
             hint="原始 p<0.05 的“热号”个数 → 多重检验校正后仍显著的个数"),
        dict(id="runs", name="游程检验 p", value=("%.4f" % com["runs"]["p"]) if com["runs"] else "—",
             hint="大小位流的 Wald–Wolfowitz 检验，p>0.05 表示与独立随机不可区分"),
        dict(id="lb", name="Ljung–Box p（和值）", value=("%.4f" % com["lb"]["p"]) if com["lb"] else "—",
             hint="和值滞后 10 期的自相关检验，p>0.05 表示无显著自相关"),
        dict(id="kelly", name="Kelly 最优比例", value="%.2f%%" % (kel["f_star"] * 100),
             hint="负期望下 Kelly 解为 0；头奖概率 1/%s，期望等待 %s 期" % (format(com["top"]["den"], ","), format(int(kel["wait"]), ","))),
        dict(id="ev", name="实测返奖率", value=("%.1f%%" % (ret["rate"] * 100)) if ret else "—",
             hint=("近 %d 期官方奖金 / 销售额；单注 2 元期望回报 %.2f 元" % (ret["periods"], 2 * ret["rate"])) if ret else ev["note"]),
        dict(id="wheel", name="轮转保证（%d 个号）" % v, value="%d 注 · %d 元" % (wheel["bets"], wheel["cost"]),
             hint="全轮转覆盖 C(%d,%d)；若开奖落在所选号内的个数为 j，必有一注命中 min(j,%d) 个"
                  % (v, wheel["ticket"], wheel["ticket"])),
    ]

    return dict(cards=cards, tests=tests, fdr=fdr, dirichlet=dirichlet, poisson=poisson,
                geom=geom, markov_chi2=mchi, kelly=kel, wheel=wheel, ev=ev,
                ret_rate=(ret or {}).get("rate"), refs=REFS)


# ---------------------------------------------------------------- 对外：数字彩学术版
def _academic_digit(code, rows, rec):
    positions = rec["positions"]
    P = len(positions)
    com = _academic_common(code, rows, np.zeros((len(rows), 10), dtype=bool), 10, P,
                           kpos=[q["k"] for q in positions])

    dir_s, pg_B, mchi_p, seqs = [], [], [], []
    for q in positions:
        p, k = q["pos"], q["k"]
        seq = np.array([d["nums"][p - 1] for d in rows if len(d["nums"]) >= p], dtype=int)
        seqs.append(seq)
        M = np.zeros((seq.size, k), dtype=bool)
        M[np.arange(seq.size), seq] = True
        counts = M.sum(0).astype(float)
        s, rho, post, n_eff = _dirichlet_minka(counts, k)
        dir_s.append(s)
        pg_B.append(_poisson_gamma(counts, seq.size)["B"])
        mc = _markov_chi2_k(seq, k)
        if mc:
            mchi_p.append(mc)

    # BH 校正：对各位的卡方均匀性 p 值
    pv = np.array([q["p"] for q in positions], dtype=float)
    qv, rej = _bh_fdr(pv)
    fdr = dict(m=P, alpha=0.05, raw=int((pv < 0.05).sum()), q_sig=int(rej.sum()),
               rows=[dict(n=q["pos"], p=r(float(pv[i]), 4), q=r(float(qv[i]), 4),
                          sig=bool(qv[i] <= 0.05)) for i, q in enumerate(positions)],
               note="对 %d 个位次的卡方 p 值做 BH 校正；原始 p<0.05 有 %d 个，校正后剩 %d 个"
                    % (P, int((pv < 0.05).sum()), int(rej.sum())))

    tests = list(com["tests"])
    if mchi_p:
        worst = min(mchi_p, key=lambda x: x["p"])
        tests.append(dict(name="一阶马尔可夫独立性 χ²", scope="按位转移表（取最显著位）",
                          stat="χ²=%.1f (df=%d)" % (worst["chi2"], worst["df"]), p=worst["p"],
                          verdict=_verdict(worst["p"]), ref="Anderson & Goodman (1957)"))

    gaps = []
    for seq, q in zip(seqs, positions):
        k = q["k"]
        Mp = np.zeros((seq.size, k), dtype=bool)
        Mp[np.arange(seq.size), seq] = True
        g = _pooled_gaps(Mp)
        if g.size:
            gaps.append(g)
    geom = _geom_fit(np.concatenate(gaps) if gaps else np.array([], dtype=float))
    gmc = None
    if geom:
        gmc = _mc_geom(geom["A2"], _gen_digit_gaps([s.size for s in seqs], [q["k"] for q in positions]),
                       B=120, seed=_seed(code) + 31)
        if gmc:
            geom["p_asym"] = geom["p"]
            geom["p"] = gmc["p"]
            geom["B"] = gmc["B"]
            geom["verdict"] = _verdict(gmc["p"])
        tests.append(dict(name="遗漏间隔 vs 几何分布",
                          scope="全部位次汇总" + ("（抽样零分布 MC 校准）" if gmc else ""),
                          stat="A²=%s · D=%s" % (r(geom["A2"], 2), r(geom["D"], 4)),
                          p=geom["p"], verdict=geom["verdict"], ref="Anderson & Darling (1952)"))

    kel = com["kelly"]
    ret = com["ret"]
    ev = dict(rate=(ret or {}).get("rate"), periods=(ret or {}).get("periods"),
              per_ticket=(2.0 * ret["rate"] if ret else None),
              note=("最近 %d 期 Σ奖金 / Σ销售额 = 实测返奖率" % ret["periods"]) if ret
                   else "该彩种奖级明细缺失，无法计算实测返奖率")

    cards = [
        dict(id="dirichlet", name="Dirichlet 后验集中度", value="s̄=%s" % r(float(np.mean(dir_s)), 1),
             hint="各位位置信集中参数均值；越小表示各位出现越不均"),
        dict(id="pg", name="Poisson–Gamma 收缩", value="B̄=%s" % r(float(np.mean(pg_B)), 3),
             hint="按位经验贝叶斯收缩强度均值，B→1 表示向均匀分布收缩"),
        dict(id="fdr", name="BH-FDR 校正", value="%d → %d 个" % (fdr["raw"], fdr["q_sig"]),
             hint="各位均匀性卡方 p 值经 BH 校正后仍显著的位次数"),
        dict(id="runs", name="游程检验 p", value=("%.4f" % com["runs"]["p"]) if com["runs"] else "—",
             hint="大小位流的 Wald–Wolfowitz 检验"),
        dict(id="lb", name="Ljung–Box p（和值）", value=("%.4f" % com["lb"]["p"]) if com["lb"] else "—",
             hint="和值滞后 10 期的自相关检验"),
        dict(id="kelly", name="Kelly 最优比例", value="%.2f%%" % (kel["f_star"] * 100),
             hint="直选头奖概率 1/%s，负期望下 Kelly 解为 0" % format(com["top"]["den"], ",")),
        dict(id="ev", name="实测返奖率", value=("%.1f%%" % (ret["rate"] * 100)) if ret else "—",
             hint=ev["note"]),
    ]

    return dict(cards=cards, tests=tests, fdr=fdr, dirichlet=None, poisson=None,
                geom=geom, markov_chi2=(mchi_p[0] if mchi_p else None),
                kelly=kel, wheel=None, ev=ev, ret_rate=(ret or {}).get("rate"), refs=REFS)


# ---------------------------------------------------------------- 对外：乐透
def lotto_models(code, rows, rec, pool, pick):
    """返回该彩种的扩展模型结果"""
    seq = [d["nums"] for d in rows]
    M = np.zeros((len(seq), pool), dtype=bool)
    for i, arr in enumerate(seq):
        for v in arr:
            if 1 <= v <= pool:
                M[i, v - 1] = True

    nm = _number_models(M, pool, pick)
    ticket = 10 if code == "kl8" else pick
    sums = np.array([sum(d["nums"]) for d in rows], dtype=float)
    mc = _montecarlo_lotto(pool, pick, M[-1], _seed(code))
    boot = _bootstrap_lotto(sums, _seed(code))
    hyper = _hypergeom(pool, pick, ticket)

    p0 = nm["p0"]
    idx = sorted(range(pool), key=lambda j: -nm["fused"][j])
    keep = idx[:10] + idx[-3:]
    calibration = []
    for j in keep:
        calibration.append(dict(
            n=j + 1,
            p_hist=r(nm["p_hist"][j], 4),
            p_ewma=r(nm["ewma"][j], 4),
            p_bayes=r(nm["p_bayes"][j], 4),
            p_markov=r(nm["p_markov"][j], 4),
            p_fused=r(nm["fused"][j], 4),
            lift=r(nm["fused"][j] / p0, 3),
            ci=[r(nm["ci_lo"][j], 4), r(nm["ci_hi"][j], 4)],
            binom_p=r(nm["binom_p"][j], 4),
            omit=int(nm["cur_omit"][j]),
            omit_pct=r(nm["omit_pct"][j]),
            recent=int(nm["recent"][j]),
        ))

    # 显著偏离的号码（近 50 期双侧二项检验 p<0.05）
    hot = [j + 1 for j in idx if nm["binom_p"][j] < 0.05][:5]
    markov_hit = float(np.mean(nm["p11"]))
    markov_miss = float(np.mean(nm["p01"]))
    match4 = next((x["prob"] for x in hyper if x["match"] == 4), None)

    cards = [
        dict(id="entropy", name="号码分布熵", value="%.3f / %.3f bit" % (nm["entropy"]["h"], nm["entropy"]["hmax"]),
             hint="实测熵占最大熵 %s，越接近 100%% 越接近均匀随机" % ("%.1f%%" % (nm["entropy"]["ratio"] * 100))),
        dict(id="kl", name="KL 散度", value="%.5f bit" % nm["entropy"]["kl"],
             hint="实测分布与均匀分布的信息差，值越小越随机"),
        dict(id="markov", name="一阶马尔可夫", value="出→出 %.1f%% · 漏→出 %.1f%%" % (markov_hit * 100, markov_miss * 100),
             hint="理论边际概率 %.1f%%，两组若接近说明无记忆性" % (p0 * 100)),
        dict(id="bayes", name="贝叶斯后验最高值", value="%.2f%%（理论 %.2f%%）" % (float(nm["p_bayes"].max()) * 100, p0 * 100),
             hint="Beta-Binomial 收缩后验均值最大值；收缩使极端频率回归理论值"),
        dict(id="bootstrap", name="Bootstrap 和值均值", value="[%s, %s]" % tuple(boot["mean_ci"]),
             hint="%d 次重采样的 95%% 置信区间，衡量历史均值的抽样误差" % BOOT_ITERS),
        dict(id="mc", name="蒙特卡洛和值区间", value="%s ~ %s" % (mc["sum"]["p5"], mc["sum"]["p95"]),
             hint="%d 次均匀模拟的 90%% 区间，可与历史 80%% 区间对照" % MC_SIMS),
        dict(id="hyper", name="超几何命中概率", value="中 4 个 %s" % ("%.3f%%" % (match4 * 100) if match4 else "—"),
             hint="固定 %d 个号，命中 m 个的理论概率（m=4 举例）" % ticket),
        dict(id="hot", name="近期显著偏热", value="、".join("%02d" % n for n in hot) if hot else "无",
             hint="近 %d 期双侧二项检验 p<0.05 的号码；样本小，多为随机波动" % nm["recent_n"]),
    ]

    # 特别号（若存在）
    special = None
    sp_conf = rec.get("special")
    if sp_conf:
        spool, spick = sp_conf["pool"], sp_conf["pick"]
        SM = np.zeros((len(rows), spool), dtype=bool)
        for i, d in enumerate(rows):
            for v in d["sp"]:
                if 1 <= v <= spool:
                    SM[i, v - 1] = True
        sm = _number_models(SM, spool, spick)
        order = sorted(range(spool), key=lambda j: -sm["fused"][j])
        special = dict(
            pool=spool, pick=spick,
            entropy=sm["entropy"],
            p11=r(float(np.mean(sm["p11"])), 4),
            p01=r(float(np.mean(sm["p01"])), 4),
            top=[dict(n=j + 1, p_fused=r(sm["fused"][j], 4), lift=r(sm["fused"][j] / sm["p0"], 3),
                      p_bayes=r(sm["p_bayes"][j], 4)) for j in order[:5]],
        )

    return dict(
        calibration=calibration,
        cards=cards,
        fusion=dict(weights=FUSION_W, method="log-odds 加权融合（Benter 式），再归一化使 Σp = %d" % pick,
                    base=r(p0, 4)),
        markov=dict(p11=r(markov_hit, 4), p01=r(markov_miss, 4), base=r(p0, 4),
                    note="无记忆性下两者应等于边际概率 %.4f" % p0),
        entropy=nm["entropy"],
        montecarlo=mc,
        bootstrap=boot,
        hypergeom=hyper,
        special=special,
        academic=_academic_lotto(code, rows, M, nm, pool, pick),
    )


# ---------------------------------------------------------------- 对外：数字彩
def digit_models(code, rows, rec):
    positions = rec["positions"]
    seed = _seed(code)
    rng = np.random.default_rng(seed)
    pos_out, ents, markov_dev = [], [], []
    cols = []
    for q in positions:
        p, k = q["pos"], q["k"]
        seq = np.array([d["nums"][p - 1] for d in rows if len(d["nums"]) >= p], dtype=int)
        n = seq.size
        M = np.zeros((n, k), dtype=bool)
        M[np.arange(n), seq] = True
        counts = M.sum(0).astype(float)

        w = 0.5 ** ((n - 1 - np.arange(n)) / HALF_LIFE)
        ewma = (w @ M) / w.sum()
        a = counts + KAPPA / k
        p_bayes = a / (n + KAPPA)

        trans = np.zeros((k, k))
        for i in range(n - 1):
            trans[seq[i], seq[i + 1]] += 1
        rows_sum = np.maximum(trans.sum(1, keepdims=True), 1)
        ptrans = trans / rows_sum
        last = int(seq[-1])
        p_markov = ptrans[last]

        fused = _sigmoid(FUSION_W["hist"] * _logit(counts / n)
                         + FUSION_W["ewma"] * _logit(ewma)
                         + FUSION_W["bayes"] * _logit(p_bayes)
                         + FUSION_W["markov"] * _logit(p_markov))
        fused = fused / fused.sum()

        h = _entropy(counts)
        ents.append(h / math.log2(k))
        markov_dev.append(float(np.abs(ptrans - 1.0 / k).mean()))

        order = sorted(range(k), key=lambda d: -fused[d])
        pos_out.append(dict(
            pos=p, k=k, last=last,
            entropy=dict(h=r(h, 4), hmax=r(math.log2(k), 4)),
            markov=dict(diag=r(float(np.mean(np.diag(ptrans))), 4), base=r(1.0 / k, 4),
                        dev=r(float(np.abs(ptrans - 1.0 / k).mean()), 4),
                        last_row=[r(x, 4) for x in ptrans[last]]),
            top=[dict(d=d, p_fused=r(fused[d], 4), lift=r(fused[d] * k, 3),
                      p_hist=r(counts[d] / n, 4), p_ewma=r(ewma[d], 4),
                      p_bayes=r(p_bayes[d], 4), p_markov=r(p_markov[d], 4))
                 for d in order[:3]],
        ))
        cols.append(rng.integers(0, k, size=MC_SIMS))

    mat = np.array(cols).T
    s = mat.sum(1)
    span = mat.max(1) - mat.min(1)
    sums = np.array([sum(d["nums"]) for d in rows], dtype=float)
    boot = _bootstrap_lotto(sums, seed)
    comb = 10 ** len(positions)
    if code == "qxc":
        comb = 10 ** 6 * 15

    cards = [
        dict(id="entropy", name="位次平均熵", value="%.1f%%" % (float(np.mean(ents)) * 100),
             hint="各位实测熵 / 最大熵 的平均值，越接近 100% 越接近均匀"),
        dict(id="chi2", name="按位卡方均匀性", value="p=%s" % ("%.4f" % rec["uniformity"]["p"] if rec["uniformity"]["p"] is not None else "—"),
             hint=rec["uniformity"]["verdict"]),
        dict(id="markov", name="位次转移偏离", value="%.4f" % float(np.mean(markov_dev)),
             hint="转移矩阵与均匀分布的绝对偏离，理论随机下趋近 0"),
        dict(id="bootstrap", name="Bootstrap 和值均值", value="[%s, %s]" % tuple(boot["mean_ci"]),
             hint="%d 次重采样的 95%% 置信区间" % BOOT_ITERS),
        dict(id="mc", name="蒙特卡洛和值区间", value="%s ~ %s" % (r(np.percentile(s, 5), 1), r(np.percentile(s, 95), 1)),
             hint="%d 次均匀模拟的 90%% 区间（各期独立）" % MC_SIMS),
        dict(id="odds", name="直选单注概率", value="1/%s" % format(comb, ","),
             hint="所有直选组合概率相同"),
    ]

    return dict(
        cards=cards,
        positions=pos_out,
        fusion=dict(weights=FUSION_W, method="按位 log-odds 加权融合后归一化"),
        markov=dict(dev=r(float(np.mean(markov_dev)), 4),
                    note="转移矩阵各列与 1/K 的平均绝对偏离，越小越接近独立随机"),
        entropy=dict(mean_ratio=r(float(np.mean(ents)), 4),
                     note="各位熵与最大熵之比的平均值"),
        montecarlo=dict(sims=MC_SIMS,
                        sum=dict(p5=r(np.percentile(s, 5), 1), p10=r(np.percentile(s, 10), 1),
                                 p50=r(np.percentile(s, 50), 1), p90=r(np.percentile(s, 90), 1),
                                 p95=r(np.percentile(s, 95), 1), mean=r(float(s.mean()), 2)),
                        span=dict(p5=r(np.percentile(span, 5), 1), p10=r(np.percentile(span, 10), 1),
                                  p90=r(np.percentile(span, 90), 1), p95=r(np.percentile(span, 95), 1)),
                        note="各期各位独立均匀抽样 %d 次" % MC_SIMS),
        bootstrap=boot,
        hypergeom=None,
        academic=_academic_digit(code, rows, rec),
    )
