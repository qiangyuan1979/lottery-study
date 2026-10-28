# -*- coding: utf-8 -*-
"""
实盘预测台账与逐期盈亏结算
--------------------------------------------------------------
只统计真正发出过的预测，不再把模型套用到历史全部开奖上。

流程
  1. 登记（snapshot）：读 data/predictions.json，把每个彩种的“下期推荐”
     （5 注单式 + 1 组复式）连同目标期号、开奖日期、预测时间写入
     data/predict_log.json。同一期只保留第一次登记，之后重算不覆盖。
  2. 结算（settle）：开奖数据里一旦出现该期号，就用当期真实开奖号码与
     官方公布的单注奖金结算，写入投入 / 奖金 / 净盈亏。
  3. 汇总：按彩种给出从首期预测起的累计台账，输出 data/pnl.json。

结算口径
  · 单式：注数 × 2 元
  · 复式：推荐方案注数 × 2 元（predictions.json 已给出注数与金额）
  · 浮动奖级无人中出时官方单注奖金为 0/空，回退到该奖级最近一次公布值；
    3D 奖级明细缺失，按固定单选 1040 元。
"""
import json
import os
from datetime import datetime

import pandas as pd

from analyze import ORDER, SPEC
from backtest import load_prize_table, prize_of, settle_parlay, settle_single

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
PRED = os.path.join(DATA, "predictions.json")
DRAWS = os.path.join(DATA, "draws.csv")
LOG = os.path.join(DATA, "predict_log.json")
OUT = os.path.join(DATA, "pnl.json")

PRICE = 2

NOTE = ("本页只统计真正发出过的预测：从首期预测开始逐期留档，开奖后按当期真实号码与官方单注奖金结算，"
        "不再把模型回测套用到历史全部开奖。同一期只在第一次预测时登记，后续重算不会覆盖台账。"
        "彩票返奖率约 50%，长期累计必然为负，台账只用于检验推荐的真实表现，不构成投注建议。")

METHOD = ("每日 update_daily 的顺序：抓取开奖 → 重新统计 → 生成下期推荐 → 台账登记与结算。"
          "推荐 = 5 注单式（每注 2 元）+ 1 组复式（按方案注数 × 2 元），"
          "与“预测下期”页展示的号码完全一致。")


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


def _save_json(path, obj, indent=None):
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(obj, fp, ensure_ascii=False, indent=indent,
                  separators=None if indent else (",", ":"))


def _ints(s):
    if s is None:
        return []
    t = str(s).strip()
    if not t or t.lower() == "nan":
        return []
    return [int(v) for v in t.split() if v.lstrip("-").isdigit()]


def load_draw_index():
    """(code, issue) -> {issue, date, nums, sp}，用全量开奖数据结算"""
    df = pd.read_csv(DRAWS, encoding="utf-8-sig", dtype=str)
    idx = {}
    for _, x in df.iterrows():
        idx[(x["lottery_code"], str(x["issue"]))] = dict(
            issue=str(x["issue"]), date=str(x["draw_date"]),
            nums=_ints(x["front_numbers"]), sp=_ints(x["back_numbers"]),
        )
    return idx


# ---------------------------------------------------------------- 登记
def snapshot(pred, log, didx):
    """把 predictions.json 的下期推荐登记进台账；同一期只登记一次"""
    have = {(e["code"], e["issue"]) for e in log["entries"]}
    added = []
    for x in pred.get("lotteries") or []:
        nx = x.get("next") or {}
        issue, code = nx.get("issue"), x.get("code")
        if not issue or code not in SPEC:
            continue
        if (code, issue) in have:
            continue
        if (code, issue) in didx:
            # 该期已开奖，此时登记等于“事后预测”，不计入台账
            print("  跳过 %s %s：该期已开奖，避免事后登记" % (x.get("name"), issue))
            continue
        tickets = [dict(nums=list(t.get("nums") or []), sp=list(t.get("sp") or []))
                   for t in (x.get("tickets") or [])]
        if not tickets:
            print("  跳过 %s %s：未生成推荐组合" % (x.get("name"), issue))
            continue
        par = x.get("parlay")
        par = dict(label=par.get("label"), front=par.get("front") or [],
                   back=par.get("back") or [], bets=par.get("bets") or 0,
                   cost=par.get("cost") or 0) if par else None

        e = dict(
            code=code, name=x.get("name") or SPEC[code]["name"],
            issue=str(issue), date=nx.get("date"),
            predicted_at=pred.get("generated_at"),
            based_on_issue=(x.get("last") or {}).get("issue"),
            tickets=tickets, parlay=par,
            cost_single=len(tickets) * PRICE,
            cost_parlay=(par or {}).get("cost") or 0,
            status="pending",
            draw=None, single=None, parlay_result=None, net=None, settled_at=None,
        )
        log["entries"].append(e)
        have.add((code, str(issue)))
        added.append(e)
    return added


# ---------------------------------------------------------------- 结算
def settle(log, didx, pmap):
    done = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for e in log["entries"]:
        if e.get("status") != "pending":
            continue
        d = didx.get((e["code"], e["issue"]))
        if not d:
            continue

        s_prize, s_hits = 0.0, []
        for t in e["tickets"]:
            lv, amt = settle_single(e["code"], t, d, pmap)
            s_prize += amt
            if lv:
                s_hits.append(dict(level=lv, amount=round(amt, 2)))

        if e.get("parlay"):
            p_prize, p_bets, p_hits = settle_parlay(e["code"], e["parlay"], d, pmap)
            par_hits = [dict(level=k, count=v,
                             amount=round(prize_of(pmap, e["code"], d["issue"], k) * v, 2))
                        for k, v in p_hits.items()]
        else:
            p_prize, p_bets, par_hits = 0.0, 0, []

        e["status"] = "settled"
        e["settled_at"] = now
        e["draw"] = d
        e["single"] = dict(cost=e["cost_single"], prize=round(s_prize, 2), hits=s_hits)
        e["parlay_result"] = dict(cost=e["cost_parlay"], prize=round(p_prize, 2),
                                  bets=p_bets, hits=par_hits)
        e["net"] = round(s_prize + p_prize - e["cost_single"] - e["cost_parlay"], 2)
        done += 1
    return done


