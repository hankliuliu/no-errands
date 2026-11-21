#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mattermost 共用：設定檔解析 + 唯讀 GET（純 stdlib）。mm_ro.py 與發件匣共用。

本模組**只會發 GET**（方法寫死）。整個系統裡唯一的 POST 在 outbox.py 的送出轉接器，
而且只由發件匣伺服器在使用者按下核准後呼叫。

token 只存在記憶體：不輸出、不寫檔、不進 log、不放進例外訊息。
"""
import json, os, urllib.error, urllib.parse, urllib.request

URL_KEYS = ("url", "server", "host", "base_url", "mattermost_url", "mm_url")
TOKEN_KEYS = ("token", "access_token", "access token", "存取 token", "存取token", "pat", "mattermost_token", "mm_token")


def read_kv_file(path):
    """回傳 dict（小寫鍵）。支援：JSON 物件；每行 `key=value` / `key: value` / `標籤：值`（全形冒號，
    鍵可含空白，例如 Mattermost 個人存取 token 頁面複製下來的「存取 Token：…」）；或兩行「網址、token」。"""
    with open(path, encoding="utf-8-sig") as f:
        text = f.read().strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return {str(k).lower(): str(v) for k, v in obj.items()}
    except Exception:
        pass
    kv, bare = {}, []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        for sep in ("：", "=", ":"):
            k, s, v = line.partition(sep)
            k = k.strip()
            if s and k and len(k) <= 24 and not k.lower().startswith("http") and (" " not in k or sep == "："):
                kv[k.lower()] = v.strip().strip('"').strip("'")
                break
        else:
            bare.append(line)
    if not kv and len(bare) == 2:
        url = [b for b in bare if b.startswith("http")]
        tok = [b for b in bare if not b.startswith("http")]
        if url and tok:
            kv = {"url": url[0], "token": tok[0]}
    return kv


def describe_format(path):
    """不顯示內容地描述檔案格式：行數、每行的鍵名（值一律不回）。"""
    with open(path, encoding="utf-8-sig") as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    kv = read_kv_file(path)
    return {"lines": len(lines), "keys": sorted(kv.keys()),
            "has_url": any(kv.get(k) for k in URL_KEYS),
            "has_token": any(kv.get(k) for k in TOKEN_KEYS)}


def conf(token_file, url_override=None):
    """回 (base_url, token)。檔案裡沒有網址時用 url_override（政策檔 mattermost.url）。"""
    if not token_file or not os.path.exists(token_file):
        raise RuntimeError("Mattermost 設定檔不存在（政策檔 mattermost.token_file）")
    kv = read_kv_file(token_file)
    url = next((kv[k] for k in URL_KEYS if kv.get(k)), "") or (url_override or "")
    tok = next((kv[k] for k in TOKEN_KEYS if kv.get(k)), "")
    if not url or not tok:
        raise RuntimeError("Mattermost 設定檔格式無法辨識（需要伺服器網址與 token 兩個欄位）")
    if not url.startswith("http"):
        url = "https://" + url
    return url.rstrip("/"), tok


def get(base, tok, path, params=None):
    """唯讀 GET；path 必須以 / 開頭，一律加在 /api/v4 之後。"""
    if not path.startswith("/") or ".." in path:
        raise ValueError("bad path")
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    req = urllib.request.Request(base + "/api/v4" + path + q, method="GET",
                                 headers={"Authorization": "Bearer " + tok,
                                          "User-Agent": "no-errands"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        raise RuntimeError("Mattermost GET %s → HTTP %d" % (path.split("?")[0], e.code)) from None


def resolve_target(base, tok, to):
    """把發件匣的 to（頻道 id 或 @使用者名稱）解析成人看得懂的標籤。只用 GET。
    回 {"label": str, "kind": "channel"|"dm"}；失敗丟例外。"""
    to = to.strip()
    if to.startswith("@"):
        u = get(base, tok, "/users/username/" + urllib.parse.quote(to[1:]))
        return {"label": "私訊：%s" % _user_label(u), "kind": "dm"}
    ch = get(base, tok, "/channels/" + urllib.parse.quote(to))
    if ch.get("type") == "D":
        me = get(base, tok, "/users/me")
        ids = [i for i in (ch.get("name") or "").split("__") if i and i != me.get("id")]
        other = get(base, tok, "/users/" + ids[0]) if ids else me
        return {"label": "私訊：%s" % _user_label(other), "kind": "dm"}
    if ch.get("type") == "G":
        return {"label": "群組私訊：%s" % (ch.get("display_name") or ch.get("name")), "kind": "channel"}
    team = ""
    if ch.get("team_id"):
        try:
            team = get(base, tok, "/teams/" + ch["team_id"]).get("display_name", "")
        except Exception:
            team = ""
    kind = "公開頻道" if ch.get("type") == "O" else "私人頻道"
    return {"label": "%s%s：%s" % (team + " / " if team else "", kind,
                                  ch.get("display_name") or ch.get("name")), "kind": "channel"}


def _user_label(u):
    full = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x).strip()
    nick = u.get("nickname") or ""
    name = nick or full
    return "%s (@%s)" % (name, u.get("username")) if name else "@%s" % u.get("username")
