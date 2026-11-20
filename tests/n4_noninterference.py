#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""N4 不干擾實測（Windows）。全程用 sampler.ps1 每 50ms 記錄前景視窗與游標（不記標題）。

第一部分（共用測試，約 20–25 秒）：
  視窗 A = NE-TYPEPAD：以 PostMessage(WM_CHAR) 每 100ms 往自己的文字框送一字、共 200 字
  （**不取得前景**——使用者同時在用電腦，不能為了測試把他的前景搶走 20 秒）。
  同時對視窗 B（假聊天，-NoActivate）用 chat_guard 反覆 locate/open/read/export/close，並截圖。
  驗收：A 收到的字串與送出的完全一致；B 操作全成功；前景沒有變成我們的視窗；游標沒被我們移動。
第二部分（逐項量測）：agent-desktop screenshot --window-id、launch/close-app（小算盤，只在它沒開時）、
  agent-browser headless open/click/screenshot/close、chat_guard export（另存新檔對話框）、
  outbox draft（背景模式，不開頁面）。

判定：某一步期間前景變成「我們的視窗/程序」→ interference=true。前景變成其他視窗或游標移動，
可能是使用者自己的操作，標 "maybe_user"，不算我們的干擾，但照實列出。
用法：python tests/n4_noninterference.py --home <no-errands home（含 bin/）> [--out result.json]
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SC = os.path.join(REPO, "skills", "no-errands", "scripts")
FCD = os.path.join(REPO, "scenarios", "fake-chat")
STEPS = []


def now_ms():
    return int(time.time() * 1000)


def ps_start(script, *args):
    """以隱藏的 PowerShell 啟動 .ps1，回傳 Popen。"""
    return subprocess.Popen(["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File",
                             os.path.join(FCD, script)] + list(args),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=0x08000000)  # CREATE_NO_WINDOW


def step(name, fn):
    t0 = now_ms()
    try:
        ok, info = fn()
    except Exception as e:
        ok, info = False, str(e)[:200]
    STEPS.append({"step": name, "t0": t0, "t1": now_ms(), "ok": ok, "info": info})
    return ok