# ---------------------------------------------------------------- 汇总
def _levels(rows):
    m = {}
    for r in rows:
        for h in ((r.get("single") or {}).get("hits") or []):
            o = m.setdefault(h["level"], dict(level=h["level"], single=0, parlay=0,
                                              s_amount=0.0, p_amount=0.0))
            o["single"] += 1
            o["s_amount"] += h["amount"]
        for h in ((r.get("parlay_result") or {}).get("hits") or []):
            o = m.setdefault(h["level"], dict(level=h["level"], single=0, parlay=0,
                                              s_amount=0.0, p_amount=0.0))
            o["parlay"] += h["count"]
            o["p_amount"] += h["amount"]
    out = []
    for o in sorted(m.values(), key=lambda x: -(x["s_amount"] + x["p_amount"])):
        o["s_amount"] = round(o["s_amount"], 2)
        o["p_amount"] = round(o["p_amount"], 2)
        o["amount"] = round(o["s_amount"] + o["p_amount"], 2)
        out.append(o)
    return out


def build_payload(log, price):
    lots = []
    for code in ORDER:
        ents = [e for e in log["entries"] if e["code"] == code]
        ents.sort(key=lambda e: (e.get("date") or "", e.get("issue") or ""))
        cum, rows = 0.0, []
        cost = prize = 0.0
        settled = pending = hit_periods = 0
        first_at = None
        for e in ents:
            if e.get("predicted_at") and (first_at is None or e["predicted_at"] < first_at):
                first_at = e["predicted_at"]
            row = dict(issue=e["issue"], date=e.get("date"), predicted_at=e.get("predicted_at"),
                       based_on_issue=e.get("based_on_issue"), status=e["status"],
                       tickets=e["tickets"], parlay=e.get("parlay"),
                       cost_single=e["cost_single"], cost_parlay=e["cost_parlay"],
                       single=e.get("single"), parlay_result=e.get("parlay_result"),
                       draw=e.get("draw"), net=e.get("net"), settled_at=e.get("settled_at"))
            if e["status"] == "settled":
                cum += e["net"]
                cost += e["cost_single"] + e["cost_parlay"]
                prize += (e["single"]["prize"] + e["parlay_result"]["prize"])
                settled += 1
                if e["single"]["hits"] or e["parlay_result"]["hits"]:
                    hit_periods += 1
                row["cost_total"] = round(e["cost_single"] + e["cost_parlay"], 2)
                row["prize_total"] = round(e["single"]["prize"] + e["parlay_result"]["prize"], 2)
            else:
                pending += 1
                row["cost_total"] = round(e["cost_single"] + e["cost_parlay"], 2)
                row["prize_total"] = None
            row["cum_net"] = round(cum, 2)
            rows.append(row)

        if not rows:
            continue
        lots.append(dict(
            code=code, name=SPEC[code]["name"],
            first_at=first_at,
            entries=rows,
            current=rows[-1],
            summary=dict(
                periods=settled, pending=pending,
                cost=round(cost, 2), prize=round(prize, 2),
                net=round(prize - cost, 2),
                roi=round((prize - cost) / cost, 4) if cost else None,
                hit_periods=hit_periods,
                levels=_levels([e for e in log["entries"]
                                if e["code"] == code and e["status"] == "settled"]),
            ),
        ))

    settled_all = sum(l["summary"]["periods"] for l in lots)
    pending_all = sum(l["summary"]["pending"] for l in lots)
    cost_all = sum(l["summary"]["cost"] for l in lots)
    prize_all = sum(l["summary"]["prize"] for l in lots)
    firsts = [l["first_at"] for l in lots if l["first_at"]]

    return dict(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        price=price, note=NOTE, method=METHOD,
        first_at=min(firsts) if firsts else None,
        totals=dict(settled=settled_all, pending=pending_all,
                    cost=round(cost_all, 2), prize=round(prize_all, 2),
                    net=round(prize_all - cost_all, 2),
                    roi=round((prize_all - cost_all) / cost_all, 4) if cost_all else None),
        lotteries=lots,
    )


def main():
    pred = _load_json(PRED, None)
    if not pred:
        print("缺少 data/predictions.json，请先运行 python predict.py")
        return 1
    log = _load_json(LOG, dict(created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
                               entries=[]))
    didx = load_draw_index()
    pmap = load_prize_table()

    added = snapshot(pred, log, didx)
    done = settle(log, didx, pmap)
    _save_json(LOG, log, indent=1)

    payload = build_payload(log, PRICE)
    _save_json(OUT, payload)

    print("新登记 %d 期，结算 %d 期，台账共 %d 期" % (len(added), done, len(log["entries"])))
    print("写入 %s %.1f KB" % (OUT, os.path.getsize(OUT) / 1024))
    for l in payload["lotteries"]:
        c, s = l["current"], l["summary"]
        print("  %-6s 本期 %-8s %-10s %-4s | 已结算 %2d 期 待开奖 %d | 投入 %8.0f 奖金 %8.0f 净 %+9.0f"
              % (l["name"], c["issue"], c.get("date") or "-", c["status"],
                 s["periods"], s["pending"], s["cost"], s["prize"], s["net"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
