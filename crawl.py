# -*- coding: utf-8 -*-
"""
中国福利彩票 / 中国体育彩票 全量开奖与中奖数据爬虫

数据源：
  福彩（www.cwl.gov.cn）   双色球 ssq / 快乐8 kl8 / 福彩3D 3d / 七乐彩 qlc
  体彩（www.lottery.gov.cn）超级大乐透 85 / 七星彩 04
  竞彩（www.lottery.gov.cn）足球 / 篮球 历史赛果与赔率

输出：
  data/raw/*.jsonl          原始 JSON（逐条）
  data/draws.csv            开奖主表
  data/prizes.csv           奖级中奖表（奖级 / 中奖注数 / 单注奖金 / 奖金合计）
  data/regions.csv          一等奖地域分布（官方仅福彩双色球、快乐8公布）
  data/jingcai_football.csv 竞彩足球赛果
  data/jingcai_basketball.csv 竞彩篮球赛果
"""
import argparse
import csv
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE, "data", "raw")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

CWL_API = "https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice"
TICAI_API = "https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry"
JC_FB = "https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry"
JC_BB = "https://webapi.sporttery.cn/gateway/uniform/basketball/getUniformMatchResultV1.qry"

FUCAI_GAMES = {"ssq": "双色球", "kl8": "快乐8", "3d": "福彩3D", "qlc": "七乐彩"}
TICAI_GAMES = {"85": "超级大乐透", "04": "七星彩"}

PAUSE = 0.25          # 请求间隔（秒）
RETRY = 4             # 单请求最大重试次数
TIMEOUT = 30


def log(*a):
    print(*a, flush=True)


