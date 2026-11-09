#!/usr/bin/env python3
"""图审台 v1：本地 Web（stdlib http.server，零依赖），pipeline.db 唯一事实源。

页面:
    /board        流水线看板（选品→审图全过程，只读）
    /review       图审队列 + 详情 + 三动作（过/打回/毙品，落 reviews eval 标签）
    /product/<id> 单品 trace（events 时间线 + reviews 历史）
    POST /action  {pid, decision, reason, note} → record_review → 状态机流转
    /media/<f>    data/gen 下的生成图（防目录穿越）

用法: python3 review_server.py [port=8080]，浏览器开 http://127.0.0.1:8080
"""
import html
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pipeline as P

ROOT = Path(__file__).parent
GEN = ROOT / "data/gen"
ESC = html.escape

CSS = """
:root{--bg:#0f1115;--card:#1a1d24;--card2:#22262f;--border:#2e3440;--text:#e6e8ec;
--dim:#9aa3b2;--accent:#4f8cff;--green:#34c759;--yellow:#ffb340;--red:#ff5f57;--purple:#b48cff}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:-apple-system,"PingFang SC",sans-serif;line-height:1.6;padding:20px}
a{color:var(--accent);text-decoration:none}
.wrap{max-width:1180px;margin:0 auto}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--border);margin-bottom:18px;font-size:14px}
.tabs a{padding:8px 16px;color:var(--dim);border:1px solid transparent;border-bottom:none;border-radius:8px 8px 0 0}
.tabs a.on{background:var(--card);border-color:var(--border);color:var(--text);font-weight:600}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 20px;margin-bottom:14px}
.dim{color:var(--dim);font-size:12.5px}
.tag{display:inline-block;padding:1px 8px;border-radius:4px;font-size:12px;font-weight:600;margin-right:6px}
.t-ok{background:rgba(52,199,89,.15);color:var(--green)}
.t-warn{background:rgba(255,179,64,.15);color:var(--yellow)}
.t-bad{background:rgba(255,95,87,.15);color:var(--red)}
.t-manual{background:rgba(180,140,255,.15);color:var(--purple)}
code{background:var(--card2);padding:1px 6px;border-radius:4px;font-size:12px;color:#7ecfff}
.board{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}
.col{background:var(--card2);border:1px solid var(--border);border-radius:10px;padding:8px;min-height:120px}
.col.human{border:2px solid var(--purple);background:rgba(180,140,255,.05)}
.col h5{font-size:12px;color:var(--dim);margin:2px 2px 8px;display:flex;justify-content:space-between}
.col h5 .cnt{color:var(--text)}
.mini{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:5px 8px;font-size:11.5px;margin-bottom:6px;display:block;color:var(--text)}
.mini .v{color:var(--dim);font-size:10.5px;display:block}
.mini.dead{opacity:.45}.mini.dead b{text-decoration:line-through}
.rl{display:flex;gap:14px}
.queue{width:250px;flex-shrink:0}
.detail{flex:1;min-width:0}
.imgs{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px;margin:10px 0}
.imgs figure{margin:0}
.imgs img{width:100%;border-radius:8px;border:1px solid var(--border);display:block}
.imgs figcaption{font-size:11px;color:var(--dim);text-align:center;margin-top:3px}
.kv{font-size:13px;margin:10px 0}
.kv .row{display:flex;justify-content:space-between;gap:12px;padding:5px 0;border-bottom:1px dashed var(--border)}
.kv .row span:first-child{color:var(--dim);white-space:nowrap}
.desc{background:var(--card2);border-radius:8px;padding:10px 14px;font-size:13px;white-space:pre-wrap;margin:8px 0}
.actions{display:flex;gap:10px;margin-top:14px}
button{flex:1;border:none;border-radius:10px;padding:12px;font-size:14.5px;font-weight:700;cursor:pointer}
button.ok{background:var(--green)}button.re{background:var(--yellow)}button.kill{background:var(--red)}
select,input[type=text]{background:var(--card2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px;font-size:13px}
.trace{margin:10px 0 0 8px;padding-left:20px;border-left:2px solid var(--border);font-size:13px}
.ev{position:relative;margin-bottom:12px}
.ev::before{content:"";position:absolute;left:-26px;top:5px;width:10px;height:10px;border-radius:50%;background:var(--accent)}
.ev.human::before{background:var(--purple)}
.ev.dead::before{background:var(--red)}
.warnbox{background:rgba(255,179,64,.08);border:1px solid rgba(255,179,64,.3);border-radius:8px;padding:10px 14px;margin:10px 0;font-size:13px}
h1{font-size:20px;margin-bottom:4px}h3{font-size:15px;margin:16px 0 8px}
"""

