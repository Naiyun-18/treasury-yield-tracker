US Treasury yield tracker

Generated page: https://naiyun-18.github.io/treasury-yield-tracker/

- fetch_and_render.py : fetch 5Y/10Y/30Y via iwencai & render standalone HTML with TWO charts
- close_data.json    : 每美股交易日收盘采样 (北京 06:00), 图表一, 最多60个交易日
- intraday_data.json : 盘中每3小时采样 (北京 23:00/02:00/05:00), 图表二, 最多60采样点(~20交易日)
- index.html         : 双折线图 (自包含 SVG, 无 CDN 依赖)

采样规则: 开盘(北京20:00)不采集利率数据; 美债休市(北京周日/周一凌晨)自动跳过。
