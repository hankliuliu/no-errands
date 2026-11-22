#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示範舞台（Windows；tkinter＋Win32，純 stdlib）。取代給講者看的小播放器：觀眾只看到四樣東西——
現在是誰、他在操作的畫面、agent 動了哪裡、這一步的意思。

  demo_stage.py <執行資料夾>

讀 <執行資料夾>/now.json（引擎寫）、寫 control.json（和播放器同一格式：state／advance／next_seq）。
  背板：全螢幕不透明，蓋住桌面、工作列與其他程式；片頭、預備清單、換角色、片尾卡畫在背板上。
  角色帶（頂端）：名牌＋很短的段落名稱；同時蓋住移到舞台上方的瀏覽器殼與裁掉的頁面頂端。
  舞台：只放正在動作的角色的瀏覽器視窗（Win32 擺位置、不搶焦點）；另一個角色的視窗移到螢幕外。
  外框：「看這裡」與「agent 動到的元素」，用元素位置畫在舞台上（四條細的置頂視窗），不往頁面注入東西。
  字幕帶（底部）：全寬、一拍一句、最多兩行；右端只有「下一步」「暫停」「⋯」與還剩幾句的小圓點。
  鍵盤／簡報筆：→、PageDown 下一步；空白鍵暫停／繼續（RegisterHotKey，不靠焦點；舞台結束就取消）。
now.json 用到的欄位：status、card（title|checklist|transition|end|None）、card_data、account、role、role_color、
  section、crop、zoom、beat（{text,i,n}）、look（{box:[x,y,w,h] CSS}）、slow_s、waited_s、slow_say、planb、replay、
  title、accounts（{帳號:{role,color}}）。