STATES = ["discovered", "scored", "sourced", "checked", "profitable",
          "imaging", "review_pending"]
STATE_CN = {"discovered": "① 发现", "scored": "① 选品", "sourced": "② 找货",
            "checked": "③ 趋势", "profitable": "④ 算账", "imaging": "⑥ 生图",
            "review_pending": "👤 待审"}
DECISION_CN = {"approve": "✅ 通过", "regenerate": "🔁 打回重生", "kill": "❌ 毙品"}


def page(title: str, tab: str, body: str) -> bytes:
    tabs = [("/board", "📊 流水线", "board"), ("/review", "🖼 图审", "review")]
    nav = "".join(
        f'<a href="{u}" class="{"on" if k == tab else ""}">{n}</a>'
        for u, n, k in tabs)
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>{ESC(title)} · 审核台</title><style>{CSS}</style></head><body><div class="wrap">
<h1>🛂 Ozon 审核台 <span class="dim">pipeline.db 唯一事实源</span></h1>
<div class="tabs">{nav}<span class="dim" style="margin-left:auto;padding:8px 0">类目首验 / 视频审（占位，依赖任务5/7）</span></div>
{body}</div></body></html>""".encode()


def load(pid_row) -> dict:
    return json.loads(pid_row["metrics_json"] or "{}")


def mini(p, m, dead=False) -> str:
    bits = []
    sel, src = m.get("selection", {}), m.get("sourcing", {})
    if sel:
        bits.append(f"月搜{sel.get('searchVolume', '?')}")
    if src:
        bits.append(f"¥{src.get('price_cny', '?')}")
    v = " · ".join(bits)
    label = m.get("queryCn") or p["query"]
    cls = "mini dead" if dead else "mini"
    return (f'<a class="{cls}" href="/product/{p["id"]}"><b>{ESC(label)}</b>'
            f'<span class="v">{ESC(v)}</span></a>')


def board() -> bytes:
    with P.conn() as c:
        rows = c.execute("SELECT * FROM products").fetchall()
        dead_from = {r["product_id"]: r["from_state"] for r in c.execute(
            "SELECT product_id, from_state FROM events WHERE to_state='dead'"
            " ORDER BY id")}
    cols = {s: [] for s in STATES}
    for p in rows:
        m = load(p)
        if p["state"] == "dead":
            cols.get(dead_from.get(p["id"], "scored"), cols["scored"]).append(
                mini(p, m, dead=True))
        elif p["state"] in cols:
            cols[p["state"]].append(mini(p, m))
    html_cols = "".join(
        f'<div class="col{" human" if s == "review_pending" else ""}">'
        f'<h5>{STATE_CN[s]}<span class="cnt">{len(cards)}</span></h5>'
        + "".join(cards) + "</div>"
        for s, cards in cols.items())
    body = f"""<div class="card"><span class="dim">每张卡一路过闸到此；变灰划线=在该闸被杀（悬停看卡面指标，点卡看完整 trace）。</span></div>
