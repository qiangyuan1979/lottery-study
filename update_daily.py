"""每日更新：抓取最新开奖 -> 重新统计 -> 生成下期推荐 -> 预测台账登记与结算。

用法：
    python update_daily.py                # 福彩 + 体彩乐透 + 重建 CSV + 统计 + 预测 + 台账
    python update_daily.py --with-jingcai # 额外更新竞彩足球/篮球（较慢）
    python update_daily.py --skip-crawl   # 只重跑统计、预测与台账结算
    python update_daily.py --only predict # 只跑某一步：crawl / analyze / predict / ledger

台账说明：推荐一经发出即登记到 data/predict_log.json，开奖后按当期真实号码与官方
单注奖金结算，只统计真正发出过的预测，不把模型回测套用到历史全部开奖。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
STATUS = os.path.join(BASE, "data", "update_status.json")


def log(msg):
    print(msg, flush=True)


def run(script, args, timeout=3600):
    cmd = [sys.executable, os.path.join(BASE, script)] + args
    log(">> " + " ".join(cmd))
    t0 = time.time()
    p = subprocess.run(cmd, cwd=BASE, timeout=timeout)
    cost = round(time.time() - t0, 1)
    log("<< %s 退出码 %s  用时 %ss" % (script, p.returncode, cost))
    return dict(step=script + " " + " ".join(args), code=p.returncode, seconds=cost)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-jingcai", action="store_true", help="同时更新竞彩足球/篮球")
    ap.add_argument("--skip-crawl", action="store_true", help="跳过抓取，只重跑统计、预测与台账结算")
    ap.add_argument("--only", default="", choices=["", "crawl", "analyze", "predict", "ledger"])
    ap.add_argument("--jc-start-year", type=int, default=2025)
    args = ap.parse_args()

    steps = []
    t0 = time.time()

    do_crawl = not args.skip_crawl and args.only in ("", "crawl")
    do_analyze = args.only in ("", "analyze")
    do_predict = args.only in ("", "predict")
    do_ledger = args.only in ("", "ledger")

    try:
        if do_crawl:
            for only in ("fucai", "ticai"):
                steps.append(run("crawl.py", ["--only", only], timeout=1800))
            if args.with_jingcai:
                steps.append(run("crawl.py", ["--only", "jingcai",
                                              "--jc-start-year", str(args.jc_start_year)],
                                 timeout=3600))
            steps.append(run("crawl.py", ["--only", "build"], timeout=600))

        if do_analyze:
            steps.append(run("analyze.py", [], timeout=1800))

        if do_predict:
            steps.append(run("predict.py", [], timeout=1800))

        if do_ledger:
            steps.append(run("ledger.py", [], timeout=1800))
    except Exception as e:  # 网络异常等，记录后退出非零
        steps.append(dict(step="exception", code=-1, seconds=0, error=str(e)))
        log("!! 中断：%s" % e)

    ok = all(s.get("code") == 0 for s in steps)
    status = dict(
        at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ok=ok,
        seconds=round(time.time() - t0, 1),
        steps=steps,
    )
    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    with open(STATUS, "w", encoding="utf-8") as fp:
        json.dump(status, fp, ensure_ascii=False, indent=2)

    log("== 完成 %s 用时 %ss ==" % ("成功" if ok else "有失败步骤", status["seconds"]))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
