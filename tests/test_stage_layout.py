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
    b, s, c = lay["band"], lay["stage"], lay["caption"]
    check("%d×%d：角色帶／舞台／字幕帶互不重疊" % (W, H), not L.overlaps(b, s) and not L.overlaps(s, c) and not L.overlaps(b, c), lay)
    check("%d×%d：三塊剛好鋪滿整個螢幕（高度加總＝螢幕高、全寬）" % (W, H),
          b[3] + s[3] + c[3] == H and b[2] == s[2] == c[2] == W and s[1] == b[3] and c[1] == s[1] + s[3], lay)
    ct, ctl = lay["caption_text"], lay["controls"]
    check("%d×%d：字幕文字區與控制區不重疊、都在字幕帶內" % (W, H),
          not L.overlaps(ct, ctl) and ct[0] >= 0 and ctl[0] + ctl[2] <= W, (ct, ctl))
    check("%d×%d：兩行字幕放得進字幕帶" % (W, H), 2 * lay["cap_line_h"] <= c[3], (lay["cap_line_h"], c[3]))
    check("%d×%d：舞台至少佔螢幕高度的 75%%" % (W, H), s[3] >= 0.75 * H, s)

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

print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