<div class="board">{html_cols}</div>"""
    return page("流水线", "board", body)


def _imgs(m) -> str:
    d, names = m.get("assets_dir"), m.get("images", [])
    if not d or not names:
        return '<div class="dim">（无生成图）</div>'
    figs = "".join(
        f'<figure><img src="/media/{ESC(d)}/{ESC(n)}" loading="lazy">'
        f'<figcaption>{ESC(n)}</figcaption></figure>' for n in names)
    return f'<div class="imgs">{figs}</div>'


def review(pid: int | None, flash: str = "") -> bytes:
    with P.conn() as c:
        pend = c.execute("SELECT * FROM products WHERE state='review_pending'"
                         " ORDER BY updated_at").fetchall()
        cur = None
        if pend:
            want = pid or pend[0]["id"]
            cur = next((p for p in pend if p["id"] == want), pend[0])
    q = "".join(
        f'<a class="mini{" " if cur and p["id"] == cur["id"] else ""}" '
        f'href="/review?pid={p["id"]}"><b>{ESC(load(p).get("queryCn") or p["query"])}</b>'
        f'<span class="v">#{p["id"]} · {ESC(p["query"][:30])}</span></a>' for p in pend)
    queue_html = f'<div class="queue"><div class="dim" style="margin:4px">待审 {len(pend)}</div>{q or "<div class=dim>队列空 🎉</div>"}</div>'

    if not cur:
        return page("图审", "review",
                    f'{flash}<div class="rl">{queue_html}'
                    f'<div class="detail card">队列空——去 /board 看流水线</div></div>')

    m = load(cur)
    sel, src = m.get("selection", {}), m.get("sourcing", {})
    warn = ""
    if m.get("need_confirm"):
        probs = m.get("spec_validation")
        plist = "".join(f"<li>{ESC(str(x))}</li>" for x in probs) \
            if isinstance(probs, list) else ""
        warn = (f'<div class="warnbox">⚠️ <b>need_confirm</b>：内容闸机械校验未过，'
                f"请重点核对以下问题<ul>{plist}</ul></div>")
    kv = "".join(f'<div class="row"><span>{a}</span><span>{b}</span></div>' for a, b in [
        ("类目", ESC(m.get("spec_path", "—"))),
        ("月搜热度", sel.get("searchVolume", "—")),
        ("30天增长", f"{sel.get('growth30d', '—')}%"),
        ("竞争商品", sel.get("competingProducts", "—")),
        ("均价", f"{sel.get('avgPrice', '—')}₽"),
        ("退货率", f"{sel.get('returnRate', '—')}%"),
        ("1688 货源", f"¥{src.get('price_cny', '—')} · 月销 {src.get('sold_count', '—')} · offer {src.get('offer_id', '—')}"),
        ("趋势/经济闸", "⚠️ sync 跳过（worker 未实现，任务3/4）"),
    ])
    opts = "".join(f"<option>{ESC(r)}</option>" for r in P.KILL_REASONS)
    actions = f"""
<form method="POST" action="/action">
  <input type="hidden" name="pid" value="{cur['id']}">
  <div style="display:flex;gap:8px;margin-top:12px">
    <select name="reason"><option value="">死因（打回/毙品必选）</option>{opts}</select>
    <input type="text" name="note" placeholder="备注（选「其他」必填）" size="30">
  </div>
  <div class="actions">
    <button class="ok" name="decision" value="approve">✅ 通过 → 上架队列</button>
    <button class="re" name="decision" value="regenerate">🔁 打回重生（换 seed，$0.03/张）</button>
    <button class="kill" name="decision" value="kill">❌ 毙品</button>
  </div>
</form>"""
    detail = f"""<div class="detail">
<div class="card">
  <span class="tag t-manual">#{cur['id']}</span><span class="tag t-warn">review_pending</span>
  <a class="dim" href="/product/{cur['id']}" style="float:right">完整 trace →</a>
  <h3 style="margin-top:8px">{ESC(m.get('ozon_title') or cur['query'])}</h3>
  <div class="dim">Ozon 标题（content_agent 产出，上架直接用）</div>
  <h3>俄语详情介绍</h3>
  <div class="desc">{ESC(m.get('description_ru') or '（spec 未产出 description_ru）')}</div>
  {warn}
  <h3>套图素材（{ESC(m.get('assets_dir', '—'))}）</h3>
  {_imgs(m)}
  <div class="kv">{kv}</div>
  {actions}
