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


def css_point(pt, stage, crop, zoom, dpr):
    """頁面上的 CSS 點 → 螢幕實體像素（示範游標用）；夾在舞台內。"""
    k = zoom * dpr
    sx, sy, sw, sh = stage
    X = sx + (pt[0] - crop.get("left", 0)) * k
    Y = sy + (pt[1] - crop.get("top", 0)) * k
    return int(min(max(X, sx), sx + sw - 1)), int(min(max(Y, sy), sy + sh - 1))


def ease(t):
    """加減速（smoothstep）。"""
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def pointer_path(p0, p1, t, bend=0.12):
    """示範游標在 t∈[0,1] 的位置：二次貝茲曲線（略帶弧度），加減速。"""
    e = ease(t)
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2 - dy * bend, (y0 + y1) / 2 + dx * bend
    x = (1 - e) ** 2 * x0 + 2 * (1 - e) * e * cx + e * e * x1
    y = (1 - e) ** 2 * y0 + 2 * (1 - e) * e * cy + e * e * y1
    return x, y


def look_alpha(age, peak=0.55, hold=1.2, fade=0.4):
    """讀內容的淡外框：出現後 hold 秒維持 peak 不透明度，再 fade 秒淡到 0。"""
    if age <= hold:
        return peak
    return max(0.0, peak * (1 - (age - hold) / fade))


def caption_pos(box_wh, lay, look=None, user=None, margin=12):
    """字幕塊的左上角。user＝(rx, ry) 舞台比例（使用者拖過的位置；拖過就不自動讓開）；
    否則預設下方置中，與「看這裡」外框重疊（含邊距）→ 改到上方。
    控制列不會讓字幕移動（5c：滑鼠一動字幕就跳，要拖的人在追一個會動的目標）——重疊時由控制列讓位（controls_pos）。"""
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
    x = min(max(x, sx), sx + sw - bw)                     # 拖不出舞台
    y = min(max(y, sy), sy + sh - bh)
    return int(x), int(y)


def controls_pos(lay, cap=None, margin=12):
    """控制列（含上方一行提示）的位置：預設右下角；和字幕塊重疊（含邊距）時往上移到字幕上方——字幕留在原地。"""
    x, y, w, h = lay["controls"]
    hint_h = lay["ctl_font"] * 2
    if cap:
        grown = (cap[0] - margin, cap[1] - margin, cap[2] + 2 * margin, cap[3] + 2 * margin)
        if overlaps((x, y - hint_h, w, h + hint_h), grown):
            y = max(lay["stage"][1] + hint_h, cap[1] - margin - h)
    return x, y, w, h


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


def inside(a, b, tol=0):
    """矩形 a 完全落在 b 內（容許 tol 像素）。"""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax >= bx - tol and ay >= by - tol and ax + aw <= bx + bw + tol and ay + ah <= by + bh + tol


def fit_report(screen, stage, content, tol=3):
    """台上瀏覽器頁面的實測範圍（實體像素）對舞台：頁面底邊要貼齊螢幕底邊；舞台的四邊都不能露出背板（空白帶）。
    頁面超出舞台（頂端被角色帶蓋住、左邊伸到螢幕外）是刻意的裁切，不算空白。"""
    W, H = screen
    sx, sy, sw, sh = stage
    if not content:
        return {"ok": False, "why": ["量不到頁面範圍"], "gaps": None, "bottom_diff": None}
    cx, cy, cw, ch = content
    gaps = {"上": cy - sy, "左": cx - sx, "右": (sx + sw) - (cx + cw), "下": (sy + sh) - (cy + ch)}
    why = []
    bottom_diff = (cy + ch) - H
    if abs(bottom_diff) > tol:
        why.append("頁面底邊和螢幕底邊差 %d 像素" % bottom_diff)
    for k, v in gaps.items():
        if v > tol:
            why.append("舞台%s緣露出 %d 像素的空白帶" % (k, v))
    return {"ok": not why, "why": why, "gaps": gaps, "bottom_diff": bottom_diff}


def placement_error(want, got):
    """要求的視窗矩形和實測矩形的最大差距（像素）。"""
    if not want or not got:
        return None
    return max(abs(a - b) for a, b in zip(want, got))


def align_plan(lst, straddler, target, gap=4):
    """訊息區頂端不留半行（CSS px）。lst：訊息區可視範圍；straddler：跨在頂端、被切到的那一則（None＝已對齊）；
    target：這一拍要看的東西（或最後一則）。回 (做法, 值)：
      ("ok", 0)        沒有被切到的訊息
      ("scroll", px)   往上捲 px，被切的那則從開頭顯示，而且 target 仍完整在訊息區內
      ("mask", box)    捲了會把 target 擠出去：用頁面底色蓋住被切那則露出的殘段（target 在那則裡面時只蓋到 target 上方）
      ("none", 原因)   做不到（target 自己被切到頂端）"""
    if not straddler:
        return ("ok", 0)
    lx, ly, lw, lh = lst
    top, bottom = ly, ly + lh
    sy, sh = straddler[1], straddler[3]
    c = top - sy
    if target is None:
        return ("scroll", int(math.ceil(c)))
    ty, th = target[1], target[3]
    if ty + th + c <= bottom + 1:
        return ("scroll", int(math.ceil(c)))
    within = ty >= sy - 1 and ty + th <= sy + sh + 1
    if not within:
        return ("mask", [lx, top, lw, round(sy + sh - top, 1)])
    if ty - top >= 2 * gap:
        return ("mask", [lx, top, lw, round(ty - gap - top, 1)])
    return ("none", "這一拍要看的東西本身跨在訊息區頂端")
