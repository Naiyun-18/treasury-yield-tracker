#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
US Treasury yield tracker - fetch 5Y/10Y/30Y + render self-contained HTML with TWO charts.

Data source: 同花顺问财 (hithink-macro-query). Append each sample to the correct data file,
then render a standalone index.html with two 3-line SVG charts (no CDN dependency).

  Chart 1 (日线收盘) : 每美股交易日收盘价, 1 sample per US trading day. X轴固定最多 60 个采样点 (=60个交易日).
  Chart 2 (盘中3小时) : 每交易日 3 次 (北京 23:00 / 02:00 / 05:00), 30 samples per ~20 trading days X轴固定最多 60 个采样点.
                       开盘(20:00)不采集利率数据.

Data files (both gitignored):
  close_data.json      - daily close samples  {ts, us5y, us10y, us30y}
  intraday_data.json   - intraday samples     {ts, us5y, us10y, us30y}
"""
import os, json, sys, subprocess, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CLOSE = os.path.join(BASE, "close_data.json")
INTRADAY = os.path.join(BASE, "intraday_data.json")
HTML = os.path.join(BASE, "index.html")
CLI = os.path.expanduser("~/.openclaw/workspace/skills/hithink-macro-query/scripts/cli.py")

QUERIES = {"us5y": "美国5年期国债收益率",
           "us10y": "美国10年期国债收益率",
           "us30y": "美国30年期国债收益率"}
KEYS = ["us5y", "us10y", "us30y"]
LABELS = {"us5y": "5年期", "us10y": "10年期", "us30y": "30年期"}
COLORS = {"us5y": "#2471a3", "us10y": "#e67e22", "us30y": "#c0392b"}


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
    """Fetch 3 tenors from iwencai. Returns dict {us5y, us10y, us30y}."""
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


def sample_kind(now):
    """Map a Beijing capture time -> data kind.

    Returns 'close' | 'intraday' | None(skip).
    US non-trading in Beijing time:
      - Beijing Sunday 所有时段           = US 周六
      - 北京周一 02/05/06 点              = US 周日(休市)
      - 北京周六 23:00                    = US 周六
      - 北京 20:00                        = 开盘(不采集利率)
    美债休市判断简表：
      23:00 — 周一~周五有效
      02/05/06 — 周二~周六有效
      20:00 — 开盘，跳过不采集
    """
    h = now.hour
    wd = now.weekday()  # Mon=0 ... Sun=6

    if h == 20:                 # 开盘，不采集利率数据
        return None
    if wd == 6:                 # 北京周日 = US 周六
        return None
    # 北京周一 02/05/06 = US 周日(休市)；周一 23:00 = US 周一(有效)
    if wd == 0 and h in (2, 5, 6):
        return None
    # 北京周六 23:00 = US 周六(休市)；周六 02/05 = US 周五(有效)
    if wd == 5 and h == 23:
        return None
    if h == 23:
        return "intraday"
    if h in (2, 5):
        return "intraday"
    if h == 6:
        return "close"
    return None


def render_chart(points, title, subtitle, n_target, x_when):
    """Render one 3-line SVG chart as an HTML string."""
    xs = list(range(len(points)))
    all_vals = [p[k] for p in points for k in KEYS if p.get(k) is not None]
    lo, hi = min(all_vals), max(all_vals)
    pad = max((hi - lo) * 0.12, 0.05)
    ylo, yhi = round(lo - pad, 3), round(hi + pad, 3)

    W, H, L, R, T, B = 1200, 620, 70, 40, 40, 55
    iw, ih = W - L - R, H - T - B

    def px(i):
        # 固定 60 个槽位坐标：有效数据从 Y 轴(左侧)排起，不足时右侧留空(新上市股票K线风格)
        return L + iw * (i / (n_target - 1))

    def py(v):
        return T + ih * (1 - (v - ylo) / (yhi - ylo))

    def line_pts(k):
        pts = []
        for i, p in enumerate(points):
            if p.get(k) is not None:
                pts.append((px(i), py(p[k])))
        return pts

    grid = []
    # 固定槽位竖线：每 10 格一条虚线，提示保留的 60 个采样槽位(右侧留空)
    for slot in (0, 10, 20, 30, 40, 50, 59):
        sx = px(slot)
        grid.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#dde6f0" stroke-dasharray="2,4"/>'
                    % (sx, T, sx, T + ih))
    for g in range(6):
        v = ylo + (yhi - ylo) * g / 5
        gy = py(v)
        grid.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e8e8e8"/>'
                    % (L, gy, W - R, gy))
        grid.append('<text x="%d" y="%.1f" class="ylab">%.3f</text>' % (L - 8, gy + 4, v))

    xlab = []
    for idx in (0, len(points) // 2, len(points) - 1):
        lab = x_when(points[idx]["ts"])
        xlab.append('<text x="%.1f" y="%d" class="xlab">%s</text>' % (px(idx), H - B + 20, lab))

    polylines = []
    for k in KEYS:
        pts = line_pts(k)
        if not pts:
            continue
        polylines.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2.5"/>'
                         % (" ".join("%.1f,%.1f" % (x, y) for x, y in pts), COLORS[k]))

    dots = []
    for k in KEYS:
        pts = line_pts(k)
        if pts:
            x, y = pts[-1]
            dots.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s"/>' % (x, y, COLORS[k]))

    header = ('<div class="chart-title">%s</div>'
              '<div class="chart-sub">%s &nbsp;·&nbsp; 最新采样：%s</div>' % (title, subtitle, points[-1].get("ts", "")))
    svg = ('<svg viewBox="0 0 %d %d" class="zoomable" onclick="openZoom(this.closest(\'.chart-card\'))">'
           '%s%s%s%s</svg>' % (W, H, "".join(grid), "".join(xlab), "".join(polylines), "".join(dots)))

    rows = []
    for p in reversed(points[-10:]):
        tds = "".join("<td>%s</td>" % (("%.3f" % p[k]) if p.get(k) is not None else "—") for k in KEYS)
        rows.append("<tr><td>%s</td>%s</tr>" % (p.get("ts", ""), tds))
    table = ('<table><tr><th>采样时间(北京时间)</th><th>5年期(%%)</th>'
             '<th>10年期(%%)</th><th>30年期(%%)</th></tr>%s</table>'
             % "".join(rows))

    return '<div class="chart-card">%s%s%s</div>' % (header, svg, table)


def _when_day(ts):
    return ts[5:10]  # YYYY-MM-DD -> MM-DD


def _when_intra(ts):
    # "2026-09-25 05:00:00" -> "09-25 05:00"
    return "%s %s" % (ts[5:10], ts[11:16])


def render(close_pts, intra_pts):
    cpts = close_pts[-60:]
    ipts = intra_pts[-60:]
    if not cpts and not ipts:
        return "<html><body><h2>暂无数据</h2></body></html>"

    head = ("<h1>美国国债收益率追踪</h1>"
            "<div class='sub'>5Y / 10Y / 30Y &nbsp;·&nbsp; "
            "图表一：每美股交易日收盘价 &nbsp;·&nbsp; "
            "图表二：盘中每3小时采样（北京 23:00/02:00/05:00）</div>")

    # 顶部最新收盘价卡片（附图卡片风格）
    rate_cards_html = ""
    if cpts:
        latest = cpts[-1]
        cards = []
        for k in KEYS:
            v = latest.get(k)
            txt = ("%.3f%%" % v) if v is not None else "—"
            c = COLORS[k]
            cards.append(
                '<div class="rate-card" style="border-left-color:%s">'
                '<div class="rate-head"><span class="rate-ico" style="background:%s"></span>%s</div>'
                '<div class="rate-val" style="color:%s">%s</div></div>' % (c, c, LABELS[k], c, txt))
        rate_cards_html = ('<div class="rate-cards-label">最新交易日收盘价（北京时间 06:00 采样）</div>'
                           '<div class="rate-cards">%s</div>' % "".join(cards))

    body = rate_cards_html + "".join(filter(None, [
        render_chart(cpts, "图表一 · 每日收盘价走势", "每美股交易日收盘价 · 横坐标 60 个交易日收盘采样", 60, _when_day) if cpts else None,
        render_chart(ipts, "图表二 · 盘中每3小时利率走势", "交易日（北京 23:00 / 02:00 / 05:00）· 横坐标 60 个采样点（约 20 个交易日）", 60, _when_intra) if ipts else None,
    ]))

    return """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>美国国债收益率追踪</title>
