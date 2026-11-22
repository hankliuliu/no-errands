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
    """回各區塊的矩形 (x, y, w, h) 與字級（像素）。Sprint F-2：沒有固定字幕帶——頁面用到角色帶以下的整個高度；
    字幕是浮在舞台上的小方塊（大小跟著文字），控制列是右下角一小塊。"""
    band_h = max(40, round(H * 0.06))
    pad = max(16, round(W * 0.017))
    cap_font = max(18, round(H * 0.025))                 # 1200 高→30、1080→27
    stage = (0, band_h, W, H - band_h)
    ctl_w, ctl_h = max(260, round(W * 0.2)), max(44, round(H * 0.05))
    return {
        "screen": (W, H),
        "band": (0, 0, W, band_h),
        "stage": stage,
        "controls": (W - pad - ctl_w, H - pad - ctl_h, ctl_w, ctl_h),
        "cap_font": cap_font,
        "cap_line_h": round(cap_font * 1.35),
        "cap_pad": max(8, round(cap_font * 0.45)),
        "cap_max_w": round(W * 0.62),
        "cap_bottom": band_h + round((H - band_h) * 0.93),
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
    avail = (lay["cap_max_w"] - 2 * lay["cap_pad"]) * 0.94   # 字幕塊最大寬度扣內距；安全邊際
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


def caption_pos(box_wh, lay, look=None, ctl=None, user=None, margin=12):
    """字幕塊的左上角。user＝(rx, ry) 舞台比例（使用者拖過的位置；拖過就不自動讓開）；
    否則預設下方置中，與「看這裡」外框重疊（含邊距）→ 改到上方；與控制列左右重疊 → 放在控制列上方。"""
    bw, bh = box_wh
    sx, sy, sw, sh = lay["stage"]
    if user:
        x = sx + user[0] * sw
        y = sy + user[1] * sh
    else:
        x = sx + (sw - bw) / 2
        y = lay["cap_bottom"] - bh
        box = (x - margin, y - margin, bw + 2 * margin, bh + 2 * margin)
        if look and overlaps(box, look):
            y = sy + margin                               # 讓開外框：放到上方（角色帶下面）
        elif ctl and overlaps((x, y, bw, bh), ctl):
            y = ctl[1] - bh - margin                      # 和控制列重疊：放在控制列上方
    x = min(max(x, sx), sx + sw - bw)                     # 拖不出舞台
    y = min(max(y, sy), sy + sh - bh)
    return int(x), int(y)


def caption_ratio(pos, lay):
    """左上角（像素）→ 舞台比例（存到本機，下次沿用；解析度不同也放在相同的相對位置）。"""
    sx, sy, sw, sh = lay["stage"]
    return round((pos[0] - sx) / sw, 4), round((pos[1] - sy) / sh, 4)


def controls_state(presenter, status, paused, slow, moved_ago, fade_s=2.5):
    """控制列要不要顯示、亮哪顆、旁邊寫什麼（像影片播放器）。
    自動播放：滑鼠動過 fade_s 秒內才顯示；講者模式輪到他按：常駐、亮「下一步」；暫停：常駐；等待超時／失敗：出現並亮備案。"""
    waiting = status in ("waiting_next", "ready", "failed")
    if paused:
        return {"visible": True, "hint": "已暫停", "next_hot": False, "planb": bool(slow or status == "failed")}
    if slow or status == "failed":
        return {"visible": True, "hint": "這一步比平常久" if slow else "這一步沒有完成", "next_hot": waiting, "planb": True}
    if presenter and waiting:
        return {"visible": True, "hint": "輪到你：按下一步", "next_hot": True, "planb": False}
    return {"visible": moved_ago is not None and moved_ago < fade_s, "hint": "", "next_hot": False, "planb": False}


def overlaps(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah
