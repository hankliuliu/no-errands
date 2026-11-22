#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示範舞台版面（純 stdlib、無畫面；三平台 CI）：四種解析度下
  - 角色帶、舞台、字幕帶互不重疊、剛好鋪滿整個螢幕；
  - 字幕估算：短句一行、長句兩行、太長的句子被驗證抓出來；
  - 瀏覽器視窗位置：裁切後的頁面剛好填滿舞台；頁面方框換算到舞台、在舞台外不畫。
用法：python tests/test_stage_layout.py
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "no-errands", "scripts"))
import stage_layout as L  # noqa: E402

R = []


def check(n, c, d=""):
    R.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else "  -- " + str(d)[:300]))


for W, H in L.RESOLUTIONS:
    lay = L.layout(W, H)
    b, s_, c = lay["band"], lay["stage"], lay["controls"]
    check("%d×%d：角色帶和舞台不重疊、合起來鋪滿整個螢幕（沒有固定字幕帶）" % (W, H),
          not L.overlaps(b, s_) and b[3] + s_[3] == H and s_[1] == b[3] and b[2] == s_[2] == W, lay)
    check("%d×%d：控制列在舞台右下角、在螢幕內" % (W, H),
          c[0] + c[2] <= W and c[1] + c[3] <= H and c[0] > W / 2 and c[1] > H / 2, c)
    check("%d×%d：字級＝螢幕高 2.5%%（下限 18）" % (W, H), lay["cap_font"] == max(18, round(H * 0.025)), lay["cap_font"])
    check("%d×%d：字幕最大寬度＝舞台寬 62%%" % (W, H), lay["cap_max_w"] == round(W * 0.62), lay["cap_max_w"])
    bw, bh = 600, 2 * lay["cap_line_h"] + 2 * lay["cap_pad"]
    x, y = L.caption_pos((bw, bh), lay)
    check("%d×%d：預設下方置中（底邊在舞台高 93%%）" % (W, H), abs((x + bw / 2) - W / 2) <= 1 and abs((y + bh) - lay["cap_bottom"]) <= 1, (x, y))

lay = L.layout(1920, 1200)
bw, bh = 700, 90
dx, dy = L.caption_pos((bw, bh), lay)
look = (dx + 100, dy + 10, 300, 40)
ux, uy = L.caption_pos((bw, bh), lay, look=look)
check("字幕和「看這裡」外框重疊 → 自動讓到上方（角色帶下面）", uy < lay["stage"][1] + 40 and uy < dy, (ux, uy))
far = (10, lay["stage"][1] + 10, 100, 30)
check("外框不重疊 → 留在預設位置", L.caption_pos((bw, bh), lay, look=far) == (dx, dy))
ctl = lay["controls"]
wide = (1500, bh)
cx, cy = L.caption_pos(wide, lay, ctl=ctl)
check("和控制列左右重疊 → 放在控制列上方", cy + bh <= ctl[1], (cy, ctl))
user = (0.1, 0.2)
check("拖過（使用者位置）→ 用使用者的位置，不再自動讓開",
      L.caption_pos((bw, bh), lay, look=look, user=user) == (int(lay["stage"][0] + 0.1 * lay["stage"][2]), int(lay["stage"][1] + 0.2 * lay["stage"][3])))
check("拖不出舞台範圍", L.caption_pos((bw, bh), lay, user=(1.5, -0.5)) == (lay["stage"][2] - bw, lay["stage"][1]))
r = L.caption_ratio((192, 72 + 112), lay)
check("位置以舞台比例記住（換解析度也在相同的相對位置）", r == (0.1, round(112 / lay["stage"][3], 4)), r)
st = L.controls_state(False, "running", False, False, None)
check("控制列：自動播放、滑鼠沒動 → 隱藏", st["visible"] is False, st)
check("控制列：自動播放、滑鼠剛動過 → 出現，2.5 秒後隱藏",
      L.controls_state(False, "running", False, False, 1.0)["visible"] and not L.controls_state(False, "running", False, False, 3.0)["visible"])
st = L.controls_state(True, "waiting_next", False, False, None)
check("控制列：講者模式輪到他按 → 常駐、亮下一步、寫「輪到你」", st["visible"] and st["next_hot"] and "輪到你" in st["hint"], st)
st = L.controls_state(False, "running", True, False, None)
check("控制列：暫停 → 常駐、寫已暫停", st["visible"] and st["hint"] == "已暫停", st)
st = L.controls_state(False, "waiting_system", False, True, None)
check("控制列：等待超時 → 自己出現並亮備案", st["visible"] and st["planb"], st)

