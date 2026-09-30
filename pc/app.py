"""
«Скачиватель» — приложение на ПК: окно (HTML в Chrome/Edge) + Telegram-бот + очередь загрузок.
Работает в фоне и после закрытия окна (чтобы бот принимал ссылки). Выключить — Настройки → «Выключить».

    pythonw app.py            — запустить (или открыть окно, если уже запущено)
    pythonw app.py --hidden   — запустить без окна (автозапуск)
"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paths  # noqa: E402

sys.path.insert(0, paths.APP_DIR)
if sys.stdout is None:  # без консоли (pythonw / установленная версия)
    sys.stdout = sys.stderr = open(paths.data("app_log.txt"), "a", encoding="utf-8", buffering=1)

import requests  # noqa: E402

import core as C  # noqa: E402
import remote  # noqa: E402
from core import Cfg, Q, TL, log  # noqa: E402

PORT = int(os.environ.get("SKACHIVATEL_PORT", 47821))
UI = os.path.join(paths.APP_DIR, "app_ui.html")
STATE = {"tg": {"api": False, "user": None, "big": False}, "tg_at": 0, "updating": False}


def tg_status():
    if time.time() - STATE["tg_at"] > 30:
        try:
            STATE["tg"] = TL.status()
        except Exception:  # noqa: BLE001
            pass
        STATE["tg_at"] = time.time()
    return STATE["tg"]


def phone_state():
    st = {"devices": [{"id": k, "name": v["name"], "added": v.get("added"), "seen": v.get("seen")}
                      for k, v in remote.devices().items()],
          "pairing": remote.Pair.active(), "ips": remote.lan_ips(),
          "pending": [{"nonce": n, "name": p["name"], "fp": p["fp"]} for n, p in list(remote.Pair.pending.items())
                      if not p["event"].is_set()]}
    if st["pairing"]:
        link = remote.Pair.qr()
        st.update(qr=remote.qr_svg(link), code=remote.Pair.code(), id=remote.pc_id(),
                  left=30 - int(time.time()) % 30)
    return st


def start_bot():
    import tgbot
    if tgbot.BOT.running:
        return
    threading.Thread(target=tgbot.BOT.run, daemon=True).start()


def pick_folder():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askdirectory(title="Выберите папку для загрузок")
    root.destroy()
    return os.path.normpath(path) if path else ""


def pick_file():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(title="Выберите cookies.txt", filetypes=[("cookies", "*.txt"), ("все", "*.*")],
                                      initialdir=os.path.join(os.path.expanduser("~"), "Downloads"))
    root.destroy()
    return path


def known_path(p):
    p = os.path.normcase(os.path.abspath(p))
    for h in Q.history:
        for f in h.get("files", []):
            nf = os.path.normcase(os.path.abspath(f))
            if p in (nf, os.path.dirname(nf)):
                return True
    return any(p == os.path.normcase(os.path.abspath(f["path"])) for f in Cfg.data["folders"])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/":
            with open(UI, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/ping":
            self._json({"ok": True, "app": "downloader"})
        elif self.path == "/api/state":
            import tgbot
            with Q.lock:
                jobs = [dict(j, who_name=C.who_name(j["who"])) for j in Q.jobs]
            hist = [dict(h, who_name=C.who_name(h["who"])) for h in Q.history[:150]]
            users = [{"id": k, "name": u["name"], "added": u.get("added"), "owner": Cfg.is_owner(k)}
                     for k, u in Cfg.data["users"].items()]
            self._json({"jobs": jobs, "history": hist, "users": users, "folders": Cfg.data["folders"],
                        "pc_defaults": Cfg.data["pc_defaults"],
                        "bot": {"running": tgbot.BOT.running, "username": tgbot.BOT.username,
                                "token": bool(Cfg.data.get("token"))},
                        "tg": tg_status(), "login_step": "code" if TL.login_state else None,
                        "autostart": C.autostart_on(), "updating": STATE["updating"],
                        "cookie_sites": C.cookie_sites(), "proxy": C.find_proxy(),
                        "phone": phone_state(),
                        "log": list(C.LOG)[-200:], "what": C.WHAT})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        d = json.loads(self.rfile.read(n) or b"{}")
        p = self.path
        try:
            if p == "/api/add":
                urls = [u.strip() for u in C.URL_RE.findall(d.get("text", ""))]
                for u in dict.fromkeys(urls):
                    Q.add(url=u.rstrip(").,"), what=d.get("what", "best"), where="pc", folder=int(d.get("folder", 0)))
                Cfg.data["pc_defaults"] = {"what": d.get("what", "best"), "folder": int(d.get("folder", 0))}
                Cfg.save()
                self._json({"ok": True, "added": len(set(urls))})
            elif p == "/api/cancel":
                self._json({"ok": Q.cancel(d["id"])})
            elif p == "/api/retry":
                h = next((x for x in Q.history if x["id"] == d["id"]), None)
                if h and h.get("url"):
                    Q.add(url=h["url"], what=h["what"], where="pc", folder=0)
                self._json({"ok": bool(h)})
            elif p == "/api/open":
                path = d.get("path", "")
                if os.path.exists(path) and known_path(path):
                    if d.get("select") and os.path.isfile(path):
                        subprocess.Popen(["explorer", "/select,", path])
                    else:
                        os.startfile(path)
                self._json({"ok": True})
            elif p == "/api/code":
                self._json({"code": Cfg.new_code()})
            elif p == "/api/user/remove":
                if not Cfg.is_owner(d["id"]):
                    Cfg.remove_user(d["id"])
                self._json({"ok": True})
            elif p == "/api/folders/pick":
                self._json({"path": pick_folder()})
            elif p == "/api/folders/add":
                path = d.get("path", "").strip()
                if path:
                    os.makedirs(path, exist_ok=True)
                    Cfg.data["folders"].append({"name": d.get("name") or os.path.basename(path), "path": path})
                    Cfg.save()
                self._json({"ok": True})
            elif p == "/api/folders/remove":
                i = int(d["i"])
                if len(Cfg.data["folders"]) > 1:
                    Cfg.data["folders"].pop(i)
                    Cfg.save()
                self._json({"ok": True})
            elif p == "/api/token":
                tok = d.get("token", "").strip()
                r = requests.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=20).json()
                if not r.get("ok"):
                    return self._json({"error": "Ключ не подходит — проверьте, что скопировали его целиком."})
                Cfg.data["token"] = tok
                Cfg.save()
                start_bot()
                self._json({"ok": True, "username": r["result"]["username"]})
            elif p == "/api/tg/send_code":
                TL.login_send_code(d["api_id"], d["api_hash"], d["phone"])
                self._json({"ok": True})
            elif p == "/api/tg/code":
                r = TL.login_code(d["code"])
                STATE["tg_at"] = 0
                self._json({"ok": True, "next": r})
            elif p == "/api/tg/password":
                TL.login_password(d["password"])
                STATE["tg_at"] = 0
                self._json({"ok": True})
            elif p == "/api/tg/logout":
                TL.logout()
                STATE["tg_at"] = 0
                self._json({"ok": True})
            elif p == "/api/cookies/add":
                src = pick_file()
                if not src:
                    return self._json({"ok": False})
                new = [l for l in open(src, encoding="utf-8", errors="replace").read().splitlines()
                       if l.strip() and (not l.startswith("#") or l.startswith("#HttpOnly_"))]
                if not any("\t" in l for l in new):
                    return self._json({"error": "Это не файл cookies.txt — выгрузите его расширением «Get cookies.txt LOCALLY»."})
                self._json({"ok": True, "sites": C.merge_cookies(new)})
            elif p == "/api/site_login":
                site = d.get("site")
                import login_window
                if site not in login_window.SITES:
                    return self._json({"error": "неизвестный сайт"})
                # окно входа — отдельным процессом (ему нужен свой главный поток)
                cmd = [sys.executable, "--login", site] if paths.FROZEN else \
                    [sys.executable, os.path.join(paths.APP_DIR, "app.py"), "--login", site]
                subprocess.Popen(cmd, creationflags=paths.NO_WINDOW)
                self._json({"ok": True})
            elif p == "/api/cookies/clear":
                if os.path.exists(C.COOKIES):
                    os.remove(C.COOKIES)
                self._json({"ok": True})
            elif p == "/api/phone/pair":
                remote.Pair.start() if d.get("on") else remote.Pair.stop()
                self._json({"ok": True})
            elif p == "/api/phone/answer":
                pend = remote.Pair.pending.get(d.get("nonce"))
                if pend:
                    pend["ok"] = bool(d.get("ok"))
                    pend["event"].set()
                self._json({"ok": True})
            elif p == "/api/phone/remove":
                remote.devices().pop(d.get("id"), None)
                Cfg.save()
                self._json({"ok": True})
            elif p == "/api/autostart":
                C.set_autostart(bool(d.get("on")))
                self._json({"ok": True})
            elif p == "/api/update":
                if not STATE["updating"]:
                    threading.Thread(target=update_engines, daemon=True).start()
                self._json({"ok": True})
            elif p == "/api/quit":
                self._json({"ok": True})
                log("Выключение по кнопке")
                threading.Timer(0.5, lambda: os._exit(0)).start()
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            log(f"ошибка окна: {e}")
            self._json({"error": str(e)}, 500)


def update_engines():
    """Свежие движки: из исходников — через pip, в установленной версии — обновляемые .exe в %APPDATA%."""
    STATE["updating"] = True
    log("Обновляю движки скачивания…")
    try:
        if paths.FROZEN:
            yt = paths.engine_path("yt_dlp")
            if yt:
                r = subprocess.run([yt, "--update-to", "nightly"], capture_output=True, creationflags=paths.NO_WINDOW)
                log("yt-dlp: " + r.stdout.decode("utf-8", "replace").strip().splitlines()[-1:][0] if r.stdout else "yt-dlp: ?")
            gd = os.path.join(paths.ENGINES, "gallery-dl.exe")
            r = requests.get("https://github.com/gdl-org/builds/releases/latest/download/gallery-dl_windows.exe", timeout=120)
            r.raise_for_status()
            if r.content[:2] != b"MZ":
                raise RuntimeError("gallery-dl: скачался не исполняемый файл")
            with open(gd + ".new", "wb") as f:
                f.write(r.content)
            os.replace(gd + ".new", gd)
            log("gallery-dl: обновлён")
        else:
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "--pre", "--disable-pip-version-check",
                                "yt-dlp[default]", "gallery-dl"], capture_output=True, creationflags=paths.NO_WINDOW)
            log("Обновление: " + ("готово" if r.returncode == 0 else r.stderr.decode("utf-8", "replace")[-200:]))
    except Exception as e:  # noqa: BLE001
        log(f"Обновление не удалось: {e}")
    STATE["updating"] = False


def open_window():
    """Окно-приложение без адресной строки: Chrome, иначе Edge, иначе обычный браузер."""
    import webbrowser
    url = f"http://127.0.0.1:{PORT}/"
    local = os.environ.get("LOCALAPPDATA", "")
    for exe in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(exe):
            subprocess.Popen([exe, f"--app={url}", "--window-size=1180,800"])
            return
    webbrowser.open(url)


def already_running():
    try:
        return requests.get(f"http://127.0.0.1:{PORT}/api/ping", timeout=2).json().get("app") == "downloader"
    except Exception:  # noqa: BLE001
        return False


def main():
    if "--login" in sys.argv:  # окно «Войти на сайт» (запускается из настроек)
        import login_window
        Cfg.load()
        login_window.run(sys.argv[sys.argv.index("--login") + 1])
        return
    hidden = "--hidden" in sys.argv
    if already_running():
        if not hidden:
            open_window()
        return
    Cfg.load()
    Q.load()
    log("Скачиватель запущен")
    try:  # вход, сделанный в окне входа, но ещё не сохранённый (окно закрыли раньше времени)
        import login_window
        missing = [s for s in login_window.SITES if s not in C.cookie_sites()]
        got = login_window.import_from_profile(missing) if missing else []
        if got:
            log("Вход подхвачен из окна входа: " + ", ".join(got))
    except Exception as e:  # noqa: BLE001
        log(f"Вход из окна входа не прочитан: {e}")
    for f in Cfg.data["folders"]:
        os.makedirs(f["path"], exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        TL.start()
    except Exception as e:  # noqa: BLE001
        log(f"Telegram MTProto не запущен: {e}")
    threading.Thread(target=Q.worker, daemon=True).start()
    Q.wake.set()
    try:
        remote.start()
    except Exception as e:  # noqa: BLE001
        log(f"Связь с телефоном не запущена: {e}")
    start_bot()
    if not hidden:
        open_window()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
