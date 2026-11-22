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
CAP_ALPHA = 0.62                                        # 字幕底板的不透明度（字本身完全不透明）
PAD_ALPHA = 3 / 255.0                                   # 字幕的輸入墊片：看不見，但會接到點擊（alpha 0 會穿透；
                                                        # 不用 1/255：換算成整數時可能被捨入成 0 而整個穿透）
SHIELD_ALPHA = 3 / 255.0                                # 隔離層：肉眼看不出，但不是 0（同墊片：不用 1/255）
CURSOR_IDLE_S = 2.0                                     # 真的游標在舞台上不動這麼久就隱藏
PTR_KEY = "#010204"                                     # 示範游標視窗的透明色鍵
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
    # 一定要宣告型別：沒宣告時 -1 被當成 32 位元整數傳，64 位元的 HWND 參數收到的不是 -1 → SetWindowPos 整個失敗，
    # 瀏覽器沒被擺上台、被置頂的背板蓋住（排練 4：舞台上一片黑）
    u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u32.SetWindowPos.restype = wintypes.BOOL
    # 繞過系統的最大視窗尺寸：沒有這個旗標時，系統在 WM_WINDOWPOSCHANGING 把視窗截到「螢幕＋邊框」大小，
    # 往上移出螢幕的瀏覽器底邊跟著縮上來，舞台下方露出一條黑帶（00:54 那次：1941×1106，底邊停在 915）
    SWP_NOSENDCHANGING = 0x0400
    g32 = ctypes.windll.gdi32
    g32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
    g32.CreateRoundRectRgn.restype = wintypes.HANDLE
    u32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
    u32.GetWindowRgnBox.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]


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


def caption_pos_file():
    return os.path.join(os.environ.get("NO_ERRANDS_HOME") or os.path.join(os.path.expanduser("~"), ".no-errands"),
                        "stage-caption.json")


def load_caption_pos():
    try:
        v = json.load(open(caption_pos_file(), encoding="utf-8")).get("pos")
        return tuple(v) if v else None
    except Exception:
        return None


def save_caption_pos(pos):
    try:
        json.dump({"pos": list(pos) if pos else None}, open(caption_pos_file(), "w", encoding="utf-8"))
    except OSError:
        pass


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


CURSOR0 = {}