check("這台（1920×1200、150%%）：系統縮放已經夠 → 頁面維持 100%%", L.page_zoom(1200, 1.5) == 1.0, L.page_zoom(1200, 1.5))
check("投影機 1920×1080、100%% → 頁面縮放 1.5", L.page_zoom(1080, 1.0) == 1.5, L.page_zoom(1080, 1.0))
check("1366×768、100%% → 依高度等比（約 1.07 → 不到 12%% 就維持 100%%）", L.page_zoom(768, 1.0) == 1.0, L.page_zoom(768, 1.0))
check("1920×1080、125%% → 補到 1.2", L.page_zoom(1080, 1.25) == 1.2, L.page_zoom(1080, 1.25))
stage1200 = L.layout(1920, 1200)["stage"]
box = L.css_to_screen((100, 100, 200, 20), stage1200, {}, 1.0, 1.5)
check("150%% 縮放、頁面 100%%：CSS→實體 ×1.5", box == (150, stage1200[1] + 150, 300, 30), box)

short = "案件建好了，系統回了一張案件卡片。"
check("短句在四種解析度都是一行", all(L.caption_lines(short, W, H) == 1 for W, H in L.RESOLUTIONS),
      [L.caption_lines(short, W, H) for W, H in L.RESOLUTIONS])
mid = "它只根據院內提供的說明來寫回覆，引用的依據逐字核對過，列在草稿下面。"
check("中等長度的句子最多兩行", all(L.caption_lines(mid, W, H) <= 2 for W, H in L.RESOLUTIONS),
      [L.caption_lines(mid, W, H) for W, H in L.RESOLUTIONS])
long_ = "這一句故意寫得非常非常長，" * 6
errs = L.caption_errors({"s05": [short, long_]})
check("太長的句子 → 驗證錯誤（指出步驟、第幾句、哪個解析度）", len(errs) == 1 and "s05" in errs[0] and "第 2 句" in errs[0], errs)

stage = L.layout(1920, 1080)["stage"]
shell = (8, 130, 8, 8)
crop = {"top": 84, "bottom": 127, "left": 264}
x, y, w, h = L.browser_rect(stage, shell, crop, 1.5, 1.0)
k = 1.5
content_x, content_y = x + shell[0], y + shell[1]
check("瀏覽器視窗：頁面可視區左上角＝舞台左上角往外推裁掉的量",
      abs(content_x - (stage[0] - 264 * k)) <= 1 and abs(content_y - (stage[1] - 84 * k)) <= 1, (x, y))
check("瀏覽器視窗：裁切後剛好填滿舞台（寬高）",
      abs((w - shell[0] - shell[2]) - (stage[2] + 264 * k)) <= 1 and abs((h - shell[1] - shell[3]) - (stage[3] + (84 + 127) * k)) <= 1, (w, h))
box = L.css_to_screen((300, 200, 400, 50), stage, crop, 1.5, 1.0)
check("頁面方框 → 舞台座標（扣掉裁切、乘倍率）", box == (int((300 - 264) * 1.5), int(stage[1] + (200 - 84) * 1.5), 600, 75), box)
check("被裁掉（在舞台外）的方框不畫", L.css_to_screen((0, 0, 200, 40), stage, crop, 1.5, 1.0) is None)
part = L.css_to_screen((300, 60, 400, 50), stage, crop, 1.5, 1.0)
check("一半在舞台外 → 只畫舞台內的部分", part and part[1] == stage[1] and part[3] < 75, part)

import demo_stage as DS  # noqa: E402
q = r'"C:\x\chrome.exe" "--remote-debugging-port=0" "--user-data-dir=C:\Users\u\p\demo_a" "--window-size=1280,720"'
check("認得自己的瀏覽器：參數整個包在引號裡的 --user-data-dir（agent-browser 的啟動方式）",
      DS.profile_of_cmdline(q) == r"C:\Users\u\p\demo_a", DS.profile_of_cmdline(q))
q2 = r"chrome.exe --user-data-dir=C:\p\b --x=1"
check("認得自己的瀏覽器：沒有引號的 --user-data-dir", DS.profile_of_cmdline(q2) == r"C:\p\b", DS.profile_of_cmdline(q2))
check("沒有 --user-data-dir → 不認", DS.profile_of_cmdline("chrome.exe --type=renderer") is None)

print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
