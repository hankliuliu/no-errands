#!/usr/bin/env python3
"""errand_audit.py — 跑腿稽核：掃 Claude Code 對話歷史，找出 agent 要求人類動手的時刻。

用法:
    python3 errand_audit.py [--root <projects-dir>] [--json out.json] [--samples N]

預設 root: ~/.claude/projects（Windows 下從 WSL 跑可用 /mnt/c/Users/<u>/.claude/projects）。
全程本機處理，不外送任何資料。輸出含原文片段，分享前請自行去識別化。
"""
import argparse, hashlib, json, os, re, sys, collections

HOME_NE = os.environ.get("NO_ERRANDS_HOME") or os.path.join(os.path.expanduser("~"), ".no-errands")


def deid(text):
    """去識別化：路徑、email、網址、Windows 使用者目錄。"""
    text = re.sub(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+", "<HOME>", text)
    text = re.sub(r"/(home|Users)/[^/\s]+", r"<HOME>", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<EMAIL>", text)
    text = re.sub(r"https?://\S+", "<URL>", text)
    return text


def proj_id(name):
    return "p-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]   # 專案目錄名含使用者名稱 → 只給雜湊

# ── 分類規則：每類一組 regex（中英並列）。順序即優先序，先中者得。 ──
CATEGORIES = [
    ("verify_screen", "驗收畫面/成果", [
        r"請(你|您)?(幫我)?(檢查|確認|驗收|看一下|看看|試試|測試)一?下?.{0,12}(畫面|頁面|網頁|結果|效果|顯示|UI|介面)",
        r"(重新整理|刷新|refresh).{0,10}(頁面|瀏覽器|page|browser)",
        r"(開|打開|open).{0,20}(瀏覽器|browser).{0,20}(看|確認|check|verify)",
        r"please\s+(check|verify|confirm|take a look|have a look|test)\b",
        r"let me know (if|whether|how)\b",
        r"(截圖|screenshot).{0,12}(給我|貼|傳|分享|share|paste|send)",
        r"(請|麻煩)(你|您)?.{0,6}(截圖|截個圖)",
        r"看看(有沒有|是否|能不能|會不會)",
        r"(跑|執行|run).{0,16}(看看|試試).{0,10}(結果|輸出)?(告訴我|回報|貼)",
    ]),
    ("gui_operation", "GUI 操作", [
        r"請(你|您)?(幫我)?手動",
        r"(你|您)?(需要|必須|得)手動",
        r"(manually|by hand)\b",
        r"請(你|您)?(幫我)?(打開|開啟|點擊|點選|按一?下|切換到|選擇|拖曳)",
        r"please\s+(open|click|select|switch to|drag|navigate to)\b",
        r"我(無法|不能|沒辦法)(直接)?(操作|點擊|打開|控制).{0,10}(GUI|視窗|介面|畫面|app|應用)",
    ]),
    ("install_setup", "安裝/設定", [
        r"請(你|您)?(先)?(幫我)?(安裝|裝|下載並安裝|更新)",
        r"please\s+(install|download and install|update)\b",
        r"(你|您)(需要|必須|得)(先)?(安裝|設定|配置)",
        r"(安裝精靈|installer|設定精靈|按.{0,6}(下一步|同意|確定)).{0,20}(請|麻煩|手動)?",
        r"(到|去|前往|open)\s*(系統)?設定.{0,16}(開啟|啟用|允許|勾選)",
        r"(需要|requires?)\s*(管理員|admin|sudo|UAC|系統管理員)",
    ]),
    ("login_credential", "登入後資訊/憑證", [
        r"請(你|您)?(幫我)?(登入|登錄|sign in|log ?in)",
        r"please\s+(log ?in|sign ?in|authenticate)\b",
        r"(提供|給我|貼上|填入).{0,12}(token|API key|金鑰|密碼|帳號|credentials?)",
        r"我(沒有|無法取得|讀不到|看不到|存取不到).{0,16}(你的|您的)?(帳號|登入|權限|token|憑證|credentials?|session)",
        r"I\s+(don'?t|do not|can'?t|cannot)\s+(have\s+)?(access|see|read)\b",
        r"(需要|requires?)\s*(登入|驗證|authentication|OAuth|授權)",
    ]),
    ("paste_relay", "請人轉貼/回報資訊", [
        r"請(你|您)?(幫我)?(貼上|貼給我|複製.{0,8}(給我|過來)|回傳|回報|告訴我)",
        r"please\s+(paste|copy|share|send me|tell me|report back)\b",
        r"(把|將).{0,16}(輸出|結果|錯誤|訊息|log).{0,8}(貼|傳|給我)",
        r"(paste|share)\s+the\s+(output|error|result|log)s?\b",
    ]),
    ("run_for_me", "請人代跑指令", [
        r"請(你|您)?(幫我)?(執行|跑|輸入|下)(以下|這個|這條|下面)?.{0,8}(指令|命令|腳本|command)",
        r"please\s+run\b",
        r"(在|到)(你的|您的)?(終端|terminal|命令列|cmd|PowerShell).{0,10}(執行|跑|輸入)",
        r"could you\s+(run|execute)\b",
    ]),
    ("handback", "完成後回報/等你接手", [
        r"(需要|只能|得|要麻煩)(由)?(你|您)(自己|親自|動手|來)",
        r"(弄好|完成|跑完|裝好|好了)(之後|後|再)?(跟我說|告訴我|回來|叫我|通知我)",
        r"(when|once|after)\s+(you'?re|you are|it'?s)\s+(done|ready|finished).{0,20}(let me know|tell me)",
        r"這一?步(只能|需要|得)(你|您)",
    ]),
]
COMPILED = [(key, label, [re.compile(p, re.I) for p in pats]) for key, label, pats in CATEGORIES]

# 排除：擷取到的片段若命中這些，多半是誤判（計畫書敘述、引用使用者的話、程式碼）
EXCLUDE = re.compile(
    r"^\s*([#>]|```|\|)"               # markdown 標題/引用/表格/程式碼
    r"|不需要(你|您)?手動|無需手動|不用手動"       # 否定句
    r"|我(會|來|已|正在|剛剛?|實際)(幫你|自己)?在?.{0,8}(安裝|執行|跑|打開|檢查|確認)"  # agent 自己做(了)
    , re.I)

# 第一人稱敘述（我做了/我可以做）而且沒有要求對方 → 不是跑腿
FIRST_PERSON = re.compile(r"^(我|I\b)")
SECOND_PERSON = re.compile(r"請|麻煩|你|您|please|you\b|your\b", re.I)

# ── 手把手教學偵測（message 層級）：agent 寫操作手冊代替自己動手 ──
LIST_LINE = re.compile(r"^\s*(\d+[.、)]|[-*•])\s+")
GUI_VERB = re.compile(r"點(擊|選|一下|開)?|按(下|一下)?|打開|開啟|切(換|到)|選(擇|取)|輸入|勾選|拖|雙擊|右鍵|視窗|頁面|分頁|選單|按鈕|欄位|貼上|click|open|select|press|type|menu|button|tab\b|window", re.I)
def is_step_guide(text):
    lines = text.splitlines()
    gui_steps = sum(1 for ln in lines if LIST_LINE.match(ln) and GUI_VERB.search(ln))
    return gui_steps >= 3, gui_steps

def iter_messages(path):
    """yield (ts, role, text) for user/assistant text (for context windows)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("type") not in ("user", "assistant"):
                continue
            c = (rec.get("message") or {}).get("content")
            if isinstance(c, str):
                txt = c
            elif isinstance(c, list):
                txt = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
            else:
                txt = ""
            if txt.strip():
                yield rec.get("timestamp", ""), rec["type"], txt


def iter_text_blocks(path):
    """yield (ts, role, is_sidechain, text) for assistant text blocks."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            ts = rec.get("timestamp", "")
            side = bool(rec.get("isSidechain"))
            for blk in content:
                if isinstance(blk, dict):
                    if blk.get("type") == "text" and blk.get("text"):
                        yield ts, side, "text", blk["text"]
                    elif blk.get("type") == "tool_use" and blk.get("name") == "AskUserQuestion":
                        q = json.dumps(blk.get("input", {}), ensure_ascii=False)[:300]
                        yield ts, side, "ask_user_question", q

def split_sentences(text):
    # 粗略斷句：換行、中文句號/問號/驚嘆號、英文句點
    for s in re.split(r"[\n]+|(?<=[。！？!?])|(?<=\.\s)", text):
        s = s.strip()
        if 6 <= len(s) <= 400:
            yield s

def audit(root, exclude=None):
    hits = []          # dict(project, file, ts, sidechain, category, sentence)
    stats = collections.Counter()
    session_count = 0
    sessions_with_hit = set()
    askq_count = 0
    sessions = {}   # fpath -> first timestamp
    for proj in sorted(os.listdir(root)):
        pdir = os.path.join(root, proj)
        if not os.path.isdir(pdir) or (exclude and re.search(exclude, proj)):
            continue
        for fn in sorted(os.listdir(pdir)):
            if not fn.endswith(".jsonl"):
                continue
            fpath = os.path.join(pdir, fn)
            session_count += 1
            first = next((m[0] for m in iter_messages(fpath)), "")
            sessions[fpath] = first[:10]
            for ts, side, kind, text in iter_text_blocks(fpath):
                if kind == "ask_user_question":
                    askq_count += 1
                    hits.append(dict(project=proj, file=fn, ts=ts, sidechain=side,
                                     category="ask_user_question", sentence=text))
                    stats["ask_user_question"] += 1
                    sessions_with_hit.add(fpath)
                    continue
                guide, nsteps = is_step_guide(text)
                if guide:
                    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
                    hits.append(dict(project=proj, file=fn, ts=ts, sidechain=side,
                                     category="step_by_step_guide",
                                     sentence=f"[{nsteps} 步 GUI 教學] {first[:120]}"))
                    stats["step_by_step_guide"] += 1
                    sessions_with_hit.add(fpath)
                for sent in split_sentences(text):
                    if EXCLUDE.search(sent):
                        continue
                    if FIRST_PERSON.match(sent) and not SECOND_PERSON.search(sent):
                        continue
                    for key, label, pats in COMPILED:
                        if any(p.search(sent) for p in pats):
                            hits.append(dict(project=proj, file=fn, ts=ts, sidechain=side,
                                             category=key, sentence=sent))
                            stats[key] += 1
                            sessions_with_hit.add(fpath)
                            break   # 一句最多記一類
    return dict(session_count=session_count, sessions=sessions,
                sessions_with_hits=len(sessions_with_hit),
                total_hits=len(hits), stats=dict(stats),
                ask_user_question=askq_count, hits=hits)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--json", help="完整命中結果輸出到 JSON 檔")
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--candidates", help="候選清單（含前後文、日期、去識別化）輸出 JSON，給 agent 複判")
    ap.add_argument("--split-date", help="以這天切前後（預設：~/.no-errands/config.json 的 installed_at）")
    ap.add_argument("--exclude", help="略過專案目錄名稱符合這個 regex 的（例如測試沙盒）")
    args = ap.parse_args()

    res = audit(args.root, args.exclude)
    split = args.split_date
    if not split:
        try:
            split = json.load(open(os.path.join(HOME_NE, "config.json"), encoding="utf-8")).get("installed_at", "")[:10]
        except Exception:
            split = ""
    if split:
        before = [f for f, d in res["sessions"].items() if d and d < split]
        after = [f for f, d in res["sessions"].items() if d and d >= split]
        hb = sum(1 for h in res["hits"] if res["sessions"].get(os.path.join(args.root, h["project"], h["file"]), "") < split)
        ha = len(res["hits"]) - hb
        res["split"] = {"date": split, "sessions_before": len(before), "hits_before": hb,
                        "per_session_before": round(hb / len(before), 2) if before else None,
                        "sessions_after": len(after), "hits_after": ha,
                        "per_session_after": round(ha / len(after), 2) if after else None}
        print("split @ %s: before %s sessions / %s hits, after %s sessions / %s hits" % (
            split, len(before), hb, len(after), ha))
    if args.candidates:
        cands = []
        for h in res["hits"]:
            if h["category"] == "ask_user_question":
                continue
            fpath = os.path.join(args.root, h["project"], h["file"])
            msgs = list(iter_messages(fpath))
            idx = next((i for i, m in enumerate(msgs) if m[0] == h["ts"] and m[1] == "assistant"), None)
            prev_user = next((m[2] for m in reversed(msgs[:idx]) if m[1] == "user"), "") if idx is not None else ""
            nxt = msgs[idx + 1][2] if idx is not None and idx + 1 < len(msgs) else ""
            cands.append({"session": proj_id(h["project"]) + "/" + h["file"][:8], "date": res["sessions"].get(fpath, ""),
                          "category": h["category"], "sentence": deid(h["sentence"]),
                          "prev_user": deid(prev_user)[:300], "next": deid(nxt)[:200]})
        os.makedirs(os.path.dirname(os.path.abspath(args.candidates)) or ".", exist_ok=True)
        json.dump({"candidates": cands, "session_count": res["session_count"], "split": res.get("split")},
                  open(args.candidates, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("candidates -> %s (%d)" % (args.candidates, len(cands)))
    print(f"sessions scanned : {res['session_count']}")
    print(f"sessions w/ hits : {res['sessions_with_hits']}")
    print(f"total hits       : {res['total_hits']}")
    print("by category:")
    labels = {k: l for k, l, _ in CATEGORIES}
    labels["ask_user_question"] = "AskUserQuestion 工具呼叫"
    for k, v in sorted(res["stats"].items(), key=lambda x: -x[1]):
        print(f"  {k:18s} {labels.get(k,k):14s} {v}")
    print(f"\n--- sample hits (up to {args.samples}) ---")
    seen = set()
    shown = 0
    for h in res["hits"]:
        key = h["sentence"][:60]
        if key in seen or h["category"] == "ask_user_question":
            continue
        seen.add(key)
        print(f"[{h['category']}] ({proj_id(h['project'])}) {deid(h['sentence'])[:160]}")
        shown += 1
        if shown >= args.samples:
            break
    if args.json:
        res.pop("sessions", None)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print(f"\nfull results -> {args.json}")

if __name__ == "__main__":
    main()
