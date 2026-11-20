#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
国债和黄金石油价格跟踪报告 - fetch US treasury yields + London gold + Brent oil + render HTML.

Data source: 同花顺问财 (hithink-macro-query). 每采样点写入对应数据文件，然后渲染自包含 HTML。

  图表一（每日收盘·伦敦金+短长端）: 伦敦金(左轴·美元/盎司) + 2Y + 10Y(右轴·%) 每日收盘价同图。
         X轴固定 90 个采样点(交易日)，不足留空。伦敦金几乎24H交易 → 每日更新。
         数据文件 track_daily.json : [{date, gold, us2y, us10y}]，date=交易日(YYYY-MM-DD)。
  图表二（每美股交易日收盘·美债）: 2Y/5Y/10Y/30Y 收盘价。 close_data.json。
  图表三（盘中每3小时）: 交易日(北京 23:00/02:00/05:00) 利率采样。 intraday_data.json。开盘(20:00)不采集。

Data files:
  close_data.json      - daily close samples  {ts, us2y, us5y, us10y, us30y}
  intraday_data.json   - intraday samples     {ts, us2y, us5y, us10y, us30y}
  track_daily.json     - 图表一每日收盘       {date, gold, us2y, us10y}
"""
import os, json, re, sys, shutil, subprocess, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CLOSE = os.path.join(BASE, "close_data.json")
INTRADAY = os.path.join(BASE, "intraday_data.json")
TRACK = os.path.join(BASE, "track_daily.json")
HTML = os.path.join(BASE, "index.html")
CLI = os.path.expanduser("~/.openclaw/workspace/skills/hithink-macro-query/scripts/cli.py")

QUERIES = {"us2y": "美国2年期国债收益率",
           "us5y": "美国5年期国债收益率",
           "us10y": "美国10年期国债收益率",
           "us30y": "美国30年期国债收益率",
           "eu2y": "德国2年期国债收益率",
           "eu10y": "德国10年期国债收益率",
           "cny": "美元兑人民币汇率",
           "eur": "美元兑欧元汇率",
           "jpy": "美元兑日元汇率",
           "gold": "伦敦金现货"}
KEYS = ["us2y", "us5y", "us10y", "us30y"]
TRACKKEYS = ["gold", "us2y", "us10y", "eu2y", "eu10y", "cny", "eur", "jpy", "brent"]
LABELS = {"us2y": "2年期", "us5y": "5年期", "us10y": "10年期", "us30y": "30年期",
          "eu2y": "欧盟2Y", "eu10y": "欧盟10Y",
          "cny": "美元兑人民币(÷6)", "eur": "美元兑欧元", "jpy": "美元兑日元(÷150)",
          "gold": "伦敦金",
          "brent": "布伦特石油（美元/桶）"}
# 表格列名（展示真实值，不标÷除数；除法仅用于图表呈现）
TAB_LABELS = {"cny": "美元兑人民币", "jpy": "美元兑日元", "brent": "布伦特石油"}
COLORS = {"us2y": "#27ae60", "us5y": "#2471a3", "us10y": "#e67e22", "us30y": "#c0392b",
          "eu2y": "#2e86c1", "eu10y": "#8e44ad",
          "cny": "#16a085", "eur": "#e91e63", "jpy": "#5d6d7e",
          "gold": "#d4af37", "brent": "#34495e"}
LINKS = {"us2y": "https://finance.baidu.com/bond/global-US2YT",
         "us5y": "https://finance.baidu.com/bond/global-US5YT",
         "us10y": "https://finance.baidu.com/bond/global-US10YT",
         "us30y": "https://finance.baidu.com/bond/global-US30YT",
         "gold": "https://finance.baidu.com/qf/hjs"}


def _api_key():
    k = os.environ.get("IWENCAI_API_KEY", "")
    if not k:
        try:
            p = subprocess.run("bash -lc 'source ~/.profile >/dev/null 2>&1; echo $IWENCAI_API_KEY'",
                               shell=True, capture_output=True, text=True)
            k = p.stdout.strip()
        except Exception:
            pass
    return k


def _extract_value(row):
    """Robustly extract a numeric value from an iwencai row, independent of field-name drift.
    优先级：①指标值 ②外汇dated字段(形如 外汇@收盘价/最新价/值[YYYYMMDD]，取最新日期) ③任意数字型外汇字段。
    修复：此前仅匹配字面含'收盘价/最新价'的字段名，问财外汇字段名会漂移(曾在'收盘价'与'最新价'间切换)，
    导致 cny/eur/jpy 被静默置 None、当日汇率整段缺失。此实现改为按 [YYYYMMDD] 日期标记通用匹配，字段名漂移不再丢数据。"""
    v = row.get("指标值")
    if v is not None and (isinstance(v, (int, float)) or (isinstance(v, str) and v.strip())):
        try:
            return float(v)
        except Exception:
            pass
    dated_re = re.compile(r"^外汇@[^\[]*\[(\d{8})")
    best, best_d = None, None
    for fk, fv in row.items():
        if not fk.startswith("外汇@"):
            continue
        m = dated_re.match(fk)
        try:
            num = float(fv)
        except Exception:
            continue
        if m:
            d = int(m.group(1))
            if best_d is None or d > best_d:
                best, best_d = num, d
        elif best is None and "代码" not in fk and "名称" not in fk:
            best = num
    return best


def fetch_now():
    """Fetch all series from iwencai. Returns {us2y, us5y, us10y, us30y, eu2y, eu10y, cny, eur, jpy, gold}.
    US/EU 收益率走 '指标值'；外汇对走 '外汇@...收盘价/最新价/值[YYYYMMDD]' 字段；伦敦金走 '指标值'。"""
    api_key = _api_key()
    out = {}
    for k, q in QUERIES.items():
        val = None
        for attempt in range(3):
            try:
                cmd = [sys.executable, CLI, "--query", q, "--timeout", "40"]
                if api_key:
                    cmd += ["--api-key", api_key]
                r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ))
                j = json.loads(r.stdout)
                if j.get("datas"):
                    row = j["datas"][0]
                    val = _extract_value(row)
                    val = float(val) if val is not None else None
                if val is not None:
                    break
            except Exception as e:
                if attempt == 2:
                    print("ERR %s: %s" % (k, e), file=sys.stderr)
        out[k] = val
    return out


def _run_query(q, page=1, limit=20):
    """调用问财 cli 查询并返回解析后的 JSON（带翻页参数）。"""
    api_key = _api_key()
    cmd = [sys.executable, CLI, "--query", q, "--timeout", "40",
           "--page", str(page), "--limit", str(limit)]
    if api_key:
        cmd += ["--api-key", api_key]
    r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ))
    return json.loads(r.stdout)


def fetch_brent_series(days=140):
    """布伦特原油收盘历史序列 {YYYY-MM-DD: val}（美元/桶）。两步查询：
    1) 「布伦特原油连续期货收盘价」-> 取有收盘价的最近月活跃合约简称（如 布伦特原油2704）；
    2) 「{合约简称}从X日到Y日的收盘价」-> 翻页汇总 收盘价[YYYYMMDD] 字段。
    仅收盘采样时调用。"""
    try:
        j = _run_query("布伦特原油连续期货收盘价", limit=10)
        best = None
        for row in j.get("datas") or []:
            name = row.get("合约简称") or ""
            m = re.match(r"^布伦特原油(\d{4})$", name)
            has_px = any(k.startswith("收盘价[") and v is not None
                         for k, v in row.items() if isinstance(v, (int, float)))
            if m and has_px:
                ym = int(m.group(1))
                if best is None or ym < best[0]:
                    best = (ym, name)
        if not best:
            print("WARN brent: 未找到有收盘价的布伦特活跃合约", file=sys.stderr)
            return {}
        name = best[1]
        end = datetime.date.today()
        start = end - datetime.timedelta(days=days)
        q = "%s从%d年%d月%d日到%d年%d月%d日的收盘价" % (
            name, start.year, start.month, start.day, end.year, end.month, end.day)
        out = {}
        page = 1
        while page <= 15:  # 最多15页防死循环
            j = _run_query(q, page=page, limit=20)
            rows = j.get("datas") or []
            if not rows:
                break
            for row in rows:
                for fk, v in row.items():
                    if fk.startswith("收盘价[") and v is not None:
                        raw = fk.replace("收盘价[", "").replace("]", "")
                        d = ("%s-%s-%s" % (raw[:4], raw[4:6], raw[6:8])) if len(raw) == 8 else raw
                        try:
                            out[d] = float(v)
                        except Exception:
                            pass
            if not j.get("has_more"):
                break
            page += 1
        if not out:
            print("WARN brent: 合约 %s 历史序列为空" % name, file=sys.stderr)
        return out
    except Exception as e:
        print("ERR brent: %s" % e, file=sys.stderr)
    return {}


def load_data(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_data(data, path):
    # 防数据丢失：保存前先把旧文件滚动备份为 .bak，即使本次覆盖异常也能回退
    if os.path.exists(path):
        try:
            shutil.copy(path, path + ".bak")
        except Exception:
            pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("saved %d samples -> %s" % (len(data), path))


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def sample_kind(now):
    """Map a Beijing capture time -> data kind.

    Returns 'close' | 'intraday' | None(skip).
    US non-trading in Beijing time:
      - Beijing Sunday 所有时段           = US 周六
      - 北京周一 02/05/06 点              = US 周日(休市)
      - 北京周六 23:00                    = US 周六
      - 北京 20:00                        = 开盘(不采集利率)
    """
    h = now.hour
    wd = now.weekday()  # Mon=0 ... Sun=6

    if h == 20:
        return None
    if wd == 6:
        return None
    if wd == 0 and h in (2, 5, 6):
        return None
    if wd == 5 and h == 23:
        return None
    if h == 23:
        return "intraday"
    if h in (2, 5):
        return "intraday"
    if h == 6:
        return "close"
    return None


def _axis(vlo, vhi):
    pad = max((vhi - vlo) * 0.12, 0.05)
    return round(vlo - pad, 3), round(vhi + pad, 3)


def _px(i, L, iw, n_target):
    return L + iw * (i / (n_target - 1))


def _py(v, ylo, yhi, T, ih):
    return T + ih * (1 - (v - ylo) / (yhi - ylo))


def _scale_pts(points, key, ylo, yhi, L, iw, T, ih, n_target):
    pts = []
    for i, p in enumerate(points):
        if p.get(key) is not None:
            pts.append((_px(i, L, iw, n_target), _py(p[key], ylo, yhi, T, ih)))
    return pts


def render_dual(points, left_key, right_keys, divisors, title, subtitle, n_target, x_when,
                 left_axis_title="伦敦金（美元/盎司）", right_axis_title="债券收益率（%）",
                 ylab_fmt="%.2f%%", bottom_key=None, bottom_title="布伦特石油主力（美元/桶）"):
    """双轴折线图（上下双面板，共享同一X轴）：
      上图 = left_key(左轴·金价) + right_keys(右轴)；
      下图(可选) = bottom_key 单线，独立左轴刻度(真实值)，与上图共享X轴。
    points=[{date, ...},...]；divisors=None 或 {key: 除数}。用于图表一(金价+Yield)与图表二(金价+汇率)。"""
    def dv(k):
        return divisors.get(k, 1.0) if divisors else 1.0
    gvals = [p[left_key] / dv(left_key) for p in points if p.get(left_key) is not None]
    yvals = [p[k] / dv(k) for p in points for k in right_keys if p.get(k) is not None]
    g_lo, g_hi = _axis(min(gvals), max(gvals)) if gvals else (4000, 4400)
    y_lo, y_hi = _axis(min(yvals), max(yvals)) if yvals else (4.0, 8.0)

    W, L, R = 1200, 88, 80
    iw = W - L - R
    top_y, top_h, bot_y, bot_h = 45, 275, 358, 178
    bot_end = bot_y + bot_h
    H = bot_end + 52
    xlbl_y = bot_end + 24

    b_lo = b_hi = None
    if bottom_key:
        bvals = [p[bottom_key] for p in points if p.get(bottom_key) is not None]
        if bvals:
            b_lo, b_hi = _axis(min(bvals), max(bvals))
        else:
            b_lo, b_hi = (95.0, 110.0)

    parts = ['<line x1="0" y1="0" x2="%d" y2="0" stroke="#f5f7fa" stroke-width="0"/>' % W]
    # 垂直网格线跨上下两面板（共享X轴）
    vgrid = []
    for slot in (0, 15, 30, 45, 60, 75, 89):
        sx = _px(slot, L, iw, n_target)
        vgrid.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#dde6f0" stroke-dasharray="2,4"/>'
                     % (sx, top_y, sx, bot_end))
    parts.append("".join(vgrid))

    # 上图横向网格 + 左轴金价 + 右轴%
    tg = []
    for g in range(6):
        glab = g_lo + (g_hi - g_lo) * g / 5
        gy = _py(glab, g_lo, g_hi, top_y, top_h)
        tg.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#ececec"/>' % (L, gy, W - R, gy))
        tg.append('<text x="%d" y="%.1f" class="ylab" text-anchor="end">%.0f</text>' % (L - 8, gy + 4, glab))
        ylab = y_lo + (y_hi - y_lo) * g / 5
        yy = _py(ylab, y_lo, y_hi, top_y, top_h)
        tg.append('<text x="%d" y="%.1f" class="ylab">%s</text>' % (W - R + 12, yy + 4, ylab_fmt % ylab))
    parts.append("".join(tg))

    ax = ''.join([
        '<text x="%d" y="16" class="ylab" text-anchor="middle">%s</text>' % (L + iw // 2, left_axis_title),
        '<text x="%d" y="16" class="ylab" text-anchor="end">%s</text>' % (W - R - 4, right_axis_title),
    ])
    parts.append(ax)

    # 下图横向网格 + 布伦特左轴
    bot = []
    if bottom_key and b_lo is not None:
        for g in range(5):
            blab = b_lo + (b_hi - b_lo) * g / 4
            by = _py(blab, b_lo, b_hi, bot_y, bot_h)
            bot.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#ececec"/>' % (L, by, W - R, by))
            bot.append('<text x="%d" y="%.1f" class="ylab" text-anchor="end">%.0f</text>' % (L - 8, by + 4, blab))
        bot.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#9db3c9" stroke-width="1.5"/>'
                   % (L, bot_y - 20, W - R, bot_y - 20))
        bot.append('<text x="%d" y="%d" class="ylab" text-anchor="middle" font-weight="600">%s</text>'
                   % (L + iw // 2, bot_y - 26, bottom_title))
    parts.append("".join(bot))

    # X轴标签（最底部，上下两图共享）
    xlab = []
    for idx in sorted(set([0, len(points) // 2, len(points) - 1])):
        lab = x_when(points[idx].get("date", ""))
        xlab.append('<text x="%.1f" y="%d" class="xlab">%s</text>' % (_px(idx, L, iw, n_target), xlbl_y, lab))
    parts.append("".join(xlab))

    def series_pts(key, ylo, yhi, ytop, yh):
        d = dv(key)
        pts = []
        for i, p in enumerate(points):
            if p.get(key) is not None:
                pts.append((_px(i, L, iw, n_target), _py(p[key] / d, ylo, yhi, ytop, yh)))
        return pts

    polylines, dots = [], []
    pts = series_pts(left_key, g_lo, g_hi, top_y, top_h)
    if pts:
        polylines.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2.5"/>'
                         % (" ".join("%.1f,%.1f" % (x, y) for x, y in pts), COLORS[left_key]))
        x, y = pts[-1]
        dots.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s"/>' % (x, y, COLORS[left_key]))
    for k in right_keys:
        pts = series_pts(k, y_lo, y_hi, top_y, top_h)
        if pts:
            polylines.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2.5"/>'
                             % (" ".join("%.1f,%.1f" % (x, y) for x, y in pts), COLORS[k]))
            x, y = pts[-1]
            dots.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s"/>' % (x, y, COLORS[k]))
    if bottom_key and b_lo is not None:
        pts = series_pts(bottom_key, b_lo, b_hi, bot_y, bot_h)
        if pts:
            polylines.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2.5"/>'
                             % (" ".join("%.1f,%.1f" % (x, y) for x, y in pts), COLORS[bottom_key]))
            x, y = pts[-1]
            dots.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s"/>' % (x, y, COLORS[bottom_key]))
    parts.append("".join(polylines) + "".join(dots))

    legend_keys = [left_key] + right_keys + ([bottom_key] if bottom_key else [])
    legend = ('<div class="legendbox">%s</div>' % "".join(
        '<span class="legend-dot" style="background:%s"></span><span class="legend-name">%s</span>'
        % (COLORS[k], LABELS[k]) for k in legend_keys))

    cols = legend_keys
    header = ('<div class="chart-title">%s</div>'
              '<div class="chart-sub">%s &nbsp;·&nbsp; 最新采样：%s</div>'
              % (title, subtitle, points[-1].get("date", "")))
    svg = ('<svg viewBox="0 0 %d %d" class="zoomable" onclick="openZoom(this.closest(\'.chart-card\'))">'
           '<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>'
           '%s</svg>'
           % (W, H, W, H, "".join(parts)))

    def cell(p, k):
        v = p.get(k)
        return ("%.3f" % v) if v is not None else "—"
    rows = []
    for p in reversed(points[-10:]):
        rows.append("<tr><td>%s</td>%s</tr>"
                    % (p.get("date", ""), "".join("<td>%s</td>" % cell(p, k) for k in cols)))
    th = "".join("<th>%s</th>" % TAB_LABELS.get(k, LABELS[k]) for k in cols)
    table = ('<table><tr><th>日期</th>%s</tr>%s</table>' % (th, "".join(rows)))

    return '<div class="chart-card">%s%s%s%s</div>' % (header, legend, svg, table)


def render_chart(points, title, subtitle, n_target, x_when, xlab_key="ts"):
    """图表二/三：单轴多条美债利率折线。points=[{ts, us2y, us5y, ...}]"""
    all_vals = [p[k] for p in points for k in KEYS if p.get(k) is not None]
    lo, hi = min(all_vals), max(all_vals)
    ylo, yhi = _axis(lo, hi)

    W, H, L, R, T, B = 1200, 620, 70, 40, 40, 55
    iw, ih = W - L - R, H - T - B

    grid = []
    for slot in (0, 10, 20, 30, 40, 50, 59):
        sx = _px(slot, L, iw, n_target)
        grid.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#dde6f0" stroke-dasharray="2,4"/>'
                    % (sx, T, sx, T + ih))
    for g in range(6):
        v = ylo + (yhi - ylo) * g / 5
        gy = _py(v, ylo, yhi, T, ih)
        grid.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e8e8e8"/>'
                    % (L, gy, W - R, gy))
        grid.append('<text x="%d" y="%.1f" class="ylab">%.3f</text>' % (L - 8, gy + 4, v))

    xlab = []
    for idx in (0, len(points) // 2, len(points) - 1):
        lab = x_when(points[idx][xlab_key])
        xlab.append('<text x="%.1f" y="%d" class="xlab">%s</text>'
                    % (_px(idx, L, iw, n_target), H - B + 20, lab))

    polylines, dots = [], []
    for k in KEYS:
        pts = _scale_pts(points, k, ylo, yhi, L, iw, T, ih, n_target)
        if not pts:
            continue
        polylines.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2.5"/>'
                         % (" ".join("%.1f,%.1f" % (x, y) for x, y in pts), COLORS[k]))
        x, y = pts[-1]
        dots.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s"/>' % (x, y, COLORS[k]))

    header = ('<div class="chart-title">%s</div>'
              '<div class="chart-sub">%s &nbsp;·&nbsp; 最新采样：%s</div>'
              % (title, subtitle, points[-1].get(xlab_key, "")))
    svg = ('<svg viewBox="0 0 %d %d" class="zoomable" onclick="openZoom(this.closest(\'.chart-card\'))">'
           '<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>'
           '%s%s%s%s</svg>'
           % (W, H, W, H, "".join(grid), "".join(xlab), "".join(polylines), "".join(dots)))

    rows = []
    for p in reversed(points[-10:]):
        tds = "".join("<td>%s</td>" % (("%.3f" % p[k]) if p.get(k) is not None else "—") for k in KEYS)
        rows.append("<tr><td>%s</td>%s</tr>" % (p.get(xlab_key, ""), tds))
    th_cells = "".join("<th>%s(%%)</th>" % LABELS[k] for k in KEYS)
    table = ('<table><tr><th>采样时间(北京时间)</th>%s</tr>%s</table>'
             % (th_cells, "".join(rows)))

    return '<div class="chart-card">%s%s%s</div>' % (header, svg, table)


def _when_day(ts):
    return ts[5:10]


def _when_intra(ts):
    return "%s %s" % (ts[5:10], ts[11:16])


def _when_date(date):
    return date[5:10]


CSS = """<style>
 body{font-family:-apple-system,'Segoe UI','PingFang SC',Microsoft YaHei,sans-serif;background:#f4f7fb;color:#1c2b3a;margin:0;padding:24px}
 .wrap{max-width:1280px;margin:0 auto}
 h1{font-size:22px;margin:0 0 4px}.sub{color:#7a8ca0;font-size:13px;margin-bottom:18px}
 .chart-card{background:#fff;border-radius:10px;box-shadow:0 1px 6px rgba(20,40,80,.08);padding:22px 26px;margin-bottom:22px}
 .chart-title{font-size:16px;font-weight:700;margin-bottom:2px}
 .chart-sub{color:#7a8ca0;font-size:12px;margin-bottom:10px}
 .rate-cards-label{color:#7a8ca0;font-size:12px;margin:0 2px 8px}
 .rate-cards{display:flex;gap:14px;margin-bottom:20px}
 .rate-card{flex:1;background:#fff;border-radius:10px;box-shadow:0 1px 6px rgba(20,40,80,.08);padding:16px 20px;border-left:4px solid #eef2f6}
 .rate-head{display:flex;align-items:center;gap:8px;font-size:13px;color:#7a8ca0;font-weight:600}
 .rate-ico{width:20px;height:20px;border-radius:6px;display:inline-block}
 .rate-val{font-size:34px;font-weight:800;margin-top:10px;line-height:1.1;letter-spacing:.5px}
 .rate-val b{font-size:14px;font-weight:600;margin-left:2px}
 .legendbox{padding-top:2px;padding-bottom:2px}
 .legend-dot{display:inline-block;width:14px;height:14px;border-radius:4px;margin:0 6px 0 18px;vertical-align:middle}
 .legend-name{font-size:13px;vertical-align:middle;color:#1c2b3a;font-weight:600}
 .legend-dot:first-child{margin-left:0}
 .zoomable{cursor:zoom-in}
 .zoom-overlay{position:fixed;inset:0;background:rgba(10,18,30,.88);z-index:999;display:none;align-items:center;justify-content:center;padding:8px}
 .zoom-overlay.open{display:flex}
 .zoom-box{background:#fff;border-radius:14px;padding:22px;width:min(1500px,100vw);max-height:calc(100vh - 16px);box-sizing:border-box;overflow:auto;position:relative;box-shadow:0 8px 40px rgba(0,0,0,.4);display:flex;flex-direction:column}
 #zoomBox:fullscreen{width:100vw;height:100dvh;padding:0;background:#000;display:flex}
 #zoomBox:-webkit-full-screen{width:100vw;height:100dvh;padding:0;background:#000;display:flex}
 .zoom-box svg{width:100%%;height:auto;margin:auto}
 .zoom-close{position:sticky;top:0;align-self:flex-end;cursor:pointer;font-size:20px;line-height:1;color:#fff;background:#c0392b;border:none;border-radius:8px;width:34px;height:34px;display:flex;align-items:center;justify-content:center;flex:none;margin:-6px -6px 6px 6px;z-index:10}
 .zoom-title{font-size:16px;font-weight:700;margin:0 0 12px;padding-right:44px}
 @media(max-width:768px),(max-height:600px) and (orientation:landscape){
   body{padding:8px}
   .sub{font-size:12px;line-height:1.6;margin-bottom:12px}
   h1{font-size:19px}
   .chart-card{padding:14px 10px;margin-bottom:14px}
   .rate-cards{gap:8px;margin-bottom:12px}
   .rate-card{padding:12px 12px;border-left-width:3px}
   .rate-ico{width:18px;height:18px;border-radius:5px}
   .rate-head{font-size:12px}
   .rate-val{font-size:24px;margin-top:8px}
   table{font-size:12px}
   th,td{padding:6px 6px}
   .legend-dot{width:12px;height:12px;margin:0 4px 0 10px}
   .legend-name{font-size:12px}
   .zoom-overlay{background:#000;padding:0}
   .zoom-box{width:100vw;height:100dvh;max-height:100dvh;border-radius:0;padding:0;display:block;position:relative;overflow:hidden;background:#000;box-shadow:none}
   .zoom-box #zoomContent{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;overflow:hidden;margin:0}
   .zoom-box svg{width:auto;height:auto;max-width:100%%;max-height:100%%;margin:0;display:block}
   .zoom-close{position:absolute;top:12px;right:12px;z-index:6;background:rgba(0,0,0,.55);margin:0;border-radius:50%%;width:36px;height:36px}
   .zoom-title{position:absolute;top:16px;left:16px;right:60px;z-index:5;font-size:14px;color:#fff;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-shadow:0 1px 3px rgba(0,0,0,.6)}
 }
 svg{width:100%%;height:auto;display:block}
 .ylab{font-size:11px;fill:#7a8ca0}.xlab{font-size:10px;fill:#7a8ca0;text-anchor:middle}
 table{width:100%%;border-collapse:collapse;margin-top:14px;font-size:13px}
 th,td{border-bottom:1px solid #eef2f6;padding:7px 10px;text-align:left}
 th{color:#7a8ca0;font-weight:600}
 .analysis{background:#fff;border-radius:10px;box-shadow:0 1px 6px rgba(20,40,80,.08);padding:20px 26px;margin-bottom:20px;border-left:4px solid #8e44ad}
 .analysis-title{font-size:19px;font-weight:700;margin-bottom:12px;color:#1c2b3a}
 .analysis-body{font-size:16px;line-height:1.9;color:#2c3e50}
 .analysis-body p{margin:10px 0}
 .analysis-body b{color:#1c2b3a}
 .rate-link{display:inline-block;margin-top:10px;font-size:13px;color:#2b6cb0;text-decoration:none;border-bottom:1px dashed #a8c6ef;cursor:pointer}
 .rate-link:hover{color:#1a4f8a}
 .hint{color:#9aa9ba;font-size:12px;margin-top:14px}
</style>"""

JS = """<script>
function openZoom(el){
  var svg=el.querySelector('.zoomable');
  if(!svg)return;
  document.getElementById('zoomTitle').textContent=el.querySelector('.chart-title').textContent;
  document.getElementById('zoomContent').innerHTML=svg.outerHTML.replace(/ on[a-z]+="[^"]*"/gi,'');
  document.getElementById('zoomBox').classList.add('open');
}
function closeZoom(){
  var b=document.getElementById('zoomBox');
  b.classList.remove('open');
  if(document.fullscreenElement||document.webkitFullscreenElement){
    try{(document.exitFullscreen||document.webkitExitFullscreen).call(document);}catch(e){}
  }
}
function requestFs(el){
  try{if(el.requestFullscreen){return el.requestFullscreen();}
      if(el.webkitRequestFullscreen){return el.webkitRequestFullscreen();}}
  catch(e){}
  return Promise.reject();
}
function openZoom(el){
  var svg=el.querySelector('.zoomable');
  if(!svg)return;
  document.getElementById('zoomTitle').textContent=el.querySelector('.chart-title').textContent;
  document.getElementById('zoomContent').innerHTML=svg.outerHTML.replace(/ on[a-z]+="[^"]*"/gi,'');
  var b=document.getElementById('zoomBox');
  b.classList.add('open');
  var isMobile=window.matchMedia&&window.matchMedia('(max-width:768px),(max-height:600px) and (orientation:landscape)').matches;
  if(isMobile){requestFs(b).catch(function(){});}
}
document.getElementById('zoomBox').addEventListener('click',function(e){if(e.target===this)closeZoom();});
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeZoom();});
document.addEventListener('fullscreenchange',function(){if(!document.fullscreenElement)document.getElementById('zoomBox').classList.remove('open');});
document.addEventListener('webkitfullscreenchange',function(){if(!document.webkitFullscreenElement)document.getElementById('zoomBox').classList.remove('open');});
</script>"""

ANALYSIS = """<div class="analysis"><div class="analysis-title">📊 市场解读</div>
<div class="analysis-body">
<p><b>🥇 伦敦金：</b>最新收盘 <b>4171.60 美元/盎司</b>（10-12）。金价在 4100–4195 区间高位震荡，10-12 收于 4171 附近，整体仍处历史高位区，反映避险与美元/实际利率博弈。</p>
<p><b>📌 美债期限结构：</b>2Y <b>4.80%</b> / 5Y 5.02% / 10Y <b>5.24%</b> / 30Y 5.60%，呈“短端平稳、长端强”的<b>熊陡(bear steepening)</b>形态——长端(10Y/30Y)被期限溢价推高，反映财政赤字与长期通胀担忧。</p>
<p><b>🔍 驱动因素：</b>短端(≤2Y)未大幅上行→非加息/流动性紧缩驱动；主导因素是<b>期限溢价</b>。30Y 5.60% 高位，超长端买盘偏弱。</p>
<p><b>💧 对流动性：</b>高长端利率→全球资金增配美元资产、融资成本升/去杠杆、美元偏强、新兴市场承压。</p>
<p><b>📈 对股市：</b>结构性压高估值成长，风格偏向价值/红利/银行。</p>
<p><b>⚠️ 关注点：</b>金价与实际利率、美元方向高度相关——若短端转强(全面走强、实际利率上行)→黄金承压；若长端上行源于财政/通胀担忧(实际利率未升)→黄金偏多支撑。</p>
</div></div>"""

ANALYSIS_CACHE = os.path.join(BASE, "analysis_cache.json")


def _fmtpct(v, n=3):
    return ("%.*f%%" % (n, v)) if v is not None else "—"


def _delta(a, b, unit="", n=2):
    """返回 (差值文本, 方向符号)；a=最新 b=前值。"""
    if a is None or b is None:
        return "", "→"
    d = a - b
    if abs(d) < 1e-9:
        return "持平", "→"
    sign = "+" if d > 0 else ""
    arrow = "↑" if d > 0 else "↓"
    return ("%s%.*f%s" % (sign, n, d, unit)), arrow


def build_analysis(track, close):
    """基于最新数据自动生成「市场解读」HTML（仅收盘采样时生成，每日一次）。"""
    if not track:
        return ANALYSIS
    latest = track[-1]
    prev = track[-2] if len(track) > 1 else {}
    d = latest.get("date", "")
    cl = close[-1] if close else {}
    cp = close[-2] if len(close) > 1 else {}

    # —— 伦敦金 ——
    gold = latest.get("gold")
    gtxt, garrow = _delta(gold, prev.get("gold"), "")
    gold_line = "<p><b>🥇 伦敦金：</b>最新收盘 <b>%s 美元/盎司</b>（%s）" % (
        ("%.2f" % gold) if gold is not None else "—", d)
    if gtxt:
        _gp = prev.get("gold")
        _pct = ((gold - _gp) / _gp * 100.0) if (_gp and gold is not None) else None
        gold_line += "，较前一交易日 <b>%s</b>%s" % (
            gtxt, ("（%+.2f%%）" % _pct) if _pct is not None else "")
    gold_line += "。</p>"

    # —— 布伦特石油（取最近一个有值的记录） ——
    b_now = latest.get("brent")
    b_prev = None
    for s in reversed(track[:-1]):
        if s.get("brent") is not None:
            b_prev = s.get("brent")
            break
    oil_line = ""
    if b_now is not None:
        btxt, _ = _delta(b_now, b_prev, "")
        oil_line = "<p><b>🛢️ 布伦特石油：</b>最新收盘 <b>%.2f 美元/桶</b>%s。</p>" % (
            b_now, ("，较前值 " + btxt) if btxt else "")
    else:
        _last_b, _last_bd = None, None
        for s in reversed(track):
            if s.get("brent") is not None:
                _last_b, _last_bd = s.get("brent"), s.get("date")
                break
        if _last_b is not None:
            oil_line = ("<p><b>🛢️ 布伦特石油：</b>最新可得收盘 <b>%.2f 美元/桶</b>（%s），"
                        "当日价待次日凌晨入库后自动回填。</p>") % (_last_b, _last_bd)
        else:
            oil_line = "<p><b>🛢️ 布伦特石油：</b>本日收盘价尚未入库（数据源延迟），将在后续采集点自动回填。</p>"

    # —— 美债期限结构 + 形态判别 ——
    u2, u5, u10, u30 = cl.get("us2y"), cl.get("us5y"), cl.get("us10y"), cl.get("us30y")
    d2 = (u2 - cp.get("us2y")) if (u2 is not None and cp.get("us2y") is not None) else None
    d5 = (u5 - cp.get("us5y")) if (u5 is not None and cp.get("us5y") is not None) else None
    d10 = (u10 - cp.get("us10y")) if (u10 is not None and cp.get("us10y") is not None) else None
    d30 = (u30 - cp.get("us30y")) if (u30 is not None and cp.get("us30y") is not None) else None

    # 形态判别：短端(2Y) vs 长端(10Y/30Y) 变化方向与幅度
    shape, shape_desc, driver, stock_view, gold_view = "窄幅震荡", "各期限变动幅度有限", \
        "多空因素交织，缺乏明确方向性驱动", "风格轮动暂缓，维持现有结构", "短线跟随美元与实际利率波动"
    if None not in (d2, d10):
        long_up = (d10 > 0.005) and (d30 is None or d30 > -0.005)
        long_dn = (d10 < -0.005) and (d30 is None or d30 < 0.005)
        short_up = d2 > 0.005
        short_dn = d2 < -0.005
        if long_up and abs(d2) <= 0.02 and d10 > 0.02:
            shape, shape_desc = "熊陡(bear steepening)", "短端平稳、长端独自走强"
            driver = "期限溢价抬升（财政赤字/长期通胀担忧），而非加息预期驱动"
            stock_view = "结构性压高估值成长，资金偏向价值/红利/银行"
            gold_view = "长端上行若源于财政/通胀担忧（实际利率未同步抬升）→ 黄金偏多支撑"
        elif short_up and long_up and d2 > d10:
            shape, shape_desc = "熊平(bear flattening)", "短端上行快于长端"
            driver = "加息/紧缩预期升温，短端对政策最敏感"
            stock_view = "全面压估值，流动性收紧，双杀风险更大"
            gold_view = "实际利率抬升 → 黄金承压"
        elif short_up and long_up:
            shape, shape_desc = "平行上移", "整条曲线同步上行"
            driver = "货币政策收紧与流动性收缩预期"
            stock_view = "系统性压估值，成长股承压更明显"
            gold_view = "实际利率走高 → 黄金承压"
        elif short_dn and long_dn:
            shape, shape_desc = "整体下移", "整条曲线同步回落"
            driver = "降息预期升温或避险买盘推动债市走强"
            stock_view = "估值压力缓解，成长风格相对受益"
            gold_view = "实际利率回落 → 黄金偏多"
        elif short_dn and long_up:
            shape, shape_desc = "牛陡(bull steepening)", "短端下行、长端上行"
            driver = "短端定价宽松、长端担忧财政，曲线两端分化"
            stock_view = "风格分化加剧，长久期成长与价值各有压力"
            gold_view = "长端财政担忧占优时，黄金偏多"

    def _d(v):
        return "—" if v is None else ("%+.0fbp" % (v * 100))
    term_line = ("<p><b>📌 美债期限结构：</b>2Y <b>%s</b>%s / 5Y %s%s / 10Y <b>%s</b>%s / 30Y %s%s，"
                 "呈<b>%s</b>形态——%s。</p>") % (
        _fmtpct(u2), ("(" + _d(d2) + ")") if d2 is not None else "",
        _fmtpct(u5), ("(" + _d(d5) + ")") if d5 is not None else "",
        _fmtpct(u10), ("(" + _d(d10) + ")") if d10 is not None else "",
        _fmtpct(u30), ("(" + _d(d30) + ")") if d30 is not None else "",
        shape, shape_desc)

    # —— 联动观察 ——
    link = ""
    if gold is not None and prev.get("gold") is not None and d10 is not None:
        g_up = gold > prev.get("gold")
        if g_up and d10 > 0.005:
            link = "<p><b>🔗 联动观察：</b>金价与长端收益率同步上行——若由通胀/财政担忧主导，两者可同向；若实际利率抬升，则金价后续承压。</p>"
        elif (not g_up) and d10 > 0.005:
            link = "<p><b>🔗 联动观察：</b>金价回落而长端收益率上行，符合「利率上行压制金价」的负相关逻辑。</p>"
        elif g_up and d10 < -0.005:
            link = "<p><b>🔗 联动观察：</b>金价上行与收益率回落同向，指向避险情绪升温、宽松预期回暖。</p>"
        else:
            link = "<p><b>🔗 联动观察：</b>金价与收益率方向一致向下，债市偏强、风险偏好边际回落。</p>"

    html = ("<div class=\"analysis\"><div class=\"analysis-title\">📊 市场解读</div>\n"
            "<div class=\"analysis-body\">\n%s\n%s\n%s\n"
            "<p><b>🔍 驱动因素：</b>%s。</p>\n"
            "%s\n"
            "<p><b>📈 对股市：</b>%s。</p>\n"
            "<p><b>⚠️ 对黄金：</b>%s。</p>\n"
            "<p><b>💧 对流动性：</b>高长端利率→全球资金增配美元资产、融资成本上升、美元偏强、新兴市场承压。</p>\n"
            "</div></div>") % (gold_line, oil_line, term_line, driver, link, stock_view, gold_view)
    return html


def load_analysis_cache():
    """读取缓存的每日市场解读（仅 06:00 收盘采样时刷新）。"""
    try:
        j = json.load(open(ANALYSIS_CACHE, encoding="utf-8"))
        if j.get("html"):
            return j["html"]
    except Exception:
        pass
    return ANALYSIS


def save_analysis_cache(html):
    try:
        json.dump({"date": today_str(), "html": html}, open(ANALYSIS_CACHE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception as e:
        print("WARN analysis cache save: %s" % e, file=sys.stderr)


def render(close_pts, intra_pts, track_pts, analysis_html=None):
    cpts = close_pts[-60:]
    ipts = intra_pts[-60:]
    tpts = track_pts[-90:]
    if not cpts and not ipts and not tpts:
        return "<html><body><h2>暂无数据</h2></body></html>"

    head = ("<h1>国债和黄金石油价格跟踪报告</h1>"
            "<div class='sub'>美债 2Y / 5Y / 10Y / 30Y ＋ 欧盟 2Y / 10Y ＋ 伦敦金 ＋ 布伦特石油 ＋ 主要汇率<br>"
            "图表一：伦敦金+美/欧 2Y/10Y（每图下方附图含布伦特石油）<b>（90采样点，每日更新）</b> &nbsp;·&nbsp; "
            "图表二：伦敦金+主要汇率（每图下方附图含布伦特石油）<b>（90采样点，每工作日更新）</b> &nbsp;·&nbsp; "
            "图表三：美债每日收盘 &nbsp;·&nbsp; "
            "图表四：盘中每3小时（北京 23:00/02:00/05:00）</div>")

    # 顶部最新收盘卡片：伦敦金 + 美债各期限
    rate_cards_html = ""
    latest_close = cpts[-1] if cpts else None
    latest_track = tpts[-1] if tpts else None
    cards = []
    if latest_track and latest_track.get("gold") is not None:
        v = latest_track["gold"]
        cards.append(
            '<div class="rate-card" style="border-left-color:%s">'
            '<div class="rate-head"><span class="rate-ico" style="background:%s"></span>伦敦金</div>'
            '<div class="rate-val" style="color:%s">%.2f <b>美元/盎司</b></div>'
            '<a class="rate-link" href="%s" target="_blank" rel="noopener">行情详情 &nbsp;↗</a>'
            '</div>' % (COLORS["gold"], COLORS["gold"], COLORS["gold"], v, LINKS["gold"]))
    if latest_close:
        for k in KEYS:
            v = latest_close.get(k)
            txt = ("%.3f%%" % v) if v is not None else "—"
            c = COLORS[k]
            cards.append(
                '<div class="rate-card" style="border-left-color:%s">'
                '<div class="rate-head"><span class="rate-ico" style="background:%s"></span>%s</div>'
                '<div class="rate-val" style="color:%s">%s</div>'
                '<a class="rate-link" href="%s" target="_blank" rel="noopener">行情详情 &nbsp;↗</a>'
                '</div>' % (c, c, LABELS[k], c, txt, LINKS[k]))
    if cards:
        rate_cards_html = ('<div class="rate-cards-label">最新收盘价&nbsp;·&nbsp;伦敦金（每交易日）&nbsp;/&nbsp;美债（北京 06:00 采样）</div>'
                           '<div class="rate-cards">%s</div>' % "".join(cards))

    charts = "".join(filter(None, [
        render_dual(tpts, "gold", ["us2y", "us10y", "eu2y", "eu10y"], None,
                    "图表一 · 伦敦金与美/欧国债短长端每日收盘价",
                    "上图：伦敦金(左轴·美元/盎司) + 美/欧 2Y / 10Y(右轴·%) &nbsp;下图：布伦特石油主力(美元/桶,独立刻度) &nbsp;·&nbsp; 上下两图共享同一X轴（90 个交易日采样，每日更新）",
                    90, _when_date, "伦敦金（美元/盎司）", "债券收益率（%）", "%.2f%%",
                    bottom_key="brent") if tpts else None,
        render_dual(tpts, "gold", ["cny", "eur", "jpy"], {"jpy": 150, "cny": 6},
                    "图表二 · 主要汇率走向与伦敦金每日收盘价",
                    "上图：伦敦金(左轴·美元/盎司) + 美元兑人民币(÷6)/美元兑欧元/美元兑日元(÷150)(右轴) &nbsp;下图：布伦特石油主力(美元/桶,独立刻度) &nbsp;·&nbsp; 上下两图共享同一X轴（90 个交易日采样，每工作日更新）",
                    90, _when_date, "伦敦金（美元/盎司）", "汇率（美元兑X）", "%.2f",
                    bottom_key="brent") if tpts else None,
        render_chart(cpts, "图表三 · 美债每日收盘价走势",
                     "每美股交易日收盘价 · 横坐标 60 个交易日收盘采样", 60, _when_day) if cpts else None,
        render_chart(ipts, "图表四 · 盘中每3小时利率走势",
                     "交易日（北京 23:00 / 02:00 / 05:00）· 横坐标 60 个采样点（约 20 个交易日）", 60, _when_intra) if ipts else None,
    ]))

    body = (analysis_html or ANALYSIS) + rate_cards_html + charts

    return ("<!DOCTYPE html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>国债和黄金石油价格跟踪报告</title>"
            + CSS + "</head><body><div class=\"wrap\">%s\n%s\n"
            "<div class=\"hint\">数据来源：同花顺问财；图表一按交易日每日更新（伦敦金周末休市）；盘中免费源受限，同一交易日内每3小时采样值可能保持不变。</div>"
            "<div id=\"zoomBox\" class=\"zoom-overlay\"><div class=\"zoom-box\">"
            "<button class=\"zoom-close\" onclick=\"closeZoom()\" aria-label=\"关闭\">&#10005;</button>"
            "<div id=\"zoomTitle\" class=\"zoom-title\"></div><div id=\"zoomContent\"></div></div></div>"
            + JS + "</div></body></html>") % (head, body)


def _upsert_track(track, date, sample):
    """按 date 去重：同一天已存在则更新，否则追加。返回更新后的列表。"""
    for i, s in enumerate(track):
        if s.get("date") == date:
            track[i] = {**s, **{k: v for k, v in sample.items() if v is not None}}
            return track
    track.append(sample)
    return track


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    now = datetime.datetime.now()
    kind = sample_kind(now)

    if kind is None:
        print("当前时刻非美债盘中/收盘采集点（休市或开盘不采集），跳过抓取，仅渲染页面")
        close = load_data(CLOSE); intra = load_data(INTRADAY); track = load_data(TRACK)
        with open(HTML, "w", encoding="utf-8") as f:
            f.write(render(close, intra, track, load_analysis_cache()))
        print("rendered(existing) -> %s | close=%d intraday=%d track=%d"
              % (HTML, len(close), len(intra), len(track)))
        return

    fetched = fetch_now()
    if not any(v is not None for v in fetched.values()):
        print("抓取失败，无有效数据，跳过")
        return

    # 布伦特石油：每次运行都尝试抓取并回填历史缺口
    # （问财收盘价入库滞后于北京 06:00 采集点，靠后续采集点自动补当日值）
    brent_full = fetch_brent_series()
    if brent_full:
        _tr = load_data(TRACK)
        _filled = 0
        for s in _tr:
            _d = s.get("date")
            if _d and _d in brent_full and s.get("brent") is None:
                s["brent"] = brent_full[_d]
                _filled += 1
        if _filled:
            save_data(_tr, TRACK)
            print("brent 回填 %d 条" % _filled)
        if today_str() in brent_full:
            fetched["brent"] = brent_full[today_str()]
    else:
        print("WARN brent 未抓到收盘价（数据源可能尚未入库，后续采集点将自动回填）", file=sys.stderr)

    if kind == "close":
        # 收盘：美债写 close_data（图表二）；同时伦敦金+2Y+10Y 写 track_daily（图表一每日更新）
        close = load_data(CLOSE)
        sample = {"ts": now_str()}
        for k in KEYS:
            if fetched.get(k) is not None:
                sample[k] = fetched[k]
        if close and close[-1].get("ts") == sample["ts"]:
            print("same minute, skip close append")
        else:
            close.append(sample)
            save_data(close, CLOSE)

        track = load_data(TRACK)
        tdate = today_str()
        tsample = {"date": tdate}
        for k in ("gold", "eu2y", "eu10y", "us2y", "us10y", "cny", "eur", "jpy", "brent"):
            if fetched.get(k) is not None:
                tsample[k] = fetched[k]
        miss = [k for k in ("cny", "eur", "jpy") if fetched.get(k) is None]
        if miss:
            print("WARN close 外汇缺失: %s -> 图表二当日汇率未更新，请核查问财返回字段/网络" % ",".join(miss),
                  file=sys.stderr)
        track = _upsert_track(track, tdate, tsample)
        save_data(track, TRACK)
        # 市场解读：仅收盘采样（每日 06:00）时重新生成并缓存
        try:
            save_analysis_cache(build_analysis(track, close))
            print("analysis cache updated for %s" % tdate)
        except Exception as e:
            print("WARN analysis build: %s" % e, file=sys.stderr)

    else:  # intraday
        intra = load_data(INTRADAY)
        sample = {"ts": now_str()}
        for k in KEYS:
            if fetched.get(k) is not None:
                sample[k] = fetched[k]
        if intra and intra[-1].get("ts") == sample["ts"]:
            print("same minute, skip intraday append")
        else:
            intra.append(sample)
            save_data(intra, INTRADAY)

    close = load_data(CLOSE); intra = load_data(INTRADAY); track = load_data(TRACK)
    with open(HTML, "w", encoding="utf-8") as f:
        f.write(render(close, intra, track, load_analysis_cache()))
    print("rendered -> %s | close=%d intraday=%d track=%d | kind=%s latest gold=%.2f 2Y=%.3f 10Y=%.3f"
          % (HTML, len(close), len(intra), len(track), kind,
             fetched.get("gold", 0) or 0, fetched.get("us2y", 0) or 0, fetched.get("us10y", 0) or 0))


if __name__ == "__main__":
    main()
