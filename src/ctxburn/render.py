"""Render a Report as one self-contained HTML file. No CDN, no server, no JS deps."""

from __future__ import annotations

import json
from html import escape

from .analyze import Report

_HUE = {"system": 266, "tools": 190, "user": 45, "assistant": 140, "tool": 20}


def _money(v: float) -> str:
    if v >= 1:
        return f"${v:,.2f}"
    if v >= 0.01:
        return f"${v:.3f}"
    return f"${v:.4f}"


def summary_line(r: Report) -> str:
    if not r.n_calls:
        return "No calls recorded."
    pct = r.replay_ratio
    if pct >= 0.5:
        return (
            f"{pct:.0%} of input spend was text the agent had already sent before. "
            f"{r.n_calls} calls, {r.context_growth:.1f}x context growth."
        )
    return (
        f"{pct:.0%} of input spend was replay. "
        f"{r.n_calls} calls, {r.context_growth:.1f}x context growth."
    )


def render_html(report: Report, title: str | None = None) -> str:
    r = report
    data = r.to_dict()
    data["hues"] = _HUE
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    headline = title or f"ctxburn · {r.name}"
    kinds = sorted(data["by_kind"].items(), key=lambda kv: -kv[1][1])
    kind_rows = "".join(
        f'<tr><td><span class="dot" style="background:hsl({_HUE.get(k, 0)} 70% 55%)"></span>{k}</td>'
        f"<td class='num'>{int(v[0]):,}</td>"
        f"<td class='num'>{_money(v[1])}</td>"
        f"<td class='num'>{(v[2] / v[0] if v[0] else 0):.0%}</td></tr>"
        for k, v in kinds
    )
    winnable = r.cost_replay + r.cost_cached

    def _top_row(s: dict) -> str:
        hue = _HUE.get(s["kind"], 0)
        return (
            f'<tr><td><span class="dot" style="background:hsl({hue} 70% 55%)"></span>'
            f'<code>{s["kind"]}</code> <span class="dim">{escape(s["preview"])}</span></td>'
            f'<td class="num">{int(s["tokens"]):,}</td>'
            f'<td class="num">{s["sends"]}×</td>'
            f'<td class="num">{s["replays"]}×</td>'
            f'<td class="num">{s["cost"]:.4f}</td>'
            f"</tr>"
        )

    top_rows = "".join(_top_row(s) for s in r.top_segments)
    html = _TEMPLATE
    html = html.replace("__SUMMARY__", summary_line(r))
    html = html.replace("__TITLE__", headline)
    html = html.replace("__TOTAL__", _money(r.total_cost))
    html = html.replace("__REPLAY_PCT__", f"{r.replay_ratio:.0%}")
    html = html.replace("__REPLAY__", _money(winnable))
    html = html.replace("__FRESH__", _money(r.cost_fresh))
    html = html.replace("__OUTPUT__", _money(r.cost_output))
    html = html.replace("__CALLS__", str(r.n_calls))
    html = html.replace("__GROWTH__", f"{r.context_growth:.1f}x")
    html = html.replace("__INTOK__", f"{r.total_input_tokens:,}")
    html = html.replace("__KINDROWS__", kind_rows)
    html = html.replace("__TOP_ROWS__", top_rows)
    html = html.replace("__DATA__", payload.replace("</", "<\\/"))
    return html


