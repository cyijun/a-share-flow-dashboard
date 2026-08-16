# A股资金流、RPS与ETF仪表盘

按同花顺行业模板跟踪 A 股资金流，同时分析沪深个股绝对净额与市值归一化强度、全市场 RPS、ETF 价格强度和 ETF 申赎资金估算。资金流周期、RPS周期和大市值门槛均可通过命令行修改；每次运行都会保存原始数据、结果、验证报告和自包含网页。

## 能做什么

- 同花顺行业板块与沪深个股净流入、净流出双向榜单。
- 任意正整数交易日资金流窗口，例如 `1,5,10` 或 `2,6,9`。
- 市值不低于指定门槛的沪深个股“累计净额 / 最新总市值”。
- 任意正整数交易日 RPS，例如 `10,20` 或 `8,15`，并筛选全市场前5%。
- ETF 综合 RPS 与基于份额变化的净申赎估算。
- 从原始 CSV 独立复算行业、个股、市值比例、RPS 和 ETF 指标。
- 纯 Python 标准库生成自包含、响应式 HTML，不依赖本机插件或前端构建工具。

## 环境要求

- Python 3.11 或更高版本；项目运行时只使用标准库。
- 可访问所需接口的 Tushare Pro token。本项目按6000积分权限设计。
- macOS、Linux 均可运行；Windows 请把示例中的 `python3` 换成对应 Python 命令。

## 快速开始

```bash
git clone <仓库地址>
cd a-share-flow-dashboard
cp .env.example .env
```

编辑 `.env`：

```text
TUSHARE_TOKEN=你的token
```

运行默认分析：

```bash
python3 scripts/update_dashboard.py
```

默认口径是资金流 `1/5/10` 个交易日、RPS `10/20` 个交易日、市值门槛200亿元。

打开生成结果：

```bash
open outputs/latest.html       # macOS
xdg-open outputs/latest.html  # Linux
```

## 更换时间周期

下面的例子计算资金流 `2/6/9` 日、RPS `8/15` 日，并把市值比例榜门槛改为300亿元：

```bash
python3 scripts/update_dashboard.py \
  --flow-days 2,6,9 \
  --rps-days 8,15 \
  --market-cap-floor 300
```

指定统计截止日：

```bash
python3 scripts/update_dashboard.py --as-of 20260814
```

如需同时保留多套周期配置，可给每套配置指定独立输出根目录：

```bash
python3 scripts/update_dashboard.py \
  --flow-days 1,3,5 \
  --rps-days 10,20,60 \
  --output-root outputs/short_term
```

参数说明：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `--as-of` | 当天 | 截止日期，格式 `YYYYMMDD`；遇非交易日自动取此前最新交易日 |
| `--flow-days` | `1,5,10` | 资金流窗口，逗号分隔的正整数交易日数 |
| `--rps-days` | `10,20` | RPS窗口，逗号分隔的正整数交易日数 |
| `--market-cap-floor` | `200` | 资金/市值比例榜最低总市值，单位亿元 |
| `--output-root` | `outputs` | 快照保存根目录 |
| `--skip-data` | 关闭 | 不访问接口，使用输出目录中最新快照重建网页并复验 |

`--skip-data` 使用快照内已经记录的周期配置，命令行中的周期参数不会改写旧快照。

## 输出结构

```text
outputs/
├── latest.html
├── latest.json
└── YYYYMMDD/
    ├── raw/                         # Tushare原始CSV
    ├── results/                     # 指标、榜单和质量检查
    │   ├── summary.json             # 周期配置、口径、榜首与质量状态
    │   └── independent_validation.json
    ├── metadata.json
    └── report.html                  # 自包含网页
```

`outputs/` 与 `.env` 已加入 `.gitignore`，真实 token、原始行情和生成快照不会进入仓库。

## 计算口径