</div></div>"""
    return page("图审", "review", f'{flash}<div class="rl">{queue_html}{detail}</div>')


def trace(pid: int) -> bytes:
    with P.conn() as c:
        p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not p:
            return page("404", "board", "<div class=card>产品不存在</div>")
        evs = c.execute("SELECT * FROM events WHERE product_id=? ORDER BY id",
                        (pid,)).fetchall()
        revs = c.execute("SELECT * FROM reviews WHERE product_id=? ORDER BY id",
                         (pid,)).fetchall()
    m = load(p)
    ev_html = "".join(
        f'<div class="ev{" dead" if e["to_state"] == "dead" else " human" if e["to_state"] in ("review_pending", "video_pending") else ""}">'
        f'<b>{ESC(e["from_state"] or "∅")} → {ESC(e["to_state"])}</b>'
        f'{" · " + ESC(e["note"] or "") if e["note"] else ""}'
        f'<div class="dim">{ESC(e["created_at"])}</div></div>' for e in evs)
    rev_html = "".join(
        f'<div class="ev human"><b>{DECISION_CN.get(r["decision"], r["decision"])}</b>'
        f' · {ESC(r["kind"])} · {ESC(r["asset_path"])}'
        f'{" · 死因: " + ESC(r["reason"]) if r["reason"] else ""}'
        f'<div class="dim">{ESC(r["decided_at"] or "")}</div></div>' for r in revs)
    body = f"""<div class="card">
<span class="tag t-manual">#{p['id']}</span><b>{ESC(m.get('ozon_title') or p['query'])}</b>
<span class="tag t-warn">{ESC(p['state'])}</span>
<div class="dim">{ESC(p['query'])} · ozon {ESC(p['ozon_id'] or '—')} · 1688 {ESC(m.get('sourcing', {}).get('offer_id', '—'))}</div>
<h3>状态流转（events）</h3><div class="trace">{ev_html}</div>
<h3>审核标签（reviews / eval golden set）</h3><div class="trace">{rev_html or '<div class=dim>尚无</div>'}</div>
</div>"""
    return page(f"trace #{pid}", "board", body)


class Handler(BaseHTTPRequestHandler):
    def _send(self, data: bytes, ctype="text/html; charset=utf-8", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, url):
        self.send_response(303)
        self.send_header("Location", url)
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path == "/":
            return self._redirect("/board")
        if u.path == "/board":
            return self._send(board())
        if u.path == "/review":
            pid = int(qs["pid"][0]) if "pid" in qs else None
            return self._send(review(pid))
        if u.path.startswith("/product/"):
            return self._send(trace(int(u.path.split("/")[-1])))
        if u.path.startswith("/media/"):
            rel = Path(urllib.parse.unquote(u.path[len("/media/"):]))
            full = (ROOT / rel).resolve()          # 防目录穿越
            if full.is_file() and GEN.resolve() in full.parents:
                return self._send(full.read_bytes(), "image/png")
            return self._send(b"404", code=404)
        return self._send(b"404", code=404)

    def do_POST(self):
        if self.path != "/action":
            return self._send(b"404", code=404)
        form = urllib.parse.parse_qs(
            self.rfile.read(int(self.headers["Content-Length"])).decode())
        pid = int(form["pid"][0])
        decision = form["decision"][0]
        reason = form.get("reason", [""])[0]
        note = form.get("note", [""])[0].strip()
        if reason == "其他":
            reason = f"其他: {note}" if note else "其他"
        with P.conn() as c:
            m = load(c.execute("SELECT metrics_json FROM products WHERE id=?",
                               (pid,)).fetchone())
        try:
            P.record_review(pid, "image",
                            m.get("assets_dir", m.get("spec_path", "")),
                            decision, reason)
        except ValueError as exc:
            return self._send(review(pid, f'<div class="warnbox">⚠️ {ESC(str(exc))}</div>'))
        self._redirect("/review")

    def log_message(self, *a):  # 静默
        pass


if __name__ == "__main__":
    P.cmd_init()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    print(f"审核台 http://127.0.0.1:{port}  (Ctrl+C 停止)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
