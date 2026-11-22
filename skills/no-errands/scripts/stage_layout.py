#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示範舞台的版面（純函式、純 stdlib；引擎、舞台、驗證、測試共用）。

一切由當下螢幕尺寸算出來（1920×1080、1366×768、1280×720、1024×768 都要成立）：
  角色帶（頂端）：現在是誰＋很短的段落名稱；同時蓋住被移到舞台上方的瀏覽器殼與頁面頂端裁掉的部分。
  舞台（中間）：只顯示正在動作的那個角色的頁面。
  字幕帶（底部）：全寬、一拍一句、最多兩行；右端是主持人的小控制。
字幕放不下兩行是「劇本驗證的錯誤」，不是上場才發現。
"""
import math

RESOLUTIONS = [(1920, 1080), (1366, 768), (1280, 720), (1024, 768), (1920, 1200)]
MAX_CAPTION_LINES = 2


def layout(W, H):
    """回各區塊的矩形 (x, y, w, h) 與字級（像素）。"""
    band_h = max(40, round(H * 0.06))
    cap_h = max(110, round(H * 0.15))
    pad = max(16, round(W * 0.017))
    ctl_w = max(200, round(W * 0.17))                    # 右端控制：下一步、暫停、⋯、句數小圓點
    cap_font = max(24, round(H * 0.0426))                # 1080p：46 px
    return {
        "screen": (W, H),
        "band": (0, 0, W, band_h),
        "stage": (0, band_h, W, H - band_h - cap_h),
        "caption": (0, H - cap_h, W, cap_h),
        "caption_text": (pad, H - cap_h, W - 2 * pad - ctl_w, cap_h),
        "controls": (W - pad - ctl_w, H - cap_h, ctl_w, cap_h),
        "cap_font": cap_font,
        "cap_line_h": round(cap_font * 1.3),
        "band_font": max(16, round(band_h * 0.44)),
        "ctl_font": max(12, round(H * 0.0167)),
        "pad": pad,
    }


def page_zoom(H, dpr, target_1080=1.5):
    """頁面縮放（只在啟動瀏覽器時設，示範中途不變）。用結果定義：有效倍率＝系統縮放（dpr）×頁面縮放，
    目標是在 1080 高的螢幕上約 1.5，依螢幕高度等比。系統縮放已經夠（差不到 12%）就維持 100%，不另外管理狀態。
    例：1920×1200、150% 縮放 → 目標 1.67、系統 1.5 → 1.0；投影機 1920×1080、100% → 1.5。"""
    want = target_1080 * H / 1080.0
    z = want / max(dpr, 0.5)
    return 1.0 if z <= 1.12 else round(z, 2)


def char_w(c, font):
    """估算單一字元寬度：全形（中日韓、全形標點）＝字級；半形約 0.56。"""
    o = ord(c)
    if o >= 0x2E80 or 0xFF00 <= o <= 0xFFEF or 0x3000 <= o <= 0x303F:
        return font
    if c == " ":
        return font * 0.3
    return font * 0.56


def caption_lines(text, W, H):
    """這句字幕在 W×H 上會排成幾行（逐字斷行，與舞台的 Label 一致）。"""
    lay = layout(W, H)
    avail = lay["caption_text"][2] * 0.94               # 安全邊際：Label 內距、字型實際字寬略大於字級
    font = lay["cap_font"]
    lines, cur = 1, 0.0
    for c in str(text):
        if c == "\n":
            lines, cur = lines + 1, 0.0
            continue
        w = char_w(c, font)
        if cur + w > avail:
            lines, cur = lines + 1, w
        else:
            cur += w
    return lines


def caption_errors(beats_by_step):
    """{步驟 id: [字幕, …]} → 四種解析度下超過兩行的錯誤清單。"""
    errs = []
    for sid, texts in beats_by_step.items():
        for i, t in enumerate(texts, 1):
            for W, H in RESOLUTIONS:
                n = caption_lines(t, W, H)
                if n > MAX_CAPTION_LINES:
                    errs.append("步驟 %s 第 %d 句在 %d×%d 會排成 %d 行（上限 %d）：%s" % (sid, i, W, H, n, MAX_CAPTION_LINES, t))
                    break
    return errs


def browser_rect(stage, shell, crop, zoom, dpr):
    """瀏覽器視窗要擺在哪裡（實體像素），才會讓「裁切後的頁面」剛好填滿舞台。
    stage：舞台矩形；shell：(left, top, right, bottom) 殼與邊框寬度（實體像素，由實際視窗量得）；
    crop：{"top": css, "bottom": css, "left": css, "right": css} 要藏到舞台外的頁面部分；zoom×dpr＝CSS→實體。"""
    sx, sy, sw, sh = stage
    k = zoom * dpr
    ct, cb = crop.get("top", 0) * k, crop.get("bottom", 0) * k
    cl, cr = crop.get("left", 0) * k, crop.get("right", 0) * k
    bl, bt, br, bb = shell
    x = sx - cl - bl
    y = sy - ct - bt
    w = sw + cl + cr + bl + br
    h = sh + ct + cb + bt + bb
    return tuple(int(math.floor(v)) for v in (x, y)) + tuple(int(math.ceil(v)) for v in (w, h))


def css_to_screen(box, stage, crop, zoom, dpr):
    """頁面上的 CSS 方框（相對於頁面可視區左上角）→ 舞台上的實體像素方框；整個不在舞台內回 None。"""
    x, y, w, h = box
    k = zoom * dpr
    sx, sy, sw, sh = stage
    X = sx + (x - crop.get("left", 0)) * k
    Y = sy + (y - crop.get("top", 0)) * k
    Wp, Hp = w * k, h * k
    if X + Wp <= sx or Y + Hp <= sy or X >= sx + sw or Y >= sy + sh:
        return None
    # 只畫在舞台內的部分
    x0, y0 = max(X, sx), max(Y, sy)
    x1, y1 = min(X + Wp, sx + sw), min(Y + Hp, sy + sh)
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0)


def overlaps(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah
