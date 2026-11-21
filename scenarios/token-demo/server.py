#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Token demo site (stdlib). Mimics "Profile -> Security -> Personal access token":
  /login (demo/demo123) -> /tokens -> fill description -> [Create] -> the new token is shown ONCE in #new-token.
The sha256 of every issued token is appended to --issued <file> so tests can verify without printing it."""
import hashlib, http.cookies, secrets, sys, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8472
ISSUED = sys.argv[sys.argv.index("--issued") + 1] if "--issued" in sys.argv else None
SESS = set()

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>%s</title></head><body>%s</body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _page(self, title, body, code=200, cookie=None):
        b = (PAGE % (title, body)).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", "text/html; charset=utf-8")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def _redirect(self, to, cookie=None):
        self.send_response(302); self.send_header("Location", to)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _authed(self):
        c = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return "sid" in c and c["sid"].value in SESS

    def _form(self):
        n = int(self.headers.get("Content-Length", "0"))
        return dict(urllib.parse.parse_qsl(self.rfile.read(n).decode("utf-8")))

    def do_GET(self):
        if self.path.startswith("/login"):
            return self._page("Sign in", '<form method="post" action="/login"><input id="user" name="user">'
                              '<input id="pass" name="pass" type="password"><button id="signin">Sign in</button></form>')
        if not self._authed():
            return self._redirect("/login")
        if self.path.startswith("/tokens"):
            return self._page("Personal access tokens", '<h1>Personal access tokens</h1><form method="post" action="/tokens">'
                              '<input id="desc" name="desc" placeholder="Description"><button id="create">Create token</button></form>')
        return self._page("Home", '<a id="tokens" href="/tokens">Security</a>')

    def do_POST(self):
        f = self._form()
        if ISSUED:
            with open(ISSUED + ".posts", "a") as fh:
                fh.write("%s authed=%s\n" % (self.path, self._authed()))
        if self.path == "/login":
            if f.get("user") == "demo" and f.get("pass") == "demo123":
                sid = secrets.token_hex(16); SESS.add(sid)
                return self._redirect("/", cookie="sid=%s; Path=/; HttpOnly" % sid)
            return self._redirect("/login")
        if self.path == "/tokens" and self._authed():
            tok = secrets.token_hex(13)   # 26 chars, like Mattermost
            if ISSUED:
                with open(ISSUED, "a") as fh:
                    fh.write(hashlib.sha256(tok.encode()).hexdigest() + "\n")
            return self._page("Token created", '<p>Copy it now, it will not be shown again.</p>'
                              '<code id="new-token">%s</code><p id="desc-echo">%s</p>' % (tok, f.get("desc", "")))
        self.send_response(403); self.end_headers()


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
