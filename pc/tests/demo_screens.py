"""
Снимки окна для README — на ВЫДУМАННЫХ демо-данных (никаких личных данных).
    python tests/demo_screens.py
Кладёт PNG в docs/img/.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

DEMO = tempfile.mkdtemp()
os.environ["APPDATA"] = DEMO
os.environ["SKACHIVATEL_PORT"] = "47831"
os.environ["SKACHIVATEL_LEGACY"] = os.path.join(DEMO, "нет")  # не переносить настоящие настройки
PC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(PC)
sys.path.insert(0, PC)

data = os.path.join(DEMO, "Скачиватель")
os.makedirs(data, exist_ok=True)
home = os.path.expanduser("~")
cfg = {"users": {"1001": {"name": "Аня", "added": "12.09.2026", "defaults": {"what": "best", "where": "pc", "folder": 0}},
                 "1002": {"name": "Мама", "added": "14.09.2026", "defaults": {"what": "mp3", "where": "tg", "folder": 2}}},
       "owner": "1001", "token": "", "api": {}, "folders": [
           {"name": "Скачано", "path": os.path.join(DEMO, "Скачано")}, {"name": "Видео", "path": os.path.join(DEMO, "Видео")},
           {"name": "Музыка", "path": os.path.join(DEMO, "Музыка")}, {"name": "Курсы", "path": os.path.join(DEMO, "Курсы")}],
       "pc_defaults": {"what": "best", "folder": 0},
       "devices": {"d1": {"id": "d1", "name": "Xiaomi Redmi Note 13", "secret": "AAAA", "added": "20.09.2026", "seen": "30.09 18:12"}}}
json.dump(cfg, open(os.path.join(data, "app_config.json"), "w", encoding="utf-8"), ensure_ascii=False)
hist = [
    {"id": "h1", "url": "https://rutube.ru/video/x", "what": "best", "where": "pc", "who": "pc", "status": "done",
     "title": "Как устроена ракета — лекция", "files": [os.path.join(DEMO, "a.mp4")], "size": 734003200, "error": "",
     "created": "30.09 17:40", "finished": "30.09 17:46"},
    {"id": "h2", "url": "https://youtube.com/watch?v=x", "what": "mp3", "where": "tg", "who": "1002", "status": "done",
     "title": "Утренняя зарядка для спины", "files": [os.path.join(DEMO, "b.mp3")], "size": 22020096, "error": "",
     "created": "30.09 16:02", "finished": "30.09 16:03"},
    {"id": "h3", "url": "https://ru.pinterest.com/demo/", "what": "all", "where": "pc", "who": "1001", "status": "done",
     "title": "Pinterest · demo", "files": [os.path.join(DEMO, "c.jpg")] * 1, "size": 412090368, "error": "",
     "created": "30.09 12:10", "finished": "30.09 12:21"},
    {"id": "h4", "url": "https://www.instagram.com/demo/", "what": "photo", "where": "pc", "who": "pc", "status": "error",
     "title": "", "files": [], "size": 0, "created": "30.09 11:00", "finished": "30.09 11:00",
     "error": "Сайт отдаёт это только после входа в аккаунт."},
]
json.dump(hist, open(os.path.join(data, "app_history.json"), "w", encoding="utf-8"), ensure_ascii=False)
for f in ("a.mp4", "b.mp3", "c.jpg"):
    open(os.path.join(DEMO, f), "wb").write(b"0")

import app  # noqa: E402
import core as C  # noqa: E402

C.Cfg.load()
C.Q.load()
C.Q.jobs = [{"id": "j1", "url": "https://vkvideo.ru/video-1_2", "what": "1080", "where": "both", "folder": 1, "who": "1001",
             "status": "running", "pct": 63, "text": "63% из 1.4GiB · 12.3MiB/s · осталось 00:41", "files": [], "size": 0,
             "title": "Большой разбор: как учиться быстрее", "created": "30.09 18:20"},
            {"id": "j2", "url": "https://rutube.ru/plst/1/", "what": "mp3", "where": "pc", "folder": 2, "who": "phone:d1",
             "status": "queued", "pct": 0, "text": "в очереди", "files": [], "size": 0, "title": "Подкаст: 24 выпуска",
             "created": "30.09 18:21"}]
C.Q.worker = lambda: None
app.start_bot = lambda: None
C.TL.start = lambda: None
app.remote.start = lambda: None
threading.Thread(target=app.main, daemon=True).start() if False else None
from http.server import ThreadingHTTPServer  # noqa: E402

srv = ThreadingHTTPServer(("127.0.0.1", app.PORT), app.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
import tgbot  # noqa: E402

tgbot.BOT.running, tgbot.BOT.username = True, "my_skachivatel_bot"
C.Cfg.data["token"] = "demo"
app.STATE["tg"] = {"api": True, "user": "Аня @anya", "big": True}
app.STATE["tg_at"] = time.time() + 10 ** 6
C.find_proxy = lambda: "socks5://127.0.0.1:10808"

chrome = next(p for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                          r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe") if os.path.exists(p))
out = os.path.join(ROOT, "docs", "img")
os.makedirs(out, exist_ok=True)
# Снимки через протокол отладки Chrome: открыть → подождать → Page.captureScreenshot
import base64  # noqa: E402
import urllib.request  # noqa: E402

import websocket  # noqa: E402  (pip install websocket-client)

prof = os.path.join(DEMO, "chrome")
proc = subprocess.Popen([chrome, "--headless=new", f"--user-data-dir={prof}", "--remote-debugging-port=9333",
                         "--remote-allow-origins=*",
                         "--no-first-run", "--disable-extensions", "--hide-scrollbars", "--window-size=1180,760",
                         "--force-device-scale-factor=1.5", "about:blank"])
for _ in range(50):
    try:
        tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9333/json").read())
        ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
        break
    except Exception:  # noqa: BLE001
        time.sleep(0.3)
ws = websocket.create_connection(ws_url, timeout=30)
n = [0]


def cdp(method, **params):
    n[0] += 1
    ws.send(json.dumps({"id": n[0], "method": method, "params": params}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == n[0]:
            return r.get("result", {})


cdp("Emulation.setDeviceMetricsOverride", width=1180, height=760, deviceScaleFactor=1.5, mobile=False)
for tab, name in (("queue", "screen-pc.png"), ("add", "screen-add.png"), ("hist", "screen-history.png"),
                  ("phone", "screen-pc-phone.png"), ("settings", "screen-settings.png")):
    cdp("Page.navigate", url=f"http://127.0.0.1:{app.PORT}/?tab={tab}")
    time.sleep(3)
    shot = cdp("Page.captureScreenshot", format="png")
    open(os.path.join(out, name), "wb").write(base64.b64decode(shot["data"]))
    print("снимок:", name, flush=True)
ws.close()
proc.kill()
os._exit(0)
