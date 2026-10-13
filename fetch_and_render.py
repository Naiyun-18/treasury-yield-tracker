#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
各国国债和黄金价格追踪分析报告 - fetch US treasury yields + London gold close + render HTML.

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
import os, json, sys, subprocess, datetime

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
           "gold": "伦敦金现货"}
KEYS = ["us2y", "us5y", "us10y", "us30y"]
TRACKKEYS = ["gold", "us2y", "us10y", "eu2y", "eu10y"]
LABELS = {"us2y": "2年期", "us5y": "5年期", "us10y": "10年期", "us30y": "30年期",
          "eu2y": "欧盟2Y", "eu10y": "欧盟10Y", "gold": "伦敦金"}
COLORS = {"us2y": "#27ae60", "us5y": "#2471a3", "us10y": "#e67e22", "us30y": "#c0392b",
          "eu2y": "#2e86c1", "eu10y": "#8e44ad", "gold": "#d4af37"}
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


def fetch_now():
    """Fetch all 5 series from iwencai. Returns dict {us2y, us5y, us10y, us30y, gold}."""
    api_key = _api_key()
    out = {}
    for k, q in QUERIES.items():
        val = None
        try:
            cmd = [sys.executable, CLI, "--query", q, "--timeout", "40"]
            if api_key:
                cmd += ["--api-key", api_key]
            r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ))
            j = json.loads(r.stdout)
            if j.get("datas"):
                val = float(j["datas"][0]["指标值"])
        except Exception as e:
            print("ERR %s: %s" % (k, e), file=sys.stderr)
        out[k] = val
    return out


