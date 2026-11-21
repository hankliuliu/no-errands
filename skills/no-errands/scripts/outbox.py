#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""no-errands 核准發件匣（Outbox）——純 stdlib。

agent 想傳訊息時只能「起草」；草稿出現在固定的本機頁面，使用者看過、可修改、按核准後，
由**本伺服器程序**呼叫送出轉接器。agent 沒有送出的子指令。

agent 可用的子指令（只有這三個；沒有 send）：
  outbox.py draft --service <fake|mattermost> --to <對象> --body-file <檔> --why "<來由>"
      → 草稿寫進 ~/.no-errands/outbox/pending/，確保伺服器在跑，回 {id, url}
  outbox.py status <id>
      → pending | sent | rejected（附使用者的退回原因）；60 秒內查同一 id 超過 3 次會附 stop_polling
  outbox.py wait <id> [--timeout 秒]
      → 有上限（120 秒）的等待；一有結果就回，逾時回 pending 與「現在結束回合」

人或安裝程式用的：
  outbox.py serve            # 前景啟動伺服器（draft 會自動在背景啟動它）
  outbox.py url              # 印出頁面網址

對象（to）：fake → 假聊天視窗裡的聯絡人名稱；mattermost → 頻道 id 或 @使用者名稱（私訊）。
頁面上顯示的「送到」標籤由伺服器自己用唯讀查詢解析，不採用 agent 提供的任何文字；
解析不出來就不能核准。