"""
import ctypes, json, os, sys, threading, time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage_layout as L  # noqa: E402
import nowin  # noqa: E402,F401

BG, FG, DIM, ACCENT, LOOK = "#0b0d12", "#f4f4f4", "#7d8794", "#2f7de1", "#ffcc33"
FONT = "Microsoft JhengHei UI"
OFF = -32000                                            # 移到螢幕外
EXIT_HINT = "要離開這個畫面：按 Esc 或 Ctrl+Shift+F12，或雙擊背景"


def px(n):
    """Tk 的字級：正數是「點」（96 DPI 下大 33%，150% 縮放再放大）→ 一律用負數＝像素，排版才和 stage_layout 的估算一致。"""
    return -max(1, int(round(n)))


def read_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {} if default is None else default


def write_control(run_dir, **kw):
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
    for k in range(40):
        try:
            os.replace(tmp, p)
            break
        except PermissionError:
            if k == 39:
                raise
            time.sleep(0.05)
    return c


# ---------------------------------------------------------------- Win32：找到並擺放我們自己的瀏覽器視窗
if os.name == "nt":
    u32 = ctypes.windll.user32
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    SWP_NOACTIVATE, SWP_NOZORDER, SWP_SHOWWINDOW, SWP_NOMOVE, SWP_NOSIZE = 0x0010, 0x0004, 0x0040, 0x0002, 0x0001
    HWND_TOP, HWND_TOPMOST, HWND_NOTOPMOST = 0, -1, -2


def _cls(h):
    b = ctypes.create_unicode_buffer(128)
    u32.GetClassNameW(h, b, 128)
    return b.value


def _rect(h):
    r = wintypes.RECT()
    u32.GetWindowRect(h, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def profile_of_cmdline(cl):
    """Chrome 命令列裡的 --user-data-dir。參數可能整個包在引號裡（agent-browser 這樣啟動），也可能沒有引號。"""
    if "--user-data-dir=" not in (cl or ""):
        return None
    v = cl.split("--user-data-dir=", 1)[1]
    return v[1:].split('"', 1)[0] if v.startswith('"') else v.split('"', 1)[0].split(" --", 1)[0].strip()


def chrome_pids(profile):
    """命令列裡有這個 profile 的 Chrome 瀏覽器主行程（只認自己開的：--user-data-dir 完全相同）。"""
    import subprocess
    want = os.path.normcase(os.path.abspath(profile))
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                              "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20).stdout
    except Exception:
        return set()
    pids = set()
    for line in out.splitlines():
        pid, _, cl = line.partition("\t")
        if "--type=" in cl:
            continue
        v = profile_of_cmdline(cl)
        if v and os.path.normcase(os.path.abspath(v)) == want and pid.strip().isdigit():
            pids.add(int(pid))
    return pids


def top_window(pids):
    best = None

    def f(h, _):
        nonlocal best
        if not u32.IsWindowVisible(h) or _cls(h) != "Chrome_WidgetWin_1":
            return True
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value in pids:
            x, y, w, hh = _rect(h)
            if best is None or w * hh > best[1]:
                best = (h, w * hh)
        return True
    u32.EnumWindows(EnumProc(f), 0)
    return best and best[0]


def content_rect(hwnd):
    """頁面可視區（Chrome_RenderWidgetHostHWND）的螢幕矩形。"""
    found = []

    def f(h, _):
        if _cls(h) == "Chrome_RenderWidgetHostHWND" and u32.IsWindowVisible(h):
            found.append(_rect(h))
        return True
    u32.EnumChildWindows(hwnd, EnumProc(f), 0)
    return max(found, key=lambda r: r[2] * r[3]) if found else None


def pid_alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x00100000 | 0x1000, False, pid)   # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(h, 0) == 0x102      # WAIT_TIMEOUT＝還在跑
    finally:
        k32.CloseHandle(h)


def user_input_since(tick):
    """GetLastInputInfo 的時間晚於 tick（GetTickCount 單位）→ 有人動了鍵盤或滑鼠。"""
    class LII(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    li = LII(); li.cbSize = ctypes.sizeof(LII)
    u32.GetLastInputInfo(ctypes.byref(li))
    return li.dwTime > tick


def dpr_of(hwnd):
    try:
        return u32.GetDpiForWindow(hwnd) / 96.0
    except Exception:
        return 1.0


# ---------------------------------------------------------------- 舞台
class Stage:
    def __init__(self, run_dir):
        if os.name == "nt":
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)   # 實體像素（和瀏覽器視窗座標一致）
            except Exception:
                pass
        import tkinter as tk
        self.tk, self.run_dir = tk, run_dir
        self.now, self.wins, self.placed = {}, {}, {}
        self.t_start = time.time()
        self.tick0 = ctypes.windll.kernel32.GetTickCount() + 3000 if os.name == "nt" else 0
        self.root = r = tk.Tk()
        r.title("no-errands 示範舞台")
        W, H = r.winfo_screenwidth(), r.winfo_screenheight()
        if os.environ.get("NE_STAGE_SCREEN"):            # 測試：模擬解析度
            W, H = (int(v) for v in os.environ["NE_STAGE_SCREEN"].split("x"))
        self.W, self.H = W, H
        self.lay = lay = L.layout(W, H)
        # 背板：置頂（不靠搶前景——從捷徑帶起來的行程搶不到前景，第一次排練別的程式和工作列蓋在背板上）。
        # 上下順序每秒重排一次：背板 → 台上的瀏覽器 → 角色帶、字幕帶、外框。
        r.overrideredirect(True)
        r.geometry("%dx%d+0+0" % (W, H))
        r.attributes("-topmost", True)
        r.configure(bg=BG)
        self.card = tk.Canvas(r, bg=BG, highlightthickness=0, width=W, height=H)
        self.card.pack(fill="both", expand=True)
        self.card.bind("<Double-Button-1>", lambda e: self.drop("double_click"))   # 使用者自己的出口：雙擊背板
        r.bind_all("<Escape>", lambda e: self.drop("esc"))                        # 舞台有焦點時按 Esc（不攔全域）
        # 角色帶
        self.band = self._top(*lay["band"])
        self.band.configure(bg="#11151d")
        bf = lay["band_font"]
        self.chip = tk.Label(self.band, text="", font=(FONT, px(bf), "bold"), fg="#fff", bg=ACCENT, padx=bf // 1.5)
        self.chip.pack(side="left", padx=(lay["pad"], 14), pady=6)
        self.sect = tk.Label(self.band, text="", font=(FONT, px(int(bf * 0.8))), fg=DIM, bg="#11151d")
        self.sect.pack(side="left")
        self.where = tk.Label(self.band, text="", font=(FONT, px(int(bf * 0.62))), fg=DIM, bg="#11151d")
        self.where.pack(side="right", padx=lay["pad"])        # 「5 / 13 · 現場操作」：這是現場操作，不是錄影
        self.rule = tk.Frame(self.band, height=3, bg=ACCENT)
        self.rule.place(relx=0, rely=1.0, anchor="sw", relwidth=1.0)
        # 字幕帶
        cx, cy, cw, ch = lay["caption"]
        self.capw = self._top(cx, cy, cw, ch)
        self.capw.configure(bg=BG)
        tx, _, tw, _ = lay["caption_text"]
        self.cap = tk.Label(self.capw, text="", font=(FONT, px(lay["cap_font"]), "bold"), fg=FG, bg=BG, justify="left",
                            anchor="w", wraplength=tw)
        self.cap.place(x=tx, y=0, width=tw, height=ch)
        ox, _, ow, _ = lay["controls"]
        ctl = tk.Frame(self.capw, bg=BG)
        ctl.place(x=ox, y=0, width=ow, height=ch)
        cf = (FONT, px(lay["ctl_font"]), "bold")
        mk = lambda t, cmd: tk.Button(ctl, text=t, command=cmd, font=cf, bg="#1b2029", fg="#9aa4b2", relief="flat",
                                      activebackground="#2a3140", activeforeground=FG, padx=10, pady=4, takefocus=0, bd=0)
        row = tk.Frame(ctl, bg=BG)
        row.place(relx=1.0, rely=0.5, anchor="e")
        self.b_more = mk("⋯", self.more)
        self.b_pause = mk("⏸", self.toggle_pause)
        self.b_next = mk("下一步 →", self.next)
        self.b_planb = mk("備案畫面", self.planb)
        for b in (self.b_next, self.b_pause, self.b_more):
            b.pack(in_=row, side="left", padx=3)
        self.dots = tk.Canvas(ctl, bg=BG, highlightthickness=0, height=14, width=ow)
        self.dots.place(relx=1.0, rely=0.5, anchor="e", y=-lay["ctl_font"] * 2.4)
        self.state = tk.Label(ctl, text="", font=(FONT, px(max(11, lay["ctl_font"] - 2))), fg=ACCENT, bg=BG, anchor="e")
        self.state.place(relx=1.0, rely=0.5, anchor="e", y=lay["ctl_font"] * 2.4)
        # 外框：四條置頂細窗
        self.edges = [self._top(0, 0, 1, 1) for _ in range(4)]
        for e in self.edges:
            e.configure(bg=LOOK)
            e.withdraw()
        self.menu = tk.Menu(r, tearoff=0, font=(FONT, px(lay["ctl_font"])))
        self.menu.add_command(label="自動／逐步", command=self.toggle_mode)
        self.menu.add_command(label="備案畫面", command=self.planb)
        self.menu.add_command(label="停止示範", command=self.stop)
        self.hotkeys()
        r.after(300, self.take_front)                    # 上場時舞台取得前景（背板蓋住一切；勿擾也靠這個）
        if os.environ.get("NE_STAGE_MAX_S"):             # 測試：最多活這麼久（CI 不會因為一個沒關的視窗卡住整個 job）
            r.after(int(float(os.environ["NE_STAGE_MAX_S"]) * 1000), self.root.destroy)
        self.tick()

    def take_front(self):
        try:
            self.root.lift()
            self.root.focus_force()
            if os.name == "nt":
                u32.SetForegroundWindow(int(self.root.frame(), 16))
        except Exception:
            pass

    def drop(self, why=""):
        """撤下舞台：先把我們的瀏覽器取消置頂、送出停止，再收掉所有視窗（任何情況都不能留一個蓋住螢幕的視窗）。"""
        try:
            h = getattr(self, "active_hwnd", None)
            if os.name == "nt" and h and u32.IsWindow(h):
                u32.SetWindowPos(h, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception:
            pass
        try:
            write_control(self.run_dir, state="stopped", dropped=why or "drop")
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def safety(self, ctl):
        """三個安全出口之二：主控端（引擎或 standby）不在了 → 撤下；絕對上限 2 小時；
        無人值守（排練、agent 示範）時偵測到使用者的輸入 → 撤下（講者模式不做：講者本來就會按鍵）。"""
        now_t = time.time()
        if now_t - self.t_start > float(os.environ.get("NE_STAGE_MAX_S") or 7200):
            return self.drop("max_lifetime") or True
        if now_t - getattr(self, "_own_t", 0) >= 2:
            self._own_t = now_t
            pid = self.now.get("owner_pid")
            if pid and not pid_alive(int(pid)):
                self._dead_since = getattr(self, "_dead_since", None) or now_t
                if now_t - self._dead_since >= 5:
                    return self.drop("owner_gone") or True
            else:
                self._dead_since = None
        if ctl.get("unattended") and os.name == "nt" and now_t - self.t_start > 3:
            if user_input_since(self.tick0):
                return self.drop("user_input") or True
        return False

    def _top(self, x, y, w, h):
        t = self.tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.geometry("%dx%d+%d+%d" % (w, h, x, y))
        return t

    # ---------- 控制
    def toast(self, text):
        """按鍵有沒有被收到，畫面上一定有反應（字幕帶右下的狀態列）。"""
        try:
            self._toast = (text, time.time())
            self.state.configure(text="● " + text, fg="#d29922")
        except Exception:
            pass

    def engine_alive(self):
        pid = self.now.get("owner_pid")
        return (not pid) or pid_alive(int(pid))

    def next(self):
        if not self.engine_alive():
            self.toast("示範已經結束，正在關閉")
            self.root.after(1200, lambda: self.drop("no_engine"))
            return
        self.toast("已收到：下一步")
        if self.now.get("card") == "end":
            write_control(self.run_dir, finish=True, next=True)    # 片尾按「結束」：引擎還原後收掉舞台
        else:
            write_control(self.run_dir, next=True)

    def toggle_pause(self):
        if not self.engine_alive():
            self.toast("示範已經結束，正在關閉")
            self.root.after(1200, lambda: self.drop("no_engine"))
            return
        c = read_json(os.path.join(self.run_dir, "control.json"))
        self.toast("已收到：" + ("繼續" if c.get("state") == "paused" else "暫停"))
        write_control(self.run_dir, state="playing" if c.get("state") == "paused" else "paused")

    def toggle_mode(self):
        c = read_json(os.path.join(self.run_dir, "control.json"))
        write_control(self.run_dir, advance="auto" if c.get("advance") == "step" else "step")

    def stop(self):
        write_control(self.run_dir, state="stopped")

    def more(self):
        b = self.b_more
        self.menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() - 10)

    def planb(self):
        for p in (self.now.get("planb"), self.now.get("replay")):
            if p and os.path.exists(p):
                os.startfile(p)  # noqa
                return

    def hotkeys(self):
        """→／PageDown 下一步、空白暫停（簡報筆送的是 PageDown）。只在舞台存在期間註冊。"""
        if os.name != "nt" or os.environ.get("NE_STAGE_NO_HOTKEYS"):
            return
        # VK_RIGHT、VK_NEXT（PageDown）、VK_SPACE；撤下用 Ctrl+Shift+F12（不攔單獨的 Esc：台上的瀏覽器與其他程式都收得到）
        keys = {1: (0, 0x27), 2: (0, 0x22), 3: (0, 0x20), 4: (0x0002 | 0x0004, 0x7B)}

        def loop():
            for i, (mod, vk) in keys.items():
                u32.RegisterHotKey(None, i, 0x4000 | mod, vk)    # MOD_NOREPEAT
            msg = wintypes.MSG()
            while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:                  # WM_HOTKEY
                    if msg.wParam in (1, 2):
                        self.root.after(0, self.next)
                    elif msg.wParam == 3:
                        self.root.after(0, self.toggle_pause)
                    elif msg.wParam == 4:
                        self.root.after(0, lambda: self.drop("esc"))
            for i in keys:
                u32.UnregisterHotKey(None, i)
        self.hk = threading.Thread(target=loop, daemon=True)
        self.hk.start()

    # ---------- 瀏覽器視窗
    def win_of(self, acct, profile):
        h = self.wins.get(acct)
        if h and u32.IsWindow(h):
            return h
        h = top_window(chrome_pids(profile)) if profile else None
        if h:
            self.wins[acct] = h
        return h

    def place_browsers(self, now):
        if os.name != "nt":
            return
        active = None if now.get("card") else now.get("account")
        for acct, info in (now.get("accounts") or {}).items():
            h = self.win_of(acct, info.get("profile"))
            if not h:
                continue
            if acct != active:
                if self.placed.get(acct) != "off":
                    u32.SetWindowPos(h, HWND_NOTOPMOST, OFF, OFF, 0, 0, SWP_NOACTIVATE | SWP_NOSIZE)   # 下台：移到螢幕外
                    self.placed[acct] = "off"
                continue
            self.active_hwnd = h
            crop = now.get("crop") or {}
            key = json.dumps([crop, now.get("zoom"), self.W, self.H])
            if self.placed.get(acct) == key:
                continue
            wr, cr = _rect(h), content_rect(h)
            if not cr:
                continue
            shell = (cr[0] - wr[0], cr[1] - wr[1], (wr[0] + wr[2]) - (cr[0] + cr[2]), (wr[1] + wr[3]) - (cr[1] + cr[3]))
            x, y, w, hh = L.browser_rect(self.lay["stage"], shell, crop, float(now.get("zoom") or 1.5), dpr_of(h))
            u32.SetWindowPos(h, HWND_TOPMOST, x, y, w, hh, SWP_NOACTIVATE | SWP_SHOWWINDOW)
            self.placed[acct] = key
            self.dpr = dpr_of(h)

    def draw_look(self, now):
        box = (now.get("look") or {}).get("box")
        acct = now.get("account")
        rect = None
        if box and not now.get("card") and acct and self.placed.get(acct) not in (None, "off"):
            rect = L.css_to_screen(box, self.lay["stage"], now.get("crop") or {}, float(now.get("zoom") or 1.5),
                                   getattr(self, "dpr", 1.0))
        if not rect:
            for e in self.edges:
                e.withdraw()
            return
        x, y, w, h = rect
        t = max(3, round(self.H * 0.0045))
        pad = t + 4
        x, y, w, h = x - pad, y - pad, w + 2 * pad, h + 2 * pad
        for e, (ex, ey, ew, eh) in zip(self.edges, ((x, y, w, t), (x, y + h - t, w, t), (x, y, t, h), (x + w - t, y, t, h))):
            e.geometry("%dx%d+%d+%d" % (max(1, ew), max(1, eh), ex, ey))
            e.deiconify()
            e.attributes("-topmost", True)

    # ---------- 背板上的卡片
    def draw_card(self, now):
        c, W, H = self.card, self.W, self.H
        c.delete("all")
        kind = now.get("card")
        if not kind:
            return
        d = now.get("card_data") or {}
        big, mid, small = round(H * 0.06), round(H * 0.035), round(H * 0.026)
        if kind == "title":
            c.create_text(W // 2, H * 0.32, text=now.get("title", ""), fill=FG, font=(FONT, px(big), "bold"), width=W * 0.8)
            if d.get("subtitle"):
                c.create_text(W // 2, H * 0.42, text=d["subtitle"], fill="#c9d1dc", font=(FONT, px(mid)), width=W * 0.8)
            if d.get("mode"):                              # 現在是哪個模式（講者開場前一眼確認）
                c.create_text(W // 2, H * 0.86, text=d["mode"], fill=DIM, font=(FONT, px(small)))
            c.create_text(W * 0.98, H * 0.97, text=EXIT_HINT, fill="#4a5260", font=(FONT, px(small - 6)), anchor="e")
            self.card_texts = [d.get("subtitle", ""), d.get("mode", "")]
            y = H * 0.54
            for acct, info in (now.get("accounts") or {}).items():          # 角色圖例
                col = info.get("color") or ACCENT
                c.create_rectangle(W * 0.38, y - mid * 0.6, W * 0.38 + mid * 1.2, y + mid * 0.6, fill=col, width=0)
                c.create_text(W * 0.38 + mid * 1.8, y, text=info.get("role", acct), fill=FG, font=(FONT, px(mid)), anchor="w")
                y += mid * 1.9
        elif kind == "checklist":
            c.create_text(W // 2, H * 0.18, text=d.get("heading", "準備中…"), fill=FG, font=(FONT, px(mid), "bold"))
            c.create_text(W * 0.98, H * 0.97, text=EXIT_HINT, fill=DIM, font=(FONT, px(small - 4)), anchor="e")
            y = H * 0.28
            for it in d.get("items", []):
                mark, col = {"ok": ("✓", "#3fb950"), "fail": ("✗", "#f47067"), "warn": ("!", "#d29922"),
                         "run": ("…", DIM)}.get(it.get("state"), ("·", DIM))
                c.create_text(W * 0.3, y, text=mark, fill=col, font=(FONT, px(mid), "bold"))
                c.create_text(W * 0.33, y, text=it.get("name", ""), fill=FG, font=(FONT, px(small + 4)), anchor="w")
                if it.get("state") in ("fail", "warn") and it.get("say"):
                    y += small * 1.5
                    c.create_text(W * 0.33, y, text=it["say"], fill="#f4a9a3", font=(FONT, px(small)), anchor="w", width=W * 0.6)
                y += small * 2.1
        elif kind == "transition":
            col = now.get("role_color") or ACCENT
            c.create_rectangle(0, H * 0.42, W, H * 0.58, fill=col, width=0)
            c.create_text(W // 2, H // 2, text="換成：" + (now.get("role") or ""), fill="#fff", font=(FONT, px(big), "bold"))
        elif kind == "end":
            c.create_text(W // 2, H * 0.24, text=d.get("heading", "示範結束"), fill=FG, font=(FONT, px(big), "bold"))
            y = H * 0.38
            for pt in d.get("points") or []:                 # 三行重點（只用前面字幕講過的話）
                c.create_text(W * 0.25, y, text="●", fill=ACCENT, font=(FONT, px(small)))
                c.create_text(W * 0.28, y, text=pt, fill=FG, font=(FONT, px(mid)), anchor="w", width=W * 0.6)
                y += mid * 2.0
            if d.get("text"):
                c.create_text(W // 2, y + mid, text=d["text"], fill=DIM, font=(FONT, px(small + 2)), width=W * 0.7)
            if d.get("reset"):
                c.create_text(W // 2, H * 0.80, text=d["reset"], fill="#3fb950" if d.get("reset_ok") else "#f47067",
                              font=(FONT, px(small + 2)))

    # ---------- 每 200 ms
    def tick(self):
        now = read_json(os.path.join(self.run_dir, "now.json"))
        ctl = read_json(os.path.join(self.run_dir, "control.json"))
        changed_card = now.get("card") != self.now.get("card") or now.get("card_data") != self.now.get("card_data")
        self.now = now
        st = now.get("status")
        if self.safety(ctl):
            return
        if now.get("card") == "title" and time.time() - getattr(self, "_title_t", 0) >= 2:
            self._title_t = time.time()
            changed_card = True                          # 片頭定期重畫（副標、模式那一行一定畫得上）
        if st == "closing":
            self.root.destroy()
            return
        if ctl.get("state") == "stopped" or st == "stopped":
            # 示範停了（講者按停止、驅動偵測到使用者回來、引擎結束）：不管是哪張卡片都說一句、自己關，
            # 絕不留一個蓋滿螢幕、按什麼都沒反應的背板（第一次排練就是這樣卡住使用者）
            if not getattr(self, "_closing_at", None):
                self._closing_at = time.time()
                self.toast("示範已經停止，畫面即將關閉")
            elif time.time() - self._closing_at >= 1.5:
                return self.drop("stopped")
            self.root.after(200, self.tick)
            return
        if changed_card:
            self.draw_card(now)
        col = now.get("role_color") or ACCENT
        if now.get("role"):
            self.chip.configure(text=now["role"], bg=col)
        else:
            self.chip.configure(text="", bg="#11151d")   # 片頭／預備時沒有角色：不畫空的名牌
        self.rule.configure(bg=col)
        self.sect.configure(text=now.get("section") or "")
        i_, n_ = now.get("step_index") or 0, now.get("steps_total") or 0
        self.where.configure(text=("%d / %d　·　現場操作" % (i_, n_)) if i_ and not now.get("card") else "現場操作")
        if now.get("card") in ("title", "checklist", "transition"):
            self.band.withdraw() if now.get("card") == "checklist" else self.band.deiconify()
        else:
            self.band.deiconify()
        beat = now.get("beat") or {}
        slow = st == "waiting_system" and now.get("slow_s") and float(now.get("waited_s") or 0) >= float(now["slow_s"])
        text = now.get("slow_say") if slow else beat.get("text", "")
        if now.get("card") == "checklist":
            text = ""
        self.cap.configure(text=text or "")
        # 還剩幾句：小圓點（講者看；觀眾不需要知道「第幾拍」）
        self.dots.delete("all")
        n, i = int(beat.get("n") or 0), int(beat.get("i") or 0)
        r_ = 4
        for k in range(n):
            x = self.dots.winfo_width() - 8 - (n - 1 - k) * (r_ * 3)
            self.dots.create_oval(x - r_, 7 - r_, x + r_, 7 + r_, fill=ACCENT if k < i else "#2a3140", width=0)
        paused = ctl.get("state") == "paused"
        waiting = st in ("waiting_next", "ready", "failed") or now.get("card") == "end"
        self.b_next.configure(text="結束" if now.get("card") == "end" else "下一步 →",
                              bg=ACCENT if waiting and not paused else "#1b2029", fg="#fff" if waiting else "#9aa4b2")
        self.b_pause.configure(text="▶" if paused else "⏸")
        tt = getattr(self, "_toast", None)
        if tt and time.time() - tt[1] < 1.2:
            pass
        else:
            self.state.configure(text="● 已暫停" if paused else "● 等你按下一步" if waiting else
                             ("● 等待%s %d 秒" % (now.get("wait_label") or "系統", now.get("waited_s") or 0))
                             if st == "waiting_system" else "", fg="#d29922" if paused or slow else ACCENT)

        if (slow or st == "failed") and not self.b_planb.winfo_ismapped():
            self.b_planb.pack(side="left", padx=3, before=self.b_next)
            self.b_planb.configure(bg="#b8860b", fg="#fff")
        elif not (slow or st == "failed") and self.b_planb.winfo_ismapped():
            self.b_planb.pack_forget()
        try:
            self.place_browsers(now)
        except Exception:
            pass
        self.draw_look(now)
        if time.time() - getattr(self, "_z_t", 0) >= 1.0:
            self.restack()
        if os.environ.get("NE_STAGE_GEOM"):
            self.dump_geom()
        self.root.after(200, self.tick)

    def restack(self):
        """實際的上下順序：背板 → 台上的瀏覽器 → 角色帶、字幕帶、外框（都在置頂層；工作列、通知被壓在下面）。"""
        self._z_t = time.time()
        try:
            self.root.attributes("-topmost", True)
            self.root.lift()
            h = getattr(self, "active_hwnd", None)
            if os.name == "nt" and h and not self.now.get("card") and u32.IsWindow(h):
                u32.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            for w in [self.band, self.capw] + self.edges:
                if w.winfo_viewable():
                    w.attributes("-topmost", True)
                    w.lift()
        except Exception:
            pass

    def dump_geom(self):
        self.root.update_idletasks()
        if not (self.b_next.winfo_ismapped() and self.b_next.winfo_width() > 1):
            return                                        # 還沒排好：下一輪再寫（測試不會讀到 0,0）
        g = {"screen": [self.W, self.H], "caption_text": self.cap.cget("text"), "card_texts": getattr(self, "card_texts", []),
             "caption_req_h": self.cap.winfo_reqheight(), "caption_h": self.lay["caption"][3],
             "next": [self.b_next.winfo_rootx() + self.b_next.winfo_width() // 2,
                      self.b_next.winfo_rooty() + self.b_next.winfo_height() // 2]}
        tmp = os.environ["NE_STAGE_GEOM"] + ".tmp"
        json.dump(g, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        try:
            os.replace(tmp, os.environ["NE_STAGE_GEOM"])
        except PermissionError:
            pass

    def run(self):
        self.root.mainloop()


def main():
    if len(sys.argv) < 2 or not os.path.isdir(sys.argv[1]):
        print("用法：demo_stage.py <執行資料夾>")
        sys.exit(2)
    Stage(sys.argv[1]).run()


if __name__ == "__main__":
    main()
