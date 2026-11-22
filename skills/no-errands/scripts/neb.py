#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""neb — agent-browser 的網域守門代理（純 stdlib），仿 ned。

  neb.py [--session S] [--profile P] <agent-browser 子指令> [args...]

- 敏感網域（郵件、聊天、社群、金融）：對應的 app 不在政策檔 granted_apps → 拒絕（同意閘門）。
- 已授權的敏感網域：**唯讀**——放行 open/snapshot/get/screenshot/scroll/wait/close 與一般點擊；
  拒絕 fill/type/press/keyboard/upload/eval 等輸入類，以及名稱像「送出/傳送/回覆/發佈/Send/Reply/Post/Submit」的點擊；
  點擊只接受 neb snapshot 產生的 ref（才知道點的是什麼）。要傳訊息 → outbox.py draft。
- 使用者登記的敏感網域（policy.json 的 sensitive_domains，例：經 SSH 通道的示範主機 localhost:8065）同樣處理，
  而且連點擊都要有核准過的示範劇本。
- 示範劇本的窄例外：`neb … <fill|type|press|click|check|uncheck> … --runbook-approval <id> --step <n>`。
  逐步輸入（live demo 看起來像有人在打字）：`neb … fill @ref <整段文字> --runbook-approval <id> --step <n> --progressive
  [--pace-file <這次執行資料夾>/control.json]`——只在劇本例外之下；整段先照常比對核准，再由 neb 自己切段、
  每段前後讀回（焦點欄位的身分＋內容＝核准文字的開頭）、以「插入文字」（不產生按鍵事件）附加；任何一次不符就停。
  放行條件全部成立：核准有效（未到期、未撤銷、清單雜湊相符）；目前主機＝核准的目標主機；--profile 的資料夾名
  ＝該步核准的示範帳號；輸入的欄位名稱與文字逐字相符（或符合核准的變動規則）／按鍵與點擊的名稱在該步清單內。
  任一不符 → 拒絕並回「需重新核准」。沒帶核准時行為不變。