限制（誠實版）：核准需要只出現在頁面裡的隨機密鑰、核對使用者看到的修訂雜湊、檢查
Host/Origin，並擋下自動化瀏覽器。這擋得住「過度熱心」的 agent 順手按核准，**不是**對抗
惡意程式的安全邊界——能在你電腦上任意執行程式的東西，本來就能做任何事。
"""
import hashlib, json, os, secrets, socket, subprocess, sys, threading, time, urllib.error
import urllib.parse, urllib.request, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy  # noqa: E402
import mm_common  # noqa: E402

HOME = policy.HOME
BOX = os.path.join(HOME, "outbox")
STATES = ("pending", "sent", "rejected")
SERVICES = ("fake", "mattermost")
MAX_BODY = 20000
LOCK = threading.Lock()
RESOLVE_CACHE = {}  # (service, to) -> (ts, result)
RESOLVE_TTL_OK, RESOLVE_TTL_FAIL = 300, 15
AUTOMATION_UA = ("headlesschrome", "headless", "phantomjs", "playwright", "puppeteer")


# ---------------------------------------------------------------- 儲存

def _dir(state):
    d = os.path.join(BOX, state)
    os.makedirs(d, exist_ok=True)
    return d


def _write(state, rec):
    p = os.path.join(_dir(state), rec["id"] + ".json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def find(draft_id):
    if not draft_id or not all(c.isalnum() or c == "-" for c in draft_id):
        return None, None
    for st in STATES:
        p = os.path.join(_dir(st), draft_id + ".json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return st, json.load(f)
    return None, None


def list_state(state, limit=200):
    d = _dir(state)
    out = []
    for fn in os.listdir(d):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    out.append(json.load(f))
            except Exception:
                pass
    key = "created" if state == "pending" else "decided"
    out.sort(key=lambda r: r.get(key, ""), reverse=(state != "pending"))
    return out[:limit]


def move(rec, src, dst):
    _write(dst, rec)
    try:
        os.remove(os.path.join(_dir(src), rec["id"] + ".json"))
    except FileNotFoundError:
        pass


def rev_of(rec):
    """使用者看到的那一版的修訂雜湊（service|to|body|why）。"""
    h = hashlib.sha256()
    for k in ("service", "to", "body", "why"):
        h.update(str(rec.get(k, "")).encode("utf-8") + b"\x1f")
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- 對象解析（伺服器端、唯讀）

def _fake_contacts_path(pol):
    return pol.get("outbox", {}).get("fake_contacts") or os.path.join(BOX, "fake-chat-contacts.json")


def resolve_fake(rec, pol):
    """假聊天視窗啟動時把聯絡人清單寫到 fake-chat-contacts.json；對象必須在清單裡。"""
    p = _fake_contacts_path(pol)
    if not os.path.exists(p):
        raise RuntimeError("假聊天視窗沒有在跑（找不到它的聯絡人清單）")
    with open(p, encoding="utf-8") as f:
        contacts = json.load(f)
    if rec["to"] not in contacts:
        raise RuntimeError("假聊天視窗裡沒有這個對象")
    return {"label": "假聊天視窗（測試）：%s" % rec["to"]}


def _mm_conf(pol):
    mm = pol.get("mattermost", {})
    return mm_common.conf(mm.get("token_file") or pol.get("outbox", {}).get("mattermost_token_file"), mm.get("url"))


def resolve_mattermost(rec, pol):
    base, tok = _mm_conf(pol)
    return {"label": "Mattermost：" + mm_common.resolve_target(base, tok, rec["to"])["label"]}


RESOLVERS = {"fake": resolve_fake, "mattermost": resolve_mattermost}


def resolve(rec, pol, fresh=False):
    """回 {resolved: True, label} 或 {resolved: False, resolve_error}。"""
    key = (rec["service"], rec["to"])
    now = time.time()
    hit = RESOLVE_CACHE.get(key)
    if hit and not fresh:
        ttl = RESOLVE_TTL_OK if hit[1].get("resolved") else RESOLVE_TTL_FAIL
        if now - hit[0] < ttl:
            return hit[1]
    try:
        out = {"resolved": True, "label": RESOLVERS[rec["service"]](rec, pol)["label"]}
    except Exception as e:
        out = {"resolved": False, "resolve_error": str(e)[:200]}
    RESOLVE_CACHE[key] = (now, out)
    return out


# ---------------------------------------------------------------- 送出轉接器（只由伺服器在核准後呼叫）

def _mm_post(base, tok, path, body):
    """整個系統唯一的 Mattermost POST。"""
    req = urllib.request.Request(base + "/api/v4" + path, method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + tok,
                                          "Content-Type": "application/json",
                                          "User-Agent": "no-errands-outbox"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def send_mattermost(rec, text, pol):
    base, tok = _mm_conf(pol)
    to = rec["to"].strip()
    if to.startswith("@"):
        me = mm_common.get(base, tok, "/users/me")
        other = mm_common.get(base, tok, "/users/username/" + urllib.parse.quote(to[1:]))
        channel_id = _mm_post(base, tok, "/channels/direct", [me["id"], other["id"]])["id"]
    else:
        channel_id = to
    post = _mm_post(base, tok, "/posts", {"channel_id": channel_id, "message": text})
    return {"post_id": post.get("id"), "channel_id": channel_id}


def send_fake(rec, text, pol):
    """測試用：寫進假聊天視窗監看的檔案（這條路有標記，不算違規）。"""
    feed = pol.get("outbox", {}).get("fake_feed") or os.path.join(BOX, "fake-chat-feed.jsonl")
    os.makedirs(os.path.dirname(feed), exist_ok=True)
    with open(feed, "a", encoding="utf-8") as f:
        f.write(json.dumps({"via": "ne-outbox", "id": rec["id"], "to": rec["to"], "text": text,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False) + "\n")
    return {"feed": os.path.basename(feed)}


ADAPTERS = {"fake": send_fake, "mattermost": send_mattermost}


# ---------------------------------------------------------------- 伺服器

def port_of(pol=None):
    return int((pol or policy.load(HOME)).get("outbox", {}).get("port", 8765))


def url_of(pol=None):
    return "http://127.0.0.1:%d/" % port_of(pol)


def ping(pol=None, timeout=1.0):
    try:
        with urllib.request.urlopen(url_of(pol) + "api/ping", timeout=timeout) as r:
            return json.loads(r.read().decode()).get("app") == "ne-outbox"
    except Exception:
        return False


PAGE = r"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NE-OUTBOX 不求人發件匣</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1f2328;--mute:#656d76;--line:#d8d6cf;--ok:#1a7f37;--no:#b42318;--acc:#0b57d0;--warn:#9a6700}
@media (prefers-color-scheme:dark){:root{--bg:#16181c;--card:#202329;--ink:#e6e6e6;--mute:#9aa0a6;--line:#363a42;--ok:#3fb950;--no:#f47067;--acc:#6ea8fe;--warn:#d29922}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,"Microsoft JhengHei","PingFang TC",sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 16px;display:flex;gap:16px;align-items:center;flex-wrap:wrap;z-index:1}
h1{font-size:17px;margin:0}nav a{color:var(--mute);text-decoration:none;margin-right:12px;padding-bottom:3px}nav a.on{color:var(--ink);border-bottom:2px solid var(--acc)}
.badge{background:var(--acc);color:#fff;border-radius:10px;padding:0 7px;font-size:12px;margin-left:4px}
main{max-width:860px;margin:0 auto;padding:16px}
.note{color:var(--mute);font-size:13px;margin:0 0 14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:14px}
.meta{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px;font-size:13px;color:var(--mute);margin-bottom:10px}
.to{font-size:15px;color:var(--ink);font-weight:600}.to.bad{color:var(--no)}.rawid{color:var(--mute);font-size:12px;margin-left:8px;word-break:break-all}
.why{background:color-mix(in srgb,var(--warn) 10%,transparent);border-left:3px solid var(--warn);padding:6px 10px;font-size:13px;margin-bottom:10px;white-space:pre-wrap}
textarea{width:100%;min-height:110px;font:inherit;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;resize:vertical}
.edited{color:var(--warn);font-size:12px;visibility:hidden}.edited.on{visibility:visible}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}
button{font:inherit;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:7px 14px;cursor:pointer}
button.ok{background:var(--ok);border-color:var(--ok);color:#fff}button.no{color:var(--no);border-color:var(--no)}
button:disabled{opacity:.45;cursor:not-allowed}
input.reason{flex:1;min-width:160px;font:inherit;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
.empty{color:var(--mute);text-align:center;padding:40px 0}
.st-sent{color:var(--ok)}.st-rejected{color:var(--no)}
pre{white-space:pre-wrap;margin:6px 0 0;font:inherit;background:var(--bg);border-radius:8px;padding:8px 10px}
.err{color:var(--no);font-size:13px}
#robot{border-color:var(--no)}
</style></head><body>
<header><h1>📮 不求人發件匣</h1>
<nav><a href="#pending" id="t-p" class="on">待核准<span class="badge" id="cnt">0</span></a><a href="#history" id="t-h">歷史</a></nav></header>
<main>
<p class="note">agent 起草、你決定。只有按下「核准送出」後，發件匣才會把<b>框裡顯示的文字</b>原封不動送到<b>「送到」標示的對象</b>。規則禁止 agent 操作這一頁；每次核准都有紀錄。</p>
<div id="robot" class="card err" hidden>偵測到自動化瀏覽器：這個視窗不能核准。請用你平常的瀏覽器開這一頁。</div>
<div id="view"></div>
</main>
<script>
const KEY=__KEY__;const ROBOT=!!navigator.webdriver;if(ROBOT)document.getElementById("robot").hidden=false;
let data={pending:[],sent:[],rejected:[]};let sig="";
const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function tab(){return location.hash==="#history"?"history":"pending"}
async function load(){
  try{const r=await fetch("api/state",{cache:"no-store"});data=await r.json();}catch(e){return}
  $("#cnt").textContent=data.pending.length;
  document.title=(data.pending.length?"("+data.pending.length+") ":"")+"NE-OUTBOX 不求人發件匣";
  const s=tab()+JSON.stringify(data.pending.map(d=>d.id+d.rev+d.resolved))+JSON.stringify([...data.sent,...data.rejected].map(d=>d.id+d.state));
  if(s!==sig){sig=s;render()}
}
function who(d){
  if(d.state==="pending"&&!d.resolved)return `<span class="to bad">⚠ 無法確認對象：${esc(d.resolve_error)}</span><span class="rawid">${esc(d.to)}</span>`;
  const lab=d.label||d.to;
  return `<span class="to">${esc(lab)}</span>`+(lab.indexOf(d.to)<0?`<span class="rawid">${esc(d.to)}</span>`:"")}
function meta(d){return `<div class="meta"><span>送到</span><span>${who(d)}</span><span>建立</span><span>${esc(d.created)}</span><span>來源專案</span><span title="${esc(d.project)}">${esc(d.project_name||"—")}</span></div>`}
function render(){
  $("#t-p").className=tab()==="pending"?"on":"";$("#t-h").className=tab()==="history"?"on":"";
  const v=$("#view");
  if(tab()==="pending"){
    if(!data.pending.length){v.innerHTML='<div class="empty">目前沒有等你核准的訊息。</div>';return}
    v.innerHTML=data.pending.map(d=>`<div class="card" data-id="${esc(d.id)}">${meta(d)}
      ${d.why?`<div class="why">agent 的來由：${esc(d.why)}</div>`:""}
      <textarea aria-label="訊息內文">${esc(d.body)}</textarea>
      <div class="edited">已修改：送出的會是修改後的版本</div>
      <div class="row"><button class="ok" data-a="approve">核准送出</button>
      <input class="reason" placeholder="退回原因（選填，agent 會看到）"><button class="no" data-a="reject">退回</button></div>
      <div class="err">${d.last_error?"上次送出失敗："+esc(d.last_error):""}</div></div>`).join("");
    v.querySelectorAll(".card").forEach(c=>{
      const d=data.pending.find(x=>x.id===c.dataset.id),ta=c.querySelector("textarea"),ok=c.querySelector("[data-a=approve]");
      ta.addEventListener("input",()=>{const ch=ta.value!==d.body;c.querySelector(".edited").classList.toggle("on",ch);ok.textContent=ch?"修改後送出":"核准送出"});
      c.querySelectorAll("button").forEach(b=>b.addEventListener("click",()=>decide(c,d,b.dataset.a)));
      if(!d.resolved)ok.disabled=true;
      if(ROBOT)c.querySelectorAll("button").forEach(b=>b.disabled=true);
    });
  }else{
    const h=[...data.sent,...data.rejected].sort((a,b)=>(b.decided||"").localeCompare(a.decided||""));
    if(!h.length){v.innerHTML='<div class="empty">還沒有紀錄。</div>';return}
    v.innerHTML=h.map(d=>`<div class="card">${meta(d)}
      <div class="st-${d.state}"><b>${d.state==="sent"?(d.edited?"已送出（你修改過）":"已送出"):"已退回"}</b> · ${esc(d.decided)}</div>
      ${d.state==="sent"?`<pre>${esc(d.sent_text)}</pre>`:`<pre>${esc(d.body)}</pre>${d.reject_reason?`<div class="note">退回原因：${esc(d.reject_reason)}</div>`:""}`}
      </div>`).join("");
  }
}
async function decide(c,d,a){
  const ta=c.querySelector("textarea"),text=ta.value,err=c.querySelector(".err");
  if(a==="approve"&&!text.trim()){err.textContent="內文是空的";return}
  const btns=c.querySelectorAll("button");btns.forEach(b=>b.disabled=true);
  const body={id:d.id,action:a==="approve"?(text!==d.body?"edit":"approve"):"reject",text,reason:c.querySelector(".reason").value,rev:d.rev,key:KEY};
  try{const r=await fetch("api/decide",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const j=await r.json();
    if(!j.ok){err.textContent=j.error||"失敗";if(j.stale){sig="";setTimeout(load,1500)}else btns.forEach(b=>b.disabled=false);return}
  }catch(e){err.textContent="連不上發件匣伺服器";btns.forEach(b=>b.disabled=false);return}
  sig="";load();
}
window.addEventListener("hashchange",()=>{sig="";render()});
load();setInterval(load,2000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "ne-outbox"

    def log_message(self, fmt, *args):  # 不記錄請求內容
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _host_ok(self):
        port = self.server.server_address[1]
        allowed = {"127.0.0.1:%d" % port, "localhost:%d" % port}
        if self.headers.get("Host", "") not in allowed:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {"http://" + a for a in allowed}

    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {"ok": False, "error": "host not allowed"})
        path = self.path.split("?")[0]
        if path == "/":
            return self._send(200, PAGE.replace("__KEY__", json.dumps(self.server.key)),
                              "text/html; charset=utf-8")
        if path == "/api/ping":
            return self._json(200, {"app": "ne-outbox"})
        if path == "/api/state":
            pol = policy.load(HOME)
            out = {}
            for st in STATES:
                rows = []
                for r in list_state(st):
                    r = dict(r, state=st,
                             project_name=os.path.basename((r.get("project") or "").rstrip("\\/")))
                    if st == "pending":
                        r.update(rev=rev_of(r), **resolve(r, pol))
                    rows.append(r)
                out[st] = rows
            return self._json(200, out)
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._host_ok():
            return self._json(403, {"ok": False, "error": "host not allowed"})
        if self.path != "/api/decide":
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(min(n, 200000)).decode("utf-8"))
        except Exception:
            return self._json(400, {"ok": False, "error": "bad request"})
        if not secrets.compare_digest(str(req.get("key", "")), self.server.key):
            return self._json(403, {"ok": False, "error": "核准只能在發件匣頁面由使用者操作（缺少頁面密鑰）"})
        action = req.get("action")
        ua = self.headers.get("User-Agent", "")[:300]
        if action != "reject" and any(m in ua.lower() for m in AUTOMATION_UA):
            return self._json(403, {"ok": False, "error": "自動化瀏覽器不可核准（請用你平常的瀏覽器）"})
        with LOCK:
            st, rec = find(str(req.get("id", "")))
            if st != "pending":
                return self._json(409, {"ok": False, "error": "這則草稿已經不在待核准清單"})
            if str(req.get("rev", "")) != rev_of(rec):
                return self._json(409, {"ok": False, "stale": True,
                                        "error": "這則草稿在你打開頁面之後被改過（對象或內容），已重新載入，請再確認一次"})
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            if action == "reject":
                rec.update(decided=now, decided_via=ua, reject_reason=str(req.get("reason", ""))[:1000])
                move(rec, "pending", "rejected")
                return self._json(200, {"ok": True, "state": "rejected"})
            if action not in ("approve", "edit"):
                return self._json(400, {"ok": False, "error": "unknown action"})
            text = str(req.get("text", ""))
            if not text.strip() or len(text) > MAX_BODY:
                return self._json(400, {"ok": False, "error": "內文是空的或太長"})
            edited = text != rec["body"]
            pol = policy.load(HOME)
            who = resolve(rec, pol, fresh=True)
            if not who.get("resolved"):
                return self._json(409, {"ok": False, "error": "無法確認對象，不能送出：" + who.get("resolve_error", "")})
            try:
                result, err = ADAPTERS[rec["service"]](rec, text, pol), None
            except urllib.error.HTTPError as e:
                result, err = None, "HTTP %d" % e.code
            except Exception as e:
                result, err = None, str(e)[:300]
            if err:
                rec["last_error"] = err
                _write("pending", rec)  # 留在待核准，讓使用者看到錯誤、可重試
                return self._json(502, {"ok": False, "error": "送出失敗：" + err})
            rec.pop("last_error", None)
            rec.update(decided=now, decided_via=ua, sent_text=text, edited=edited, result=result,
                       label=who["label"])
            move(rec, "pending", "sent")
            return self._json(200, {"ok": True, "state": "sent", "edited": edited})


class Server(ThreadingHTTPServer):
    # Windows 的 SO_REUSEADDR 允許搶別人已在聽的埠 → 關掉，並要求獨占。
    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve():
    pol = policy.load(HOME)
    port = port_of(pol)
    srv = Server(("127.0.0.1", port), Handler)
    srv.key = secrets.token_urlsafe(24)   # 只存在記憶體與頁面裡
    for st in STATES:
        _dir(st)
    print(json.dumps({"ok": True, "url": url_of(pol), "pid": os.getpid()}), flush=True)
    srv.serve_forever()


def port_busy(port):
    """試著自己綁定該埠：綁不上就是被占用。（用 connect 判斷在 Windows 不可靠：
    對方 backlog 滿時 connect 會逾時，看起來像沒人在聽。）"""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def ensure_server(pol):
    """回 (ok, error)。埠被別的程式占用 → 不啟動，回明確錯誤與 recovery。"""
    if ping(pol):
        return True, None
    port = port_of(pol)
    if port_busy(port):
        return False, ("埠 %d 被別的程式占用（不是發件匣）。recovery：把 %s 的 outbox.port 改成沒用到的埠"
                       "（例如 8766）再重跑；草稿已保存，不會遺失。" % (port, policy.path(HOME)))
    log = os.path.join(BOX, "server.log")
    os.makedirs(BOX, exist_ok=True)
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED|NEW_GROUP|NO_WINDOW
    else:
        kw["start_new_session"] = True
    with open(log, "a", encoding="utf-8") as lf:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "serve"], stdin=subprocess.DEVNULL,
                         stdout=lf, stderr=lf, env=dict(os.environ, NO_ERRANDS_HOME=HOME), **kw)
    for _ in range(50):
        time.sleep(0.1)
        if ping(pol):
            return True, None
    try:
        tail = open(log, encoding="utf-8", errors="replace").read()[-400:]
    except Exception:
        tail = ""
    return False, "發件匣伺服器沒有起來；log 尾段：" + tail


# ---------------------------------------------------------------- CLI

def _out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    sys.exit(code)


def _arg(a, name, default=None):
    if name in a:
        i = a.index(name)
        if i + 1 < len(a):
            return a[i + 1]
    return default


def cmd_draft(a):
    service, to, body_file, why = (_arg(a, "--service"), _arg(a, "--to"),
                                   _arg(a, "--body-file"), _arg(a, "--why", ""))
    if service not in SERVICES:
        _out({"ok": False, "error": "--service 必須是 %s 之一" % "|".join(SERVICES)}, 2)
    if not to or not body_file:
        _out({"ok": False, "error": "需要 --to 與 --body-file"}, 2)
    try:
        with open(body_file, encoding="utf-8-sig") as f:
            body = f.read().rstrip("\n")
    except Exception as e:
        _out({"ok": False, "error": "讀不到內文檔：%s" % e}, 2)
    if not body.strip() or len(body) > MAX_BODY:
        _out({"ok": False, "error": "內文是空的或超過 %d 字" % MAX_BODY}, 2)
    rec = {"id": time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3), "service": service,
           "to": to, "body": body, "why": why[:2000], "project": os.getcwd(),
           "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    _write("pending", rec)
    pol = policy.load(HOME)
    up, err = ensure_server(pol)
    if not up:
        _out({"ok": False, "id": rec["id"], "saved": True, "error": err,
              "next": "把錯誤與 recovery 告訴使用者；草稿已保存在待核准清單，伺服器起來後就會出現在頁面上"}, 1)
    opened = False
    if pol.get("visibility") == "watch":
        try:
            opened = bool(webbrowser.open(url_of(pol)))  # 觀看模式才自動用預設瀏覽器開
        except Exception:
            opened = False
    _out({"ok": True, "id": rec["id"], "url": url_of(pol), "server": "running", "page_opened": opened,
          "next": ("頁面已在使用者的瀏覽器開啟（觀看模式）。" if opened else
                   "告訴使用者：草稿在發件匣頁面等他核准（給上面的網址）。") +
                  "你不要開啟或操作那個頁面。最多等一次 `outbox.py wait %s`（上限 120 秒），沒有結果就結束回合；"
                  "不要自己寫迴圈反覆查詢。" % rec["id"]})


STOP_NEXT = "現在結束回合；使用者核准或退回後再繼續（不要在前景反覆查詢）"
WAIT_CAP = 120


def _status_obj(st, rec):
    out = {"ok": True, "id": rec["id"], "state": st}
    if st == "rejected":
        out["reason"] = rec.get("reject_reason", "")
    if st == "sent":
        out["edited"] = rec.get("edited", False)
        out["decided"] = rec.get("decided")
    if st == "pending" and rec.get("last_error"):
        out["last_error"] = rec["last_error"]
    return out


def _poll_count(draft_id):
    """記錄 status 呼叫時間；回傳最近 60 秒內對同一 id 的次數。"""
    p = os.path.join(BOX, "status-calls.json")
    now = time.time()
    try:
        calls = json.load(open(p, encoding="utf-8"))
    except Exception:
        calls = {}
    lst = [t for t in calls.get(draft_id, []) if now - t < 60] + [now]
    calls = {k: v for k, v in calls.items() if k == draft_id or (v and now - v[-1] < 600)}
    calls[draft_id] = lst[-20:]
    try:
        json.dump(calls, open(p, "w", encoding="utf-8"))
    except Exception:
        pass
    return len(lst)


def cmd_status(a):
    if len(a) < 2:
        _out({"ok": False, "error": "用法：outbox.py status <id>"}, 2)
    st, rec = find(a[1])
    if not st:
        _out({"ok": False, "error": "找不到這個草稿 id"}, 1)
    out = _status_obj(st, rec)
    if st == "pending" and _poll_count(rec["id"]) > 3:
        out.update(stop_polling=True, next=STOP_NEXT)
    _out(out)


def cmd_wait(a):
    """有上限的等待（最多 120 秒）：一有結果就回；逾時回 pending 與「結束回合」。"""
    if len(a) < 2:
        _out({"ok": False, "error": "用法：outbox.py wait <id> [--timeout 秒(<=120)]"}, 2)
    try:
        limit = min(WAIT_CAP, max(1, int(_arg(a, "--timeout", str(WAIT_CAP)))))
    except ValueError:
        limit = WAIT_CAP
    t0 = time.time()
    while True:
        st, rec = find(a[1])
        if not st:
            _out({"ok": False, "error": "找不到這個草稿 id"}, 1)
        if st != "pending" or time.time() - t0 >= limit:
            out = _status_obj(st, rec)
            if st == "pending":
                out.update(waited=int(time.time() - t0), next=STOP_NEXT)
            _out(out)
        time.sleep(1)


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); sys.exit(2)
    if a[0] == "draft":
        cmd_draft(a)
    elif a[0] == "status":
        cmd_status(a)
    elif a[0] == "wait":
        cmd_wait(a)
    elif a[0] == "serve":
        serve()
    elif a[0] == "url":
        print(url_of())
    else:
        _out({"ok": False, "error": "沒有這個子指令。agent 只能用 draft 與 status；沒有 send。"}, 2)


if __name__ == "__main__":
    main()
