#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示範舞台的視窗測試（Windows；會開全螢幕視窗——只在 CI 跑，要 --i-am-ci）。

五種解析度（1920×1080、1366×768、1280×720、1024×768、1920×1200）各開一次舞台：
  1 字幕帶裡兩行長的字幕實際排出來的高度 ≤ 字幕帶高度（不被截斷）
  2 字幕文字就是 now.json 的這一拍；「下一步」按鈕在字幕帶右端
  3 now.json 寫 status=closing → 舞台自己收掉（不留視窗）
另外：預備清單卡（checklist）時講者按「停止」→ 舞台收掉。
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


def watchdog(limit=180):
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


def main():
    if os.name != "nt" or "--i-am-ci" not in sys.argv:
        print("SKIP 舞台視窗測試：只在 Windows CI 跑（要 --i-am-ci）")
        return 0
    wd = watchdog()
    two_lines = "它只根據院內說明來寫，依據列在下面；核准的人不能確認寄出，所以每一封回覆至少經過兩個人。"
    for W, H in L.RESOLUTIONS:
        d = tempfile.mkdtemp(prefix="ne-stage-")
        geom = os.path.join(d, "geom.json")
        json.dump({"status": "running", "card": None, "role": "長官", "role_color": "#c2410c", "section": "第一道關卡",
                   "step_index": 8, "steps_total": 13, "beat": {"text": two_lines, "i": 2, "n": 3}, "accounts": {}},
                  open(os.path.join(d, "now.json"), "w", encoding="utf-8"), ensure_ascii=False)
        env = dict(os.environ, NE_STAGE_SCREEN="%dx%d" % (W, H), NE_STAGE_GEOM=geom, NE_STAGE_NO_HOTKEYS="1")
        p = spawn(d, env)
        g = wait(lambda: rd(geom).get("caption_text") and rd(geom).get("next", [0, 0]) != [0, 0] and rd(geom))
        lines = L.caption_lines(two_lines, W, H)
        check("%d×%d：字幕估算兩行以內（%d 行）" % (W, H, lines), lines <= 2, lines)
        check("%d×%d：字幕實際排出來的高度放得進字幕帶（不截斷）" % (W, H),
              g and g["caption_req_h"] <= g["caption_h"], g)
        check("%d×%d：字幕就是這一拍的句子" % (W, H), g and g["caption_text"] == two_lines, g and g.get("caption_text"))
        check("%d×%d：「下一步」在字幕帶的右半部" % (W, H), g and g["next"][0] > W * 0.6 and g["next"][1] > H - L.layout(W, H)["caption"][3],
              g and g.get("next"))
        json.dump({"status": "closing"}, open(os.path.join(d, "now.json"), "w"))
        try:
            p.wait(10)
            closed = True
        except subprocess.TimeoutExpired:
            kill_tree(p); closed = False
        check("%d×%d：status=closing → 舞台自己收掉" % (W, H), closed)
        shutil.rmtree(d, ignore_errors=True)
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
