各国国债和黄金价格追踪分析报告

Generated page: https://naiyun-18.github.io/treasury-yield-tracker/

- fetch_and_render.py : 通过问财(iwencai)抓取美债各期限 + 伦敦金现货，渲染自包含 HTML。
- close_data.json    : 每美股交易日美债收盘采样 (北京 06:00), 图表二, 最多60个交易日。
- intraday_data.json : 盘中每3小时采样 (北京 23:00/02:00/05:00), 图表三, 最多60采样点(~20交易日)。
- track_daily.json   : 图表一每日收盘 (伦敦金 + 2Y + 10Y), date=交易日, 固定90采样点, 每日更新。
- index.html         : 三张图 (图表一:伦敦金+2Y/10Y双轴 / 图表二:美债日收盘 / 图表三:盘中) 自包含SVG。

采样规则: 开盘(北京20:00)不采集利率; 美债休市(北京周日/周一凌晨)自动跳过。
图表一: 伦敦金24H交易但周末休市，按交易日每日更新，横坐标固定90个交易日采样。