_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{
    --bg:#0c0f14; --panel:#12161d; --line:#1e2531; --tx:#e6edf3; --dim:#8b97a8;
    --fresh:#4fd1c5; --replay:#2b3a4f; --out:#a78bfa; --hot:#ff6b6b;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--tx);
    font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    -webkit-font-smoothing:antialiased}
  .wrap{max-width:1120px;margin:0 auto;padding:40px 24px 80px}
  h1{font-size:20px;margin:0 0 6px;font-weight:600;letter-spacing:-.01em}
  .sub{color:var(--dim);font-size:13px;margin:0 0 28px}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;
    background:var(--line);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-bottom:28px}
  .kpi{background:var(--panel);padding:16px 16px 14px}
  .kpi .k{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim)}
  .kpi .v{font-size:24px;font-weight:650;margin-top:4px;font-variant-numeric:tabular-nums;
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  .kpi .v.hot{color:var(--hot)}
  section{margin:36px 0 0}
  h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);
    font-weight:600;margin:0 0 14px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}
  canvas{width:100%;display:block;cursor:crosshair}
  .legend{display:flex;gap:18px;flex-wrap:wrap;margin-top:12px;font-size:12px;color:var(--dim)}
  .legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px;
    vertical-align:middle}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left}
  th{color:var(--dim);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.06em}
  td.num{text-align:right;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
    font-variant-numeric:tabular-nums}
  tr:last-child td{border-bottom:0}
  .dot{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:8px}
  #tip{position:fixed;pointer-events:none;opacity:0;transition:opacity .08s;
    background:#05070a;border:1px solid var(--line);border-radius:7px;padding:7px 10px;
    font-size:12px;max-width:420px;z-index:9;box-shadow:0 8px 24px #0009}
  #tip b{color:var(--fresh)}
  .dim{color:var(--dim)}
  footer{margin-top:44px;color:#5c6779;font-size:12px}
  code{font-family:ui-monospace,Menlo,monospace;color:#c9d4e2}
</style></head><body><div class="wrap">
  <h1>__TITLE__</h1>
  <p class="sub">__SUMMARY__</p>

  <div class="kpis">
    <div class="kpi"><div class="k">total spend</div><div class="v">__TOTAL__</div></div>
    <div class="kpi"><div class="k">replay share</div><div class="v hot">__REPLAY_PCT__</div></div>
    <div class="kpi"><div class="k">spent re-sending</div><div class="v hot">__REPLAY__</div></div>
    <div class="kpi"><div class="k">fresh input</div><div class="v">__FRESH__</div></div>
    <div class="kpi"><div class="k">output</div><div class="v">__OUTPUT__</div></div>
    <div class="kpi"><div class="k">turns / growth</div><div class="v">__CALLS__ · __GROWTH__</div></div>
  </div>

  <section>
    <h2>context per turn <span style="text-transform:none;letter-spacing:0">— width is tokens, dim is replay</span></h2>
    <div class="card"><canvas id="flame"></canvas>
      <div class="legend" id="legend"></div>
    </div>
  </section>

  <section>
    <h2>who owns the bill</h2>
    <div class="card"><table>
      <thead><tr><th>kind</th><th class="num">tokens sent</th><th class="num">cost</th><th class="num">replay</th></tr></thead>
      <tbody>__KINDROWS__</tbody>
    </table></div>
  </section>

  <section>
    <h2>heaviest text in the run <span style="text-transform:none;letter-spacing:0">— the pieces worth fixing first</span></h2>
    <div class="card"><table>
      <thead><tr><th>piece</th><th class="num">tokens sent</th><th class="num">calls</th><th class="num">replays</th><th class="num">cost</th></tr></thead>
      <tbody>__TOP_ROWS__</tbody>
    </table></div>
  </section>

  <footer>generated by <code>ctxburn</code> · __INTOK__ input tokens observed ·
  costs are estimates from a built-in price table</footer>
</div>
<div id="tip"></div>
<script>
const D = __DATA__;
const HUE = D.hues || {};
const calls = D.calls || [];
const cvs = document.getElementById('flame');
const ctx = cvs.getContext('2d');
const tip = document.getElementById('tip');
let rects = [];

function color(kind, replay){
  const h = HUE[kind] !== undefined ? HUE[kind] : 0;
  return replay ? `hsl(${h} 26% 27%)` : `hsl(${h} 68% 56%)`;
}
function money(v){
  if (v >= 1) return '$' + v.toFixed(2);
  if (v >= 0.01) return '$' + v.toFixed(3);
  return '$' + v.toFixed(4);
}
function tok(v){
  if (v >= 1000) return (v/1000).toFixed(1) + 'k';
  return String(v);
}

function draw(){
  const dpr = window.devicePixelRatio || 1;
  const W = cvs.clientWidth || 900;
  const rowH = 30, gap = 7, top = 22, left = 34;
  const n = calls.length;
  const H = top + n * (rowH + gap) + 6;
  cvs.width = Math.round(W * dpr);
  cvs.height = Math.round(H * dpr);
  cvs.style.height = H + 'px';
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  rects = [];

  const maxTok = Math.max(1, ...calls.map(c => c.input_tokens));
  const usable = W - left - 8;

  // axis ticks
  ctx.fillStyle = '#5c6779';
  ctx.font = '10px ui-monospace, Menlo, monospace';
  const tickStep = maxTok > 60000 ? 20000 : maxTok > 20000 ? 10000 : maxTok > 8000 ? 5000 : 2000;
  for (let t = 0; t <= maxTok; t += tickStep){
    const x = left + (t / maxTok) * usable;
    ctx.fillText(tok(t), x + 2, 13);
    ctx.strokeStyle = '#161c25';
    ctx.beginPath(); ctx.moveTo(x, top - 4); ctx.lineTo(x, H - 4); ctx.stroke();
  }

  calls.forEach((c, i) => {
    const y = top + i * (rowH + gap);
    ctx.fillStyle = '#5c6779';
    ctx.font = '10px ui-monospace, Menlo, monospace';
    ctx.fillText(String(i + 1).padStart(2, '0'), 6, y + rowH / 2 + 4);

    let x = left;
    c.segments.forEach(s => {
      const w = (s.tokens / maxTok) * usable;
      if (w <= 0) return;
      const col = color(s.kind, s.replay);
      ctx.fillStyle = col;
      ctx.fillRect(x, y, Math.max(w - 0.7, 0.4), rowH);
      rects.push({x, y, w: Math.max(w, 1), h: rowH, seg: s, call: i,
                  right: c.input_tokens});
      if (w > 46){
        ctx.fillStyle = s.replay ? 'rgba(230,237,243,.55)' : 'rgba(6,12,16,.85)';
        ctx.font = '600 10px ui-sans-serif, system-ui';
        ctx.fillText(s.kind, x + 5, y + rowH / 2 + 3.5);
      }
      x += w;
    });
  });

  // avg line
  const avg = calls.reduce((a, c) => a + c.input_tokens, 0) / Math.max(1, n);
  const ax = left + (avg / maxTok) * usable;
  ctx.setLineDash([3, 4]);
  ctx.strokeStyle = 'rgba(255,107,107,.55)';
  ctx.beginPath(); ctx.moveTo(ax, top - 4); ctx.lineTo(ax, H - 6); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = 'rgba(255,107,107,.85)';
  ctx.font = '10px ui-sans-serif, system-ui';
  ctx.fillText('avg', ax + 3, H - 1);
}

cvs.addEventListener('mousemove', e => {
  const b = cvs.getBoundingClientRect();
  const mx = e.clientX - b.left, my = e.clientY - b.top;
  const hit = rects.find(r => mx >= r.x && mx <= r.x + r.w && my >= r.y && my <= r.y + r.h);
  if (!hit){ tip.style.opacity = 0; return; }
  const s = hit.seg;
  tip.innerHTML = `<b>${s.kind}</b> ${s.replay ? '· replay' : '· fresh'}<br>` +
    `${s.tokens} tok · ${money(s.cost)}` +
    (s.cached_tokens ? ` · ${s.cached_tokens} cached` : '') +
    (s.replay ? `<br><span style="color:#8b97a8">first sent at turn ${s.first_seen_at + 1}</span>` : '') +
    `<br><span style="color:#8b97a8">${(s.preview || '').replace(/[<>&]/g, c =>
      ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}</span>`;
  tip.style.opacity = 1;
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  tip.style.left = Math.min(e.clientX + 14, window.innerWidth - tw - 10) + 'px';
  tip.style.top = Math.max(e.clientY - th - 12, 8) + 'px';
});
cvs.addEventListener('mouseleave', () => { tip.style.opacity = 0; });

function legend(){
  const kinds = Object.keys(HUE);
  document.getElementById('legend').innerHTML =
    kinds.map(k => `<span><i style="background:${color(k,false)}"></i>${k}</span>`).join('') +
    `<span><i style="background:${color('system',true)}"></i>replay (already sent)</span>`;
}
legend();
draw();
let t; window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(draw, 120); });
</script>
</body></html>
"""
