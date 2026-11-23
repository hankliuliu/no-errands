#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示範舞台版面（純 stdlib、無畫面；三平台 CI）：四種解析度下
  - 角色帶、舞台、字幕帶互不重疊、剛好鋪滿整個螢幕；
  - 字幕估算：短句一行、長句兩行、太長的句子被驗證抓出來；
  - 瀏覽器視窗位置：裁切後的頁面剛好填滿舞台；頁面方框換算到舞台、在舞台外不畫。
用法：python tests/test_stage_layout.py
"""
import os, time, sys

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
check("字幕和「看這裡」外框重疊 → 讓到離原位最近、不重疊的位置（這裡是右下）",
      not L.overlaps((ux - 12, uy - 12, bw + 24, bh + 24), look) and uy == dy and ux > dx, (ux, uy))
tall = (300, lay["stage"][1] + 40, 920, lay["stage"][3] - 60)            # 表單：外框幾乎佔滿整個高度
tx_, ty_ = L.caption_pos((600, bh), lay, look=tall)
check("外框幾乎佔滿整個高度 → 字幕放到旁邊的空位（不壓外框）", not L.overlaps((tx_, ty_, 600, bh), tall), (tx_, ty_))
check("外框不重疊時字幕不動（優先穩定）", L.caption_pos((bw, bh), lay, look=(10, lay["stage"][1] + 10, 100, 30)) == (dx, dy))
far = (10, lay["stage"][1] + 10, 100, 30)
check("外框不重疊 → 留在預設位置", L.caption_pos((bw, bh), lay, look=far) == (dx, dy))
ctl = lay["controls"]
wide = (1500, bh)
cx, cy = L.caption_pos(wide, lay)
check("字幕不因控制列移動（caption_pos 不看控制列）", (cx, cy) == L.caption_pos(wide, lay, look=None), (cx, cy))
cp_ = L.controls_pos(lay, (cx, cy, wide[0], wide[1]))
hint_h = lay["ctl_font"] * 2
check("控制列和字幕重疊 → 控制列（連同提示列）讓到字幕上方，字幕不動",
      cp_[1] + cp_[3] <= cy - 12 and not L.overlaps((cp_[0], cp_[1] - hint_h, cp_[2], cp_[3] + hint_h), (cx, cy, wide[0], wide[1])), (cp_, cy))
check("控制列和字幕不重疊 → 留在右下角", L.controls_pos(lay, (10, cy, 200, bh)) == tuple(ctl))
check("沒有字幕 → 控制列在右下角", L.controls_pos(lay, None) == tuple(ctl))
user = (0.1, 0.2)
check("拖過（使用者位置）→ 用使用者的位置，不再自動讓開",
      L.caption_pos((bw, bh), lay, look=look, user=user) == (int(lay["stage"][0] + 0.1 * lay["stage"][2]), int(lay["stage"][1] + 0.2 * lay["stage"][3])))
check("拖不出舞台範圍", L.caption_pos((bw, bh), lay, user=(1.5, -0.5)) == (lay["stage"][2] - bw, lay["stage"][1]))
r = L.caption_ratio((192, lay["stage"][1] + 112), lay)
check("位置以舞台比例記住（換解析度也在相同的相對位置）", r == (0.1, round(112 / lay["stage"][3], 4)), r)
for W_, H_ in ((1920, 1200), (1920, 1080), (1280, 720)):
    lg = L.layout(W_, H_)
    check("Sprint G：沒有角色帶——頁面範圍＝整個螢幕（%d×%d）" % (W_, H_), tuple(lg["stage"]) == (0, 0, W_, H_), lg["stage"])
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

# ---- A1/A19：頁面範圍實測（1920×1080、角色帶 65）
lay = L.layout(1920, 1080)
stg = lay["stage"]
good = (-404, stg[1] - 126, 1920 + 404, 1080 - stg[1] + 126)
r = L.fit_report((1920, 1080), stg, good)
check("A1 頁面底邊貼齊螢幕底邊、四邊沒有空白帶 → 合格（頂端與左邊伸出舞台是刻意裁切）", r["ok"], r)
short = (-11, -191 + 87, 1920, 915 - (-191 + 87))           # 00:54 那次：視窗被系統截到螢幕大小，底邊停在 915
r = L.fit_report((1920, 1080), stg, short)
check("A1 00:54 那次的實測值（底邊停在 915）→ 不合格，指出底邊差距與下緣空白帶",
      not r["ok"] and r["bottom_diff"] == -165 and any("下緣" in w for w in r["why"]), r)
r = L.fit_report((1920, 1080), stg, (0, stg[1] - 126, 1900, 1080 - stg[1] + 126))
check("A1 右緣露出 20 像素 → 不合格", not r["ok"] and any("右緣" in w for w in r["why"]), r)
check("A1 量不到頁面 → 不合格", not L.fit_report((1920, 1080), stg, None)["ok"])
check("A1 3 像素以內算貼齊", L.fit_report((1920, 1080), stg, (0, stg[1], 1920, 1080 - stg[1] + 2))["ok"])
check("A19 要求與實測的差距", L.placement_error((-11, -191, 2400, 1400), (-11, -191, 1941, 1106)) == 459 and
      L.placement_error((1, 2, 3, 4), (1, 2, 3, 4)) == 0)
check("字幕塊完全落在頁面內／超出頁面", L.inside((10, 900, 300, 60), good) and not L.inside((10, 1050, 300, 60), good))
# ---- A9：訊息區頂端不留半行
lst = (269, 141, 984, 354)
check("A9 沒有被切到的訊息 → 已對齊", L.align_plan(lst, None, (269, 397, 984, 90)) == ("ok", 0))
p = L.align_plan(lst, (269, 120, 984, 200), (269, 330, 984, 100))
check("A9 被切 21 px、往上捲之後目標仍完整 → 往上捲 21", p == ("scroll", 21), p)
p = L.align_plan(lst, (269, -14.4, 984, 411.4), (269, 397, 984, 90.8))
check("A9 被切 155 px、往上捲會把最後一則擠出去 → 用底色蓋住殘段（到被切那則的底邊）",
      p[0] == "mask" and p[1][1] == 141 and abs(p[1][1] + p[1][3] - 397) <= 0.1, p)
p = L.align_plan(lst, (269, 0, 984, 600), (269, 300, 984, 80))
check("A9 目標在被切的那則裡面 → 只蓋到目標上方 4 px", p[0] == "mask" and abs(p[1][1] + p[1][3] - 296) <= 0.1, p)
p = L.align_plan(lst, (269, 0, 984, 600), (269, 140, 984, 400))
check("A9 目標自己跨在頂端 → 做不到（照實記錄）", p[0] == "none", p)

# ---- A9：Look 的對齊流程（假的瀏覽器：只回 get box／get count／scroll）
import demo_runbook as DR  # noqa: E402


class FakeBr:
    def __init__(self, boxes, modal=0):
        self.boxes, self.modal, self.calls, self.dy = boxes, modal, [], 0

    def neb(self, acct, *a):
        self.calls.append(a)
        if a[:2] == ("get", "count"):
            return 0, "%d\n" % self.modal, ""
        if a[:2] == ("get", "styles"):
            return 0, "--center-channel-bg: #111827\n", ""
        if a[0] == "scroll":
            self.dy += int(a[2]) if a[1] == "up" else -int(a[2])
            return 0, "✓ Done", ""
        if a[:2] == ("get", "box"):
            b = self.boxes.get(a[2])
            if not b:
                return 1, "", "no"
            y = b[1] + (self.dy if a[2] != "#post-list" else 0)
            return 0, "x: %s\ny: %s\nwidth: %s\nheight: %s\n" % (b[0], y, b[2], b[3]), ""
        return 0, "", ""


class FakeCtl:
    def __init__(self):
        self.now = {}

    def publish(self, **kw):
        self.now.update(kw)


AL = {"list": "#post-list", "scroller": ".s", "item_nth": "#p :nth-last-child({k} of [role=\"listitem\"])",
      "skip_if": ".modal-content", "color_var": "--center-channel-bg"}
nth = lambda k: AL["item_nth"].format(k=k)
boxes = {"#post-list": [269, 141, 984, 354], nth(1): [269, 397, 984, 90.8], nth(2): [269, -14.4, 984, 411.4], "#t": [269, 397, 984, 90.8]}
br1 = FakeBr(boxes); c = FakeCtl(); lk = DR.Look(br1, c, align=dict(AL, top_align=True)); lk.step("a", True); lk.set("a", "#t")
check("A9 不對齊、不遮罩（5c）：劇本就算寫了 top_align，讀內容的拍子也只取目標位置——不逐則查訊息、不往上捲、不發布遮罩",
      c.now.get("mask") is None and c.now.get("align") is None and not any(x[0] == "scroll" for x in br1.calls)
      and not any(x[:2] == ("get", "count") for x in br1.calls) and not any(x[:2] == ("get", "box") and "nth-last-child" in x[2] for x in br1.calls) and len(br1.calls) <= 3
      and c.now["look"]["box"] == [269.0, 397.0, 984.0, 90.8], (c.now, br1.calls))

# ---- 等待中的字幕（5c）：真的在等才講、等到就停；超時說一句就往下（不停掉整場）
class WaitBr:
    def __init__(self, busy_reads):
        self.n, self.busy = 0, busy_reads

    def text(self, acct, sc):
        self.n += 1
        return "🤔 處理中..." if self.n <= self.busy else "這是回答"

    def return_to(self, acct):
        return False


class WaitCtl(FakeCtl):
    def __init__(self):
        super().__init__(); self.beats = []

    def publish(self, **kw):
        super().publish(**kw)
        if kw.get("beat"):
            self.beats.append(kw["beat"]["text"])

    def gate(self, on_resume=None):
        return 0.0


sw = [{"say": "第一句", "s": 0.15}, {"say": "第二句", "s": 0.15}, {"say": "第三句", "s": 5}]
act_w = {"do": "expect_absent", "text": ["處理中"], "scopes": ["#rhs"], "budget_s": 5, "poll_s": 0.05, "say_while": sw}
st_w = {"id": "sx", "account": "a"}
c = WaitCtl(); ok_, inf_ = DR.run_action(WaitBr(12), {}, st_w, act_w, {}, None, {}, c)
check("等待中字幕：依序出現、等到了就停（不會硬播完）", ok_ and c.beats[:2] == ["第一句", "第二句"] and len(inf_.get("said_while", [])) >= 2
      and inf_.get("t_end"), (ok_, c.beats, inf_))
c = WaitCtl(); ok_, inf_ = DR.run_action(WaitBr(0), {}, st_w, act_w, {}, None, {}, c)
check("等待中字幕：等的東西已經到了 → 一句都不講、直接往下", ok_ and c.beats == [], (ok_, c.beats))
act_t = dict(act_w, budget_s=0.3, say_while=None, timeout_say="AI 助理這次回得比較慢，先往下看。", timeout_say_s=0)
c = WaitCtl(); ok_, inf_ = DR.run_action(WaitBr(10 ** 6), {}, st_w, act_t, {}, None, {}, c)
check("等太久：說一句「先往下看」、記成超時略過", not ok_ and inf_.get("timeout_skipped") and c.beats == ["AI 助理這次回得比較慢，先往下看。"],
      (ok_, c.beats, inf_))

# ---- F-3：示範游標、捲動、外框淡出、不碰系統輸入
st_ = lay["stage"] if False else L.layout(1920, 1080)["stage"]
check("A21 CSS 點 → 螢幕（扣裁切、乘倍率、夾在舞台內）",
      L.css_point((369, 184), st_, {"top": 84, "left": 269}, 1.0, 1.5) == (150, st_[1] + 150) and
      L.css_point((0, 0), st_, {"top": 84, "left": 269}, 1.0, 1.5) == (st_[0], st_[1]))
pts = [L.pointer_path((100, 900), (1500, 300), i / 60.0) for i in range(61)]
steps = [((pts[i + 1][0] - pts[i][0]) ** 2 + (pts[i + 1][1] - pts[i][1]) ** 2) ** 0.5 for i in range(60)]
check("A21 示範游標的路徑連續、有加減速（頭尾慢、中間快）、終點準確",
      pts[0] == (100, 900) and abs(pts[-1][0] - 1500) < 0.01 and abs(pts[-1][1] - 300) < 0.01 and max(steps) < 60
      and steps[0] < steps[30] / 4 and steps[-1] < steps[30] / 4, (max(steps), steps[0], steps[30]))
mid = L.pointer_path((0, 0), (1000, 0), 0.5)
check("A21 路徑略帶弧度（不是直線）", abs(mid[1]) > 20, mid)
check("A25 讀內容的外框：1.2 秒內維持很淡（0.55），1.6 秒後完全淡出",
      L.look_alpha(0.5) == 0.55 and 0 < L.look_alpha(1.4) < 0.55 and L.look_alpha(1.6) == 0)


class FakeCtl2(FakeCtl):
    def gate(self, on_resume=None):
        return 0.0


boxes3 = {"#post-list": [269, 141, 984, 354], nth(1): [269, 397, 984, 90.8], "@e9": [600, 700, 200, 40], "#t": [269, 397, 984, 90.8]}
class OrderCtl(FakeCtl2):
    def __init__(self):
        super().__init__(); self.seq = []

    def publish(self, **kw):
        super().publish(**kw)
        if kw.get("pointer"):
            self.seq.append("pointer")


oc = OrderCtl(); lko = DR.Look(FakeBr(boxes3), oc, align=AL); lko.step("a", True)
lko._pre_point = lambda box: oc.seq.append("caption")
lko.aim("a", "@e9", click=True)
lko.set("a", "#t")
check("G（5c）：講動作與讀內容的拍子都是先出字幕、游標才移動", oc.seq[:4] == ["caption", "pointer", "caption", "pointer"], oc.seq)
br4 = FakeBr(boxes3); c = FakeCtl2(); lk = DR.Look(br4, c, align=AL); lk.step("a", True)
t_ = time.time(); lk.act("a", "@e9", press=True)
sc = [x for x in br4.calls if x[0] == "scroll"]
check("A24 目標在訊息區下方 → 分 3–4 段捲過去（每段不超過總量的一半）、捲到訊息區上方約四分之一",
      3 <= len(sc) <= 4 and all(x[1] == "down" for x in sc) and abs(sum(int(x[2]) for x in sc) - (700 - (141 + 0.25 * 354))) <= 2
      and max(int(x[2]) for x in sc) <= sum(int(x[2]) for x in sc) / 2, sc)
check("A21/A22 動作：游標移到元素中心、停一下、按下效果，不畫外框",
      c.now.get("pointer", {}).get("seq") == 1 and c.now.get("press") == 1 and c.now.get("look") is None
      and abs(c.now["pointer"]["x"] - 700) < 0.01 and time.time() - t_ >= 0.6 + 0.2 + 0.18 - 0.05, c.now)
c = FakeCtl2(); lk = DR.Look(FakeBr(boxes3), c, align=AL); lk.step("a", True); lk.set("a", "#t")
check("A21/A25 讀內容：淡外框（帶這一拍的編號）＋游標指在那一段開頭的左邊、不蓋住字",
      c.now["look"]["seq"] == 1 and c.now["pointer"]["x"] < 269 and c.now.get("press") is None, c.now)
import tempfile as _tf, threading as _th, json as _js  # noqa: E402
_ackd = _tf.mkdtemp()


class FakeCtl3(FakeCtl2):
    dir = _ackd


def _ack_later():
    time.sleep(0.3)
    _ack_t[0] = time.time()
    _js.dump({"press": 1, "t": _ack_t[0]}, open(os.path.join(_ackd, "stage_ack.json"), "w"))


_ack_t = [None]
c = FakeCtl3(); lk = DR.Look(FakeBr(boxes3), c, align=AL); lk.step("a", True)
lk.ptr = (700, 720)                                          # 游標已在目標上（不用移動），只看按下與點擊的先後
_th.Thread(target=_ack_later).start()
t_ = time.time(); lk.act("a", "@e9", press=True); t_done = time.time()
check("A22 點擊在「按下的效果已畫出」之後：舞台 0.3 秒後才回報 → 引擎等到回報才回來送出點擊（先後順序）",
      _ack_t[0] is not None and t_done >= _ack_t[0] and getattr(lk, "acked", 0) == 1, (_ack_t[0], t_done))
# ---- 效果開關（真頁面上哪一項不穩就單獨關掉、退回前一種做法）
fx = DR.effects_of({"stage": {}})
check("效果開關：預設全開", all(fx.values()) and set(fx) == set(DR.EFFECTS), fx)
fx = DR.effects_of({"stage": {"effects": {"pointer": False, "typing": False, "bogus": False}}})
check("效果開關：只關指定的項目；不認得的名稱忽略", fx["pointer"] is False and fx["typing"] is False and fx["shield"] and "bogus" not in fx, fx)
br5 = FakeBr(boxes3); c = FakeCtl2(); lk = DR.Look(br5, c, align=AL, effects={"pointer": False}); lk.step("a", True)
lk.act("a", "@e9", press=True)
check("示範游標關掉 → 動作前用外框框住目標（前一種做法），不發布游標與按下",
      c.now.get("look") and c.now.get("pointer") is None and c.now.get("press") is None, c.now)
br6 = FakeBr(boxes3); c = FakeCtl2(); lk = DR.Look(br6, c, align=AL, effects={"smooth_scroll": False}); lk.step("a", True)
lk.act("a", "@e9", press=False)
check("平順捲動關掉 → 瞬間捲到（scrollintoview），沒有分段捲動",
      any(x[0] == "scrollintoview" for x in br6.calls) and not any(x[0] == "scroll" for x in br6.calls), br6.calls[-6:])
st_b = {"actions": [{"do": "open"}, {"do": "click", "target": {"role": "button", "names": ["下修改指示"]}}, {"do": "wait_text"}]}
check("每一拍都有自己的目標：look 沒寫 → 用這一拍接著要做的動作的目標",
      DR.beat_target(st_b, {"at": 1}) == {"target": {"role": "button", "names": ["下修改指示"]}, "action": "click"})
c = FakeCtl2(); lk = DR.Look(FakeBr(boxes3), c, align=AL); lk.step("a", True)
lk.aim("a", "@e9", click=True)
check("講動作的拍子：游標指到目標中心（不框、不按）", abs(c.now["pointer"]["x"] - 700) < 0.01 and c.now.get("look") is None
      and c.now.get("press") is None, c.now)
check("look 沒寫、接著的動作不是點擊或輸入 → 沒有目標；寫了 False → 不框；寫了 CSS → 照寫的",
      DR.beat_target(st_b, {"at": 2}) is None and DR.beat_target(st_b, {"at": 1, "look": False}) is False
      and DR.beat_target(st_b, {"at": 1, "look": {"css": "#x"}}) == {"css": "#x"})
check("讀內容的拍子：游標在那一段開頭的左邊 18 px", L.look_pointer([300, 200, 400, 40]) == (282, 212), L.look_pointer([300, 200, 400, 40]))
check("整列從頁面最左邊開始（側欄的一列）：游標在列內左側留白，不貼螢幕邊緣",
      L.look_pointer([0, 380, 403, 48]) == (12, 392) and L.look_pointer([4, 380, 403, 48])[0] == 16, L.look_pointer([0, 380, 403, 48]))
check("很窄的目標：留白不超過寬度的四分之一", L.look_pointer([0, 10, 20, 10]) == (5.0, 15.0), L.look_pointer([0, 10, 20, 10]))
import re as _re  # noqa: E402
bad = {}
for fn in ("demo_stage.py", "demo_runbook.py", "neb.py", "stage_layout.py"):
    src = open(os.path.join(os.path.dirname(L.__file__), fn), encoding="utf-8").read()
    hit = _re.findall(r"\b(SendInput|mouse_event|keybd_event|SetCursorPos|SetPhysicalCursorPos)\b", src)
    if hit:
        bad[fn] = sorted(set(hit))
check("A26 舞台、引擎、守門的程式裡沒有任何系統層的滑鼠鍵盤輸入呼叫", not bad, bad)

import demo_stage as DS  # noqa: E402
q = r'"C:\x\chrome.exe" "--remote-debugging-port=0" "--user-data-dir=C:\Users\u\p\demo_a" "--window-size=1280,720"'
check("認得自己的瀏覽器：參數整個包在引號裡的 --user-data-dir（agent-browser 的啟動方式）",
      DS.profile_of_cmdline(q) == r"C:\Users\u\p\demo_a", DS.profile_of_cmdline(q))
q2 = r"chrome.exe --user-data-dir=C:\p\b --x=1"
check("認得自己的瀏覽器：沒有引號的 --user-data-dir", DS.profile_of_cmdline(q2) == r"C:\p\b", DS.profile_of_cmdline(q2))
check("沒有 --user-data-dir → 不認", DS.profile_of_cmdline("chrome.exe --type=renderer") is None)
if os.name == "nt":
    DS.CURSOR0.clear()
    check("A37 無人值守的「有人回來」不看系統閒置秒數：游標沒動就不算（有一條非人為輸入流時不會誤撤舞台）",
          DS.user_input_since(0) is False and DS.user_input_since(0) is False and DS.user_input_since(None) is False)
    import inspect as _insp  # noqa: E402
    check("A37 舞台程式不呼叫 GetLastInputInfo", "GetLastInputInfo(" not in _insp.getsource(DS).replace("（GetLastInputInfo）", ""))
if os.name == "nt":
    import time as _t  # noqa: E402
    _cl = DS.process_cmdline(os.getpid())
    check("原生讀行程命令列（不經 WMI）：讀得到自己的命令列", bool(_cl) and "test_stage_layout" in _cl, _cl)
    _t0 = _t.time()
    _r = DS.chrome_pids_native(os.path.join(os.environ.get("TEMP", "C:\\"), "no-such-profile-xyz"))
    check("原生找瀏覽器行程：沒有這個 profile → 空集合（不是 None），而且很快（<1 秒，不卡舞台）",
          _r == set() and _t.time() - _t0 < 1.0, (_r, round(_t.time() - _t0, 3)))

print("ALL PASS (%d)" % len(R) if all(R) else "FAILED %d/%d" % (R.count(False), len(R)))
sys.exit(0 if all(R) else 1)
