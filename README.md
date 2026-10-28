# 彩票数据统计与预测站点

抓取官方开奖与中奖数据 → 统计建模 → 生成下期参考号码 → 本地网站展示 → 实盘预测台账与逐期盈亏结算。

> **免责声明**：本项目仅用于统计学习与模拟研究。各期开奖相互独立，任何统计规律都不能提高单注中奖概率；项目不销售彩票，也不构成任何投注建议。

## 功能概览

- **数据抓取**：福彩（双色球、快乐8、福彩3D、七乐彩）、体彩（超级大乐透、七星彩）全部历史开奖与奖级明细，另含竞彩足球、篮球赛果与赔率。
- **统计分析**：号码频数与卡方均匀性检验、和值/跨度分布、奇偶比/大小比/连号/重号形态、遗漏分析、自相关与游程检验、号码对共现、数字彩按位分布。
- **概率模型**：EWMA 衰减频率、Beta-Binomial 经验贝叶斯后验、一阶马尔可夫转移、二项检验、遗漏几何分位、log-odds 融合、分布熵/KL 散度，以及蒙特卡洛与 Bootstrap 零分布。
- **推荐与组合**：多因子打分给出核心号/备选号，生成 5 注单式与复式方案（附注数与金额）。
- **回测与模拟**：逐期滚动回测（每期只用此前数据重算），并与"完全随机选号"对照；模拟引擎支持自选/随机/系统预测三种选号方式与资金池约束。
- **实盘台账**：推荐一经发出即登记目标期号，开奖后按真实号码与官方单注奖金结算，汇总为累计盈亏。
- **网站**：7 个栏目页 + 原版数据页，服务端 SEO 注入，账号注册登录与策略管理。

## 目录结构

```
crawl.py               官方开奖 / 中奖数据爬虫
analyze.py             分布统计建模，输出 analysis.json
models.py              扩展概率模型（EWMA / 贝叶斯 / 马尔可夫 / MC / Bootstrap）
predict.py             下期推荐与组合生成
backtest.py            逐期滚动回测与随机基准
ledger.py              实盘台账登记与结算
simulate.py            策略模拟引擎
update_daily.py        每日一键更新（抓取 → 统计 → 预测 → 台账）
server.py              网站后端（仅标准库）
index.html             原版完整数据页（/legacy 路由）
web/                   新版前端（index.html / app.js / style.css / robots.txt / sitemap.xml / llms.txt）
data/                  开奖数据与统计结果（运行时库与抓取缓存不入库）
run_update.cmd         每日更新批处理
start_site.cmd         启动网站
stop_site.cmd          停止网站
```

## 环境要求

- Python 3.10+（开发验证于 3.14）
- 抓取与统计：`requests`、`numpy`、`pandas`、`scipy`
- `server.py` 仅用标准库，无需额外依赖

```bash
pip install requests numpy pandas scipy
```

## 快速开始

### 1. 准备数据

```bash
python crawl.py          # 抓取开奖与奖级数据，生成 data/*.csv
python analyze.py        # 统计分析
python predict.py        # 生成下期推荐
python ledger.py         # 台账登记与结算
```

也可以一键串起来（推荐）：

```bash
python update_daily.py                # 福彩 + 体彩乐透 + 统计 + 预测 + 台账
python update_daily.py --with-jingcai # 额外更新竞彩足球/篮球（较慢）
python update_daily.py --skip-crawl   # 只重跑统计、预测与台账结算
python update_daily.py --only predict # 只跑某一步：crawl / analyze / predict / ledger
```

### 2. 启动网站

```bash
python server.py                       # http://127.0.0.1:8123
python server.py --port 8000 --host 127.0.0.1
```

Windows 下可直接双击 `start_site.cmd` 启动、`stop_site.cmd` 停止（端口 8123）。

### 3. 每日自动更新

用 Windows 计划任务在每晚 23:00 调用 `run_update.cmd`（内部执行 `update_daily.py`，日志追加到 `data/update.log`）：