def load_data(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_data(path, data):
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


def render_combined(points, title, subtitle, n_target, x_when):
    """图表一：伦敦金(左轴) + 2Y/10Y(右轴) 双轴折线。points=[{date,gold,us2y,us10y},...]"""
    gvals = [p["gold"] for p in points if p.get("gold") is not None]
    yvals = [p[k] for p in points for k in ("us2y", "us10y", "eu2y", "eu10y") if p.get(k) is not None]
    g_lo, g_hi = _axis(min(gvals), max(gvals)) if gvals else (4000, 4400)
    y_lo, y_hi = _axis(min(yvals), max(yvals)) if yvals else (4.7, 5.4)

    W, H, L, R, T, B = 1200, 620, 80, 70, 40, 55
    iw, ih = W - L - R, H - T - B

    grid = []
    for slot in (0, 15, 30, 45, 60, 75, 89):
        sx = _px(slot, L, iw, n_target)
        grid.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#dde6f0" stroke-dasharray="2,4"/>'
                    % (sx, T, sx, T + ih))
    # 左轴刻度(黄金) + 右侧刻度(利率)
    for g in range(6):
        glab = g_lo + (g_hi - g_lo) * g / 5
        gy = _py(glab, g_lo, g_hi, T, ih)
        grid.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#ececec"/>'
                    % (L, gy, W - R, gy))
        grid.append('<text x="%d" y="%.1f" class="ylab" text-anchor="end">%.0f</text>'
                    % (L - 8, gy + 4, glab))
        ylab = y_lo + (y_hi - y_lo) * g / 5
        yy = _py(ylab, y_lo, y_hi, T, ih)
        grid.append('<text x="%d" y="%.1f" class="ylab">%.2f%%</text>'
                    % (W - R + 10, yy + 4, ylab))

    # 轴标题
    ax = ''.join([
        '<text x="%d" y="16" class="ylab" text-anchor="middle">伦敦金（美元/盎司）</text>' % (L + iw // 2),
        '<text x="%d" y="16" class="ylab" text-anchor="end">债券收益率（%%）</text>' % (W - R - 6),
    ])

    xlab = []
    idxs = sorted(set([0, len(points) // 2, len(points) - 1]))
    for idx in idxs:
        lab = x_when(points[idx].get("date", ""))
        xlab.append('<text x="%.1f" y="%d" class="xlab">%s</text>'
                    % (_px(idx, L, iw, n_target), H - B + 20, lab))

    line_specs = [("gold", g_lo, g_hi), ("us2y", y_lo, y_hi), ("us10y", y_lo, y_hi),
                  ("eu2y", y_lo, y_hi), ("eu10y", y_lo, y_hi)]
    polylines, dots = [], []
    for key, ylo, yhi in line_specs:
        pts = _scale_pts(points, key, ylo, yhi, L, iw, T, ih, n_target)
        if not pts:
            continue
        polylines.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2.5"/>'
                         % (" ".join("%.1f,%.1f" % (x, y) for x, y in pts), COLORS[key]))
        x, y = pts[-1]
        dots.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s"/>' % (x, y, COLORS[key]))

    legend = ('<div class="legendbox">%s</div>' % "".join(
        '<span class="legend-dot" style="background:%s"></span><span class="legend-name">%s</span>'
        % (COLORS[k], LABELS[k]) for k in ("gold", "us2y", "us10y", "eu2y", "eu10y")))

    header = ('<div class="chart-title">%s</div>'
              '<div class="chart-sub">%s &nbsp;·&nbsp; 最新采样：%s</div>'
              % (title, subtitle, points[-1].get("date", "")))
    svg = ('<svg viewBox="0 0 %d %d" class="zoomable" onclick="openZoom(this.closest(\'.chart-card\'))">'
           '<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>'
           '%s%s%s%s%s</svg>'
           % (W, H, W, H, ax, "".join(grid), "".join(xlab), "".join(polylines), "".join(dots)))

    rows = []
    for p in reversed(points[-10:]):
        def cell(k):
            v = p.get(k)
            return ("%.3f" % v) if v is not None else "—"
        rows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (p.get("date", ""), cell("gold"), cell("us2y"), cell("us10y"),
                       cell("eu2y"), cell("eu10y")))
    table = ('<table><tr><th>日期</th><th>伦敦金(美元/盎司)</th><th>2Y美债(%%)</th><th>10Y美债(%%)</th>'
             '<th>2Y欧盟(%%)</th><th>10Y欧盟(%%)</th></tr>%s</table>'
             % "".join(rows))

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


def render(close_pts, intra_pts, track_pts):
    cpts = close_pts[-60:]
    ipts = intra_pts[-60:]
    tpts = track_pts[-90:]
    if not cpts and not ipts and not tpts:
        return "<html><body><h2>暂无数据</h2></body></html>"

    head = ("<h1>各国国债和黄金价格追踪分析报告</h1>"
            "<div class='sub'>美债 2Y / 5Y / 10Y / 30Y ＋ 欧盟 2Y / 10Y ＋ 伦敦金<br>"
            "图表一：伦敦金+美/欧 2Y/10Y 每日收盘 <b>（90采样点，每日更新）</b> &nbsp;·&nbsp; "
            "图表二：美债每日收盘 &nbsp;·&nbsp; "
            "图表三：盘中每3小时（北京 23:00/02:00/05:00）</div>")

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
        render_combined(tpts, "图表一 · 伦敦金与美/欧国债短长端每日收盘价",
                        "伦敦金(左轴·美元/盎司) + 美/欧 2Y / 10Y(右轴·%) · 横坐标 90 个交易日采样（每日更新）",
                        90, _when_date) if tpts else None,
        render_chart(cpts, "图表二 · 美债每日收盘价走势",
                     "每美股交易日收盘价 · 横坐标 60 个交易日收盘采样", 60, _when_day) if cpts else None,
        render_chart(ipts, "图表三 · 盘中每3小时利率走势",
                     "交易日（北京 23:00 / 02:00 / 05:00）· 横坐标 60 个采样点（约 20 个交易日）", 60, _when_intra) if ipts else None,
    ]))

    body = ANALYSIS + rate_cards_html + charts

    return ("<!DOCTYPE html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>各国国债和黄金价格追踪分析报告</title>"
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
            f.write(render(close, intra, track))
        print("rendered(existing) -> %s | close=%d intraday=%d track=%d"
              % (HTML, len(close), len(intra), len(track)))
        return

    fetched = fetch_now()
    if not any(v is not None for v in fetched.values()):
        print("抓取失败，无有效数据，跳过")
        return

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
        for k in ("gold", "eu2y", "eu10y", "us2y", "us10y"):
            if fetched.get(k) is not None:
                tsample[k] = fetched[k]
        track = _upsert_track(track, tdate, tsample)
        save_data(track, TRACK)

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
        f.write(render(close, intra, track))
    print("rendered -> %s | close=%d intraday=%d track=%d | kind=%s latest gold=%.2f 2Y=%.3f 10Y=%.3f"
          % (HTML, len(close), len(intra), len(track), kind,
             fetched.get("gold", 0) or 0, fetched.get("us2y", 0) or 0, fetched.get("us10y", 0) or 0))


if __name__ == "__main__":
    main()
