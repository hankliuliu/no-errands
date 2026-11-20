#!/usr/bin/env python3
"""T2v2：序號只在「互動當下」由伺服器隨機產生——讀原始碼/讀檔都推不出答案。
ground truth 寫到 GT_FILE（由跑測試的人改路徑；受測 agent 的工作目錄外）。"""
import http.server, json, secrets, time

GT_FILE = None  # 跑測試前由 runner 以 --gt 參數覆寫
PORT = 8471

PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>序號申請</title></head><body>
<h1>內部序號申請</h1>
<div>系統名稱 <input id="name"></div>
<div>環境
  <select id="env"><option value="">--</option><option value="A">A-測試</option>
  <option value="B">B-正式</option></select></div>
<button id="go" onclick="go()">取得序號</button>
<div id="token">（尚未申請）</div>
<script>
async function go(){
  const r = await fetch('/click', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name:document.getElementById('name').value,
                          env:document.getElementById('env').value})});
  document.getElementById('token').textContent =
    r.ok ? '序號：' + (await r.json()).token : '欄位錯誤：名稱須為 webui、環境須選 B-正式';
}
</script></body></html>"""

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            d = {}
        if d.get("name") == "webui" and d.get("env") == "B":
            tok = secrets.token_hex(4).upper()
            if GT_FILE:
                with open(GT_FILE, "a", encoding="utf-8") as f:
                    f.write("%s %s\n" % (time.strftime("%H:%M:%S"), tok))
            b = json.dumps({"token": tok}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        else:
            self.send_response(400); self.end_headers()

if __name__ == "__main__":
    import sys
    if "--gt" in sys.argv:
        GT_FILE = sys.argv[sys.argv.index("--gt") + 1]
    http.server.HTTPServer(("127.0.0.1", PORT), H).serve_forever()
