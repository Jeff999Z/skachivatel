"""
Окно «Войти на сайт»: настоящая страница входа сайта (Edge WebView2) → вход сохраняется в cookies.txt.

Пароль вводится на самом сайте и программой не читается; забираются только cookies входа,
как это делает расширение «Get cookies.txt». Заблокированные сайты открываются через VPN-прокси.
Запуск (отдельным процессом — окну нужен главный поток):  app.py --login instagram
"""
import os
import sys
import threading
import time
from email.utils import parsedate_to_datetime

import core as C
import paths

# сайт → (страница входа, домен, cookie, который появляется только после входа)
SITES = {
    "instagram": ("https://www.instagram.com/accounts/login/", "instagram.com", "sessionid"),
    "twitter": ("https://x.com/i/flow/login", "x.com", "auth_token"),
    "reddit": ("https://www.reddit.com/login/", "reddit.com", "reddit_session"),
    "vk": ("https://vk.com/", "vk.com", "remixsid"),
}
TITLES = {"instagram": "Instagram", "twitter": "X (Twitter)", "reddit": "Reddit", "vk": "ВКонтакте"}


def to_netscape(cookies):
    """Список SimpleCookie из WebView2 → строки формата cookies.txt."""
    lines = []
    for sc in cookies:
        for name, m in sc.items():
            domain = m["domain"] or ""
            try:
                exp = int(parsedate_to_datetime(m["expires"]).timestamp()) if m["expires"] else 0
            except (TypeError, ValueError):
                exp = 0
            if exp <= 0:  # сессионные — пусть живут год (как у расширения)
                exp = int(time.time()) + 365 * 86400
            prefix = "#HttpOnly_" if m["httponly"] else ""
            lines.append("\t".join([prefix + domain, "TRUE" if domain.startswith(".") else "FALSE", m["path"] or "/",
                                    "TRUE" if m["secure"] else "FALSE", str(exp), name, m.value]))
    return lines


def run(site):
    import webview
    url, domain, marker = SITES[site]
    proxy = C.find_proxy() if C.needs_proxy(url) or site in ("instagram", "twitter") else None
    if proxy:  # окно входа идёт через VPN, иначе заблокированный сайт не откроется
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"--proxy-server={proxy}"
    result = {"ok": False}

    def watch(win):
        for _ in range(900):  # до 30 минут на вход
            time.sleep(2)
            try:
                cookies = win.get_cookies() or []
            except Exception:  # noqa: BLE001  окно закрыли
                return
            names = {n: m.value for sc in cookies for n, m in sc.items() if domain in (m["domain"] or "")}
            if names.get(marker):
                lines = [l for l in to_netscape(cookies) if domain in l.split("\t")[0]]
                C.merge_cookies(lines)
                result["ok"] = True
                win.evaluate_js("document.title='Готово — вход сохранён'")
                time.sleep(1.5)
                win.destroy()
                return

    win = webview.create_window(f"Вход: {TITLES[site]} — Скачиватель", url, width=520, height=760)
    storage = os.path.join(paths.DATA_DIR, "webview")  # вход в окне сохраняется между запусками
    webview.start(watch, win, private_mode=False, storage_path=storage)
    print("OK" if result["ok"] else "CANCEL", flush=True)
    return result["ok"]


if __name__ == "__main__":
    C.Cfg.load()
    sys.exit(0 if run(sys.argv[1]) else 1)