- 行业：`moneyflow_ind_ths` 的同花顺资金流行业，窗口内累计 `net_amount`。
- 个股：`moneyflow_ths.net_amount` 窗口累计；该接口覆盖沪深市场，不覆盖北交所。北交所标准 `moneyflow` 只用于独立覆盖复核，不与THS榜单混排。
- 大市值资金强度：窗口累计净额除以最新交易日 `daily_basic.total_mv`。
- RPS：区间前复权收益在最新交易日全A可比股票中的百分位，包含北交所；使用未舍入收益排名，真实并列采用平均秩，前5%定义为 `RPS >= 95`。
- ETF综合强度：所有已配置RPS窗口的可用RPS均值。
- ETF估算资金流：逐日累计 `(当日份额 - 前一交易日份额) × 当日收盘价`。只有份额完整覆盖整个窗口时才生成正式数值及排名；部分覆盖值仅保留在 `partial_estimated_flow_*` 字段。这是申购赎回估算，不能据此识别央行、汇金等具体交易主体。
- 同花顺行业成员使用 `ths_member` 当前快照，允许一只股票属于多个行业，因此行业不可跨板块加总，历史 `as-of` 也不代表历史时点成分。
- 所有“日”均指交易日，不是自然日。

主要接口文档：[`ths_hot`](https://tushare.pro/document/2?doc_id=320)、[`moneyflow_ind_ths`](https://tushare.pro/document/2?doc_id=343)、[`moneyflow_ths`](https://tushare.pro/document/2?doc_id=348)、[`ths_member`](https://tushare.pro/document/2?doc_id=261)。脚本还会使用日线、复权因子、每日指标、标准资金流、基金日线和基金份额等接口。

## 数据验证

生产管道先检查：

- 交易日窗口、原始事实表主键和关键字段；
- 每个交易日分区是否非空，并按各接口真实上限检查是否触顶，例如 `fund_share=2000`、`fund_daily=5000`、日线和个股资金流为6000；
- 同花顺个股资金的分单算术；
- 标准 `moneyflow` 与 `moneyflow_ths` 的方向和相关性；
- 沪深THS资金流与北交所标准资金流的分交易所覆盖、市值覆盖、RPS样本、ETF名称与份额覆盖；
- 输出文件是否意外包含 Tushare token。

随后 `validate_market_dashboard.py` 完全绕过生产聚合函数，从 `raw/` 自行推导交易日、端点、样本资格和每一个已配置周期，并独立检查接口限额、交易所覆盖、未舍入RPS平均秩、ETF完整窗口、文件哈希与代码版本。任何关键复算失败都会使一键命令返回非零状态，阻止 `latest.html` 被更新。

全量更新先在隐藏暂存目录完成取数、网页渲染和独立验证，全部通过后才原子切换正式快照；同日期旧版会移入 `outputs/_previous/`，可恢复而不会直接删除。

单独复验已有快照：

```bash
python3 scripts/render_dashboard.py outputs/20260814
python3 scripts/validate_market_dashboard.py outputs/20260814
```

运行轻量单元测试：

```bash
python3 -m unittest discover -s tests -v
```

## 自动运行

建议在交易日北京时间18:00以后执行，避免部分日终数据尚未入库。仓库提供 `automation/com.wayne.flow-track.plist.example`，可按实际克隆路径修改后用于 macOS `launchd`；模板默认周一至周五18:30运行，不会自动写入 `~/Library/LaunchAgents`。

## 安全与限制

- token 只从环境变量或项目根目录 `.env` 读取，不会写入结果。
- 榜单反映指定历史窗口，不预测未来表现，也不构成投资建议。
- 同花顺资金流与标准 `moneyflow` 算法不同，后者只作独立交叉复核。
- 个股THS资金榜的准确名称是“沪深个股资金榜”；不要把它解释为含北交所的全A资金榜。
- ETF份额变化能观察申赎，但不能直接代表“国家队”或任何单一机构的交易。

旧版五日实验脚本 `scripts/ths_five_day_flow.py` 仍作为通用 Tushare 客户端与基础校验工具被主脚本复用。
