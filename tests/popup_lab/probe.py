#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Popup-menu lab (CI only; it opens windows). Tries ways to reach a popup menu item that is NOT in
agent-desktop's tree, recording for each: success, cursor moved, foreground changed.

  python tests/popup_lab/probe.py --ad <agent-desktop.exe> --out result.json [--no-a11y]

Methods (each on a freshly opened menu):
  M1 uia-by-hwnd   : Win32 EnumWindows finds the popup's top-level HWND -> UIA FromHandle -> Invoke "Save chat"
  M2 post-mouse    : capture ONLY the popup (PrintWindow) -> click position (CI stands in for the agent reading the
                     image: Qt menu rows are equal height) -> PostMessage WM_LBUTTONDOWN/UP to the popup HWND
  M3 post-keys     : PostMessage WM_KEYDOWN Down,Down,Return to the popup HWND
Success = the app logs "save-dialog-open"; the dialog is then cancelled. A "Leave group" confirmation is always
answered No (a confirm would be logged as a violation by the app).
"""
import ctypes, ctypes.wintypes as wt, json, os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
u32 = ctypes.windll.user32
RESULT = {"methods": {}}


def arg(k, d=None):
    return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d


AD = arg("--ad")
NO_A11Y = "--no-a11y" in sys.argv


def ad(*a):
    p = subprocess.run([AD] + list(a), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"ok": False}


def ps(script, *args):
    p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(HERE, script)]
                       + [str(x) for x in args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"raw": p.stdout[-300:], "err": p.stderr[-300:]}


def cursor():
    pt = wt.POINT(); u32.GetCursorPos(ctypes.byref(pt)); return (pt.x, pt.y)


def windows_of(pid):
    out = []
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        p = wt.DWORD(); u32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(h):
            cls = ctypes.create_unicode_buffer(256); u32.GetClassNameW(h, cls, 256)
            t = ctypes.create_unicode_buffer(256); u32.GetWindowTextW(h, t, 256)
            r = wt.RECT(); u32.GetWindowRect(h, ctypes.byref(r))
            out.append({"hwnd": h, "class": cls.value, "title": t.value, "rect": [r.left, r.top, r.right, r.bottom]})
        return True
    u32.EnumWindows(PROC(cb), 0)
    return out


def events(state):
    p = os.path.join(state, "events.jsonl")
    return [json.loads(l) for l in open(p, encoding="utf-8")] if os.path.exists(p) else []


def main():
    tmp = tempfile.mkdtemp(prefix="qtlab-")
    state = os.path.join(tmp, "state")
    args = [sys.executable, os.path.join(HERE, "qt_menu_app.py"), "--state", state] + (["--no-a11y"] if NO_A11Y else [])
    app = subprocess.Popen(args)
    pid = None
    for _ in range(60):
        time.sleep(0.5)
        ev = events(state)
        if ev:
            pid = ev[0]["pid"]; break
    time.sleep(1.5)
    RESULT["mode"] = "no-a11y" if NO_A11Y else "a11y"

    def main_win():
        lw = ad("list-windows").get("data") or []
        return [w for w in lw if w.get("title") == "NE-QTLAB"]

    def open_menu():
        w = main_win()
        if not w:
            return None, "main window not in list-windows"
        snap = ad("snapshot", "--window-id", w[0]["id"], "-i", "--compact")
        stack, ref = [snap.get("data", {}).get("tree", {})], None
        while stack:
            n = stack.pop()
            if n.get("name") == "More" and n.get("ref_id"):
                ref = n["ref_id"]; break
            stack.extend(n.get("children") or [])
        if not ref:
            return None, "More button not found"
        before = {x["hwnd"] for x in windows_of(pid)}
        ad("click", ref)
        time.sleep(0.8)
        new = [x for x in windows_of(pid) if x["hwnd"] not in before]
        return new, None

    def popup_in_agent_desktop():
        lw = ad("list-windows").get("data") or []
        return [w.get("title") for w in lw if w.get("pid") == pid]

    def cleanup_dialogs():
        """Cancel a save dialog / answer No to a confirmation, via UIA by HWND (never confirm)."""
        for w in windows_of(pid):
            if w["title"] in ("Save chat", "Leave group"):
                ps("uia_click.ps1", w["hwnd"], "No|Cancel|否|取消")
        time.sleep(0.5)
        for w in windows_of(pid):  # close any menu still open
            if w["title"] == "" and w["hwnd"] != 0 and "Popup" in w["class"]:
                u32.PostMessageW(w["hwnd"], 0x0100, 0x1B, 0); u32.PostMessageW(w["hwnd"], 0x0101, 0x1B, 0)  # Esc to the popup only
        time.sleep(0.4)

    def attempt(name, fn):
        n_before = len(events(state))
        c0, f0 = cursor(), u32.GetForegroundWindow()
        new, err = open_menu()
        rec = {"popup_windows": [{k: v for k, v in x.items() if k != "hwnd"} for x in (new or [])],
               "popup_listed_by_agent_desktop": popup_in_agent_desktop()}
        if err or not new:
            rec.update(ok=False, error=err or "no new top-level window appeared")
        else:
            try:
                rec.update(fn(new[0]))
            except Exception as e:
                rec.update(ok=False, error=str(e)[:200])
            time.sleep(1.5)
            ev = [e["kind"] for e in events(state)[n_before:]]
            rec["events"] = ev
            rec["ok"] = "save-dialog-open" in ev
            rec["violation"] = any(k.startswith("VIOLATION") for k in ev)
        rec["cursor_moved"] = cursor() != c0
        rec["foreground_changed"] = u32.GetForegroundWindow() != f0
        cleanup_dialogs()
        RESULT["methods"][name] = rec

    def m1(popup):
        r = ps("uia_dump.ps1", popup["hwnd"])
        res = {"uia_items": r.get("items", [])[:12]}
        names = [i.get("name") for i in r.get("items", [])]
        if "Save chat" in names:
            res["invoke"] = ps("uia_click.ps1", popup["hwnd"], "Save chat")
        else:
            res["note"] = "Save chat not exposed via UIA on the popup HWND"
        return res

    def m2(popup):
        shot = os.path.join(tmp, "popup-%s.png" % RESULT["mode"])
        cap = ps("capture.ps1", popup["hwnd"], shot)
        l, t, r, b = popup["rect"]
        w, h = r - l, b - t
        # stand-in for the agent reading the cropped image: 3 equal rows, target is row 2
        x, y = w // 2, int(h * (2 * 2 - 1) / 6)
        lp = (y << 16) | (x & 0xFFFF)
        u32.PostMessageW(popup["hwnd"], 0x0200, 0, lp)          # WM_MOUSEMOVE (to the window, not the cursor)
        u32.PostMessageW(popup["hwnd"], 0x0201, 0x0001, lp)     # WM_LBUTTONDOWN
        u32.PostMessageW(popup["hwnd"], 0x0202, 0, lp)          # WM_LBUTTONUP
        return {"capture": cap, "capture_file": os.path.basename(shot), "client_xy": [x, y]}

    def m3(popup):
        for vk in (0x28, 0x28, 0x0D):  # Down, Down, Return
            u32.PostMessageW(popup["hwnd"], 0x0100, vk, 0); time.sleep(0.15)
            u32.PostMessageW(popup["hwnd"], 0x0101, vk, 0xC0000000); time.sleep(0.15)
        return {}

    attempt("M1_uia_by_hwnd", m1)
    attempt("M2_post_mouse_to_popup", m2)
    attempt("M3_post_keys_to_popup", m3)
    RESULT["violations_total"] = sum(1 for e in events(state) if e["kind"].startswith("VIOLATION"))
    app.kill()
    out = arg("--out")
    for f in os.listdir(tmp):
        if f.endswith(".png") and out:
            os.replace(os.path.join(tmp, f), os.path.join(os.path.dirname(os.path.abspath(out)), f))
    print(json.dumps(RESULT, ensure_ascii=False, indent=1))
    if out:
        json.dump(RESULT, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
