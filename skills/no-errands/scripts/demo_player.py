#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示範播放器：像播影片一樣——字幕＋暫停／繼續、下一步、自動／逐步、停止。純 stdlib（tkinter；沒有 tk 退回本機網頁）。

  demo_player.py <run 資料夾> [--font 22] [--web]

- 讀引擎寫的 <run>/now.json（第幾步、角色、字幕、狀態、等待秒數），寫 <run>/control.json（與
  `demo_runbook.py control` 相同格式）。不碰目標網頁、不注入 DOM。
- 字幕與按鈕在同一個置頂視窗；按住空白處整塊拖動，位置記在 <home>/player.json，下次開在同一處；「歸位」回到
  引擎排好的預設位置（瀏覽器下方）。
- 不搶焦點（Windows：WS_EX_NOACTIVATE）：按按鈕、拖動之後，鍵盤仍在瀏覽器上，講者可以直接打字。
- 出錯時字幕顯示該步的備案說明，「備案畫面」打開最近一次成功排練的截圖。
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy  # noqa: E402
import nowin  # noqa: E402,F401

STATUS = {"running": "執行中", "waiting_system": "等待系統回應 {w} 秒（伺服器的模型仍在處理，暫停不了）",
          "paused": "已暫停", "waiting_next": "等你按「下一步」", "failed": "這一步沒有完成——看備案",
          "done": "示範結束", "stopped": "已停止"}


def read_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def write_control(run_dir, **kw):
    """與 demo_runbook.write_control 相同：state=playing|paused|stopped、advance=auto|step、next_seq 遞增＝下一步。"""
    p = os.path.join(run_dir, "control.json")
    c = read_json(p, {"state": "playing", "advance": "auto", "next_seq": 0})
    if kw.pop("next", False):
        c["next_seq"] = int(c.get("next_seq", 0)) + 1
        if c.get("state") == "paused":
            c["state"] = "playing"
    c.update(kw)
    c["ts"] = time.time()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)
    os.replace(tmp, p)
    return c


def pos_file():
    return os.path.join(policy.HOME, "player.json")


def open_file(p):
    if os.name == "nt":
        os.startfile(p)  # noqa
    else:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(p))


