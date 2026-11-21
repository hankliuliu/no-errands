#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI：把剛啟動的 Mattermost preview 準備好（純 API）。環境變數同 mm_flow.py。"""
import json, os, time, urllib.error, urllib.request

base = os.environ["MM_URL"].rstrip("/")
adm, admpw, bob, bobpw, team = (os.environ[k] for k in ("MM_ADMIN", "MM_ADMIN_PASS", "MM_BOB", "MM_BOB_PASS", "MM_TEAM"))


def call(method, path, token=None, body=None, raw=False):
    h = {"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + "/api/v4" + path, method=method, headers=h,
                                 data=None if body is None else json.dumps(body).encode())
    r = urllib.request.urlopen(req, timeout=30)
    return r if raw else json.loads(r.read().decode() or "null")


for _ in range(120):
    try:
        if call("GET", "/system/ping").get("status") == "OK":
            break
    except Exception:
        time.sleep(3)
try:
    call("POST", "/users", body={"email": "admin@example.com", "username": adm, "password": admpw})   # 第一個使用者 = 系統管理員
except urllib.error.HTTPError as e:
    print("admin create:", e.code)
tok = call("POST", "/users/login", body={"login_id": adm, "password": admpw}, raw=True).headers["Token"]
cfg = call("GET", "/config", tok)
cfg["ServiceSettings"]["EnableUserAccessTokens"] = False          # 讓瀏覽器測試自己去打開
cfg["ServiceSettings"]["EnableTutorial"] = False
cfg["ServiceSettings"]["EnableOnboardingFlow"] = False
cfg["TeamSettings"]["EnableOpenServer"] = True
call("PUT", "/config", tok, cfg)
try:
    t = call("POST", "/teams", tok, {"name": team, "display_name": team.upper(), "type": "O"})
except urllib.error.HTTPError:
    t = call("GET", "/teams/name/" + team, tok)
try:
    b = call("POST", "/users", tok, {"email": "bob@example.com", "username": bob, "password": bobpw})
except urllib.error.HTTPError:
    b = call("GET", "/users/username/" + bob, tok)
me = call("GET", "/users/me", tok)
for uid in (me["id"], b["id"]):
    try:
        call("POST", "/teams/%s/members" % t["id"], tok, {"team_id": t["id"], "user_id": uid})
    except urllib.error.HTTPError:
        pass
# 關掉新手導覽，免得遮住按鈕
for uid in (me["id"], b["id"]):
    try:
        call("PUT", "/users/%s/preferences" % uid, tok, [
            {"user_id": uid, "category": "tutorial_step", "name": uid, "value": "999"},
            {"user_id": uid, "category": "onboarding_task_list", "name": "onboarding_task_list_show", "value": "false"},
            {"user_id": uid, "category": "recommended_next_steps", "name": "hide", "value": "true"}])
    except urllib.error.HTTPError:
        pass
print(json.dumps({"ok": True, "team": t["name"], "pat_enabled": call("GET", "/config", tok)["ServiceSettings"]["EnableUserAccessTokens"]}))
