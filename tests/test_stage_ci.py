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
import json, os, re, shutil, subprocess, sys, tempfile, threading, time

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
    p = subprocess.Popen([sys.executable, os.path.join(SC, "demo_stage.py"), d], env=dict({"NE_STAGE_MAX_S": "40"}, **env),
                         stdin=subprocess.DEVNULL, stdout=lf, stderr=lf)
    PROCS.append(p)
    return p


def kill_tree(p):
    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)


def watchdog(limit=720):
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
</style><div id=a></div><div id=g></div><div id=s></div><div id=c>測試頁面</div><div id=f></div>
<script>let c=0,w=0,m=0,f=0,i=0;addEventListener('click',()=>c++,true);addEventListener('mousedown',()=>c++,true);addEventListener('dblclick',()=>c++,true);
addEventListener('wheel',()=>w++,{capture:true,passive:true});addEventListener('mousemove',()=>m++,true);
(function r(){f++;requestAnimationFrame(r)})();setInterval(()=>i++,100);
setInterval(()=>{document.title='c='+c+' w='+w+' m='+m+' f='+f+' i='+i},200);</script>"""


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


def f3_checks(d, p, now, h, geom, W, H, lay):
    """F-3（A21／A25／A26／A27／A28）：隔離層擋下真的滑鼠、頁面在隔離層底下照常繪製與計時、真的游標閒置隱藏、
    示範游標移動連續且落點準確、系統游標不因示範而動、使用者點字幕不打斷示範游標、外框淡出。"""
    import ctypes, re
    from ctypes import wintypes
    import demo_stage as DS
    u = ctypes.windll.user32

    def title():
        b = ctypes.create_unicode_buffer(300)
        u.GetWindowTextW(h, b, 300)
        return {k: int(v) for k, v in re.findall(r"\b([cwmfi])=(\d+)", b.value)}

    def move_abs(x_, y_):
        u.mouse_event(0x8001, int(x_ * 65535 / (W - 1)), int(y_ * 65535 / (H - 1)), 0, 0)

    def cursor():
        pt = wintypes.POINT(); u.GetCursorPos(ctypes.byref(pt)); return (pt.x, pt.y)
    g = rd(geom)
    check("A26 隔離層蓋住整個舞台", g.get("shield") == list(lay["stage"]), (g.get("shield"), lay["stage"]))
    t0 = title()
    time.sleep(1.0)
    t1 = title()
    check("A28 隔離層開著時頁面照常繪製（畫面更新計數 1 秒內增加 >20）", t1.get("f", 0) - t0.get("f", 0) > 20, (t0, t1))
    check("A28 隔離層開著時頁面計時器沒有被放慢（100 ms 計時 1 秒內增加 ≥7）", t1.get("i", 0) - t0.get("i", 0) >= 7, (t0, t1))
    # 真的滑鼠在頁面上：移動、點擊、滾輪 → 頁面都收不到
    cx, cy = lay["stage"][0] + lay["stage"][2] // 2, lay["stage"][1] + lay["stage"][3] // 3
    move_abs(cx, cy); time.sleep(0.2); move_abs(cx + 30, cy + 20); time.sleep(0.2)
    u.mouse_event(0x0002, 0, 0, 0, 0); u.mouse_event(0x0004, 0, 0, 0, 0); time.sleep(0.2)
    u.mouse_event(0x0800, 0, 0, ctypes.c_ulong(-120 & 0xffffffff).value, 0); time.sleep(0.8)
    t2 = title()
    check("A26 真的滑鼠在頁面範圍內點擊、滾輪、移動：頁面一個都沒收到",
          bool(t1) and all(t2.get(k) == t1.get(k) for k in "cwm"),
          (t1, t2))
    check("A26 鍵盤焦點不在瀏覽器上（點了頁面範圍之後前景仍不是瀏覽器）", u.GetForegroundWindow() != h, u.GetForegroundWindow())
    bad = []
    for r_ in range(20):
        px_ = lay["stage"][0] + int(lay["stage"][2] * (0.2 + 0.6 * ((r_ * 7) % 10) / 10))
        py_ = lay["stage"][1] + int(lay["stage"][3] * (0.15 + 0.5 * ((r_ * 3) % 10) / 10))
        move_abs(px_, py_); time.sleep(0.05)
        u.mouse_event(0x0002, 0, 0, 0, 0); u.mouse_event(0x0004, 0, 0, 0, 0); time.sleep(0.05)
        u.mouse_event(0x0800, 0, 0, ctypes.c_ulong(-120 & 0xffffffff).value, 0)
        time.sleep(0.25 + (r_ % 4) * 0.2)                  # 不同時間點（涵蓋舞台每秒重排的時刻）
    time.sleep(0.6)
    t3 = title()
    check("A26 隔離層穩定：20 輪真的點擊＋滾輪＋移動（不同位置與時間點），頁面一次都沒收到",
          bool(t1) and all(t3.get(k) == t1.get(k) for k in "cwm"), (t1, t3))
    time.sleep(CURSOR_WAIT)
    check("A26 真的游標不動約 2 秒就隱藏", rd(geom).get("cursor_hidden") is True, rd(geom).get("cursor_hidden"))
    move_abs(cx + 60, cy + 40); time.sleep(0.5)
    check("A26 真的游標一動就出現", rd(geom).get("cursor_hidden") is False, rd(geom).get("cursor_hidden"))

    # 示範游標：找出那個滑鼠穿透的小窗（舞台行程、WS_EX_TRANSPARENT）
    def ptr_hwnd():
        found = []
        CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def f(hw, _):
            pid = wintypes.DWORD(); u.GetWindowThreadProcessId(hw, ctypes.byref(pid))
            if pid.value == p.pid and u.IsWindowVisible(hw) and u.GetWindowLongW(hw, -20) & 0x20:
                found.append(hw)
            return True
        u.EnumWindows(CB(f), 0)
        return found[0] if found else None
    R = int(26 * max(1.0, H / 1080.0))
    crop, k = now["crop"], DS.dpr_of(h) * float(now.get("zoom") or 1.0)
    tgt1, tgt2 = (700, 400), (330, 160)
    cur0 = cursor()
    json.dump(dict(now, pointer={"x": tgt1[0], "y": tgt1[1], "dur": 0.6, "seq": 1}), open(os.path.join(d, "now.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    time.sleep(1.4)
    ph = wait(ptr_hwnd, 5)
    check("A21 示範游標出現（滑鼠穿透的小窗）", ph, ph)
    if not ph:
        return
    e1 = L.css_point(tgt1, lay["stage"], crop, float(now.get("zoom") or 1.0), DS.dpr_of(h))
    r = DS._rect(ph)
    check("A21 示範游標落在目標（箭頭尖端誤差 ≤2）", abs(r[0] + R - e1[0]) <= 2 and abs(r[1] + R - e1[1]) <= 2, (r, e1))
    check("A26 示範期間系統游標位置沒有改變", cursor() == cur0, (cur0, cursor()))
    # 第二段移動：連續取樣；途中使用者點一下字幕（A27）
    json.dump(dict(now, pointer={"x": tgt2[0], "y": tgt2[1], "dur": 0.8, "seq": 2}, look={"box": [320, 150, 300, 60], "seq": 1}),
              open(os.path.join(d, "now.json"), "w", encoding="utf-8"), ensure_ascii=False)
    samples, t_ = [], time.time()
    pl = rd(geom).get("caption_plate")
    clicked = False
    while time.time() - t_ < 1.3:
        rr = DS._rect(ph)
        samples.append((rr[0] + R, rr[1] + R))
        if not clicked and time.time() - t_ > 0.3 and pl:
            move_abs(pl[0] + 8, pl[1] + pl[3] // 2); u.mouse_event(0x0002, 0, 0, 0, 0); u.mouse_event(0x0004, 0, 0, 0, 0)
            clicked = True
        time.sleep(0.012)
    jumps = [abs(samples[i + 1][0] - samples[i][0]) + abs(samples[i + 1][1] - samples[i][1]) for i in range(len(samples) - 1)]
    e2 = L.css_point(tgt2, lay["stage"], crop, float(now.get("zoom") or 1.0), DS.dpr_of(h))
    check("A21 示範游標的移動是連續的（相鄰取樣位移 ≤40 像素、沒有瞬移）", jumps and max(jumps) <= 40, (max(jumps or [0]), len(samples)))
    check("A27 移動途中使用者點了字幕：示範游標照樣走到目標", abs(samples[-1][0] - e2[0]) <= 2 and abs(samples[-1][1] - e2[1]) <= 2,
          (samples[-1], e2))
    ackp = os.path.join(d, "stage_ack.json")
    t_pub = time.time()
    json.dump(dict(now, pointer={"x": tgt2[0], "y": tgt2[1], "dur": 0.8, "seq": 2}, look={"box": [320, 150, 300, 60], "seq": 1}, press=1),
              open(os.path.join(d, "now.json"), "w", encoding="utf-8"), ensure_ascii=False)
    ack = wait(lambda: rd(ackp) if (rd(ackp).get("press") or 0) >= 1 else None, 2) or {}
    check("A22 按下的效果在發出後 0.1 秒內畫出並回報（引擎等到這個回報才送出點擊）",
          ack.get("t") is not None and ack["t"] - t_pub <= 0.1, (t_pub, ack))
    rip = wait(lambda: rd(geom).get("ripple_on") is True, 0.5)
    check("A22 漣漪確實在畫面上（回報後 0.5 秒內讀到顯示中）", rip, rd(geom).get("ripple_on"))
    shot_png("stage-shot-4-pointer-press.png")
    a1 = rd(geom).get("look_alpha")
    time.sleep(1.9)
    a2 = rd(geom).get("look_alpha")
    check("A25 讀內容的外框很淡（不透明度 ≤0.6），而且約 1.6 秒後淡出", a1 and 0 < a1 <= 0.6 and not a2, (a1, a2))
    shot_png("stage-shot-5-after-fade.png")
    # 卡片（轉場）：示範游標淡出
    json.dump(dict(now, card="transition", pointer={"x": tgt2[0], "y": tgt2[1], "dur": 0, "seq": 2}),
              open(os.path.join(d, "now.json"), "w", encoding="utf-8"), ensure_ascii=False)
    time.sleep(0.8)
    check("轉場卡時示範游標收起", not u.IsWindowVisible(ph), u.IsWindowVisible(ph))


CURSOR_WAIT = 2.8


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
    # 這個情境有兩組 20 輪的測試：舞台要活得比預設的 40 秒久（CI 45060724781／45062678450 都在第 5 輪失敗——
    # 是舞台到了 40 秒自己關掉，不是墊片漏點擊）
    d, p = stage_with(now, None, {"NO_ERRANDS_HOME": work, "NE_STAGE_GEOM": geom, "NE_STAGE_MAX_S": "600"})
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
    # 3 滑鼠一動控制列出現：字幕不動（控制列讓位）
    if pl:
        def move_abs(x_, y_):
            u.mouse_event(0x8001, int(x_ * 65535 / (W - 1)), int(y_ * 65535 / (H - 1)), 0, 0)
        move_abs(W // 2, H // 3); time.sleep(0.15); move_abs(W // 2 + 40, H // 3 + 30); time.sleep(0.8)
        g3 = rd(geom)
        check("控制列出現時，沒有提示文字就不顯示提示列（不留空的深色方塊）", g3.get("hint_visible") is False, g3.get("hint_visible"))
        check("A2 滑鼠移動讓控制列出現時，字幕位置前後不變", g3.get("controls_visible") and g3.get("caption_plate") == pl,
              (pl, g3.get("caption_plate"), g3.get("controls_visible")))
        cr3 = g3.get("controls_rect")
        check("控制列出現時不蓋住字幕", cr3 and not L.overlaps(cr3, pl), (cr3, pl))
        # 4 滑鼠停在字幕上時換句子：字幕不跳（以中心與底邊為錨點）
        move_abs(pl[0] + pl[2] // 2, pl[1] + pl[3] // 2); time.sleep(0.6)
        n2 = dict(now, beat={"text": "第二句比較短。", "i": 2, "n": 2})
        json.dump(n2, open(os.path.join(d, "now.json"), "w", encoding="utf-8"), ensure_ascii=False)
        time.sleep(1.0)
        g4 = rd(geom)
        p4 = g4.get("caption_plate")
        check("A2 滑鼠停在字幕上換句子：字幕不跳（中心與底邊不變）",
              p4 and g4.get("caption_text") == "第二句比較短。" and abs((p4[0] + p4[2] / 2) - (pl[0] + pl[2] / 2)) <= 2
              and abs((p4[1] + p4[3]) - (pl[1] + pl[3])) <= 2, (pl, p4, g4.get("caption_text")))
        json.dump(now, open(os.path.join(d, "now.json"), "w", encoding="utf-8"), ensure_ascii=False)
        time.sleep(1.0)
        pl = rd(geom).get("caption_plate") or pl
    # 5 字幕上的操作：從三種位置（內距、字的中間、字與字之間）各拖一次；按兩下回預設；× 關字幕；都不穿到頁面
    import ctypes as _ct
    from ctypes import wintypes as _wt

    def page_clicks():
        b = _ct.create_unicode_buffer(300)
        u.GetWindowTextW(h, b, 300)
        m_ = re.search(r"\bc=(\d+)", b.value)
        return int(m_.group(1)) if m_ else None

    def move(x_, y_):
        # 真的滑鼠移動輸入（MOVE|ABSOLUTE），和人拖動一樣
        u.mouse_event(0x8001, int(x_ * 65535 / (W - 1)), int(y_ * 65535 / (H - 1)), 0, 0)

    u.WindowFromPoint.argtypes = [_wt.POINT]
    u.WindowFromPoint.restype = _wt.HWND
    u.GetAncestor.argtypes = [_wt.HWND, _wt.UINT]
    u.GetAncestor.restype = _wt.HWND

    def who(x_, y_):
        """按下處實際是哪個視窗（診斷用：點擊會送到誰）。"""
        root_ = u.GetAncestor(u.WindowFromPoint(_wt.POINT(int(x_), int(y_))), 2)
        if not root_:
            return "?"
        for k_, n_ in (("caption_pad", "墊片"), ("caption_textwin", "字"), ("caption_plate", "底板")):
            r_ = rd(geom).get(k_)
            if r_ and list(DS._rect(root_)) == r_:
                return n_
        cb = _ct.create_unicode_buffer(64); u.GetClassNameW(root_, cb, 64)
        return cb.value

    def zstack(x_, y_):
        """失敗時的診斷：這一點上由上到下的頂層視窗（類別、是否我們的）＋舞台回報的上下順序。"""
        out_, hw_ = [], u.GetTopWindow(None)
        while hw_ and len(out_) < 8:
            r_ = DS._rect(hw_)
            if u.IsWindowVisible(hw_) and r_[0] <= x_ < r_[0] + r_[2] and r_[1] <= y_ < r_[1] + r_[3]:
                cb_ = _ct.create_unicode_buffer(64); u.GetClassNameW(hw_, cb_, 64)
                pid_ = _wt.DWORD(); u.GetWindowThreadProcessId(hw_, _ct.byref(pid_))
                out_.append("%s%s" % (cb_.value, "(舞台)" if pid_.value == p.pid else ""))
            hw_ = u.GetWindow(hw_, 2)
        return {"at_point": out_, "stage_alive": p.poll() is None, "stage_z": rd(geom).get("z_order")}

    def stress_rounds(n):
        """同一次執行重複 n 輪：三種位置的拖動、按兩下回預設、× 收起再用控制列的「字幕」打開；每一步都檢查，
        頁面收到的點擊數全程不變。只過一次不算（5c）。"""
        fails, kinds = [], [(0.02, 0.5, "內距"), (0.5, 0.5, "字的中間"), (0.27, 0.62, "字與字之間")]
        c_start = page_clicks()
        for r_ in range(n):
            fx, fy, lab = kinds[r_ % 3]
            sgn = 1 if r_ % 2 == 0 else -1
            g0 = rd(geom).get("caption_plate")
            if not g0:
                fails.append((r_, "字幕沒有顯示", None)); break
            sx_, sy_ = g0[0] + fx * g0[2], g0[1] + fy * g0[3]
            move(sx_, sy_); time.sleep(0.3)
            u.mouse_event(0x0002, 0, 0, 0, 0); time.sleep(0.1)
            for s_ in range(1, 11):
                move(sx_ + sgn * 6 * s_, sy_ - sgn * 4 * s_); time.sleep(0.03)
            u.mouse_event(0x0004, 0, 0, 0, 0); time.sleep(0.6)
            g1 = rd(geom).get("caption_plate")
            if not (g1 and abs((g1[0] - g0[0]) - sgn * 60) <= 6 and abs((g1[1] - g0[1]) + sgn * 40) <= 6):
                fails.append((r_, "拖動（%s）" % lab, (g0, g1, zstack(sx_, sy_))))
            # 按兩下回預設
            g0 = rd(geom).get("caption_plate") or g0
            move(g0[0] + g0[2] // 2, g0[1] + g0[3] // 2); time.sleep(0.25)
            for _ in range(2):
                u.mouse_event(0x0002, 0, 0, 0, 0); u.mouse_event(0x0004, 0, 0, 0, 0); time.sleep(0.08)
            time.sleep(0.7)
            g1 = rd(geom)
            if g1.get("cap_user") is not None:
                fails.append((r_, "按兩下", (g1.get("caption_plate"), zstack(g0[0] + g0[2] // 2, g0[1] + g0[3] // 2))))
            # × 收起（滑鼠停在字幕上 → × 出現在外側右上角 → 點它），再用控制列的「字幕」打開
            time.sleep(0.4)                                # 按兩下之後字幕移回預設位置：等位置穩定再讀
            g0 = rd(geom).get("caption_plate") or g0
            xb = wait(lambda: rd(geom).get("xbtn"), 2)
            if xb:
                move(xb[0] + xb[2] // 2, xb[1] + xb[3] // 2); time.sleep(0.3)
                u.mouse_event(0x0002, 0, 0, 0, 0); u.mouse_event(0x0004, 0, 0, 0, 0); time.sleep(0.7)
            g1 = rd(geom)
            if g1.get("caption_on") is not False:
                fails.append((r_, "×", (xb, zstack(*(xb[:2] if xb else g0[:2])))))
            # 字幕收起後控制列移回右下角：等它實際位置＝應有位置、按鈕座標穩定了再按（讀到移動前的座標會按空）
            def ctl_settled():
                g_ = rd(geom)
                cr_, cw_ = g_.get("controls_rect"), g_.get("controls_want")
                return g_ if (g_.get("cap_btn") and cr_ and cw_ and cr_[:2] == cw_[:2]) else None
            g1 = wait(ctl_settled, 2) or rd(geom)
            time.sleep(0.25)
            g1 = ctl_settled() or g1
            cb = g1.get("cap_btn")
            if cb:
                move(cb[0], cb[1]); time.sleep(0.3)
                u.mouse_event(0x0002, 0, 0, 0, 0); u.mouse_event(0x0004, 0, 0, 0, 0); time.sleep(0.8)
            if rd(geom).get("caption_on") is not True:
                fails.append((r_, "用控制列打開字幕", {"cap_btn": cb, "controls_rect": g1.get("controls_rect"),
                                                   "controls_want": g1.get("controls_want"), "stack": zstack(*cb) if cb else None}))
                break
            c_now = page_clicks()
            if c_now != c_start:
                fails.append((r_, "頁面收到點擊 %s→%s" % (c_start, c_now), None))
                c_start = c_now
        check("字幕墊片穩定：同一次執行重複 %d 輪（拖動、按兩下、×、再打開），每一步都成立、頁面一次點擊都沒收到" % n,
              not fails, fails[:3])

    def drag_from(fx, fy, label, dx=-200, dy=-150):
        g0 = rd(geom).get("caption_plate")
        sx_, sy_ = g0[0] + fx * g0[2], g0[1] + fy * g0[3]
        move(sx_, sy_); time.sleep(0.4)
        hit = who(sx_, sy_)
        u.mouse_event(0x0002, 0, 0, 0, 0); time.sleep(0.15)
        for s_ in range(1, 21):
            move(sx_ + dx * s_ / 20, sy_ + dy * s_ / 20); time.sleep(0.04)
        time.sleep(0.2)
        u.mouse_event(0x0004, 0, 0, 0, 0); time.sleep(1.0)
        g1 = rd(geom)
        p1, t1, d1 = g1.get("caption_plate"), g1.get("caption_textwin"), g1.get("caption_pad")
        check("A2 從%s拖動：字幕移動約 %d×%d，三層（底板、字、墊片）同位置（按下處是：%s）" % (label, -dx, -dy, hit),
              p1 and p1 == t1 == d1 and abs((g0[0] - p1[0]) + dx) <= 8 and abs((g0[1] - p1[1]) + dy) <= 8,
              (g0, p1, t1, d1, "cap_user=%s" % g1.get("cap_user")))
        return p1
    c0 = page_clicks()
    if pl:
        drag_from(6.0 / pl[2], 0.5, "內距")
        drag_from(0.5, 0.5, "字的中間", dx=150, dy=60)
        drag_from(0.27, 0.62, "字與字之間／筆畫附近", dx=-80, dy=90)
        shot_png("stage-shot-3-dragged.png")
        check("A2 拖過之後位置存檔（下次沿用）", rd(os.path.join(work, "stage-caption.json")).get("pos"),
              rd(os.path.join(work, "stage-caption.json")))
        stress_rounds(20)
    f3_checks(d, p, now, h, geom, W, H, lay)
    json.dump(now, open(os.path.join(d, "now.json"), "w", encoding="utf-8"), ensure_ascii=False)   # 回到台上（f3 最後是轉場卡）
    time.sleep(1.5)
    if pl:
        # 按兩下：回到預設位置（即使游標停在字幕上也立刻移回）
        g0 = rd(geom).get("caption_plate")
        check("（接續前）字幕顯示中", g0, rd(geom).get("caption_on"))
    if pl and rd(geom).get("caption_plate"):
        g0 = rd(geom).get("caption_plate")
        move(g0[0] + g0[2] // 2, g0[1] + g0[3] // 2); time.sleep(0.3)
        for _ in range(2):
            u.mouse_event(0x0002, 0, 0, 0, 0); u.mouse_event(0x0004, 0, 0, 0, 0); time.sleep(0.08)
        time.sleep(1.0)
        g1 = rd(geom)
        check("A2 在字幕上按兩下 → 回到預設位置（下方置中）", g1.get("cap_user") is None and g1.get("caption_plate")
              and abs((g1["caption_plate"][0] + g1["caption_plate"][2] / 2) - W / 2) <= 2, (g0, g1.get("caption_plate")))
        # ×：滑鼠移到字幕上 → × 出現在字幕塊右上角外側（不壓字）→ 點它 → 字幕收起
        g0 = rd(geom).get("caption_plate")
        move(g0[0] + g0[2] - 10, g0[1] + g0[3] // 2); time.sleep(0.6)
        xb = rd(geom).get("xbtn")
        check("A2 滑鼠停在字幕上 → × 出現在字幕塊右上角（在預留的空位裡）", xb and rd(geom).get("x_shown") and L.inside(xb, g0)
              and xb[0] + xb[2] >= g0[0] + g0[2] - 8,
              (xb, g0))
        if xb:
            move(xb[0] + xb[2] // 2, xb[1] + xb[3] // 2); time.sleep(0.4)
            u.mouse_event(0x0002, 0, 0, 0, 0); u.mouse_event(0x0004, 0, 0, 0, 0); time.sleep(0.8)
        g1 = rd(geom)
        check("A2 點字幕右上角的 × → 字幕收起", g1.get("caption_on") is False and not g1.get("caption_plate"), g1.get("caption_plate"))
        time.sleep(0.5)
        c1 = page_clicks()
        check("字幕上的拖動、按兩下、× 都沒有穿到底下的頁面（頁面收到的點擊數不變）", c0 is not None and c1 == c0, (c0, c1))
    json.dump({"status": "closing"}, open(os.path.join(d, "now.json"), "w"))
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        kill_tree(p)
    kill_tree(cp)
    shutil.rmtree(work, ignore_errors=True)


def main():
    if os.name != "nt" or "--i-am-ci" not in sys.argv or os.environ.get("GITHUB_ACTIONS") != "true":
        # 全螢幕視窗、真的滑鼠輸入、擷圖：只在 GitHub Actions 的測試機上（不動使用者的螢幕、滑鼠鍵盤）
        print("SKIP 舞台視窗測試：只在 Windows CI 跑（要 --i-am-ci，且 GITHUB_ACTIONS=true）")
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
        time.sleep(1.0)                                    # 控制列依字幕位置讓位：等數值穩定再讀
        g = rd(geom) or g
        m1 = g.get("controls_moves")
        time.sleep(1.2)
        g2_ = rd(geom) or g
        check("%d×%d：控制列擺好之後不再重擺（重擺次數穩定、沒有卡住）" % (W, H),
              m1 is not None and g2_.get("controls_moves") == m1 and not g2_.get("controls_stuck"),
              (m1, g2_.get("controls_moves"), g2_.get("controls_stuck"), g2_.get("controls_rect"), g2_.get("controls_want")))
        lines = L.caption_lines(two_lines, W, H)
        check("%d×%d：字幕估算兩行以內（%d 行）" % (W, H, lines), lines <= 2, lines)
        check("%d×%d：字幕塊實際排出來最多兩行、不超過最大寬度（舞台寬 62%%）" % (W, H),
              g and g["caption_req"][1] <= 2 * g["cap_line_h"] + 2 * g["cap_pad"] + 12 and g["caption_req"][0] <= g["cap_max_w"] + 2, g)
        check("%d×%d：字幕就是這一拍的句子" % (W, H), g and g["caption_text"] == two_lines, g and g.get("caption_text"))
        check("%d×%d：講者模式輪到他按 → 控制列出現在右側，「下一步」在右半邊；和字幕重疊時控制列讓到字幕上方" % (W, H),
              g and g["controls_visible"] and g["next"][0] > W * 0.6 and g.get("controls_rect") and g.get("caption_plate")
              and not L.overlaps(g["controls_rect"], g["caption_plate"]), g and (g.get("next"), g.get("controls_rect"), g.get("caption_plate"), "want=%s" % g.get("controls_want")))
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