class Player:
    def __init__(self, run_dir, font_size=22):
        import tkinter as tk
        self.tk, self.run_dir = tk, run_dir
        self.root = r = tk.Tk()
        r.title("no-errands 示範播放器")
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        self.bg, self.fg, self.acc = "#15171c", "#f2f2f2", "#5b9cf6"
        r.configure(bg=self.bg)
        self.now, self.cap_i, self.cap_t0, self.step_seen = {}, 0, time.time(), None
        self._moved, self._homed = False, False
        self.frozen_at = None
        self.bar = tk.Canvas(r, height=5, bg="#2a2d35", highlightthickness=0)
        self.bar.pack(fill="x", side="top")
        body = tk.Frame(r, bg=self.bg)
        body.pack(fill="both", expand=True, padx=14, pady=8)
        self.left = tk.Label(body, text="", bg=self.bg, fg=self.acc, font=("Microsoft JhengHei UI", font_size - 4, "bold"),
                             justify="center", width=7)
        self.left.pack(side="left", padx=(0, 12))
        btns = tk.Frame(body, bg=self.bg)
        btns.pack(side="right")                            # 先排按鈕：窄螢幕時字幕讓位，按鈕不會被擠出去
        mid = tk.Frame(body, bg=self.bg)
        mid.pack(side="left", fill="both", expand=True)
        self.cap = tk.Label(mid, text="", bg=self.bg, fg=self.fg, font=("Microsoft JhengHei UI", font_size),
                            justify="left", anchor="w", wraplength=900)
        self.cap.pack(fill="x", anchor="w")
        self.stat = tk.Label(mid, text="", bg=self.bg, fg="#9aa0a6", font=("Microsoft JhengHei UI", max(11, font_size // 2)),
                             anchor="w")
        self.stat.pack(fill="x", anchor="w")
        bf = ("Microsoft JhengHei UI", max(12, font_size // 2 + 2), "bold")
        mk = lambda t, cmd: tk.Button(btns, text=t, command=cmd, font=bf, bg="#2a2d35", fg=self.fg,
                                      activebackground="#3a3f4a", activeforeground=self.fg, relief="flat", padx=10, pady=4,
                                      takefocus=0)
        self.b_pause = mk("⏸ 暫停", self.toggle_pause)
        self.b_next = mk("⏭ 下一步", lambda: write_control(run_dir, next=True))
        self.b_mode = mk("自動", self.toggle_mode)
        self.b_stop = mk("⏹ 停止", self.stop)
        self.b_home = mk("⌂ 歸位", self.home)
        self.b_planb = mk("備案畫面", self.planb)
        for b in (self.b_pause, self.b_next, self.b_mode, self.b_stop, self.b_home):
            b.pack(side="left", padx=3)
        # 拖動：按住空白處（不是按鈕）整塊移動
        for w in (r, body, mid, self.left, self.cap, self.stat, self.bar, btns):
            w.bind("<ButtonPress-1>", self.drag_start)
            w.bind("<B1-Motion>", self.drag_move)
            w.bind("<ButtonRelease-1>", self.drag_end)
        for key, fn in (("<space>", lambda e: self.toggle_pause()), ("<Right>", lambda e: write_control(run_dir, next=True)),
                        ("<Escape>", lambda e: self.stop())):
            r.bind(key, fn)
        self.place(initial=True)
        r.update_idletasks()
        self.no_activate()
        self.tick()
        if os.environ.get("NE_PLAYER_GEOM"):              # 測試用：寫出按鈕與字幕的螢幕座標
            r.after(800, self.dump_geom)

    def dump_geom(self):
        g = {}
        for name, w in (("pause", self.b_pause), ("next", self.b_next), ("stop", self.b_stop), ("home", self.b_home),
                        ("caption", self.cap)):
            g[name] = [w.winfo_rootx() + w.winfo_width() // 2, w.winfo_rooty() + w.winfo_height() // 2]
        g["window"] = [self.root.winfo_rootx(), self.root.winfo_rooty(), self.root.winfo_width(), self.root.winfo_height()]
        g["caption_text"] = self.cap.cget("text")
        tmp = os.environ["NE_PLAYER_GEOM"] + ".tmp"          # 先寫暫存再替換：讀的一方不會讀到一半
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(g, f, ensure_ascii=False)
        os.replace(tmp, os.environ["NE_PLAYER_GEOM"])
        self.root.after(500, self.dump_geom)

    # ---------- 視窗位置
    def default_geom(self):
        lay = (read_json(os.path.join(self.run_dir, "now.json")).get("layout") or {}).get("bar")
        if lay:
            return [int(v) for v in lay]
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        return [0, sh - 170 - 48, sw, 170]

    def place(self, initial=False):
        x, y, w, h = self.default_geom()
        saved = read_json(pos_file()).get("pos")
        if saved and initial:
            x, y = int(saved[0]), int(saved[1])
            w, h = int(saved[2]) if len(saved) > 2 else w, int(saved[3]) if len(saved) > 3 else h
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.cap.configure(wraplength=max(260, w - 700))

    def save_pos(self):
        g = self.root.winfo_geometry()                    # WxH+X+Y
        wh, x, y = g.split("+")[0], g.split("+")[1], g.split("+")[2]
        w, h = wh.split("x")
        os.makedirs(policy.HOME, exist_ok=True)
        with open(pos_file(), "w", encoding="utf-8") as f:
            json.dump({"pos": [int(x), int(y), int(w), int(h)], "ts": time.time()}, f)

    def home(self):
        self._homed, self._moved = True, False
        try:
            os.remove(pos_file())
        except FileNotFoundError:
            pass
        self.place()

    def drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()
        self._moved, self._homed = True, False

    def drag_move(self, e):
        self.root.geometry("+%d+%d" % (e.x_root - self._dx, e.y_root - self._dy))
        if time.time() - getattr(self, "_last_save", 0) > 0.3:      # 拖動途中也存（放開事件可能沒送到）
            self._last_save = time.time()
            self.save_pos()

    def drag_end(self, e):
        self.save_pos()

    def keep_pos(self):
        """畫面更新時：位置跟記住的不同（拖動後沒存到）就補存。歸位後不存。"""
        if getattr(self, "_homed", False):
            return
        try:
            cur = [self.root.winfo_x(), self.root.winfo_y()]
            saved = (read_json(pos_file()).get("pos") or [None, None])[:2]
            if self._moved and cur != saved:
                self.save_pos()
        except Exception:
            pass

    def no_activate(self):
        """Windows：點播放器不會搶走鍵盤焦點（WS_EX_NOACTIVATE｜TOOLWINDOW）。"""
        if os.name != "nt":
            return
        try:
            import ctypes
            u = ctypes.windll.user32
            hwnd = u.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            GWL_EXSTYLE, NOACT, TOOL, TOP = -20, 0x08000000, 0x00000080, 0x00000008
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, u.GetWindowLongW(hwnd, GWL_EXSTYLE) | NOACT | TOOL | TOP)
        except Exception:
            pass

    # ---------- 控制
    def toggle_pause(self):
        c = read_json(os.path.join(self.run_dir, "control.json"))
        write_control(self.run_dir, state="playing" if c.get("state") == "paused" else "paused")

    def toggle_mode(self):
        c = read_json(os.path.join(self.run_dir, "control.json"))
        write_control(self.run_dir, advance="auto" if c.get("advance") == "step" else "step")

    def stop(self):
        st = self.now.get("status")
        if st in ("done", "stopped", "failed"):
            self.root.destroy()
            return
        write_control(self.run_dir, state="stopped")

    def planb(self):
        p = self.now.get("planb")
        if p and os.path.exists(p):
            open_file(p)

    # ---------- 畫面更新
    def tick(self):
        now = read_json(os.path.join(self.run_dir, "now.json"))
        ctl = read_json(os.path.join(self.run_dir, "control.json"))
        self.now = now
        st = now.get("status", "running")
        if now.get("step_id") != self.step_seen:
            self.step_seen, self.cap_i, self.cap_t0 = now.get("step_id"), 0, time.time()
        caps = now.get("captions") or [{"text": "", "s": 6}]
        paused = ctl.get("state") == "paused" or st == "paused"
        if paused:                                         # 暫停：字幕計時凍結
            self.frozen_at = self.frozen_at or time.time()
        elif self.frozen_at:
            self.cap_t0 += time.time() - self.frozen_at
            self.frozen_at = None
        else:
            if time.time() - self.cap_t0 >= float(caps[self.cap_i % len(caps)].get("s", 6)) and self.cap_i < len(caps) - 1:
                self.cap_i += 1
                self.cap_t0 = time.time()
        self.cap.configure(text=caps[min(self.cap_i, len(caps) - 1)].get("text", ""))
        i, n = now.get("step_index") or 0, now.get("steps_total") or 0
        self.left.configure(text="%s/%s\n%s" % (i, n, now.get("role") or ""))
        self.stat.configure(text=STATUS.get("paused" if paused and st not in ("done", "stopped") else st, st)
                            .format(w=now.get("waited_s", 0)))
        self.b_pause.configure(text="▶ 繼續" if paused else "⏸ 暫停")
        self.b_mode.configure(text="逐步" if ctl.get("advance") == "step" else "自動")
        self.b_next.configure(bg=self.acc if st == "waiting_next" else "#2a2d35")
        if st in ("done", "stopped", "failed"):
            self.b_stop.configure(text="✕ 關閉")
        if st == "failed" and now.get("planb") and not self.b_planb.winfo_ismapped():
            self.b_planb.pack(side="left", padx=3)
        w = self.bar.winfo_width()
        self.bar.delete("all")
        if n:
            self.bar.create_rectangle(0, 0, int(w * i / n), 5, fill=self.acc, width=0)
        self.keep_pos()
        self.root.after(250, self.tick)

    def run(self):
        self.root.mainloop()


WEB = r"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>示範播放器</title>
<style>body{margin:0;background:#15171c;color:#f2f2f2;font:22px system-ui,"Microsoft JhengHei"}#b{height:5px;background:#5b9cf6;width:0}
.r{display:flex;gap:14px;align-items:center;padding:10px 14px}#l{color:#5b9cf6;font-weight:bold;min-width:7em;text-align:center}
#c{flex:1}#s{color:#9aa0a6;font-size:13px}button{font:inherit;font-size:16px;background:#2a2d35;color:#f2f2f2;border:0;padding:6px 12px;border-radius:6px}</style>
<div id="b"></div><div class="r"><div id="l"></div><div id="c"><div id="t"></div><div id="s"></div></div>
<button onclick="c('pause')">暫停／繼續</button><button onclick="c('next')">下一步</button><button onclick="c('mode')">自動／逐步</button><button onclick="c('stop')">停止</button></div>
<script>async function c(a){await fetch('control',{method:'POST',body:a})}
async function t(){try{const n=await (await fetch('now',{cache:'no-store'})).json();
document.getElementById('l').innerText=(n.step_index||0)+'/'+(n.steps_total||0)+'\n'+(n.role||'');
document.getElementById('t').innerText=((n.captions||[{}])[0]||{}).text||'';document.getElementById('s').innerText=n.status||'';
document.getElementById('b').style.width=(n.steps_total?100*n.step_index/n.steps_total:0)+'%'}catch(e){}}setInterval(t,500);t()</script></html>"""


def web_player(run_dir):
    """沒有 tk 的環境：本機網頁版（只綁 127.0.0.1）。"""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import webbrowser

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = (json.dumps(read_json(os.path.join(run_dir, "now.json")), ensure_ascii=False) if self.path == "/now"
                    else WEB).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json" if self.path == "/now" else "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            a = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0)).decode()
            c = read_json(os.path.join(run_dir, "control.json"))
            m = {"pause": {"state": "playing" if c.get("state") == "paused" else "paused"}, "next": {"next": True},
                 "mode": {"advance": "auto" if c.get("advance") == "step" else "step"}, "stop": {"state": "stopped"}}
            if a in m:
                write_control(run_dir, **m[a])
            self.send_response(204)
            self.end_headers()
    srv = HTTPServer(("127.0.0.1", 0), H)
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    print(json.dumps({"ok": True, "web_player": url}), flush=True)
    webbrowser.open(url)
    srv.serve_forever()


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); sys.exit(2)
    run_dir = a[0]
    font = int(a[a.index("--font") + 1]) if "--font" in a else 22
    if "--web" in a:
        web_player(run_dir); return
    try:
        import tkinter  # noqa: F401
    except Exception:
        web_player(run_dir); return
    Player(run_dir, font).run()


if __name__ == "__main__":
    main()