class Client:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/javascript, */*; q=0.01",
        })

    def get_json(self, url, params, referer):
        last = None
        for i in range(RETRY):
            try:
                r = self.s.get(url, params=params, timeout=TIMEOUT,
                               headers={"Referer": referer,
                                        "X-Requested-With": "XMLHttpRequest"})
                if r.status_code == 200 and r.text.strip():
                    return r.json()
                last = f"HTTP {r.status_code} len={len(r.text)}"
            except Exception as e:                      # noqa: BLE001
                last = repr(e)
            time.sleep(0.8 * (2 ** i))
        raise RuntimeError(f"请求失败 url={url} params={params} 末次错误={last}")


# ---------------------------------------------------------------- 福彩

def crawl_fucai(cli, name):
    """返回该彩种全部历史开奖记录（按官方接口分页）。"""
    rows, page, pages = [], 1, None
    while True:
        params = {
            "name": name, "issueCount": "", "issueStart": "", "issueEnd": "",
            "dayStart": "", "dayEnd": "", "pageNo": page, "pageSize": 200,
            "week": "", "systemType": "PC",
        }
        j = cli.get_json(CWL_API, params, "https://www.cwl.gov.cn/")
        if pages is None:
            total = int(j.get("total") or 0)
            pages = max(1, math.ceil(total / 200))
            log(f"  [福彩 {name}] 总期数={total} 页数={pages}")
        got = j.get("result") or []
        rows.extend(got)
        if page >= pages or not got:
            break
        page += 1
        time.sleep(PAUSE)
    return rows


# ---------------------------------------------------------------- 体彩乐透

def crawl_ticai(cli, game_no):
    rows, page, pages = [], 1, None
    while True:
        params = {"gameNo": game_no, "provinceId": 0, "pageSize": 100,
                  "isVerify": 1, "pageNo": page}
        j = cli.get_json(TICAI_API, params, "https://www.lottery.gov.cn/")
        v = j.get("value") or {}
        if pages is None:
            total = int(v.get("total") or 0)
            pages = max(1, math.ceil(total / 100))
            log(f"  [体彩 {TICAI_GAMES.get(game_no, game_no)}] 总期数={total} 页数={pages}")
        got = v.get("list") or []
        rows.extend(got)
        if page >= pages or not got:
            break
        page += 1
        time.sleep(PAUSE)
    return rows


# ---------------------------------------------------------------- 竞彩

def _jc_window(cli, url, start, end):
    """抓取一个时间窗口内的全部赛果（分页并发）；窗口过大时二分。"""
    params = {"matchBeginDate": start, "matchEndDate": end, "leagueId": "",
              "pageSize": 100, "pageNo": 1, "isFix": 0, "matchPage": 1, "pcOrWap": 1}
    j = cli.get_json(url, params, "https://www.lottery.gov.cn/")
    v = j.get("value")
    if v is None:                       # 区间过大被拒 -> 二分
        d0 = datetime.strptime(start, "%Y-%m-%d").date()
        d1 = datetime.strptime(end, "%Y-%m-%d").date()
        if d0 >= d1:
            return []
        mid = d0 + (d1 - d0) // 2
        return (_jc_window(cli, url, start, mid.isoformat())
                + _jc_window(cli, url, (mid + timedelta(days=1)).isoformat(), end))
    total = int(v.get("total") or 0)
    pages = max(1, math.ceil(total / 100))
    out = list(v.get("matchResult") or [])
    if pages > 1:
        def fetch(p):
            pp = dict(params, pageNo=p)
            time.sleep(0.1)
            jj = cli.get_json(url, pp, "https://www.lottery.gov.cn/")
            return (jj.get("value") or {}).get("matchResult") or []
        with ThreadPoolExecutor(max_workers=6) as ex:
            for chunk in ex.map(fetch, range(2, pages + 1)):
                out.extend(chunk)
    return out


def crawl_jingcai(cli, url, label, start_year=2015):
    """按季度窗口抓取，季度过大时自动二分。"""
    out = []
    d = date(start_year, 1, 1)
    today = date.today()
    while d <= today:
        q_end = (date(d.year + (1 if d.month > 9 else 0), (d.month + 2) % 12 + 1, 1)
                 - timedelta(days=1))
        q_end = min(q_end, today)
        got = _jc_window(cli, url, d.isoformat(), q_end.isoformat())
        out.extend(got)
        log(f"  [竞彩{label}] {d}~{q_end} 场次={len(got)} 累计={len(out)}")
        d = q_end + timedelta(days=1)
        time.sleep(0.1)
    seen, uniq = set(), []
    for m in out:
        k = m.get("matchId")
        if k in seen:
            continue
        seen.add(k)
        uniq.append(m)
    return uniq


# ---------------------------------------------------------------- 归一化

CN = "零一二三四五六七八九十"


def cn(n):
    n = int(n)
    if n <= 10:
        return CN[n]
    if n < 20:
        return "十" + CN[n % 10]
    return CN[n // 10] + "十" + (CN[n % 10] if n % 10 else "")


SSQ_LEVELS = {1: "一等奖", 2: "二等奖", 3: "三等奖", 4: "四等奖",
              5: "五等奖", 6: "六等奖", 7: "七等奖"}
D3_LEVELS = {1: "单选", 2: "组三", 3: "组六"}


def kl8_level(t):
    m = re.match(r"x(\d+)z(\d+)", str(t))
    if m:
        return f"选{cn(m.group(1))}中{cn(m.group(2))}"
    return str(t)


def num(x):
    if x is None:
        return None
    s = str(x).replace(",", "").strip()
    if s in ("", "-", "--", "-1"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def fnum(x):
    """浮点解析（赔率等），保留 2 位小数。"""
    if x is None:
        return None
    s = str(x).replace(",", "").strip()
    if s in ("", "-", "--", "-1"):
        return None
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def clean_nums(s, pad=True):
    if not s:
        return ""
    parts = [x.strip() for x in re.split(r"[,，\s]+", str(s).strip()) if x.strip()]
    return " ".join(x.zfill(2) if pad else x for x in parts)


def parse_date(s):
    if not s:
        return ""
    return re.sub(r"\(.*?\)", "", str(s)).strip()


REGION_RE = re.compile(r"([\u4e00-\u9fa5]{2,8}?)(\d+)注")


def parse_regions(content):
    if not content:
        return []
    out = []
    for name, cnt in REGION_RE.findall(content):
        if name in ("共", "合计", "总计"):
            continue
        out.append((name, int(cnt)))
    return out


def read_rows():
    """汇总福彩/体彩开奖与中奖数据到 CSV。"""
    draws, prizes, regions = [], [], []

    # --- 福彩
    for code, cn_name in FUCAI_GAMES.items():
        path = os.path.join(RAW_DIR, f"fucai_{code}.jsonl")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                it = json.loads(line)
                issue = it.get("code")
                day = parse_date(it.get("date"))
                front, back = "", ""
                if code == "ssq":
                    front, back = clean_nums(it.get("red")), clean_nums(it.get("blue"))
                elif code == "qlc":
                    front, back = clean_nums(it.get("red")), clean_nums(it.get("blue"))
                elif code == "kl8":
                    front = clean_nums(it.get("red"))
                elif code == "3d":
                    front = clean_nums(it.get("red") or it.get("blue"), pad=False)
                draws.append({
                    "lottery_code": code, "lottery_name": cn_name, "issue": issue,
                    "draw_date": day, "front_numbers": front, "back_numbers": back,
                    "sales_amount": num(it.get("sales")), "pool_after_draw": num(it.get("poolmoney")),
                    "extra_amount": num(it.get("addmoney")),
                    "source": "www.cwl.gov.cn", "raw_file": f"fucai_{code}.jsonl",
                })
                for p in it.get("prizegrades") or []:
                    t = p.get("type")
                    cnt, money = num(p.get("typenum")), num(p.get("typemoney"))
                    if cnt is None and money is None:
                        continue        # 官方未公布的奖级（如双色球七等奖、3D 四~七等奖）
                    if code == "3d":
                        level = D3_LEVELS.get(int(t)) if str(t).isdigit() else str(t)
                    elif code in ("ssq", "qlc"):
                        level = SSQ_LEVELS.get(int(t)) if str(t).isdigit() else str(t)
                    else:
                        level = kl8_level(t)
                    if code == "3d":
                        # 福彩3D 接口的 typemoney 为该奖级奖金合计（非单注），
                        # 单注奖金 = 合计 / 注数（单选约1040元、组三约346元、组六约173元，含派奖浮动）
                        total = money
                        unit = round(total / cnt) if (total is not None and cnt) else None
                        note = "官方仅公布该奖级奖金合计，单注奖金为折算值"
                    else:
                        unit = money
                        total = (cnt * money) if (cnt is not None and money is not None) else None
                        note = ""
                    prizes.append({
                        "lottery_code": code, "lottery_name": cn_name, "issue": issue,
                        "draw_date": day, "prize_level": level,
                        "winner_count": cnt, "unit_prize": unit,
                        "total_prize": total,
                        "note": note,
                        "source": "www.cwl.gov.cn",
                    })
                for rname, rcnt in parse_regions(it.get("content")):
                    regions.append({
                        "lottery_code": code, "lottery_name": cn_name, "issue": issue,
                        "draw_date": day, "region": rname, "winner_count": rcnt,
                        "prize_level": "一等奖" if code == "ssq" else ("选十中十" if code == "kl8" else "一等奖"),
                        "source": "www.cwl.gov.cn", "raw_text": it.get("content"),
                    })

    # --- 体彩乐透
    for game_no, cn_name in TICAI_GAMES.items():
        code = "dlt" if game_no == "85" else "qxc"
        path = os.path.join(RAW_DIR, f"ticai_{code}.jsonl")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                it = json.loads(line)
                issue = it.get("lotteryDrawNum")
                day = it.get("lotteryDrawTime") or ""
                nums = clean_nums(it.get("lotteryDrawResult"), pad=(code == "dlt"))
                parts = nums.split(" ")
                if code == "dlt":
                    front, back = " ".join(parts[:5]), " ".join(parts[5:])
                else:
                    front, back = nums, ""
                draws.append({
                    "lottery_code": code, "lottery_name": cn_name, "issue": issue,
                    "draw_date": day, "front_numbers": front, "back_numbers": back,
                    "sales_amount": num(it.get("totalSaleAmount") or it.get("lotterySaleAmount")),
                    "pool_after_draw": num(it.get("poolBalanceAfterdraw")),
                    "extra_amount": None,
                    "source": "www.lottery.gov.cn", "raw_file": f"ticai_{code}.jsonl",
                })
                for p in it.get("prizeLevelList") or []:
                    cnt = num(p.get("stakeCount"))
                    unit = num(p.get("stakeAmountFormat") or p.get("stakeAmount"))
                    total = num(p.get("totalPrizeamount"))
                    prizes.append({
                        "lottery_code": code, "lottery_name": cn_name, "issue": issue,
                        "draw_date": day, "prize_level": p.get("prizeLevel"),
                        "winner_count": cnt, "unit_prize": unit,
                        "total_prize": total if total is not None
                        else ((cnt * unit) if (cnt is not None and unit is not None) else None),
                        "note": "", "source": "www.lottery.gov.cn",
                    })

    draws.sort(key=lambda x: (x["lottery_code"], x["draw_date"] or "", x["issue"] or ""))
    prizes.sort(key=lambda x: (x["lottery_code"], x["draw_date"] or "", x["issue"] or "",
                               x["prize_level"] or ""))
    regions.sort(key=lambda x: (x["lottery_code"], x["draw_date"] or "", x["issue"] or ""))
    return draws, prizes, regions


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    log(f"  写入 {path}  {len(rows)} 行")


def score_result(score):
    """按全场比分推导胜平负（H/D/A），比分为空或非法时返回 None"""
    if not score or ":" not in str(score):
        return None
    a, _, b = str(score).partition(":")
    try:
        h, v = int(a), int(b)
    except ValueError:
        return None
    return "H" if h > v else ("A" if v > h else "D")


def build_jingcai_csvs():
    for kind, fname in (("football", "jingcai_football.csv"),
                        ("basketball", "jingcai_basketball.csv")):
        path = os.path.join(RAW_DIR, f"jingcai_{kind}.jsonl")
        if not os.path.exists(path):
            continue
        rows = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = json.loads(line)
                oh, od, oa = fnum(m.get("h")), fnum(m.get("d")), fnum(m.get("a"))
                score = m.get("sectionsNo999") or m.get("finalScore")
                derived = score_result(score)
                if kind == "basketball":
                    # 篮球接口的 winFlag 与全场比分不符（对应让分盘口），
                    # 统一按全场比分推导：H=主胜 A=客胜
                    result = derived or m.get("winFlag")
                else:
                    # 足球 winFlag 与比分一致，缺失时按比分补全
                    result = m.get("winFlag") or derived
                rows.append({
                    "match_id": m.get("matchId"), "match_num": m.get("matchNumStr"),
                    "match_date": m.get("matchDate"), "league": m.get("leagueName"),
                    "home_team": m.get("allHomeTeam") or m.get("homeTeam"),
                    "away_team": m.get("allAwayTeam") or m.get("awayTeam"),
                    "score": score,
                    "half_score": m.get("sectionsNo1") or m.get("oneScore", ""),
                    "result": result,
                    "odds_home": oh, "odds_draw": od, "odds_away": oa,
                    "payout_home": round(2 * oh, 2) if oh else None,
                    "payout_draw": round(2 * od, 2) if od else None,
                    "payout_away": round(2 * oa, 2) if oa else None,
                    "handicap": m.get("goalLine") or m.get("goalline"),
                    "pool_status": m.get("poolStatus"),
                    "match_status": m.get("matchResultStatus"),
                })
        rows.sort(key=lambda x: (x["match_date"] or "", x["match_num"] or ""))
        write_csv(os.path.join(BASE, "data", fname), rows,
                  ["match_id", "match_num", "match_date", "league", "home_team", "away_team",
                   "score", "half_score", "result", "odds_home", "odds_draw", "odds_away",
                   "payout_home", "payout_draw", "payout_away",
                   "handicap", "pool_status", "match_status"])


def build_csvs():
    draws, prizes, regions = read_rows()
    write_csv(os.path.join(BASE, "data", "draws.csv"), draws,
              ["lottery_code", "lottery_name", "issue", "draw_date", "front_numbers",
               "back_numbers", "sales_amount", "pool_after_draw", "extra_amount",
               "source", "raw_file"])
    write_csv(os.path.join(BASE, "data", "prizes.csv"), prizes,
              ["lottery_code", "lottery_name", "issue", "draw_date", "prize_level",
               "winner_count", "unit_prize", "total_prize", "note", "source"])
    write_csv(os.path.join(BASE, "data", "regions.csv"), regions,
              ["lottery_code", "lottery_name", "issue", "draw_date", "prize_level",
               "region", "winner_count", "source", "raw_text"])

    # --- 竞彩
    build_jingcai_csvs()


def dump_jsonl(name, rows):
    path = os.path.join(RAW_DIR, f"{name}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    log(f"  保存 {path}  {len(rows)} 条")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="all",
                    choices=["all", "fucai", "ticai", "jingcai", "build"])
    ap.add_argument("--jc-start-year", type=int, default=2015)
    args = ap.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)
    cli = Client()

    if args.only in ("all", "fucai"):
        log("== 福彩 ==")
        for code in FUCAI_GAMES:
            rows = crawl_fucai(cli, code)
            dump_jsonl(f"fucai_{code}", rows)

    if args.only in ("all", "ticai"):
        log("== 体彩乐透 ==")
        for g in TICAI_GAMES:
            rows = crawl_ticai(cli, g)
            dump_jsonl(f"ticai_{'dlt' if g == '85' else 'qxc'}", rows)

    if args.only in ("all", "jingcai"):
        log("== 竞彩足球 ==")
        dump_jsonl("jingcai_football", crawl_jingcai(cli, JC_FB, "足球", args.jc_start_year))
        log("== 竞彩篮球 ==")
        dump_jsonl("jingcai_basketball", crawl_jingcai(cli, JC_BB, "篮球", args.jc_start_year))

    if args.only in ("all", "build"):
        log("== 生成 CSV ==")
        build_csvs()


if __name__ == "__main__":
    sys.exit(main())