- 其他網域：原樣交給 agent-browser。
- 每次呼叫寫 ledger（~/.no-errands/neb-ledger.jsonl；不含頁面內容）。
"""
import hashlib, json, os, re, subprocess, sys, time, urllib.parse
import nowin  # noqa: E402,F401  Windows：子程序不開主控台視窗（使用者回報視窗一直彈出）

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import policy  # noqa: E402

HOME = policy.HOME
LEDGER = os.path.join(HOME, "neb-ledger.jsonl")
STATE = os.path.join(HOME, "neb-state.json")

# 網域（尾綴比對）→ 正規 app 名
DOMAINS = {
    "mail.google.com": "gmail", "inbox.google.com": "gmail",
    "outlook.live.com": "outlook", "outlook.office.com": "outlook", "outlook.office365.com": "outlook",
    "mail.yahoo.com": "yahoo-mail", "mail.proton.me": "protonmail",
    "line.me": "line", "chat.line.biz": "line",
    "slack.com": "slack", "app.slack.com": "slack", "teams.microsoft.com": "teams", "teams.live.com": "teams",
    "discord.com": "discord", "web.whatsapp.com": "whatsapp", "web.telegram.org": "telegram",
    "messenger.com": "messenger", "facebook.com": "facebook", "instagram.com": "instagram",
    "x.com": "x", "twitter.com": "x", "threads.net": "threads", "linkedin.com": "linkedin",
    "chat.google.com": "google-chat", "web.wechat.com": "wechat",
}
# ---- agent-browser 0.43.0 指令與選項對照（升級 agent-browser 時要重對：docs/agent-browser-command-audit.md、tests/test_neb.py）----
# 已授權的敏感網站上只放行這些（其餘一律拒）：只讀、或經 ref＋名稱檢查的點擊、或導覽到檢查過的網址
SENSITIVE_ALLOWED = {"snapshot", "get", "is", "screenshot", "scroll", "scrollintoview", "wait", "back", "forward",
                     "reload", "close", "console", "errors", "tab", "session", "pdf", "diff", "find",
                     "click", "dblclick", "hover", "tap", "open", "goto", "navigate", "read"}
# 繞過 neb 逐步檢查的控制面指令（AI 代理、MCP、錄製重播、外掛、直接 CDP、自動填帳密、確認待決動作…）：任何網站都拒
CONTROL_CMDS = {"chat", "mcp", "webmcp", "flow", "plugin", "connect", "auth", "confirm", "deny", "stream",
                "dashboard", "inspect", "removeinitscript", "upgrade"}
# 能注入腳本/標頭、改 UA、接管或換掉瀏覽器、改信任設定的全域選項：出現在指令列任何位置都拒（不只子指令前面）
BAD_OPTS = {"--init-script", "--enable", "--extension", "--headers", "--args", "--cdp", "--cdp-headers",
            "--auto-connect", "--executable-path", "--state", "--restore", "--restore-check-fn", "--restore-check-url",
            "--restore-check-text", "--restore-save", "--session-name", "--action-policy", "--confirm-actions",
            "--confirm-interactive", "--config", "--provider", "-p", "--user-agent", "--proxy", "--proxy-bypass",
            "--ca-cert", "--no-ca-cert", "--allow-file-access", "--capture", "--engine", "--namespace",
            "--ignore-https-errors", "--device", "--model"}
# find 的動作字：預設動作就是 click，所以敏感網站上只接受明寫 text、且沒有任何動作字
FIND_ACTIONS = {"click", "dblclick", "fill", "type", "hover", "focus", "check", "uncheck", "tap", "press", "select",
                "clear", "upload", "drag"}
# 舊名保留給其他模組／測試參照
INPUT_CMDS = {"fill", "type", "press", "keyboard", "keydown", "keyup", "upload", "eval", "insert", "select",
              "check", "uncheck", "drag", "set", "clipboard", "dialog", "mouse", "focus", "download", "cookies",
              "storage", "network", "pushstate", "vitals", "a11y", "record", "trace", "profiler", "react", "highlight"}
READ_CMDS = SENSITIVE_ALLOWED
SEND_WORDS = re.compile(r"(送出|傳送|发送|發送|回覆|回复|發佈|发布|發表|分享|轉傳|send|reply|post|submit|publish|share|forward|tweet)",
                        re.I)
REF_RE = re.compile(r"^@?(e\d+)$")


def ledger(**kw):
    os.makedirs(HOME, exist_ok=True)
    kw["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")


def refuse(reason, **kw):
    ledger(allowed=False, reason=reason, **kw)
    print(json.dumps({"ok": False, "tool": "neb", "error": reason}, ensure_ascii=False))
    sys.exit(3)


def ab_path():
    return os.path.join(HOME, "bin", "agent-browser.exe" if os.name == "nt" else "agent-browser")


def app_for_host(host, pol):
    host = (host or "").lower().split(":")[0]
    mm = (pol.get("mattermost") or {}).get("url")
    extra = {}
    if mm:
        extra[urllib.parse.urlparse(mm).hostname or ""] = "mattermost"
    for dom, app in list(extra.items()) + list(DOMAINS.items()):
        if dom and (host == dom or host.endswith("." + dom)):
            return app
    return None


RB_INPUT = {"fill", "press", "check", "uncheck"}          # 不含 type／keyboard（附加輸入只存在於 fill --progressive 內部）
RB_CLICK = {"click", "check", "uncheck"}
VAR_VALUE_RE = re.compile(r"[^\s]{1,64}")                 # 變數值硬上限：不含空白與換行、最長 64 字
REUSE_GAP_S = 120                                         # 同一動作 120 秒內重複（就地重試）算同一次執行
FOCUS_JS = ("(()=>{const e=document.activeElement;if(!e||e===document.body)return JSON.stringify(null);"
            "const n=[e.getAttribute('aria-label'),e.getAttribute('placeholder'),e.getAttribute('title')];"
            "if(e.labels)for(const l of e.labels)n.push(l.innerText.trim());"
            "for(const id of (e.getAttribute('aria-labelledby')||'').split(/\\s+/)){const x=id&&document.getElementById(id);"
            "if(x)n.push(x.innerText.trim())}"
            "return JSON.stringify({names:n.filter(Boolean),value:('value' in e)?e.value:e.innerText,"
            "type:(e.type||'').toLowerCase(),id:e.id||''})})()")
LOGIN_JS = ("(()=>{const vis=e=>!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);"
            "const pw=[...document.querySelectorAll('input[type=password]')].filter(vis);"
            "if(pw.length!==1)return JSON.stringify({error:'password_fields='+pw.length});"
            "const f=pw[0].form||document;const us=[...f.querySelectorAll('input[type=text],input[type=email],input:not([type])')]"
            ".filter(vis);if(us.length!==1)return JSON.stringify({error:'user_fields='+us.length});"
            "return JSON.stringify({user_id:us[0].id||'',pw_id:pw[0].id||''})})()")
WINDOW_ARGS_RE = re.compile(r"--window-(?:position|size)=\d{1,5},\d{1,5}(?:\n--window-(?:position|size)=\d{1,5},\d{1,5})?")
ID_RE = re.compile(r"^[A-Za-z][\w\-]{0,80}$")
RUN_DIR_RE = re.compile(r"^\d{8}-\d{6}-(live|rehearse)$")      # --pace-file 只接受示範執行資料夾裡的 control.json
PROG_BUDGET_S = 5.0                                        # 一個欄位的逐步輸入總時間上限（長文自動加快）
PROG_MAX_CHUNKS = 14                                       # 段數上限（每段要讀回兩次，段太多會超過時間上限）
PROG_LONG_CHARS = 40                                       # 超過這個字數的長欄位：上限 2.5 秒、最多 6 段（5c：長欄位各打 5 秒太久）
PROG_LONG_BUDGET_S = 2.5
PROG_LONG_MAX_CHUNKS = 6
PROG_PAUSE_MAX_S = 900                                     # 逐步輸入中途暫停的上限（15 分鐘）：到了就停，原因「暫停過久」


def is_cjk(ch):
    o = ord(ch)
    return 0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0xFF00 <= o <= 0xFFEF or 0x3000 <= o <= 0x303F


def progressive_chunks(text, max_chunks=PROG_MAX_CHUNKS):
    """把核准的整段文字切成逐步輸入的段（只由守門呼叫；劇本與引擎不能指定）：中文 2–4 字一段、英數逐字；
    段數超過上限就把相鄰的段合併（長文自動加快）。各段接起來一定等於原文。"""
    import random
    rng = random.Random(len(text))
    out, i = [], 0
    while i < len(text):
        if is_cjk(text[i]):
            n = rng.choice((2, 3, 3, 4))
            j = i
            while j < len(text) and j - i < n and is_cjk(text[j]):
                j += 1
            out.append(text[i:j])
            i = j
        else:
            out.append(text[i])
            i += 1
    if len(out) > max_chunks:                                # 先把英數合成小段（中文維持 2–4 字一段）
        n_cjk = sum(1 for c in out if is_cjk(c[0]))
        ga = -(-(len(out) - n_cjk) // max(1, max_chunks - n_cjk))
        merged, buf = [], ""
        for c in out:
            if is_cjk(c[0]):
                if buf:
                    merged.append(buf); buf = ""
                merged.append(c)
            else:
                buf += c
                if len(buf) >= ga:
                    merged.append(buf); buf = ""
        if buf:
            merged.append(buf)
        out = merged
    if len(out) > max_chunks:                                # 還是太多（很長的中文）：相鄰的段合併
        g = -(-len(out) // max_chunks)
        out = ["".join(out[k:k + g]) for k in range(0, len(out), g)]
    assert "".join(out) == text
    return out


def pace_state(path):
    """--pace-file：只讀 state；檔案不存在或讀不懂＝沒有暫停。"""
    try:
        with open(path, encoding="utf-8") as f:
            return (json.load(f) or {}).get("state") or "playing"
    except Exception:
        return "playing"


def pace_path_ok(path):
    """--pace-file：檔名是 control.json、所在資料夾符合示範執行資料夾的命名，而且同資料夾 now.json 的 owner_pid
    就是呼叫 neb 的那個行程（引擎）——只有擁有這次執行的引擎能把它的控制檔交給 neb（da 的建議：不接受任意位置的同名資料夾）。"""
    if not path:
        return True
    rp = os.path.realpath(path)
    d = os.path.dirname(rp)
    if os.path.basename(rp) != "control.json" or not RUN_DIR_RE.match(os.path.basename(d)):
        return False
    try:
        with open(os.path.join(d, "now.json"), encoding="utf-8") as f:
            owner = int((json.load(f) or {}).get("owner_pid") or 0)
    except Exception:
        return False
    return owner == os.getppid()


def approved_input(aid, step, name, path, text):
    """這一步核准的、符合欄位名稱＋頁面路徑＋整段文字的那一筆輸入（runbook_check 已通過後才呼叫）。"""
    import outbox  # noqa: E402
    ok, rec = outbox.runbook_approval(aid)
    if not ok:
        return None
    doc = outbox.parse_runbook_body(rec["body"])
    at = lambda x: (x.get("at") or "").rstrip("/") or "/"
    for ent in doc["sends"]:
        if str(ent.get("step")) == str(step):
            for i in ent.get("inputs") or []:
                if at(i) == path and name in (i.get("fields") or []) and text_ok(i, text):
                    return i
    return None


def do_progressive(base_cmd, ref, text, inp, pace, sleep=time.sleep):
    """逐步輸入（劇本例外已通過）。回 (ok, info)；info 不含輸入內容，只有段數、停在第幾個字、原因、暫停秒數。
    1 聚焦該欄位；焦點欄位的身分（名稱）必須是核准的欄位、內容必須是空的。
    2 每一段：讀回（身分＋內容＝前 k-1 段）→ 插入文字（keyboard inserttext：不產生 keydown/keyup）→ 下一段的讀回兼作驗證。
    3 最後讀回＝整段。任何一次不符：立刻停止，不再送任何字，欄位裡留著什麼就留著。"""
    fields = set(inp.get("fields") or [])

    def ab(*a):
        return subprocess.run(base_cmd + list(a), capture_output=True, text=True, encoding="utf-8", errors="replace").returncode

    def focus_is(expect):
        foc = ab_json(base_cmd, FOCUS_JS)
        if not foc:
            return "讀不到目前的焦點欄位"
        if not (set(foc.get("names") or []) & fields):
            return "焦點不在核准的欄位上"
        if (foc.get("value") or "") != expect:
            return "欄位內容不是核准文字的開頭一段"
        return None
    long_ = len(text) > PROG_LONG_CHARS
    chunks = progressive_chunks(text, PROG_LONG_MAX_CHUNKS if long_ else PROG_MAX_CHUNKS)
    budget = PROG_LONG_BUDGET_S if long_ else PROG_BUDGET_S
    info = {"chunks": len(chunks), "paused_s": 0.0, "typed_chars": 0}
    if ab("focus", ref) != 0:
        info["why"] = "無法聚焦到該欄位"
        return False, info
    why = focus_is("")
    if why:
        info["why"] = "開始前：" + ("欄位不是空的" if why.startswith("欄位內容") else why)
        return False, info
    weights = [0.22 if is_cjk(c[0]) else 0.09 for c in chunks]
    import random
    rng = random.Random(len(text) * 7 + 1)
    delays = [w * rng.uniform(0.7, 1.35) for w in weights]
    try:
        pause_max = min(PROG_PAUSE_MAX_S, float(os.environ.get("NE_PROG_PAUSE_MAX_S", PROG_PAUSE_MAX_S)))   # 只能調得更嚴
    except ValueError:
        pause_max = PROG_PAUSE_MAX_S
    t0 = time.time()
    done = ""
    for k, c in enumerate(chunks):
        if pace:
            while True:
                stt = pace_state(pace)
                if stt == "stopped":
                    info.update(why="示範已停止", typed_chars=len(done))
                    return False, info
                if stt != "paused":
                    break
                if info["paused_s"] >= pause_max:
                    info.update(why="暫停過久", typed_chars=len(done))
                    return False, info
                tp = time.time()
                sleep(0.2)
                info["paused_s"] = round(info["paused_s"] + time.time() - tp, 2)
        # 每一段送出前都重新確認（第一段也是：暫停期間焦點可能被換走——da 的審查）
        why = focus_is(done)
        if why:
            info.update(why=why if k else "開始時：" + ("欄位不是空的" if why.startswith("欄位內容") else why),
                        typed_chars=len(done))
            return False, info
        if ab("keyboard", "inserttext", c) != 0:
            info.update(why="插入文字失敗", typed_chars=len(done))
            return False, info
        done += c
        left = budget - (time.time() - t0 - info["paused_s"])
        remain = sum(delays[k + 1:]) or 1.0
        sleep(max(0.0, min(delays[k], delays[k] * left / remain if left > 0 else 0.0)))
    why = focus_is(text)
    info["typed_chars"] = len(done)
    info["ms"] = int((time.time() - t0 - info["paused_s"]) * 1000)
    if why:
        info["why"] = "打完：" + why
        return False, info
    return True, info


def ab_json(base_cmd, js):
    """neb 自己用（守門用的唯讀檢查）：在頁面上跑一段固定的 JS，回解析後的物件。"""
    p = subprocess.run(base_cmd + ["eval", js], capture_output=True, text=True, encoding="utf-8", errors="replace")
    raw = (p.stdout.strip().splitlines() or [""])[-1].strip()
    try:
        v = json.loads(raw)
        return json.loads(v) if isinstance(v, str) else v
    except Exception:
        return None


def runbook_usage(aid, gap=REUSE_GAP_S):
    """preflight 用：{步驟: {動作鍵: 已執行次數}}（同 uses_of 的算法）。"""
    seen = {}
    try:
        with open(LEDGER, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("runbook") == aid and e.get("runbook_ok") and e.get("key"):
                    k = (str(e.get("step")), e["key"])
                    n, last = seen.get(k, (0, None))
                    t = e.get("t", 0)
                    seen[k] = (n + 1 if last is None or t - last > gap else n, t)
    except FileNotFoundError:
        pass
    out = {}
    for (st, key), (n, _) in seen.items():
        out.setdefault(st, {})[key] = n
    return out


def pattern_ok(pat, text):
    """變數規則：整段相符，且每個具名群組的值都過硬上限；沒有具名群組＝不接受（不靠 pattern 自律）。"""
    try:
        m = re.fullmatch(pat, text)
    except re.error:
        return False
    if not m or not m.groupdict():
        return False
    return all(v is not None and VAR_VALUE_RE.fullmatch(v) for v in m.groupdict().values())


def text_ok(inp, text):
    if inp.get("text") is not None:
        return text == inp["text"]
    return bool(inp.get("pattern")) and pattern_ok(inp["pattern"], text)


def uses_of(aid, step, key, gap):
    """依 ledger 算這份核准的這一步、這個動作已經執行了幾次（gap 秒內的重複算一次）。"""
    n, last = 0, None
    try:
        with open(LEDGER, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("runbook") == aid and str(e.get("step")) == str(step) and e.get("key") == key \
                        and e.get("runbook_ok"):
                    t = e.get("t", 0)
                    if last is None or t - last > gap:
                        n += 1
                    last = t
    except FileNotFoundError:
        pass
    return n


def pop_flag(args, name):
    """從參數裡取出 `name <值>` 並移除（--runbook-approval / --step 不傳給 agent-browser）。"""
    if name in args:
        i = args.index(name)
        val = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        return val
    return None


def acct_of(profile):
    return os.path.basename(os.path.normpath(profile)) if profile else ""


def runbook_check(aid, step, sub, args, refs, profile, url, pol, base_cmd):
    """示範劇本的窄例外：全部條件成立才回 (True, 動作鍵)；否則 (False, 原因)。原因不含輸入內容。
    條件：核准有效＋清單雜湊相符；目前主機（hostname＋port，loopback 寫法視為同一台）＝核准目標，且登記為示範用途；
    profile 是登記的示範帳號，也是該步核准的帳號；動作在該步清單內且「在核准的網址路徑上」；
    fill 的欄位＋文字逐字相符（變數過硬上限）；按鍵只在焦點欄位與其內容都等於該步核准的輸入時放行；
    同一動作的執行次數不超過核准時使用者選的次數。"""
    import outbox  # noqa: E402（延遲匯入：沒帶核准時不需要）
    ok, rec = outbox.runbook_approval(aid)
    if not ok:
        return False, rec
    doc = outbox.parse_runbook_body(rec["body"])
    ap = rec.get("approval") or {}
    if outbox.sends_sha256(doc["sends"]) != ap.get("sends_sha256"):
        return False, "核准記錄的清單雜湊對不上（檔案被改過）"
    host, port = policy.url_host_port(url)
    if (host, port) != policy.split_reg(ap.get("target", "")):
        return False, "目前主機不是核准的目標主機（%s）" % ap.get("target")
    dent = policy.sensitive_domain_entry(pol, host, port)
    if not dent or not dent.get("demo"):
        return False, "這個主機沒有登記為示範用途（policy.py domain add … --demo-accounts …）"
    acct = acct_of(profile)
    if acct not in (dent.get("demo_accounts") or []):
        return False, "這個瀏覽器 profile（%s）不是登記的示範帳號" % (acct or "未指定")
    ents = [s for s in doc["sends"] if str(s.get("step")) == str(step)]
    if not ents:
        return False, "第 %s 步不在核准的送出清單裡" % step
    ent = ents[0]
    if acct != ent.get("account"):
        return False, "這個瀏覽器 profile（%s）不是第 %s 步核准的示範帳號（%s）" % (acct, step, ent.get("account"))
    path = (urllib.parse.urlparse(url).path or "/").rstrip("/") or "/"
    at = lambda x: (x.get("at") or "").rstrip("/") or "/"
    if sub == "press":
        key = args[0] if args else ""
        if not any(k.get("key") == key and at(k) == path for k in ent.get("keys") or []):
            return False, "第 %s 步沒有核准在這個頁面按這個鍵" % step
        foc = ab_json(base_cmd, FOCUS_JS)
        if not foc:
            return False, "讀不到目前的焦點欄位，不按鍵"
        if not any(at(i) == path and set(foc.get("names") or []) & set(i.get("fields") or [])
                   and text_ok(i, foc.get("value") or "") for i in ent.get("inputs") or []):
            return False, "焦點欄位或其中的內容不是第 %s 步核准的輸入，不按鍵" % step
        akey = "press|%s|%s" % (key, path)
    else:
        m = REF_RE.match(args[0]) if args else None
        if not m:
            return False, "劇本步驟只接受 neb snapshot 產生的 ref"
        name = refs.get(m.group(1))
        if name is None:
            return False, "這個 ref 不是 neb 最近一次 snapshot 產生的"
        if sub == "fill":
            text = args[1] if len(args) > 1 else ""
            if not any(at(i) == path and name in (i.get("fields") or []) and text_ok(i, text)
                       for i in ent.get("inputs") or []):
                return False, "第 %s 步沒有核准在這個頁面對「%s」輸入這段文字" % (step, name)
            akey = "fill|%s|%s" % (name, path)
        elif sub in RB_CLICK:
            if not any(c.get("name") == name and at(c) == path for c in ent.get("clicks") or []):
                return False, "第 %s 步沒有核准在這個頁面點「%s」" % (step, name)
            akey = "%s|%s|%s" % (sub, name, path)
        else:
            return False, "劇本不允許 %s" % sub
    akey = hashlib.sha256(akey.encode("utf-8")).hexdigest()[:12]
    try:
        gap = min(REUSE_GAP_S, float(os.environ.get("NE_RB_REUSE_GAP_S", REUSE_GAP_S)))   # 只能調得更嚴
    except ValueError:
        gap = REUSE_GAP_S
    limit = int(ap.get("max_runs") or 1)
    if uses_of(aid, step, akey, gap) >= limit:
        return False, "這一步已執行 %d 次，達到核准時選的上限" % limit
    return True, akey


def do_login(base_cmd, profile, url, pol):
    """示範帳號登入（使用者在政策檔同意 agent 代為登入的帳號）。密碼從 stdin 讀、經 batch 的 stdin 交給
    agent-browser，不出現在命令列參數、不寫 log。只在登記的示範主機的登入頁、只填頁面上唯一的帳號欄與
    型別確實是 password 的欄位；Enter 只在焦點位於該密碼欄時按。"""
    host, port = policy.url_host_port(url)
    dent = policy.sensitive_domain_entry(pol, host, port)
    acct = acct_of(profile)
    if not dent or not dent.get("demo"):
        refuse("login 只能用在登記為示範用途的主機", sub="login")
    if acct not in (dent.get("agent_login") or []):
        refuse("使用者沒有在政策檔同意 agent 代為登入這個帳號（policy.py domain add … --agent-login …）", sub="login")
    path = (urllib.parse.urlparse(url).path or "/").rstrip("/")
    if not (path == "/login" or path.endswith("/login")):
        refuse("目前不在登入頁，不填任何東西", sub="login")
    info = ab_json(base_cmd, LOGIN_JS)
    if not info or info.get("error") or not ID_RE.match(info.get("user_id", "")) or not ID_RE.match(info.get("pw_id", "")):
        refuse("登入頁的欄位不符預期（要剛好一個帳號欄、一個 password 型別欄位，而且都有 id）：%s"
               % ((info or {}).get("error") or "缺 id"), sub="login")
    pw = sys.stdin.readline().rstrip("\r\n")
    if not pw:
        refuse("stdin 沒有密碼", sub="login")
    cmds = [["fill", "#" + info["user_id"], acct], ["fill", "#" + info["pw_id"], pw], ["focus", "#" + info["pw_id"]]]
    p = subprocess.run(base_cmd + ["batch", "--bail"], input=json.dumps(cmds), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    pw = None
    if p.returncode != 0:
        refuse("填入失敗（rc=%s）" % p.returncode, sub="login")
    foc = ab_json(base_cmd, FOCUS_JS)
    if not foc or foc.get("type") != "password" or foc.get("id") != info["pw_id"]:
        refuse("焦點不在密碼欄，不按 Enter", sub="login")
    p = subprocess.run(base_cmd + ["press", "Enter"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    ledger(allowed=True, sub="login", account=acct, host=host, ok=p.returncode == 0)
    print(json.dumps({"ok": p.returncode == 0, "tool": "neb", "login": acct}, ensure_ascii=False))
    sys.exit(0 if p.returncode == 0 else 1)


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def save_state(st):
    os.makedirs(HOME, exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)


_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):(.*)$")


def norm_url(u):
    """回 (正規化網址, 錯誤)。只接受 http(s) 與 about:blank；javascript:/data:/file: 等一律拒。"""
    u = (u or "").strip()
    if u.lower() == "about:blank":
        return u, None
    m = _SCHEME.match(u)
    if m and not re.match(r"^\d", m.group(2)):          # 有 scheme（排除 localhost:8065 這種「主機:埠」）
        sch = m.group(1).lower()
        if sch not in ("http", "https"):
            return None, "不接受 %s: 開頭的網址（只允許 http/https 與 about:blank）" % sch
        return u, None
    return "https://" + u, None


def nav_targets(sub, args):
    """這個指令會把瀏覽器帶到哪些網址（open/read/tab new/diff url/vitals/a11y/record/pushstate）。"""
    pos = [x for x in args if not x.startswith("-")]
    if sub in ("open", "goto", "navigate", "pushstate"):
        return pos[:1]
    if sub in ("read", "vitals", "a11y"):
        return pos[:1]
    if sub == "tab" and pos[:1] == ["new"]:
        return pos[1:2]
    if sub == "diff" and pos[:1] == ["url"]:
        return pos[1:3]
    if sub == "record" and pos[:1] == ["start"]:
        return pos[2:3]
    return []


def run_batch(glob, args):
    """batch 會連續執行多個指令、中途換頁 → 拆開，每一個都重新經過 neb 檢查（任何一個被拒就停）。"""
    cmds = [x for x in args if not x.startswith("--")]
    if not cmds:
        refuse("batch 從 stdin 讀指令時 neb 看不到內容：把每個指令寫成參數，或分開執行")
    import shlex
    rc_all = 0
    for i, c in enumerate(cmds, 1):
        toks = shlex.split(c, posix=(os.name != "nt"))
        toks = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t for t in toks]
        p = subprocess.run([sys.executable, os.path.abspath(__file__)] + glob + toks, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        sys.stdout.write(p.stdout)
        if p.returncode == 3:
            refuse("batch 的第 %d 個指令被拒，後面的不執行" % i, sub="batch")
        rc_all = rc_all or p.returncode
    sys.exit(rc_all)


def main():
    a = sys.argv[1:]
    glob, i = [], 0
    while i < len(a) and a[i].startswith("--"):          # 全域旗標（--session/--profile/--headed…）
        glob.append(a[i])
        if a[i] in ("--session", "--profile", "--cdp", "--proxy", "--user-agent") and i + 1 < len(a):
            glob.append(a[i + 1]); i += 1
        i += 1
    rest = a[i:]
    if not rest:
        print(__doc__); sys.exit(2)
    sub, args = rest[0].lower(), rest[1:]
    rb_id, rb_step = pop_flag(args, "--runbook-approval"), pop_flag(args, "--step")
    progressive = "--progressive" in args
    if progressive:
        args.remove("--progressive")
    pace = pop_flag(args, "--pace-file")
    if (progressive or pace is not None) and not (sub == "fill" and rb_id is not None):
        refuse("--progressive／--pace-file 只用於示範劇本的 fill（--runbook-approval <id> --step <n>）", sub=sub)
    if pace is not None and not pace_path_ok(pace):
        refuse("--pace-file 只接受示範執行資料夾裡的 control.json", sub=sub)
    rest = [rest[0]] + args
    # 任何網站都適用的檢查（先於網域判斷）
    for j, tok in enumerate(a):
        if tok == "--args" and j + 1 < len(a) and WINDOW_ARGS_RE.fullmatch(a[j + 1]):
            continue                      # 觀看模式排視窗：只放行 --window-position／--window-size
        if tok in BAD_OPTS or tok.split("=", 1)[0] in BAD_OPTS:
            refuse("不接受 %s：它能注入腳本或標頭、改身分、或接管瀏覽器，會繞過守門" % tok.split("=", 1)[0], sub=sub)
    if sub in CONTROL_CMDS:
        refuse("%s 會繞過 neb 的逐步檢查（AI 代理／MCP／重播／外掛／直接 CDP／自動登入），不經 neb 使用" % sub, sub=sub)
    if sub == "get" and args[:1] == ["cdp-url"]:
        refuse("get cdp-url 會交出瀏覽器的直接控制權，不提供", sub=sub)
    if sub == "batch":
        if rb_id is not None:
            refuse("劇本步驟不接受 batch（要逐一經過核准檢查）", sub=sub)
        run_batch(glob, args)
    session = glob[glob.index("--session") + 1] if "--session" in glob else "default"
    profile = glob[glob.index("--profile") + 1] if "--profile" in glob else None
    pol = policy.load(HOME)
    fake = os.environ.get("NE_FAKE_AB_PY")      # 測試掛鉤
    base_cmd = ([sys.executable, fake] if fake else [ab_path()]) + glob
    st = load_state()

    # 目標網域：導覽類看參數裡的每個網址；會作用在目前頁面的指令再加上目前網址
    targets = []
    for u in nav_targets(sub, args):
        nu, err = norm_url(u)
        if err:
            refuse(err, sub=sub)
        targets.append(nu)
    url = targets[0] if targets else ""
    acts_on_current = sub not in ("open", "goto", "navigate", "read") and not (sub == "tab" and args[:1] == ["new"])
    hosts = [urllib.parse.urlparse(t).hostname or "" for t in targets]
    if acts_on_current:
        # 一律問瀏覽器目前在哪（點擊可能已經導到別的網域；不信任上次記下的網址）
        p = subprocess.run(base_cmd + ["get", "url"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        cur = (p.stdout.strip().splitlines() or [""])[-1].strip() or st.get(session, {}).get("url", "")
        url = url or cur
        targets.append(cur)
    # 每個導覽目標與目前網址：使用者登記的敏感網域（hostname＋port、loopback 統一）優先，再看內建清單
    infos = []
    for u in targets:
        d_ = policy.sensitive_domain_entry(pol, *policy.url_host_port(u))
        ap_ = policy.canonical_app(d_.get("app") or d_.get("host")) if d_ else             app_for_host(urllib.parse.urlparse(u).hostname or "", pol)
        infos.append((u, d_, ap_))
    sens = [x for x in infos if x[2]]
    apps = [x[2] for x in sens]
    app = apps[0] if apps else None
    host = (urllib.parse.urlparse(targets[0]).hostname or "") if targets else ""
    dent = sens[0][1] if sens else None
    registered = bool(dent)
    page_url = targets[-1] if (acts_on_current and targets) else url     # 動作作用的頁面

    for ap in apps:
        if not policy.is_granted(pol, ap):
            refuse("敏感網站（%s）尚未授權：先在對話中取得使用者同意（同意閘門）" % ap, app=ap, sub=sub)
    if sub == "login":
        if not (registered and dent.get("demo")):
            refuse("login 只能用在已授權、登記為示範用途的主機", sub="login")
        do_login(base_cmd, profile, page_url, pol)

    rb_checked = False
    if app and rb_id is not None:
        if any(not (x[1] and x[1].get("demo")) for x in sens):
            refuse("示範劇本的例外只適用於登記為示範用途的主機；%s 帶了核准也一律只讀" % app, app=app, sub=sub)
        if sub in RB_INPUT or sub in RB_CLICK:
            ok, why = runbook_check(rb_id, rb_step, sub, args, load_state().get(session, {}).get("refs", {}),
                                    profile, page_url, pol, base_cmd)
            ledger(runbook=rb_id, step=rb_step, app=app, sub=sub, host=host, runbook_ok=ok,
                   key=why if ok else None, t=time.time())
            if not ok:
                refuse("需重新核准：" + why, app=app, sub=sub, runbook=rb_id, step=rb_step)
            rb_checked = True
            if progressive:
                m_ = REF_RE.match(args[0]) if args else None
                if not m_ or len(args) < 2:
                    refuse("逐步輸入只接受 neb snapshot 產生的 ref 與整段文字", app=app, sub=sub, runbook=rb_id, step=rb_step)
                name = load_state().get(session, {}).get("refs", {}).get(m_.group(1))
                path = (urllib.parse.urlparse(page_url).path or "/").rstrip("/") or "/"
                inp = approved_input(rb_id, rb_step, name, path, args[1] if len(args) > 1 else "")
                if not inp:
                    refuse("找不到這一步核准的輸入（逐步輸入）", app=app, sub=sub, runbook=rb_id, step=rb_step)
                ok, info = do_progressive(base_cmd, args[0], args[1], inp, pace)
                ledger(runbook=rb_id, step=rb_step, app=app, sub="fill-progressive", host=host, progressive_ok=ok,
                       chunks=info.get("chunks"), typed_chars=info.get("typed_chars"), why=info.get("why"), t=time.time())
                print(json.dumps({"ok": ok, "tool": "neb", "progressive": info}
                                 if ok else {"ok": False, "tool": "neb", "error": "逐步輸入停在第 %d 個字：%s"
                                             % (info.get("typed_chars", 0), info.get("why")), "progressive": info},
                                 ensure_ascii=False))
                sys.exit(0 if ok else 1)
    if app and not rb_checked:
        if registered and sub in ("click", "dblclick", "tap", "check", "uncheck"):
            refuse("登記的敏感網域上的點擊要有核准過的示範劇本（--runbook-approval <id> --step <n>）", app=app, sub=sub)
        if sub not in SENSITIVE_ALLOWED:
            refuse("已授權的敏感網站只能讀：%s 不在允許清單（不輸入、不按鍵、不執行腳本；要傳訊息請用 outbox.py draft）" % sub,
                   app=app, sub=sub)
        if sub == "find":
            rest_tokens = [x.lower() for x in args[2:]]
            if "text" not in rest_tokens or any(t in FIND_ACTIONS for t in rest_tokens):
                refuse("敏感網站上的 find 只能讀文字（明寫 text 動作；不寫動作時預設是 click）。"
                       "要點東西：neb snapshot 取 ref，再 neb click @ref", app=app, sub=sub)
        if sub == "wait" and any(x == "--fn" or x.startswith("--fn=") for x in args):
            refuse("wait --fn 會在頁面上執行 JavaScript（等同 eval），敏感網站上不接受", app=app, sub=sub)
        if sub == "diff" and args[:1] not in (["snapshot"], ["screenshot"]):
            refuse("敏感網站上 diff 只接受 snapshot／screenshot", app=app, sub=sub)
        if sub in ("click", "dblclick", "hover", "tap"):
            m = REF_RE.match(args[0]) if args else None
            if not m:
                refuse("敏感網站上的點擊只接受 neb snapshot 產生的 ref（例如 @e5），才能確認點的是什麼", app=app, sub=sub)
            name = st.get(session, {}).get("refs", {}).get(m.group(1))
            if name is None:
                refuse("這個 ref 不是 neb 最近一次 snapshot 產生的：先用 neb snapshot 重拍", app=app, sub=sub)
            if SEND_WORDS.search(name):
                refuse("這個元素看起來是送出/回覆/發佈類按鈕：agent 不能送出訊息，請用發件匣", app=app, sub=sub)

    p = subprocess.run(base_cmd + [rest[0]] + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = p.stdout
    ent = st.setdefault(session, {})
    if sub in ("open", "goto", "navigate") and p.returncode == 0:
        ent["url"] = url
        ent["refs"] = {}
    if sub == "snapshot":
        refs = {}
        for line in out.splitlines():               # 例：- button "Send" [ref=e12] / - heading "X" [level=1, ref=e1]
            mm = re.search(r'"([^"]*)".*?\bref=(e\d+)', line)
            if mm:
                refs[mm.group(2)] = mm.group(1)
        ent["refs"] = refs
    if sub == "close":
        st.pop(session, None)
    save_state(st)
    ledger(allowed=True, app=app, sub=sub, host=host)
    sys.stdout.write(out)
    if p.stderr:
        sys.stderr.write(p.stderr)
    sys.exit(p.returncode)


if __name__ == "__main__":
    main()