<style>
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
   .zoom-overlay{background:#000;padding:0}
   .zoom-box{width:100vw;height:100dvh;max-height:100dvh;border-radius:0;padding:0;display:block;position:relative;overflow:hidden;background:#000;box-shadow:none}
   .zoom-box #zoomContent{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;overflow:hidden;margin:0}
   .zoom-box svg{width:auto;height:auto;max-width:100%%;max-height:100%%;margin:0;display:block}
   .zoom-close{position:absolute;top:12px;right:12px;z-index:6;background:rgba(0,0,0,.55);margin:0;border-radius:50%%;width:36px;height:36px}
   .zoom-title{position:absolute;top:16px;left:16px;right:60px;z-index:5;font-size:14px;color:#fff;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-shadow:0 1px 3px rgba(0,0,0,.6)}
 }
 svg{width:100%%;height:auto;display:block}
 .ylab{font-size:11px;fill:#7a8ca0}.xlab{font-size:10px;fill:#7a8ca0;text-anchor:middle}
 .legendbox{padding-top:4px}
 .leg{font-size:13px;fill:#1c2b3a}
 table{width:100%%;border-collapse:collapse;margin-top:14px;font-size:13px}
 th,td{border-bottom:1px solid #eef2f6;padding:7px 10px;text-align:left}
 th{color:#7a8ca0;font-weight:600}
 .hint{color:#9aa9ba;font-size:12px;margin-top:14px}
</style></head><body><div class="wrap">%s
%s
<div class="hint">数据来源：同花顺问财；盘中免费源受限，同一交易日内每3小时采样值可能保持不变。</div>
<div id="zoomBox" class="zoom-overlay">
  <div class="zoom-box">
    <button class="zoom-close" onclick="closeZoom()" aria-label="关闭">&#10005;</button>
    <div id="zoomTitle" class="zoom-title"></div>
    <div id="zoomContent"></div>
  </div>
</div>
<script>
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
</script>
</div></body></html>""" % (head, body)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    now = datetime.datetime.now()
    kind = sample_kind(now)

    if kind is None:
        print("当前时刻非美债盘中/收盘采集点（休市或开盘不采集），跳过抓取，仅渲染页面")
        close = load_data(CLOSE)
        intra = load_data(INTRADAY)
        with open(HTML, "w", encoding="utf-8") as f:
            f.write(render(close, intra))
        print("rendered(existing) -> %s | close=%d intraday=%d" % (HTML, len(close), len(intra)))
        return

    fetched = fetch_now()
    if not any(v is not None for v in fetched.values()):
        print("抓取失败，无有效数据，跳过")
        return

    sample = {"ts": now_str()}
    for k, v in fetched.items():
        if v is not None:
            sample[k] = v

    path = CLOSE if kind == "close" else INTRADAY
    points = load_data(path)
    if points and points[-1].get("ts") == sample["ts"]:
        print("same minute, skip append")
    else:
        points.append(sample)
    save_data(path, points)

    close = load_data(CLOSE)
    intra = load_data(INTRADAY)
    with open(HTML, "w", encoding="utf-8") as f:
        f.write(render(close, intra))
    print("rendered -> %s | close=%d intraday=%d | kind=%s latest 5Y=%.3f 10Y=%.3f 30Y=%.3f"
          % (HTML, len(close), len(intra), kind,
             sample.get("us5y", 0), sample.get("us10y", 0), sample.get("us30y", 0)))


if __name__ == "__main__":
    main()
