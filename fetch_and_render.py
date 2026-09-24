#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
US Treasury yield tracker - fetch 5Y/10Y/30Y + render self-contained HTML chart.
Data source: 同花顺问财 (hithink-macro-query). Append each sample to data.json,
then render a standalone index.html with 3-line SVG chart (no CDN dependency).
"""
import os, json, sys, subprocess, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data.json")
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


def load_data():
    if os.path.exists(DATA):
        try:
            with open(DATA, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_data(data):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("saved %d samples -> %s" % (len(data), DATA))


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def render(points):
    if not points:
        return "<html><body><h2>暂无数据</h2></body></html>"
    xs = list(range(len(points)))  # equal-spaced by sample index
    all_vals = [p[k] for p in points for k in KEYS if p.get(k) is not None]
    lo, hi = min(all_vals), max(all_vals)
    pad = max((hi - lo) * 0.12, 0.05)
    ylo, yhi = round(lo - pad, 3), round(hi + pad, 3)

    W, H, L, R, T, B = 1200, 620, 70, 40, 40, 55
    iw, ih = W - L - R, H - T - B

    def px(i):
        return L + iw * (i / (len(points) - 1)) if len(points) > 1 else L + iw / 2

    def py(v):
        return T + ih * (1 - (v - ylo) / (yhi - ylo))

    def line_pts(k):
        pts = []
        for i, p in enumerate(points):
            if p.get(k) is not None:
                pts.append((px(i), py(p[k])))
        return pts

    # y grid + labels
    grid = []
    for g in range(6):
        v = ylo + (yhi - ylo) * g / 5
        gy = py(v)
        grid.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e8e8e8"/>'
                    % (L, gy, W - R, gy))
        grid.append('<text x="%d" y="%.1f" class="ylab">%.3f</text>' % (L - 8, gy + 4, v))
    # x labels (first/mid/last)
    xlab = []
    for name, idx in (("首", 0), ("中", len(points) // 2), ("末", len(points) - 1)):
        xlab.append('<text x="%.1f" y="%d" class="xlab">%s</text>'
                    % (px(idx), H - B + 30, points[idx].get("ts", "")))
    xlab.append('<text x="%d" y="%d" class="xlab" text-anchor="start">%d 个采样点</text>'
                % (L, H - B + 45, len(points)))

    polylines = []
    for k in KEYS:
        pts = line_pts(k)
        if not pts:
            continue
        d = "M" + " L".join("%.1f,%.1f" % (x, y) for x, y in pts)
        polylines.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2.5"/>'
                         % (" ".join("%.1f,%.1f" % (x, y) for x, y in pts), COLORS[k]))
    # end-point dots
    dots = []
    for k in KEYS:
        pts = line_pts(k)
        if pts:
            x, y = pts[-1]
            dots.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s"/>' % (x, y, COLORS[k]))

    # legend
    lx = L
    legs = []
    for k in KEYS:
        last = None
        for p in reversed(points):
            if p.get(k) is not None:
                last = p[k]
                break
        legs.append('<rect x="%d" y="12" width="18" height="4" fill="%s"/>'
                    '<text x="%d" y="18" class="leg">%s %.3f%%</text>'
                    % (lx, COLORS[k], lx + 22, LABELS[k], last or 0))
        lx += 150

    head = ("<h1>美国国债收益率追踪</h1>"
            "<div class='sub'>5Y / 10Y / 30Y &nbsp;·&nbsp; 最新采样：%s &nbsp;·&nbsp; 交易时段每3小时 &amp; 收盘后自动更新</div>"
            % (points[-1].get("ts", "")))

    rows = []
    for p in reversed(points[-20:]):
        tds = "".join("<td>%s</td>" % (("%.3f" % p[k]) if p.get(k) is not None else "—") for k in KEYS)
        rows.append("<tr><td>%s</td>%s</tr>" % (p.get("ts", ""), tds))

    return """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>美国国债收益率追踪</title>
<style>
 body{font-family:-apple-system,'Segoe UI','PingFang SC',Microsoft YaHei,sans-serif;background:#f4f7fb;color:#1c2b3a;margin:0;padding:24px}
 .card{background:#fff;border-radius:10px;box-shadow:0 1px 6px rgba(20,40,80,.08);padding:22px 26px;max-width:1240px;margin:0 auto;}
 h1{font-size:22px;margin:0 0 4px}.sub{color:#7a8ca0;font-size:13px;margin-bottom:14px}
 svg{width:100%%;height:auto;display:block}
 .ylab{font-size:11px;fill:#7a8ca0}.xlab{font-size:10px;fill:#7a8ca0;text-anchor:middle}
 .leg{font-size:13px;fill:#1c2b3a}
 table{width:100%%;border-collapse:collapse;margin-top:18px;font-size:13px}
 th,td{border-bottom:1px solid #eef2f6;padding:7px 10px;text-align:left}
 th{color:#7a8ca0;font-weight:600}.last td{font-weight:700;background:#f7fafd}
 .hint{color:#9aa9ba;font-size:12px;margin-top:14px}
</style></head><body><div class="card">
%s<svg viewBox="0 0 %d %d">%s%s%s%s</svg>
<table><tr><th>采样时间(北京时间)</th><th>5年期(%%</th><th>10年期(%%</th><th>30年期(%%</th></tr>%s</table>
</div><div class="hint">数据来源：同花顺问财；盘中免费源受限，同一交易日内每3小时采样值可能保持不变，每日收盘后更新推进曲线。</div>
</body></html>""" % (
        head, W, H, "".join(grid), "".join(xlab), "".join(polylines), "".join(dots), "".join(rows))


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # US Treasury market: closed on Sat/Sun (Beijing). Skip to avoid flat weekend points.
    if datetime.datetime.now().weekday() >= 5:
        print("weekend, US treasury closed, skip sample")
        return
    # If a weekday-6am close capture ran, also allow runs through Monday early AM (US Sun close).
    fetched = fetch_now()
    points = load_data()
    sample = {"ts": now_str()}
    for k, v in fetched.items():
        if v is not None:
            sample[k] = v
    # avoid duplicate identical timestamps (same minute repeat run)
    if points and points[-1].get("ts") == sample["ts"]:
        print("same minute, skip append")
    else:
        points.append(sample)
    save_data(points)
    with open(HTML, "w", encoding="utf-8") as f:
        f.write(render(points))
    print("rendered -> %s | latest: 5Y=%.3f 10Y=%.3f 30Y=%.3f"
          % (HTML, sample.get("us5y", 0), sample.get("us10y", 0), sample.get("us30y", 0)))


if __name__ == "__main__":
    main()
