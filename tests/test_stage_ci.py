#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示範舞台的視窗測試（Windows；會開全螢幕視窗——只在 CI 跑，要 --i-am-ci）。

五種解析度（1920×1080、1366×768、1280×720、1024×768、1920×1200）各開一次舞台：
  1 字幕帶裡兩行長的字幕實際排出來的高度 ≤ 字幕帶高度（不被截斷）
  2 字幕文字就是 now.json 的這一拍；「下一步」按鈕在字幕帶右端
  3 now.json 寫 status=closing → 舞台自己收掉（不留視窗）
另外：預備清單卡（checklist）時講者按「停止」→ 舞台收掉。
真的 Chrome（A1／A2／A19）：頁面貼齊螢幕底邊、沒有空白帶、擺放實測差距 ≤3、字幕兩層同位置／圓角／半透明而字全白、
拖動時兩層一起動、試擺在背板下面；每個情境擷一張測試機整個螢幕的圖（stage-shot-*.png，CI artifact）。
"""
import json, os, shutil, subprocess, sys, tempfile, threading, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SC = os.path.join(REPO, "skills", "no-errands", "scripts")
sys.path.insert(0, SC)
import stage_layout as L  # noqa: E402

R = []
PROCS = []
LOGDIR = tempfile.mkdtemp(prefix="ne-stage-logs-")


def spawn(d, env):
    """舞台行程：輸出寫檔（不接到 CI 的管線，免得 tee 等一個沒結束的子行程），最多活 40 秒。"""
    lf = open(os.path.join(LOGDIR, "stage-%d.log" % len(PROCS)), "w")
    p = subprocess.Popen([sys.executable, os.path.join(SC, "demo_stage.py"), d], env=dict(env, NE_STAGE_MAX_S="40"),
                         stdin=subprocess.DEVNULL, stdout=lf, stderr=lf)
    PROCS.append(p)
    return p


def kill_tree(p):
    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)


def watchdog(limit=300):
    """整體逾時：時間到就收掉所有舞台行程、報 FAIL、結束（CI job 不會卡住）。"""
    def fire():
        print("FAIL 整體逾時（%d 秒）：收掉舞台行程" % limit, flush=True)
        for p in PROCS:
            kill_tree(p)
        print("FAILED (timeout)", flush=True)
        os._exit(1)
    t = threading.Timer(limit, fire)
    t.daemon = True
    t.start()
    return t


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]), flush=True)


def rd(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def wait(fn, t=15):
    t0 = time.time()
    while time.time() - t0 < t:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    return None


PAGE = """<!doctype html><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#ffffff;font:16px sans-serif}
#a{position:fixed;top:0;left:0;right:0;height:40px;background:#202020}
#g{position:fixed;top:40px;left:0;right:0;height:44px;background:#404040}
#s{position:fixed;top:84px;left:0;width:269px;bottom:0;background:#cccccc}
#f{position:fixed;left:0;right:0;bottom:0;height:40px;background:#00b050}
#c{position:absolute;top:84px;left:269px;right:0;padding:20px}
</style><div id=a></div><div id=g></div><div id=s></div><div id=c>測試頁面</div><div id=f></div>"""


def shot_png(path):
    """CI 測試機的整個螢幕 → PNG（純 stdlib）。回傳 (W, H, rgb bytes)。
    只在 GitHub Actions 上擷（GITHUB_ACTIONS=true）：專案紅線是不擷使用者的整個螢幕——就算有人在本機加
    --i-am-ci 跑這支測試，這裡也不擷，回 None（依賴擷圖的像素檢查跟著跳過，會印 SKIP）。"""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("SKIP 擷圖 %s：不在 CI 測試機上（不擷使用者的螢幕）" % os.path.basename(path), flush=True)
        return None
    import ctypes, struct, zlib
    from ctypes import wintypes
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    vp = ctypes.c_void_p
    u.GetDC.restype = vp; u.GetDC.argtypes = [vp]
    u.ReleaseDC.argtypes = [vp, vp]
    g.CreateCompatibleDC.restype = vp; g.CreateCompatibleDC.argtypes = [vp]
    g.CreateCompatibleBitmap.restype = vp; g.CreateCompatibleBitmap.argtypes = [vp, ctypes.c_int, ctypes.c_int]
    g.SelectObject.restype = vp; g.SelectObject.argtypes = [vp, vp]
    g.BitBlt.argtypes = [vp, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, vp, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    g.GetDIBits.argtypes = [vp, vp, wintypes.UINT, wintypes.UINT, vp, vp, wintypes.UINT]
    g.DeleteObject.argtypes = [vp]; g.DeleteDC.argtypes = [vp]
    W, H = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    hdc = u.GetDC(None)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, W, H)
    g.SelectObject(mdc, bmp)
    g.BitBlt(mdc, 0, 0, W, H, hdc, 0, 0, 0x00CC0020 | 0x40000000)       # SRCCOPY | CAPTUREBLT（含半透明視窗）

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
    bi = BIH(ctypes.sizeof(BIH), W, -H, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(W * H * 4)
    g.GetDIBits(mdc, bmp, 0, H, buf, ctypes.byref(bi), 0)
    g.DeleteObject(bmp); g.DeleteDC(mdc); u.ReleaseDC(None, hdc)
    raw = buf.raw
    rgb = bytearray(W * H * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]
    rows = b"".join(b"\x00" + bytes(rgb[y * W * 3:(y + 1) * W * 3]) for y in range(H))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)) +
                chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))
    return W, H, rgb


def px_at(img, x, y):
    W, H, rgb = img
    x, y = max(0, min(W - 1, int(x))), max(0, min(H - 1, int(y)))
    i = (y * W + x) * 3
    return tuple(rgb[i:i + 3])


def find_chrome():
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.environ.get("LOCALAPPDATA", "")):
        p = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
        if base and os.path.exists(p):
            return p
    return None


def real_chrome_checks(stage_with):
    """真的 Chrome＋真的舞台（測試機的螢幕）：A1 頁面貼齊螢幕底邊、沒有空白帶；A19 擺放實測差距 ≤3；
    A4 殼在舞台外；A2 字幕兩層同位置、圓角、半透明而字全白、在頁面內、拖動時兩層一起動；遮罩畫在指定位置；
    上場前試擺：瀏覽器在背板下面（不置頂）也量得到合格。每個情境都擷一張整個測試機螢幕的圖（CI artifact）。"""
    import ctypes
    import demo_stage as DS
    u = ctypes.windll.user32
    chrome = find_chrome()
    check("測試機上有 Chrome", chrome, chrome)
    if not chrome:
        return
    work = tempfile.mkdtemp(prefix="ne-fit-")
    prof = os.path.join(work, "profile")
    page = os.path.join(work, "page.html")
    open(page, "w", encoding="utf-8").write(PAGE)
    cp = subprocess.Popen([chrome, "--user-data-dir=" + prof, "--no-first-run", "--no-default-browser-check",
                           "--disable-search-engine-choice-screen", "--disable-features=Translate",
                           "--window-position=80,80", "--window-size=900,600", "file:///" + page.replace("\\", "/")],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    PROCS.append(cp)
    h = wait(lambda: DS.top_window(DS.chrome_pids(prof)), 30)
    check("測試用 Chrome 視窗出現", h)
    if not h:
        return
    time.sleep(2)
    W, H = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    lay = L.layout(W, H)
    crop = {"top": 84, "left": 269}
    accts = {"a": {"role": "第一線人員", "color": "#2f7de1", "profile": prof}}
    print("測試機螢幕 %d×%d、舞台 %s" % (W, H, lay["stage"]), flush=True)

    def fit_ok(d, probe):
        def f():
            r = rd(os.path.join(d, "stage_fit.json"))
            if r.get("probe") == probe and r.get("ok") is not None:
                return r
        r1 = wait(f, 25)
        time.sleep(1.2)
        return f() or r1

    # 1 上場前試擺：片頭卡顯示中，瀏覽器照裁切擺好但在背板下面
    now = {"card": "title", "status": "staging", "title": "測試", "card_data": {"subtitle": "", "mode": "自動播放"},
           "fit_probe": "a", "crop": crop, "zoom": 1.0, "accounts": accts}
    d, p = stage_with(now, None, {"NO_ERRANDS_HOME": work})
    r = fit_ok(d, True)
    check("A1 試擺（背板下面）：頁面貼齊螢幕底邊、舞台沒有空白帶", r and r.get("ok"), r)
    ex = u.GetWindowLongW(h, -20)
    check("試擺時瀏覽器不置頂（在背板下面，觀眾看不到）", not (ex & 0x8), hex(ex))
    shot_png("stage-shot-1-probe.png")
    json.dump({"status": "closing"}, open(os.path.join(d, "now.json"), "w"))
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        kill_tree(p)
    # 2 上台：頁面、字幕、遮罩
    geom = os.path.join(work, "geom.json")
    beat = "它只根據院內說明來寫，依據列在下面；核准的人不能確認寄出。"
    now = {"card": None, "status": "running", "account": "a", "role": "第一線人員", "role_color": "#2f7de1", "section": "測試",
           "step_index": 2, "steps_total": 13, "crop": crop, "zoom": 1.0, "beat": {"text": beat, "i": 1, "n": 2},
           "mask": {"box": [289, 150, 300, 30], "color": "#ff00ff"}, "accounts": accts}
    d, p = stage_with(now, None, {"NO_ERRANDS_HOME": work, "NE_STAGE_GEOM": geom})
    r = fit_ok(d, False)
    check("A1 上台：頁面底邊貼齊螢幕底邊（差 ≤3）、四邊沒有空白帶", r and r.get("fit", {}).get("ok"), r)
    check("A19 視窗實測和要求的差距 ≤3 像素", r and r.get("placement_error") is not None and r["placement_error"] <= 3, r)
    cr = r and r.get("content")
    k = (r and r.get("window") and DS.dpr_of(h)) or 1.0
    check("A4 殼與頁面頂端 84px、左側 269px 都在舞台外（頁面左上角＝舞台左上角往外推裁切量）",
          cr and abs(cr[1] - (lay["stage"][1] - 84 * k)) <= 3 and abs(cr[0] - (lay["stage"][0] - 269 * k)) <= 3, cr)
    g = wait(lambda: rd(geom).get("caption_plate") and rd(geom), 10) or {}
    img = shot_png("stage-shot-2-onstage.png")
    img and check("A1 畫面：螢幕最底下一列是頁面的頁尾（綠色），不是黑帶",
          all(px_at(img, x, H - 2)[1] > 150 and px_at(img, x, H - 2)[0] < 60 for x in (W * 0.4, W * 0.6, W * 0.9)),
          [px_at(img, x, H - 2) for x in (W * 0.4, W * 0.6, W * 0.9)])
    pl, tw = g.get("caption_plate"), g.get("caption_textwin")
    check("A2 字幕兩層（底板、字）在同一個位置", pl and pl == tw, (pl, tw))
    check("A2 字幕底板有圓角（設了視窗區域）", g.get("caption_rgn"), g.get("caption_rgn"))
    check("A2 字幕塊完全落在頁面範圍內", pl and cr and L.inside(pl, cr), (pl, cr))
    if pl and img:
        x0, y0, bw, bh = pl
        edge = px_at(img, x0 + 4, y0 + bh // 2)                 # 底板的內距（沒有字）：白色頁面透過 62% 黑
        check("A2 底板半透明：看得到底下的頁面（白底透過來是灰，不是全黑）", all(50 <= c <= 150 for c in edge), edge)
        corner = px_at(img, x0, y0)
        check("A2 圓角：底板的角落露出頁面（白）", min(corner) > 200, corner)
        mx = max(max(px_at(img, x, y)) for x in range(x0, x0 + bw, 2) for y in range(y0, y0 + bh, 2))
        check("A2 字完全不透明（白字最亮處 ≥240）", mx >= 240, mx)
    mr = g.get("mask_rect")
    check("遮罩畫在舞台上指定的位置", mr and L.inside(mr, lay["stage"]), mr)
    if mr and img:
        c = px_at(img, mr[0] + mr[2] // 2, mr[1] + mr[3] // 2)
        check("遮罩的顏色是指定的頁面底色", abs(c[0] - 255) < 20 and c[1] < 40 and abs(c[2] - 255) < 20, c)
    # 3 拖動：兩層一起動
    if pl:
        cx, cy = pl[0] + 6, pl[1] + pl[3] // 2

        def move(x_, y_):
            # 真的滑鼠移動輸入（MOVE|ABSOLUTE），和人拖動一樣；SetCursorPos 只是把游標放過去，
            # 不一定產生「按著左鍵移動」的訊息（第一次 CI：拖動沒被收到，字幕只被控制列推上去 22 px）
            u.mouse_event(0x8001, int(x_ * 65535 / (W - 1)), int(y_ * 65535 / (H - 1)), 0, 0)
        move(cx, cy); time.sleep(0.3)
        u.mouse_event(0x0002, 0, 0, 0, 0); time.sleep(0.15)          # 左鍵按下
        for s_ in range(1, 21):
            move(cx - 10 * s_, cy - 7.5 * s_); time.sleep(0.04)
        time.sleep(0.2)
        u.mouse_event(0x0004, 0, 0, 0, 0); time.sleep(1.2)           # 放開
        g2 = rd(geom)
        pl2, tw2 = g2.get("caption_plate"), g2.get("caption_textwin")
        check("A2 拖動後兩層仍在同一個位置", pl2 and pl2 == tw2, (pl2, tw2))
        check("A2 拖動有效（往左上移約 200×150）", pl2 and abs((pl[0] - pl2[0]) - 200) <= 8 and abs((pl[1] - pl2[1]) - 150) <= 8,
              (pl, pl2, "cap_user=%s" % g2.get("cap_user"), "存檔=%s" % rd(os.path.join(work, "stage-caption.json"))))
        shot_png("stage-shot-3-dragged.png")
    json.dump({"status": "closing"}, open(os.path.join(d, "now.json"), "w"))
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        kill_tree(p)
    kill_tree(cp)
    shutil.rmtree(work, ignore_errors=True)


def main():
    if os.name != "nt" or "--i-am-ci" not in sys.argv:
        print("SKIP 舞台視窗測試：只在 Windows CI 跑（要 --i-am-ci）")
        return 0
    wd = watchdog()
    two_lines = "它只根據院內說明來寫，依據列在下面；核准的人不能確認寄出，所以每一封回覆至少經過兩個人。"
    for W, H in L.RESOLUTIONS:
        d = tempfile.mkdtemp(prefix="ne-stage-")
        geom = os.path.join(d, "geom.json")
        json.dump({"status": "waiting_next", "card": None, "role": "長官", "role_color": "#c2410c", "section": "第一道關卡",
                   "step_index": 8, "steps_total": 13, "beat": {"text": two_lines, "i": 2, "n": 3}, "accounts": {}},
                  open(os.path.join(d, "now.json"), "w", encoding="utf-8"), ensure_ascii=False)
        json.dump({"state": "playing", "advance": "step", "next_seq": 0}, open(os.path.join(d, "control.json"), "w"))
        env = dict(os.environ, NE_STAGE_SCREEN="%dx%d" % (W, H), NE_STAGE_GEOM=geom, NE_STAGE_NO_HOTKEYS="1", NO_ERRANDS_HOME=d)
        p = spawn(d, env)
        g = wait(lambda: rd(geom).get("caption_box") and rd(geom).get("next") and rd(geom))
        lines = L.caption_lines(two_lines, W, H)
        check("%d×%d：字幕估算兩行以內（%d 行）" % (W, H, lines), lines <= 2, lines)
        check("%d×%d：字幕塊實際排出來最多兩行、不超過最大寬度（舞台寬 62%%）" % (W, H),
              g and g["caption_req"][1] <= 2 * g["cap_line_h"] + 2 * g["cap_pad"] + 12 and g["caption_req"][0] <= g["cap_max_w"] + 2, g)
        check("%d×%d：字幕就是這一拍的句子" % (W, H), g and g["caption_text"] == two_lines, g and g.get("caption_text"))
        check("%d×%d：講者模式輪到他按 → 控制列出現，「下一步」在右下角" % (W, H),
              g and g["controls_visible"] and g["next"][0] > W * 0.6 and g["next"][1] > H * 0.8, g and g.get("next"))
        json.dump({"status": "closing"}, open(os.path.join(d, "now.json"), "w"))
        try:
            p.wait(10)
            closed = True
        except subprocess.TimeoutExpired:
            kill_tree(p); closed = False
        check("%d×%d：status=closing → 舞台自己收掉" % (W, H), closed)
        shutil.rmtree(d, ignore_errors=True)
    # ---- 安全出口：任何卡片都不能留下蓋滿螢幕、按什麼都沒反應的背板
    import ctypes

    def closes_within(p, t):
        try:
            p.wait(t)
            return True
        except subprocess.TimeoutExpired:
            kill_tree(p)
            return False

    def stage_with(now, ctl=None, env_extra=None):
        d = tempfile.mkdtemp(prefix="ne-stage-")
        json.dump(now, open(os.path.join(d, "now.json"), "w", encoding="utf-8"), ensure_ascii=False)
        if ctl is not None:
            json.dump(ctl, open(os.path.join(d, "control.json"), "w"))
        env = dict(os.environ, NE_STAGE_NO_HOTKEYS="1")
        env.update(env_extra or {})
        return d, spawn(d, env)

    title = {"card": "title", "status": "ready", "title": "測試", "card_data": {"subtitle": "副標", "mode": "自動播放"}}
    # 1 主控端（引擎）被強制結束 → 舞台幾秒內自己關
    eng = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    d, p = stage_with(dict(title, owner_pid=eng.pid))
    time.sleep(3)
    eng.kill()
    check("引擎被強制結束 → 舞台在 15 秒內自己關（片頭卡）", closes_within(p, 15))
    # 2 引擎停了（control stopped）→ 片頭卡上也會說一句後自己關
    d, p = stage_with(dict(title), {"state": "playing", "advance": "auto", "next_seq": 0})
    time.sleep(3)
    json.dump({"state": "stopped", "advance": "auto", "next_seq": 0}, open(os.path.join(d, "control.json"), "w"))
    check("示範停止（片頭卡）→ 舞台說一句後自己關", closes_within(p, 10))
    # 3 Ctrl+Shift+F12 在每一種卡片上都能離開（全域熱鍵，不依賴引擎）
    ku = ctypes.windll.user32
    for card in ("title", "checklist", None):
        now = {"card": card, "status": "running", "card_data": {"heading": "x", "items": []}, "beat": {"text": "字幕", "i": 1, "n": 2}}
        d, p = stage_with(now, None, {"NE_STAGE_NO_HOTKEYS": ""})
        time.sleep(3)
        for vk, up in ((0x11, 0), (0x10, 0), (0x7B, 0), (0x7B, 2), (0x10, 2), (0x11, 2)):   # Ctrl、Shift、F12 按下再放開
            ku.keybd_event(vk, 0, up, 0); time.sleep(0.05)
        check("Ctrl+Shift+F12 → 離開（卡片：%s）" % (card or "示範中"), closes_within(p, 10))

    # ---- 真的瀏覽器（A1／A2／A19）：頁面貼齊螢幕底邊、舞台沒有空白帶、字幕兩層一起動、圓角、試擺在背板下面
    real_chrome_checks(stage_with)
    # 預備清單沒過、講者按停止 → 收掉
    d = tempfile.mkdtemp(prefix="ne-stage-")
    json.dump({"card": "checklist", "card_data": {"heading": "還沒準備好", "items": [{"name": "連線到示範系統", "state": "fail",
                                                                              "say": "連不到示範系統：確認網路後再雙擊一次。"}]}},
              open(os.path.join(d, "now.json"), "w", encoding="utf-8"), ensure_ascii=False)
    p = spawn(d, dict(os.environ, NE_STAGE_NO_HOTKEYS="1"))
    time.sleep(3)
    json.dump({"state": "stopped", "advance": "auto", "next_seq": 0}, open(os.path.join(d, "control.json"), "w"))
    try:
        p.wait(10)
        closed = True
    except subprocess.TimeoutExpired:
        kill_tree(p); closed = False
    check("預備清單沒過：講者按「停止」→ 舞台收掉", closed)
    wd.cancel()
    for p in PROCS:
        if p.poll() is None:
            kill_tree(p)
    for fn in sorted(os.listdir(LOGDIR)):              # 舞台的錯誤輸出（如果有）印出來，CI 上才看得到
        txt = open(os.path.join(LOGDIR, fn), encoding="utf-8", errors="replace").read().strip()
        if txt:
            print("--- %s ---" % fn)
            print(txt[-1500:])
    shutil.rmtree(d, ignore_errors=True)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
    return 0 if all(R) else 1


def run():
    """例外時也收掉所有舞台行程（看門狗之外再一層）：崩掉的測試不能留下接著 CI 管線的視窗。"""
    try:
        return main()
    except BaseException as e:
        print("FAIL 測試例外：%r" % (e,), flush=True)
        print("FAILED (exception)", flush=True)
        return 1
    finally:
        for p in PROCS:
            if p.poll() is None:
                kill_tree(p)


if __name__ == "__main__":
    sys.exit(run())