def run(cmd, env=None, timeout=120):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, timeout=timeout)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    src_bin = os.path.join(a.home, "bin")
    tmp = tempfile.mkdtemp(prefix="ne-n4-")
    home = os.path.join(tmp, "home")
    os.makedirs(os.path.join(home, "bin"))
    for exe in ("agent-desktop.exe", "agent-browser.exe"):
        shutil.copyfile(os.path.join(src_bin, exe), os.path.join(home, "bin", exe))
    AD = os.path.join(home, "bin", "agent-desktop.exe")
    AB = os.path.join(home, "bin", "agent-browser.exe")
    env = dict(os.environ, NO_ERRANDS_HOME=home, PYTHONIOENCODING="utf-8")
    for args in (["grant", "fake-chat"], ["whitelist", "add", "Alpha Team"]):
        run([sys.executable, os.path.join(SC, "policy.py")] + args + ["--reason", "N4 test"], env=env)
    with open(os.path.join(home, "policy.json"), encoding="utf-8") as f:
        pol = json.load(f)
    pol.setdefault("outbox", {})["port"] = 8797
    json.dump(pol, open(os.path.join(home, "policy.json"), "w", encoding="utf-8"))

    samples_p = os.path.join(tmp, "samples.jsonl")
    stop_p = os.path.join(tmp, "stop")
    state = os.path.join(tmp, "fakechat")
    typed_p = os.path.join(tmp, "typed.json")
    sampler = ps_start("sampler.ps1", "-Out", samples_p, "-Seconds", "240", "-IntervalMs", "50", "-StopFile", stop_p)
    time.sleep(1.5)
    our_pids = set()
    fake = None
    try:
        # ---------- 第一部分
        fake = ps_start("fakechat.ps1", "-StateDir", state, "-NoActivate")
        for _ in range(40):
            time.sleep(0.3)
            ev = os.path.join(state, "events.jsonl")
            if os.path.exists(ev) and os.path.getsize(ev) > 0:
                our_pids.add(json.loads(open(ev, encoding="utf-8").readline())["pid"])
                break
        time.sleep(1.0)
        t_shared0 = now_ms()
        pad = ps_start("typepad.ps1", "-OutFile", typed_p, "-Count", "200", "-IntervalMs", "100")
        our_pids.add(pad.pid)
        cg = [sys.executable, os.path.join(SC, "chat_guard.py"), "fake-chat"]

        def cgo(*args):
            p = run(cg + list(args), env=env)
            try:
                o = json.loads(p.stdout)
            except Exception:
                o = {"ok": False, "raw": p.stdout[-200:]}
            return bool(o.get("ok")), {k: v for k, v in o.items() if k in ("ok", "error", "lines", "via")}

        def win_b():
            lw = json.loads(run([AD, "list-windows"]).stdout)["data"]
            return [w for w in lw if w.get("title") == "NE-FAKECHAT"][0]

        shot = os.path.join(tmp, "b.png")
        rounds = 0
        while pad.poll() is None and rounds < 6:
            rounds += 1
            step("shared:locate", lambda: cgo("locate", "--chat", "Alpha Team"))
            step("shared:open", lambda: cgo("open", "--chat", "Alpha Team"))
            step("shared:read", lambda: cgo("read", "--chat", "Alpha Team"))
            step("shared:screenshot-window", lambda: (run([AD, "screenshot", shot, "--window-id", win_b()["id"]]).returncode == 0, ""))
            step("shared:export", lambda: cgo("export", "--to", os.path.join(tmp, "exp")))
            step("shared:close", lambda: cgo("close"))
        pad.wait(timeout=60)
        t_shared1 = now_ms()
        typed = json.load(open(typed_p, encoding="utf-8")) if os.path.exists(typed_p) else {}
        typing_ok = typed.get("sent") and typed.get("sent") == typed.get("received")

        # ---------- 第二部分
        step("solo:ad-screenshot --window-id", lambda: (run([AD, "screenshot", shot, "--window-id", win_b()["id"]]).returncode == 0, ""))
        step("solo:chat_guard export (save dialog)", lambda: (cgo("open", "--chat", "Alpha Team")[0] and cgo("export", "--to", os.path.join(tmp, "exp2"))[0], ""))
        cgo("close")
        CALC_TITLES = ("小算盤", "Calculator")

        def calc_windows():
            lw = json.loads(run([AD, "list-windows"]).stdout)["data"]
            return [w for w in lw if w.get("title") in CALC_TITLES]

        if calc_windows():
            STEPS.append({"step": "solo:launch calc", "skipped": "已有小算盤視窗（可能是使用者的），不測"})
        else:
            def calc():
                # calc.exe 是跳板（啟動 UWP 小算盤後即結束）→ 等新視窗出現，只關這一個視窗（點它的關閉鈕），
                # 不用 close-app（按程序名會波及使用者的視窗）。
                r1 = run([AD, "launch", "calc", "--timeout", "8000"])
                wins = []
                for _ in range(30):
                    time.sleep(0.3)
                    wins = calc_windows()
                    if wins:
                        break
                if len(wins) != 1:
                    return False, "calc window count=%d" % len(wins)
                snap = json.loads(run([AD, "snapshot", "--window-id", wins[0]["id"], "-i", "--compact"]).stdout)
                ref = None
                stack = [snap["data"]["tree"]]
                while stack:
                    n = stack.pop()
                    if (n.get("native_id") or {}).get("value") == "Close" and n.get("ref_id"):
                        ref = n["ref_id"]; break
                    stack.extend(n.get("children") or [])
                r2 = run([AD, "click", ref]) if ref else None
                time.sleep(0.8)
                return r1.returncode == 0 and r2 is not None and r2.returncode == 0 and not calc_windows(), ""
            step("solo:launch calc (new window) + close via its button", calc)
        page = "file:///" + os.path.join(REPO, "doctor-assets", "testpage.html").replace("\\", "/")
        sess = ["--session", "ne-n4"]
        step("solo:agent-browser headless open", lambda: (run([AB] + sess + ["open", page]).returncode == 0, ""))
        step("solo:agent-browser headless snapshot+click", lambda: (run([AB] + sess + ["snapshot", "-i", "-c"]).returncode == 0
                                                                     and run([AB] + sess + ["click", "button"]).returncode == 0, ""))
        step("solo:agent-browser headless screenshot", lambda: (run([AB] + sess + ["screenshot", os.path.join(tmp, "ab.png").replace("\\", "/")]).returncode == 0, ""))
        step("solo:agent-browser close", lambda: (run([AB] + sess + ["close"]).returncode == 0, ""))
        body = os.path.join(tmp, "body.txt")
        open(body, "w", encoding="utf-8").write("N4 test")
        step("solo:outbox draft (background, page not opened)", lambda: (json.loads(run(
            [sys.executable, os.path.join(SC, "outbox.py"), "draft", "--service", "fake", "--to", "Alpha Team",
             "--body-file", body], env=env).stdout).get("page_opened") is False, ""))
    finally:
        open(stop_p, "w").close()
        time.sleep(0.5)
        try:
            sampler.wait(timeout=10)
        except Exception:
            sampler.kill()
        if fake:
            for pid in list(our_pids):
                run(["taskkill", "/PID", str(pid), "/F"])
        # 只關本測試的發件匣伺服器（以 home 的 server.log 取得 PID）
        try:
            for line in open(os.path.join(home, "outbox", "server.log"), encoding="utf-8"):
                if '"pid"' in line:
                    run(["taskkill", "/PID", str(json.loads(line)["pid"]), "/F"])
        except Exception:
            pass

    samples = [json.loads(l) for l in open(samples_p, encoding="utf-8") if l.strip()]
    base = samples[0] if samples else {}

    def analyze(t0, t1):
        win = [s for s in samples if t0 - 100 <= s["t"] <= t1 + 300]
        fg_ours = any(s["ours"] or s["pid"] in our_pids for s in win)
        fg_other = len({s["hwnd"] for s in win}) > 1
        cur = len({(s["x"], s["y"]) for s in win}) > 1
        return {"samples": len(win), "interference": fg_ours, "fg_changed_other": fg_other and not fg_ours,
                "cursor_moved": cur}

    for s in STEPS:
        if "t0" in s:
            s.update(analyze(s["t0"], s["t1"]))
    shared = analyze(t_shared0, t_shared1)
    report = {"typing": {"sent_len": len(typed.get("sent", "")), "received_len": len(typed.get("received", "")),
                         "identical": bool(typing_ok)},
              "shared_window": shared, "rounds": rounds, "steps": STEPS, "samples_total": len(samples),
              "baseline_hwnd_kept": all(s["hwnd"] == base.get("hwnd") for s in samples) if samples else None}
    print(json.dumps({"typing": report["typing"], "shared": shared, "rounds": rounds}, ensure_ascii=False))
    print("%-48s %-5s %-6s %-12s %-6s" % ("step", "ok", "ours", "fg-other", "cursor"))
    for s in STEPS:
        if "skipped" in s:
            print("%-48s skipped: %s" % (s["step"], s["skipped"])); continue
        print("%-48s %-5s %-6s %-12s %-6s" % (s["step"], s["ok"], s["interference"], s["fg_changed_other"], s["cursor_moved"]))
    if a.out:
        json.dump(report, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