```
程序或脚本：  run_update.cmd
起始于：      项目根目录
```

## 数据文件

| 文件 | 内容 |
| --- | --- |
| `data/draws.csv` | 开奖主表（彩种、期号、日期、开奖号码） |
| `data/prizes.csv` | 奖级明细（奖级、中奖注数、单注奖金、奖金合计） |
| `data/regions.csv` | 一等奖地域分布（官方仅福彩双色球、快乐8公布） |
| `data/jingcai_football.csv` / `data/jingcai_basketball.csv` | 竞彩赛果与赔率 |
| `data/analysis.json` | 统计算法输出 |
| `data/predictions.json` | 下期推荐与组合方案 |
| `data/backtest.json` | 滚动回测结果 |
| `data/predict_log.json` | 实盘预测台账 |
| `data/pnl.json` | 累计投入 / 奖金 / 净盈亏 |

以下为运行时产物，**未纳入版本库**：

| 文件 | 说明 |
| --- | --- |
| `data/raw/*.jsonl` | 抓取原始缓存（体积大，可由 `crawl.py` 重新生成） |
| `data/app.db` | 账号、会话与策略（含用户数据） |
| `data/visits.json`、`data/access.log` | 访问统计与访问日志 |
| `data/update_status.json`、`data/*.log` | 每日更新状态与运行日志 |

## 网站页面

| 路由 | 内容 |
| --- | --- |
| `/` | 各彩种最近一期开奖、下期参考号码与核心指标 |
| `/draws` | 历史开奖与奖级明细查询 |
| `/analysis` | 频数、和值、跨度、形态、遗漏、独立性检验 |
| `/predict` | 下期推荐、单式与复式组合、数字彩按位推荐 |
| `/pnl` | 实盘预测台账与逐期盈亏 |
| `/sim` | 策略模拟（选号方式、注数、期数、资金池） |
| `/theory` | 项目用到的统计与概率方法说明 |
| `/legacy` | 原版完整数据页 |

## 接口

```text
GET  /api/meta                             彩种与数据版本等元信息
GET  /api/draws?code=ssq&limit=30          开奖记录（limit 上限 200）
GET  /api/prizes?code=ssq&issue=2024001    指定期奖级明细
GET  /api/analysis                         统计结果
GET  /api/predictions                      下期推荐
GET  /api/pnl                              实盘台账汇总
GET  /api/visits                           访问计数
GET  /api/me                               当前用户与策略列表（需登录）
POST /api/register  /api/login  /api/logout
POST /api/strategies                       新建策略
PUT  /api/strategies  /api/wallet          修改策略 / 资金池
DELETE /api/strategies                     删除策略
```

状态变更接口会校验同源（`Origin` 与 `Sec-Fetch-Site`），跨站请求返回 403。

## 结算口径

- 单式：5 注 × 2 元；复式：按推荐方案的注数 × 2 元
- 浮动奖级无人中出时官方单注奖金为 0 或空，回退到该奖级最近一次公布值
- 福彩3D 奖级明细缺失，按固定单选 1040 元结算
- 资金池不足当期投入时跳过该期，不赊账

## 安全设计

- 密码 PBKDF2-HMAC-SHA256 12 万轮加盐存储，Cookie 会话有效期 7 天
- 登录 / 注册按 IP 限速，超阈值锁定
- 请求体上限 1MB，并发连接上限 64（超出直接 503）
- 统一注入 CSP、X-Frame-Options、X-Content-Type-Options 等响应头
- 异常只向客户端返回通用提示，堆栈仅写服务端日志

## 数据来源

- 中国福利彩票：<https://www.cwl.gov.cn>
- 中国体育彩票 / 竞彩：<https://www.lottery.gov.cn>

数据版权归来源机构所有，本项目仅作个人研究用途，请遵守来源网站的 robots 协议与服务条款。
