#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude Code PreToolUse hook：預設不准 agent 讀 Mattermost token 檔；使用者特別設了白名單的專案資料夾才放行。

為什麼是 hook、不是權限規則：settings.json 的 deny 無法被專案層級蓋掉，做不到「白名單時可以」。
hook 每次工具呼叫前讀政策檔判斷，白名單由使用者用 `policy.py token-allow add <資料夾> --reason ...` 設定（寫 ledger）。

判斷：工具輸入（Read 的路徑、Bash／PowerShell 的指令、Grep／Glob 的路徑與樣式…）裡出現 token 檔的路徑
（各種寫法：反斜線、正斜線、/c/、大小寫）或檔名 → 若目前專案（CLAUDE_PROJECT_DIR，沒有就 cwd）不在白名單 → deny。
其他一律不表態（交回 Claude Code 原本的權限流程）。任何錯誤 → 不表態（不能因為 hook 壞掉讓使用者整個不能用）。

限制（和權限規則一樣，防的是太熱心，不是安全邊界）：把路徑拆開再組、用變數或萬用字元（mattermost.t*）、
用另一支程式去讀，都看不到。白名單看的是專案資料夾，不是單一指令。
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def variants(path):
    """token 檔路徑的各種寫法（全部小寫、斜線統一成 /）。"""
    p = os.path.abspath(path).replace("\\", "/").lower()
    out = {p}
    if len(p) > 1 and p[1] == ":":
        out.add("/" + p[0] + p[2:])          # /c/users/...（Git Bash）
        out.add("/mnt/" + p[0] + p[2:])      # WSL
        out.add(p[2:])                       # 沒有磁碟代號
    home = os.path.expanduser("~").replace("\\", "/").lower()
    if p.startswith(home + "/"):
        out.add("~" + p[len(home):])
        out.add("$home" + p[len(home):])
        out.add("$userprofile" + p[len(home):])
        out.add("%userprofile%" + p[len(home):])
    return out


def under(child, parent):
    c = os.path.normcase(os.path.abspath(child)).rstrip("\\/")
    p = os.path.normcase(os.path.abspath(parent)).rstrip("\\/")
    return c == p or c.startswith(p + os.sep)


def decide(event, pol, project_dir):
    """回傳 None（不表態）或 deny 的理由。"""
    mm = pol.get("mattermost") or {}
    tf = mm.get("token_file")
    if not tf:
        return None
    blob = json.dumps(event.get("tool_input") or {}, ensure_ascii=False).replace("\\\\", "/").replace("\\", "/").lower()
    base = os.path.basename(tf).lower()
    hit = any(v in blob for v in variants(tf)) or (len(base) >= 6 and re.search(r"(?<![\w.-])" + re.escape(base) + r"(?![\w.-])", blob))
    if not hit:
        return None
    for d in mm.get("token_read_allow") or []:
        if project_dir and under(project_dir, d):
            return None
    return ("no-errands：Mattermost token 檔預設不給 agent 讀（工具在執行時自己讀）。"
            "這個專案不在 token 讀取白名單；要開放請使用者說，再用 policy.py token-allow add <專案資料夾> --reason 加入。")


def main():
    try:
        event = json.load(sys.stdin)
        import policy
        pol = policy.load(policy.HOME)
        why = decide(event, pol, os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd())
    except Exception:
        return 0
    if why:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": why}}))   # ASCII 跳脫：不受主控台編碼影響
    return 0


if __name__ == "__main__":
    sys.exit(main())