def user_input_since(tick):
    """有人回來了：GetLastInputInfo 晚於 tick，而且游標移動超過 40 px，或游標完全沒動（鍵盤）。
    1–40 px 的小漂移不算（排練時曾被這種雜訊停掉）。"""
    class LII(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    li = LII(); li.cbSize = ctypes.sizeof(LII)
    u32.GetLastInputInfo(ctypes.byref(li))
    c = wintypes.POINT(); u32.GetCursorPos(ctypes.byref(c))
    if "p" not in CURSOR0:
        CURSOR0["p"] = (c.x, c.y)
    if li.dwTime <= tick:
        return False
    d = abs(c.x - CURSOR0["p"][0]) + abs(c.y - CURSOR0["p"][1])
    return d > 40 or d == 0


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
        # 字幕塊（Sprint F-2）：浮在頁面上、大小跟著文字、可拖、可關。兩層視窗疊在同一個位置：
        #   底板 capbg：黑色、整窗 CAP_ALPHA 透明度（看得到底下的頁面）、圓角（SetWindowRgn）
        #   字   capw ：色鍵透明（KEY 色的像素完全透明、點擊穿到底板），字本身完全不透明 → 白字清楚
        # 兩層一律同時設定位置（tick 與拖動都走 place_caption），不會錯開。
        CAPBG, KEY = "#000000", "#010203"
        self.cap_key = KEY
        self.capbg = self._top(0, 0, 10, 10)
        self.capbg.configure(bg=CAPBG)
        try:
            self.capbg.attributes("-alpha", CAP_ALPHA)
        except Exception:
            pass
        self.capw = self._top(0, 0, 10, 10)
        self.capw.configure(bg=KEY)
        try:
            self.capw.attributes("-transparentcolor", KEY)
        except Exception:
            pass
        cp = lay["cap_pad"]
        self.cap = tk.Label(self.capw, text="", font=(FONT, px(lay["cap_font"]), "bold"), fg=FG, bg=KEY,
                            justify="center", wraplength=lay["cap_max_w"] - 2 * cp, padx=cp, pady=max(4, cp // 2))
        self.cap.pack()
        # × 關字幕：字幕塊右上角「外側」的獨立小視窗（不壓到字；CI 擷圖：放在塊內時蓋住最後一個字）。
        # 滑鼠停在字幕上或 × 上才出現（用游標位置判斷）。
        xs = max(20, round(lay["cap_font"] * 0.9))
        self.xbtn_size = xs
        self.xbtn = self._top(0, 0, xs, xs)
        self.xbtn.configure(bg="#1b2029", cursor="hand2")
        self.xlbl = tk.Label(self.xbtn, text="×", font=(FONT, px(max(12, round(xs * 0.75))), "bold"), fg="#e6e6e6", bg="#1b2029")
        self.xlbl.pack(fill="both", expand=True)
        for w in (self.xbtn, self.xlbl):
            w.bind("<Button-1>", lambda e: self.toggle_caption())
        self.xbtn.withdraw()
        self.cap_on, self.cap_user, self.cap_drag0, self._cap_rgn = True, load_caption_pos(), None, None
        self.cap_geom, self._force_place = None, False
        # 輸入墊片：疊在字幕兩層之上、同一個位置大小、透明度 1/255。字幕上的所有滑鼠操作（拖動、按兩下回預設、
        # 右上角 × 關字幕、滑鼠進出）都由它接——不管點在內距、字的筆畫上或字與字之間（CI：點在字的視窗的透明
        # 像素上時，點擊沒有送到任何一層，拖動完全沒反應），也不會穿到底下的頁面。
        self.cappad = self._top(0, 0, 10, 10)
        self.cappad.configure(bg="#000000", cursor="fleur")
        try:
            self.cappad.attributes("-alpha", PAD_ALPHA)
        except Exception:
            pass
        self.cappad.bind("<ButtonPress-1>", self.cap_press)
        self.cappad.bind("<B1-Motion>", self.cap_move)
        self.cappad.bind("<ButtonRelease-1>", self.cap_release)
        self.cappad.bind("<Double-Button-1>", self.cap_reset)
        self.cappad.withdraw()
        # 遮罩：頁面底色的一塊，蓋住訊息區頂端被切一半的那則訊息露出的殘段（引擎算好位置，now.json 的 mask）
        self.maskw = self._top(0, 0, 1, 1)
        self.maskw.withdraw()
        self.want, self.fit, self.replaced = {}, None, 0
        # 控制列（像影片播放器）：右下角一小塊；自動播放時滑鼠動了才出現
        ox, oy, ow, oh = lay["controls"]
        CTLBG = "#11151d"
        self.ctlw = self._top(ox, oy, ow, oh)
        self.ctlw.configure(bg=CTLBG)
        cf = (FONT, px(lay["ctl_font"]), "bold")
        mk = lambda t, cmd: tk.Button(self.ctlw, text=t, command=cmd, font=cf, bg="#1b2029", fg="#9aa4b2", relief="flat",
                                      activebackground="#2a3140", activeforeground=FG, padx=8, pady=2, takefocus=0, bd=0)
        self.b_more = mk("⋯", self.more)
        self.b_cap = mk("字幕", self.toggle_caption)
        self.b_pause = mk("⏸", self.toggle_pause)
        self.b_next = mk("下一步 →", self.next)
        self.b_planb = mk("備案畫面", self.planb)
        for b in (self.b_more, self.b_cap, self.b_pause, self.b_next):
            b.pack(side="right", padx=3, pady=4)
        self.dots = tk.Canvas(self.ctlw, bg=CTLBG, highlightthickness=0, height=12, width=70)
        self.dots.pack(side="left", padx=6)
        # 控制列旁的提示（輪到你／已暫停／已收到…）：控制列上方一行
        self.hintw = self._top(ox, oy - lay["ctl_font"] * 2, ow, lay["ctl_font"] * 2)
        self.hintw.configure(bg=CTLBG)
        self.state = tk.Label(self.hintw, text="", font=(FONT, px(max(11, lay["ctl_font"] - 1))), fg=ACCENT, bg=CTLBG, anchor="e")
        self.state.pack(fill="both", expand=True, padx=8)
        self.ctl_shown, self._cur, self._moved_t = True, None, None
        # 外框（讀內容的拍子）：四條置頂細窗，很淡、1.2 秒後淡出（真實畫面不會變暗，也沒有粗框）
        self.edges = [self._top(0, 0, 1, 1) for _ in range(4)]
        for e in self.edges:
            e.configure(bg=LOOK)
            e.withdraw()
        self.look_key, self.look_t, self.look_a = None, 0.0, None
        # 隔離層（F-3）：蓋住台上的頁面，吃掉真的滑鼠的點擊、滾輪、移動——頁面收不到，不會誤點、不會有提示泡泡；
        # 字幕與控制列在它之上照常可用。引擎的操作走瀏覽器的控制通道（CDP），不受它影響。
        self.shield = self._top(0, 0, 1, 1)
        self.shield.configure(bg="#000000")
        try:
            self.shield.attributes("-alpha", SHIELD_ALPHA)
        except Exception:
            pass
        for ev in ("<Button>", "<ButtonRelease>", "<MouseWheel>", "<Double-Button>"):
            self.shield.bind(ev, lambda e: "break")
        self.shield.withdraw()
        self.cursor_hidden = False
        # 示範游標（F-3）：畫出來的箭頭（色鍵透明、滑鼠穿透、不取得焦點）。系統游標完全不動。
        s = max(1.0, self.H / 1080.0)
        self.ptr_R = R = int(26 * s)
        self.ptr_size = (R + int(24 * s), R + int(30 * s))
        self.ptrw = self._top(0, 0, R + self.ptr_size[0], R + self.ptr_size[1])
        self.ptrw.configure(bg=PTR_KEY)
        try:
            self.ptrw.attributes("-transparentcolor", PTR_KEY)
        except Exception:
            pass
        self.ptrc = tk.Canvas(self.ptrw, bg=PTR_KEY, highlightthickness=0, width=2 * R + int(24 * s), height=2 * R + int(30 * s))
        self.ptrc.pack()
        k = 1.25 * s
        pts = [(0, 0), (0, 17), (4.2, 13.2), (7, 19.6), (9.6, 18.6), (6.8, 12.2), (12.2, 12.2)]
        self.ptrc.create_polygon(*[(R + x * k, R + y * k) for x, y in pts], fill="#ffffff", outline="#000000",
                                 width=max(1, round(1.4 * s)), joinstyle="miter")
        self.ripple = self.ptrc.create_oval(R, R, R, R, outline=ACCENT, width=max(2, round(3 * s)), state="hidden")
        self.ptrw.withdraw()
        self.ptr_pos, self.ptr_anim, self.ptr_seq, self.press_seq, self.press_t = None, None, None, None, 0.0
        self.ptr_alpha_t = 0.0
        self.root.after(400, self.clickthrough)
        self.root.after(16, self.anim)
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

    def xbtn_rect(self):
        """× 的位置：字幕塊右上角外側（塊的上方、右緣對齊）。"""
        g = self.cap_geom
        if not g:
            return None
        xs = self.xbtn_size
        return (g[0] + g[2] - xs, max(self.lay["stage"][1], g[1] - xs - 4), xs, xs)

    def update_xbtn(self, cur):
        xr = self.xbtn_rect() if getattr(self, "_cap_shown", False) else None
        hot = bool(cur and xr and self.cap_geom and (L.inside((cur[0], cur[1], 0, 0), self.cap_geom)
                                                   or L.inside((cur[0], cur[1], 0, 0), (xr[0] - 4, xr[1] - 4, xr[2] + 8, xr[3] + 12))))
        if hot and not self.cap_drag0:
            self.xbtn.geometry("%dx%d+%d+%d" % (xr[2], xr[3], xr[0], xr[1]))
            if not self.xbtn.winfo_viewable():
                self.xbtn.deiconify()
        elif self.xbtn.winfo_viewable():
            self.xbtn.withdraw()

    def cap_press(self, e):
        g = self.cap_geom or (self.capbg.winfo_x(), self.capbg.winfo_y(), 0, 0)
        self.cap_drag0 = (e.x_root, e.y_root, g[0], g[1])

    def cap_move(self, e):
        if not self.cap_drag0:
            return
        x0, y0, wx, wy = self.cap_drag0
        sx, sy, sw, sh = self.lay["stage"]
        if (e.x_root, e.y_root) == (x0, y0):
            return
        bw, bh = (self.cap_geom or (0, 0, self.capbg.winfo_width(), self.capbg.winfo_height()))[2:]
        x = min(max(wx + e.x_root - x0, sx), sx + sw - bw)
        y = min(max(wy + e.y_root - y0, sy), sy + sh - bh)
        self.place_caption(bw, bh, x, y)
        self.cap_user = L.caption_ratio((x, y), self.lay)        # 拖過就以使用者的位置為準（不再自動讓開）

    def place_caption(self, bw, bh, x, y):
        """字幕兩層一起擺；底板大小變了才重設圓角。"""
        g = "%dx%d+%d+%d" % (bw, bh, x, y)
        self.cap_geom = (int(x), int(y), int(bw), int(bh))       # 同一格裡算控制列、判斷游標在不在字幕上都用這個（不等視窗回報）
        self.capbg.geometry(g)
        self.capw.geometry(g)
        self.cappad.geometry(g)
        if os.name == "nt" and self._cap_rgn != (bw, bh):
            try:
                self.capbg.update_idletasks()
                r = max(6, self.lay["cap_pad"])
                hw = int(self.capbg.frame(), 16)
                u32.SetWindowRgn(hw, g32.CreateRoundRectRgn(0, 0, bw + 1, bh + 1, 2 * r, 2 * r), True)
                self._cap_rgn = (bw, bh)
            except Exception:
                pass

    def cap_box(self):
        """字幕塊目前（這一格要擺）的螢幕矩形；沒顯示回 None。"""
        return self.cap_geom if getattr(self, "_cap_shown", False) else None

    def show_caption(self, on):
        self._cap_shown = bool(on)
        for w in (self.capbg, self.capw, self.cappad):
            if on and not w.winfo_viewable():
                w.deiconify()
            elif not on and w.winfo_viewable():
                w.withdraw()

    def cap_release(self, e):
        if self.cap_drag0:
            self.cap_drag0 = None
            if self.cap_user:
                save_caption_pos(self.cap_user)

    def cap_reset(self, e=None):
        """在字幕上按兩下：回到預設位置、恢復自動讓開（使用者明確要求，所以即使游標停在字幕上也立刻移回）。"""
        self.cap_user, self.cap_drag0, self._force_place = None, None, True
        save_caption_pos(None)

    def toggle_caption(self):
        self.cap_on = not self.cap_on
        self.toast("字幕：" + ("開" if self.cap_on else "關"))

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
        keys = {1: (0, 0x27), 2: (0, 0x22), 3: (0, 0x20), 4: (0x0002 | 0x0004, 0x7B), 5: (0, 0x43)}   # 5＝C（字幕開關）

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
                    elif msg.wParam == 5:
                        self.root.after(0, self.toggle_caption)
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
        # 試擺（上場前，引擎寫 fit_probe）：照裁切擺到舞台位置但不置頂——在背板下面、觀眾看不到，只為了量實際範圍
        probe = now.get("fit_probe") if now.get("card") else None
        active = probe or (None if now.get("card") else now.get("account"))
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
            key = json.dumps([crop, now.get("zoom"), self.W, self.H, bool(probe)])
            if self.placed.get(acct) == key:
                continue
            wr, cr = _rect(h), content_rect(h)
            if not cr:
                continue
            shell = (cr[0] - wr[0], cr[1] - wr[1], (wr[0] + wr[2]) - (cr[0] + cr[2]), (wr[1] + wr[3]) - (cr[1] + cr[3]))
            x, y, w, hh = L.browser_rect(self.lay["stage"], shell, crop, float(now.get("zoom") or 1.5), dpr_of(h))
            u32.SetWindowPos(h, HWND_NOTOPMOST if probe else HWND_TOPMOST, x, y, w, hh,
                             SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOSENDCHANGING)
            self.placed[acct] = key
            self.want[acct] = (x, y, w, hh)
            self.active_hwnd = h
            if not probe:
                self.restack()                               # 剛擺好的瀏覽器在最上面：立刻把我們的視窗排回它之上
            self.dpr = dpr_of(h)

    def check_fit(self, now):
        """A1／A19：每秒量一次台上瀏覽器的實際視窗與頁面範圍。被系統或瀏覽器改回（差距 >3 像素）、
        或頁面沒貼齊螢幕底邊／舞台露出空白帶 → 重量殼的大小再擺一次（最多連續 5 次）。結果寫到 stage_fit.json。"""
        probe = now.get("fit_probe") if now.get("card") else None
        acct = probe or (None if now.get("card") else now.get("account"))
        h = self.wins.get(acct) if acct else None
        rec = {"t": round(time.time(), 2), "account": acct, "probe": bool(probe), "screen": [self.W, self.H],
               "stage": list(self.lay["stage"])}
        if h and u32.IsWindow(h) and self.placed.get(acct) not in (None, "off"):
            wr, cr = _rect(h), content_rect(h)
            err = L.placement_error(self.want.get(acct), wr)
            fit = L.fit_report((self.W, self.H), self.lay["stage"], cr)
            rec.update(window=list(wr), want=list(self.want.get(acct) or []), content=list(cr) if cr else None,
                       placement_error=err, fit=fit, replaced=self.replaced)
            if (err is not None and err > 3) or not fit["ok"]:
                self._unfit = getattr(self, "_unfit", 0) + 1
                if self._unfit <= 5:
                    self.placed[acct] = None             # 下一個 tick 重量殼、重擺
                    self.replaced += 1
            else:
                self._unfit = 0
            rec["ok"] = bool(fit["ok"] and (err is None or err <= 3))
        else:
            rec["ok"] = None                             # 現在台上沒有瀏覽器（卡片）
        self.fit = rec
        try:
            p = os.path.join(self.run_dir, "stage_fit.json")
            json.dump(rec, open(p + ".tmp", "w", encoding="utf-8"), ensure_ascii=False)
            os.replace(p + ".tmp", p)
        except OSError:
            pass

    def draw_shield(self, now, cur):
        """隔離層：台上有瀏覽器時蓋住舞台；真的游標不動 2 秒就隱藏（隔離層與背板上），一動就出現。
        鍵盤焦點：前景變成我們的瀏覽器時拿回舞台（使用者按鍵不會打進頁面）。"""
        fx = now.get("effects") or {}
        on = bool(not now.get("card") and now.get("account") and self.placed.get(now.get("account")) not in (None, "off")
                  and fx.get("shield", True))
        if on:
            sx, sy, sw, sh = self.lay["stage"]
            g = "%dx%d+%d+%d" % (sw, sh, sx, sy)
            if getattr(self, "_shield_g", None) != g:
                self.shield.geometry(g)
                self._shield_g = g
            if not self.shield.winfo_viewable():
                self.shield.deiconify()
        elif self.shield.winfo_viewable():
            self.shield.withdraw()
        idle = self._moved_t is None or time.time() - self._moved_t >= CURSOR_IDLE_S
        if idle != self.cursor_hidden:
            self.cursor_hidden = idle
            for w in (self.shield, self.root, self.card):
                try:
                    w.configure(cursor="none" if idle else "")
                except Exception:
                    pass
        if os.name == "nt" and on and time.time() - getattr(self, "_fg_t", 0) >= 1.0:
            self._fg_t = time.time()
            h = getattr(self, "active_hwnd", None)
            if h and u32.GetForegroundWindow() == h:
                self.take_front()

    def clickthrough(self):
        """示範游標視窗：滑鼠穿透、不取得焦點（WS_EX_LAYERED|TRANSPARENT|NOACTIVATE|TOOLWINDOW）。"""
        if os.name != "nt":
            return
        try:
            self.ptrw.update_idletasks()
            h = int(self.ptrw.frame(), 16)
            ex = u32.GetWindowLongW(h, -20)
            u32.SetWindowLongW(h, -20, ex | 0x80000 | 0x20 | 0x08000000 | 0x80)
        except Exception:
            pass

    def ptr_screen(self, now, p):
        return L.css_point(p, self.lay["stage"], now.get("crop") or {}, float(now.get("zoom") or 1.5), getattr(self, "dpr", 1.0))

    def update_pointer(self, now):
        """示範游標：卡片（片頭、轉場、片尾）時淡出；新畫面開始時從舞台中間偏下淡入，再平順移到目標。"""
        p = now.get("pointer") or {}
        acct = now.get("account")
        onstage = bool(not now.get("card") and acct and self.placed.get(acct) not in (None, "off") and p.get("seq") is not None
                       and (now.get("effects") or {}).get("pointer", True))
        if not onstage:
            if self.ptrw.winfo_viewable():
                self.ptrw.withdraw()
            self.ptr_pos, self.ptr_anim, self._ptr_shown = None, None, False
            return
        if p.get("seq") != self.ptr_seq:
            self.ptr_seq = p["seq"]
            dst = self.ptr_screen(now, (p["x"], p["y"]))
            if self.ptr_pos is None:                     # 這個畫面的第一個位置：從中間偏下出發、淡入
                sx, sy, sw, sh = self.lay["stage"]
                self.ptr_pos = (sx + sw // 2, sy + int(sh * 0.7))
                self.ptr_alpha_t = time.time()
            self.ptr_anim = (self.ptr_pos, dst, time.time(), max(0.0, float(p.get("dur") or 0)))
        if now.get("press") and now["press"] != self.press_seq:
            self.press_seq, self.press_t = now["press"], time.time()
            self.ptrc.itemconfigure(self.ripple, state="normal")      # 漣漪這一格就畫出來
            R = self.ptr_R
            self.ptrc.coords(self.ripple, R - 4, R - 4, R + 4, R + 4)
            try:
                self.ptrw.update_idletasks()
                p_ = os.path.join(self.run_dir, "stage_ack.json")    # 回報引擎：按下的效果已經畫出來（引擎等到這個才點擊）
                json.dump({"press": self.press_seq, "t": round(time.time(), 3)}, open(p_ + ".tmp", "w"))
                os.replace(p_ + ".tmp", p_)
            except OSError:
                pass
        if not getattr(self, "_ptr_shown", False):
            self._ptr_shown = True
            self.place_ptr(*self.ptr_pos)
            self.ptrw.deiconify()
            self.clickthrough()

    def place_ptr(self, x, y):
        R = self.ptr_R
        self.ptrw.geometry("+%d+%d" % (int(x) - R, int(y) - R))

    def anim(self):
        """約 60 fps：示範游標的移動（加減速、弧線）、淡入、按下的漣漪；外框淡出。暫停時引擎不發新目標，游標停在原地。"""
        try:
            now_t = time.time()
            # 游標目標與「按下」不等 200 ms 的 tick：now.json 一更新就處理（引擎發出按下後 0.18 秒就點擊，
            # 晚 200 ms 才讀到會讓漣漪出現在點擊之後；游標也會晚出發）。用另一份副本，不動 tick 判斷卡片切換用的 self.now。
            try:
                mt = os.stat(os.path.join(self.run_dir, "now.json")).st_mtime_ns
            except OSError:
                mt = None
            if mt and mt != getattr(self, "_now_mt", None):
                self._now_mt = mt
                nf = read_json(os.path.join(self.run_dir, "now.json"))
                if nf:
                    self.update_pointer(nf)
            if self.ptr_anim:
                p0, p1, t0, dur = self.ptr_anim
                t = 1.0 if dur <= 0 else (now_t - t0) / dur
                x, y = L.pointer_path(p0, p1, t)
                self.ptr_pos = (x, y)
                if getattr(self, "_ptr_shown", False):
                    self.place_ptr(x, y)
                if t >= 1.0:
                    self.ptr_pos, self.ptr_anim = p1, None
            if getattr(self, "_ptr_shown", False):
                fa = min(1.0, (now_t - self.ptr_alpha_t) / 0.25)
                if getattr(self, "_ptr_a", None) != round(fa, 2):
                    self._ptr_a = round(fa, 2)
                    try:
                        self.ptrw.attributes("-alpha", max(0.05, fa))
                    except Exception:
                        pass
            age = now_t - self.press_t
            R = self.ptr_R
            if age < 0.35:
                r = 4 + (R - 6) * (age / 0.35)
                self.ptrc.coords(self.ripple, R - r, R - r, R + r, R + r)
                self.ptrc.itemconfigure(self.ripple, state="normal")
            else:
                self.ptrc.itemconfigure(self.ripple, state="hidden")
            if self.now.get("look") and not self.now.get("card"):
                self.draw_look(self.now)
        except Exception:
            pass
        self.root.after(16, self.anim)

    def draw_mask(self, now):
        """訊息區頂端的殘段遮罩（頁面底色）：CSS 方框 → 舞台座標；沒有就收起來。"""
        m = now.get("mask") or {}
        acct = now.get("account")
        rect = None
        if m.get("box") and not now.get("card") and acct and self.placed.get(acct) not in (None, "off"):
            rect = L.css_to_screen(m["box"], self.lay["stage"], now.get("crop") or {}, float(now.get("zoom") or 1.5),
                                   getattr(self, "dpr", 1.0))
        self.mask_rect = rect
        if not rect:
            if self.maskw.winfo_viewable():
                self.maskw.withdraw()
            return
        x, y, w, h = rect
        self.maskw.configure(bg=m.get("color") or "#000000")
        self.maskw.geometry("%dx%d+%d+%d" % (max(1, w), max(1, h), x, y))
        if not self.maskw.winfo_viewable():
            self.maskw.deiconify()

    def draw_look(self, now):
        box = (now.get("look") or {}).get("box")
        acct = now.get("account")
        rect = None
        if box and not now.get("card") and acct and self.placed.get(acct) not in (None, "off"):
            rect = L.css_to_screen(box, self.lay["stage"], now.get("crop") or {}, float(now.get("zoom") or 1.5),
                                   getattr(self, "dpr", 1.0))
        self.look_rect = rect                            # 字幕避開外框用的位置：淡出後也保留到這一拍結束（字幕不跳）
        key = (now.get("look") or {}).get("seq")
        if key != self.look_key:
            self.look_key, self.look_t = key, time.time()
        faint = (now.get("effects") or {}).get("faint_look", True)
        a = L.look_alpha(time.time() - self.look_t) if faint else 1.0       # 淡外框關掉：原本的粗框、不淡出
        if not rect or a <= 0:
            for e in self.edges:
                if e.winfo_viewable():
                    e.withdraw()
            return
        x, y, w, h = rect
        t = max(2, round(self.H * 0.0025)) if faint else max(3, round(self.H * 0.0045))
        pad = t + 6
        x, y, w, h = x - pad, y - pad, w + 2 * pad, h + 2 * pad
        for e, (ex, ey, ew, eh) in zip(self.edges, ((x, y, w, t), (x, y + h - t, w, t), (x, y, t, h), (x + w - t, y, t, h))):
            e.geometry("%dx%d+%d+%d" % (max(1, ew), max(1, eh), ex, ey))
            if not e.winfo_viewable():
                e.deiconify()                                # 上下順序只由 restack 決定（不在這裡拉到最上面）
        if self.look_a != round(a, 2):
            self.look_a = round(a, 2)
            for e in self.edges:
                try:
                    e.attributes("-alpha", a)
                except Exception:
                    pass

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
        slow = bool(st == "waiting_system" and now.get("slow_s") and float(now.get("waited_s") or 0) >= float(now["slow_s"]))
        # 角色帶右端：等待提示（字幕只講內容）＋「5 / 13 · 現場操作」
        wait_txt = ""
        if st == "waiting_system":
            wait_txt = (now.get("slow_say") or "") if slow else "等待%s %d 秒%s　·　" % (
                now.get("wait_label") or "系統", now.get("waited_s") or 0,
                ("（通常約 %d 秒）" % now["expect_s"]) if now.get("expect_s") else "")
        base_where = ("%d / %d　·　現場操作" % (i_, n_)) if i_ and not now.get("card") else "現場操作"
        self.where.configure(text=(wait_txt + ("　·　" if slow and wait_txt else "") + base_where) if wait_txt else base_where,
                             fg="#d29922" if slow else DIM)
        # 控制列：像影片播放器（自動：滑鼠動了才出現；講者輪到他、暫停、超時：常駐）
        paused = ctl.get("state") == "paused"
        presenter = ctl.get("advance") == "step"
        cur = None
        if os.name == "nt":
            pt = wintypes.POINT(); u32.GetCursorPos(ctypes.byref(pt)); cur = (pt.x, pt.y)
        if self._cur is not None and cur != self._cur:
            self._moved_t = time.time()
        self._cur = cur
        end_wait = now.get("card") == "end" and presenter
        cs = L.controls_state(presenter, "waiting_next" if end_wait else st, paused, slow,
                              (time.time() - self._moved_t) if self._moved_t else None)
        tt = getattr(self, "_toast", None)
        toast_on = bool(tt and time.time() - tt[1] < 1.5)
        show_ctl = (cs["visible"] or toast_on) and now.get("card") != "checklist"
        if show_ctl != self.ctl_shown:
            self.ctlw.deiconify() if show_ctl else self.ctlw.withdraw()
            self.ctl_shown = show_ctl
        self.b_next.configure(text="結束" if now.get("card") == "end" else "下一步 →",
                              bg=ACCENT if cs["next_hot"] else "#1b2029", fg="#fff" if cs["next_hot"] else "#9aa4b2")
        self.b_pause.configure(text="▶" if paused else "⏸")
        if not toast_on:
            self.state.configure(text=("● " + cs["hint"]) if cs["hint"] else "", fg="#d29922" if paused or slow else ACCENT)
        hint_on = show_ctl and bool(self.state.cget("text"))     # 提示列有字才顯示（沒字時不留一塊空的深色方塊）
        if hint_on != self.hintw.winfo_viewable():
            self.hintw.deiconify() if hint_on else self.hintw.withdraw()
        if cs["planb"] and not self.b_planb.winfo_ismapped():
            self.b_planb.pack(side="right", padx=3, pady=4, before=self.b_more)
            self.b_planb.configure(bg="#b8860b", fg="#fff")
        elif not cs["planb"] and self.b_planb.winfo_ismapped():
            self.b_planb.pack_forget()
        self.dots.delete("all")
        n, i = int(beat.get("n") or 0), int(beat.get("i") or 0)
        for k in range(n):
            x = 70 - 8 - (n - 1 - k) * 12
            self.dots.create_oval(x - 4, 2, x + 4, 10, fill=ACCENT if k < i else "#2a3140", width=0)
        # 字幕塊：大小跟著文字；預設下方置中、自動讓開外框；拖過以使用者位置為準。控制列不推字幕（由控制列讓位）。
        # 滑鼠停在字幕上或拖動中：位置凍結；滑鼠離開後也等到下一拍才重新計算（不在使用者眼前跳）。
        text = beat.get("text", "") if not now.get("card") else ""
        if self.cap.cget("text") != (text or ""):
            self.cap.configure(text=text or "")
        if text and self.cap_on:
            self.capw.update_idletasks()
            bw, bh = self.cap.winfo_reqwidth(), self.cap.winfo_reqheight()
            look = getattr(self, "look_rect", None)
            box0 = self.cap_box()
            hover = bool(cur and box0 and L.inside((cur[0], cur[1], 0, 0), box0))
            if hover or self.cap_drag0:
                self.cap_hold = text
            if self._force_place:
                self._force_place = False
                x, y = L.caption_pos((bw, bh), self.lay, look=look, user=self.cap_user)
                self.place_caption(bw, bh, x, y)
                self.cap_hold = text
            elif box0 and (hover or self.cap_drag0 or getattr(self, "cap_hold", None) == text):
                if not self.cap_drag0 and (bw, bh) != (box0[2], box0[3]):
                    # 凍結中換了句子：以中心與底邊為錨點換大小，位置不跳
                    sx, sy, sw, sh = self.lay["stage"]
                    nx = min(max(int(box0[0] + box0[2] / 2 - bw / 2), sx), sx + sw - bw)
                    ny = min(max(int(box0[1] + box0[3] - bh), sy), sy + sh - bh)
                    self.place_caption(bw, bh, nx, ny)
            else:
                self.cap_hold = None
                x, y = L.caption_pos((bw, bh), self.lay, look=look, user=self.cap_user)
                self.place_caption(bw, bh, x, y)
            self.show_caption(True)
        else:
            self.show_caption(False)
        self.update_xbtn(cur)
        # 控制列：和字幕重疊時往上讓到字幕上方（字幕不動）
        cx_, cy_, cw_, ch_ = L.controls_pos(self.lay, self.cap_box() if (text and self.cap_on) else None)
        # 控制列顯示中（使用者可能正要按）只往上讓、不往下跳回：例如按 × 收起字幕時，按鈕不會在游標底下移走；
        # 收起之後（滑鼠不動 2.5 秒）才回到右下角
        prev_ = getattr(self, "_ctl_xy", None)
        if self.ctl_shown and prev_ and cy_ > prev_[1]:
            cx_, cy_ = prev_
        # 和實際位置比（不只比上次下的指令）：視窗還沒顯示前下的移動會被 Tk 吃掉（CI：控制列一開始就顯示時停在預設位置）
        # 同一個目標連續重擺 3 次仍不相符（例如座標系不一致）就停手並記下，不要每一格都重擺（da 的提醒）
        actual = (self.ctlw.winfo_x(), self.ctlw.winfo_y()) if self.ctlw.winfo_viewable() else None
        if (cx_, cy_) != getattr(self, "_ctl_xy", None):
            self._ctl_retry = 0
        elif actual and actual != (cx_, cy_):
            self._ctl_retry = getattr(self, "_ctl_retry", 0) + 1
        if (cx_, cy_) != getattr(self, "_ctl_xy", None) or (actual and actual != (cx_, cy_) and self._ctl_retry <= 3):
            self.ctl_moves = getattr(self, "ctl_moves", 0) + 1
            hh_ = self.lay["ctl_font"] * 2
            self.ctlw.geometry("%dx%d+%d+%d" % (cw_, ch_, cx_, cy_))
            self.hintw.geometry("%dx%d+%d+%d" % (cw_, hh_, cx_, cy_ - hh_))
            self._ctl_xy = (cx_, cy_)
        try:
            self.place_browsers(now)
        except Exception:
            pass
        self.draw_look(now)
        self.draw_mask(now)
        self.draw_shield(now, cur)
        self.update_pointer(now)
        # 有視窗剛出現或收起（控制列、字幕、外框、遮罩…）：立刻重排——隱藏時沒被排進去的視窗，出現時可能停在
        # 瀏覽器（或隔離層）下面，看不到也點不到（F-3 CI：× 出現後點下去沒反應）
        sig = tuple(bool(w.winfo_viewable()) for w in self.overlays())
        if sig != getattr(self, "_z_sig", None):
            self._z_sig = sig
            self._z_t = 0
        if time.time() - getattr(self, "_z_t", 0) >= 1.0:
            try:
                if os.name == "nt":
                    self.check_fit(now)
            except Exception:
                pass
            self.restack()
            self.write_geom()
        if os.environ.get("NE_STAGE_GEOM"):
            self.dump_geom()
        self.root.after(200, self.tick)

    def overlays(self):
        """我們疊在瀏覽器之上的視窗，由下到上。"""
        return [self.shield, self.maskw, self.band] + self.edges + [self.ptrw, self.capbg, self.capw, self.cappad, self.xbtn, self.ctlw, self.hintw]

    def restack(self):
        """實際的上下順序（都在置頂層；工作列、通知被壓在下面），由上到下：控制列…字幕（墊片在字之上）→ 外框 →
        角色帶 → 遮罩 → 台上的瀏覽器 → 背板。由上往下一個一個「插在上一個的下方」：過程中瀏覽器從來不會在我們的
        視窗之上（舊做法先把瀏覽器拉到最上面再把我們的視窗一個個抬上去，中間幾十毫秒瀏覽器蓋住墊片，那一刻的點擊
        會落到頁面上——CI main-45057569083：頁面點擊數 0→2）。"""
        self._z_t = time.time()
        if os.name != "nt":
            try:
                self.root.lift()
                for w in self.overlays():
                    if w.winfo_viewable():
                        w.lift()
            except Exception:
                pass
            return
        try:
            chain = [int(w.frame(), 16) for w in reversed(self.overlays()) if w.winfo_viewable()]
            h = getattr(self, "active_hwnd", None)
            if h and not self.now.get("card") and u32.IsWindow(h):
                chain.append(h)
            chain.append(int(self.root.frame(), 16))
            prev = HWND_TOPMOST
            for hw in chain:
                u32.SetWindowPos(hw, prev, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                prev = hw
        except Exception:
            pass

    def z_order(self):
        """測試／診斷：我們的視窗與台上瀏覽器實際的上下順序（上→下）。"""
        if os.name != "nt":
            return []
        names = {}
        for n, w in (("hint", self.hintw), ("controls", self.ctlw), ("pad", self.cappad), ("captext", self.capw),
                     ("plate", self.capbg), ("band", self.band), ("mask", self.maskw), ("backdrop", self.root),
                     ("shield", self.shield), ("pointer", self.ptrw), ("x", self.xbtn)):
            try:
                names[int(w.frame(), 16)] = n
            except Exception:
                pass
        for e in self.edges:
            try:
                names[int(e.frame(), 16)] = "edge"
            except Exception:
                pass
        h = getattr(self, "active_hwnd", None)
        if h:
            names[h] = "browser"
        out, hw = [], u32.GetTopWindow(None)
        while hw and len(out) < 40:
            if hw in names and u32.IsWindowVisible(hw):
                out.append(names[hw])
            hw = u32.GetWindow(hw, 2)                         # GW_HWNDNEXT
        return out

    def geom(self):
        box = lambda w: [w.winfo_x(), w.winfo_y(), w.winfo_width(), w.winfo_height()] if w.winfo_viewable() else None
        return {"z_order": self.z_order(),
                "cap_btn": [self.b_cap.winfo_rootx() + self.b_cap.winfo_width() // 2, self.b_cap.winfo_rooty() + self.b_cap.winfo_height() // 2]
                if self.ctl_shown and self.b_cap.winfo_ismapped() else None,
                "caption_plate": box(self.capbg), "caption_textwin": box(self.capw), "caption_pad": box(self.cappad), "xbtn": box(self.xbtn), "hint_visible": bool(self.hintw.winfo_viewable()),
                "caption_on": self.cap_on, "controls_rect": box(self.ctlw), "controls_want": list(getattr(self, "_ctl_xy", None) or []),
                "controls_moves": getattr(self, "ctl_moves", 0), "controls_stuck": getattr(self, "_ctl_retry", 0) > 3,
                "shield": box(self.shield), "pointer": [round(v) for v in self.ptr_pos] if self.ptr_pos and self.ptrw.winfo_viewable() else None,
                "cursor_hidden": self.cursor_hidden, "ripple_on": self.ptrc.itemcget(self.ripple, "state") == "normal", "look_alpha": self.look_a if any(e.winfo_viewable() for e in self.edges) else 0,
                "look_rect": list(self.look_rect) if getattr(self, "look_rect", None) else None,
                "mask_rect": list(self.mask_rect) if getattr(self, "mask_rect", None) else None,
                "fit": self.fit, "card": self.now.get("card"), "beat": (self.now.get("beat") or {}).get("text"),
                "cap_user": self.cap_user}

    def write_geom(self):
        try:
            g = dict(self.geom(), t=round(time.time(), 2), screen=[self.W, self.H],
                     controls_visible=bool(self.ctl_shown), caption_text=self.cap.cget("text"))
            p = os.path.join(self.run_dir, "stage_geom.json")
            json.dump(g, open(p + ".tmp", "w", encoding="utf-8"), ensure_ascii=False)
            os.replace(p + ".tmp", p)
        except Exception:
            pass

    def dump_geom(self):
        self.root.update_idletasks()
        g = {**self.geom(), "screen": [self.W, self.H], "caption_text": self.cap.cget("text"), "card_texts": getattr(self, "card_texts", []),
             "caption_box": [self.capbg.winfo_x(), self.capbg.winfo_y(), self.capbg.winfo_width(), self.capbg.winfo_height()]
             if self.capbg.winfo_viewable() else None,
             "caption_rgn": self.cap_rgn_box(),
             "caption_req": [self.cap.winfo_reqwidth(), self.cap.winfo_reqheight()],
             "cap_max_w": self.lay["cap_max_w"], "cap_line_h": self.lay["cap_line_h"], "cap_pad": self.lay["cap_pad"],
             "controls_visible": bool(self.ctl_shown),
             "next": [self.b_next.winfo_rootx() + self.b_next.winfo_width() // 2,
                      self.b_next.winfo_rooty() + self.b_next.winfo_height() // 2]
             if self.ctl_shown and self.b_next.winfo_ismapped() and self.b_next.winfo_width() > 1 else None}
        tmp = os.environ["NE_STAGE_GEOM"] + ".tmp"
        json.dump(g, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        try:
            os.replace(tmp, os.environ["NE_STAGE_GEOM"])
        except PermissionError:
            pass

    def cap_rgn_box(self):
        """底板的圓角區域（測試用：有設區域才回 [l,t,r,b]）。"""
        if os.name != "nt" or not self.capbg.winfo_viewable():
            return None
        r = wintypes.RECT()
        k = u32.GetWindowRgnBox(int(self.capbg.frame(), 16), ctypes.byref(r))
        return [r.left, r.top, r.right, r.bottom] if k > 1 else None

    def run(self):
        self.root.mainloop()


def main():
    if len(sys.argv) < 2 or not os.path.isdir(sys.argv[1]):
        print("用法：demo_stage.py <執行資料夾>")
        sys.exit(2)
    Stage(sys.argv[1]).run()


if __name__ == "__main__":
    main()
