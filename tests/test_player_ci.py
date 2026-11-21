#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示範播放器的視窗測試（Windows；會用真的滑鼠、開視窗——只在 CI 跑，要 --i-am-ci）。

  1 播放器開在引擎排好的位置（now.json 的 layout.bar），字幕顯示 now.json 的字幕
  2 真的滑鼠點「暫停」→ control.json 變成 paused；點完前景視窗沒有被播放器搶走（WS_EX_NOACTIVATE）
  3 再點一次 → playing；點「下一步」→ next_seq +1
  4 按住字幕拖動 → 整個視窗（字幕與按鈕一起）移動；位置寫進 player.json
  5 關掉重開 → 開在拖動後的位置；「歸位」→ 回到預設位置
"""
import ctypes, json, os, shutil, subprocess, sys, tempfile, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SC = os.path.join(REPO, "skills", "no-errands", "scripts")
R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]), flush=True)


def rd(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def wait(fn, t=10):
    t0 = time.time()
    while time.time() - t0 < t:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    return fn()


def main():
    if os.name != "nt":
        print("SKIP (Windows only)"); return
    if "--i-am-ci" not in sys.argv:
        print("SKIP：會開視窗、動滑鼠，只在 CI 跑（加 --i-am-ci）"); return
    u = ctypes.windll.user32
    td = tempfile.mkdtemp(prefix="ne-player-")
    home = os.path.join(td, "home"); os.makedirs(home)
    run = os.path.join(td, "run"); os.makedirs(run)
    geom = os.path.join(td, "geom.json")
    json.dump({"step_index": 3, "steps_total": 12, "role": "長官", "status": "running", "step_id": "s03",
               "captions": [{"text": "字幕測試ABC", "s": 30}], "layout": {"bar": [100, 500, 1000, 170]}},
              open(os.path.join(run, "now.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"state": "playing", "advance": "auto", "next_seq": 0}, open(os.path.join(run, "control.json"), "w"))
    env = dict(os.environ, NO_ERRANDS_HOME=home, NE_PLAYER_GEOM=geom, PYTHONIOENCODING="utf-8")

    def launch():
        if os.path.exists(geom):
            os.remove(geom)
        return subprocess.Popen([sys.executable, os.path.join(SC, "demo_player.py"), run], env=env)

    def click(x, y):
        u.SetCursorPos(int(x), int(y)); time.sleep(0.1)
        u.mouse_event(0x0002, 0, 0, 0, 0); time.sleep(0.05); u.mouse_event(0x0004, 0, 0, 0, 0)

    p = launch()
    try:
        g = wait(lambda: rd(geom).get("window") and rd(geom), 15)
        check("1 播放器開在 layout.bar 的位置", g and abs(g["window"][0] - 100) <= 2 and abs(g["window"][1] - 500) <= 2, g.get("window"))
        check("1 字幕顯示 now.json 的字幕", g.get("caption_text") == "字幕測試ABC", g.get("caption_text"))
        fg0 = u.GetForegroundWindow()
        click(*g["pause"])
        st = wait(lambda: rd(os.path.join(run, "control.json")).get("state") == "paused", 5)
        check("2 真的滑鼠點「暫停」→ control.json paused", st, rd(os.path.join(run, "control.json")))
        time.sleep(0.5)
        check("2 點按鈕不搶前景（鍵盤仍在原本的視窗）", u.GetForegroundWindow() == fg0, (fg0, u.GetForegroundWindow()))
        click(*g["pause"])
        check("3 再點 → playing", wait(lambda: rd(os.path.join(run, "control.json")).get("state") == "playing", 5))
        click(*g["next"])
        check("3 下一步 → next_seq +1", wait(lambda: rd(os.path.join(run, "control.json")).get("next_seq") == 1, 5))
        x, y = g["caption"]
        u.SetCursorPos(x, y); u.mouse_event(0x0002, 0, 0, 0, 0)
        for k in range(1, 21):
            u.SetCursorPos(x + 10 * k, y - 5 * k); time.sleep(0.02)
        u.mouse_event(0x0004, 0, 0, 0, 0)
        g2 = wait(lambda: rd(geom).get("window") != g["window"] and rd(geom), 5)
        time.sleep(1.5)
        g2 = rd(geom)
        dx, dy = g2["window"][0] - g["window"][0], g2["window"][1] - g["window"][1]
        check("4 拖動字幕 → 整個視窗往拖動方向移動", dx > 60 and dy < -30, (g["window"], g2.get("window")))
        check("4 按鈕跟著字幕一起移動（位移相同）", abs((g2["pause"][0] - g["pause"][0]) - dx) <= 3
              and abs((g2["pause"][1] - g["pause"][1]) - dy) <= 3, (g["pause"], g2.get("pause"), dx, dy))
        saved = wait(lambda: rd(os.path.join(home, "player.json")).get("pos"), 5)
        check("4 位置寫進 player.json（等於目前位置）", saved and abs(saved[0] - g2["window"][0]) <= 4
              and abs(saved[1] - g2["window"][1]) <= 4, (saved, g2.get("window")))
        p.kill(); p.wait(timeout=10)
        p = launch()
        g3 = wait(lambda: rd(geom).get("window") and rd(geom), 15)
        check("5 重開 → 開在拖動後的位置", g3 and abs(g3["window"][0] - g2["window"][0]) <= 4 and abs(g3["window"][1] - g2["window"][1]) <= 4,
              ((g2 or {}).get("window"), (g3 or {}).get("window")))
        click(*g3["home"])
        g4 = wait(lambda: abs(rd(geom).get("window", [0])[0] - 100) <= 2 and rd(geom), 5)
        check("5 歸位 → 回到預設位置、清掉記住的位置", g4 and not os.path.exists(os.path.join(home, "player.json")), (g4 or {}).get("window"))
    finally:
        try:
            p.kill()
        except Exception:
            pass
        shutil.rmtree(td, ignore_errors=True)
    print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (len(R) - sum(R), len(R)))
    sys.exit(0 if all(R) else 1)


if __name__ == "__main__":
    main()
