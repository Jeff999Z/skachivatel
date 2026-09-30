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


PROFILE = os.path.join(paths.DATA_DIR, "webview", "EBWebView")


def _dpapi(blob):
    """Расшифровать ключом учётной записи Windows (только этот пользователь на этом ПК)."""
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    src = BLOB(len(blob), ctypes.create_string_buffer(blob, len(blob)))
    out = BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI: не удалось расшифровать")
    data = ctypes.string_at(out.pbData, out.cbData)
    ctypes.windll.kernel32.LocalFree(out.pbData)
    return data


def profile_cookies(domain):
    """Вход прямо из хранилища окна входа (Edge WebView2): строки cookies.txt для домена."""
    import base64
    import json
    import shutil
    import sqlite3
    import tempfile
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    state = json.load(open(os.path.join(PROFILE, "Local State"), encoding="utf-8"))
    key = _dpapi(base64.b64decode(state["os_crypt"]["encrypted_key"])[5:])  # без префикса «DPAPI»
    tmp = os.path.join(tempfile.mkdtemp(), "c.db")
    shutil.copy2(os.path.join(PROFILE, "Default", "Network", "Cookies"), tmp)
    con = sqlite3.connect(tmp)
    version = int(dict(con.execute("select key, value from meta").fetchall()).get("version", 0))
    lines = []
    rows = con.execute("select host_key, name, path, expires_utc, is_secure, is_httponly, encrypted_value, value "
                       "from cookies where host_key like ?", (f"%{domain}",)).fetchall()
    for host, name, path, exp, secure, httponly, enc, plain in rows:
        value = plain
        if enc and enc[:3] in (b"v10", b"v11"):
            pt = AESGCM(key).decrypt(enc[3:15], enc[15:], None)
            value = (pt[32:] if version >= 24 else pt).decode("utf-8", "replace")  # v24+: в начале хеш домена
        if not value:
            continue
        unix = int(exp / 1_000_000 - 11644473600) if exp else int(time.time()) + 365 * 86400
        lines.append("\t".join([("#HttpOnly_" if httponly else "") + host, "TRUE" if host.startswith(".") else "FALSE",
                                path or "/", "TRUE" if secure else "FALSE", str(unix), name, value]))
    con.close()
    return lines


def import_from_profile(sites=None):
    """Забрать вход из окна входа для сайтов, где он есть, а в cookies.txt ещё нет. Возвращает список сайтов."""
    if not os.path.exists(os.path.join(PROFILE, "Local State")):
        return []
    got = []
    for site in sites or SITES:
        _, domain, marker = SITES[site]
        try:
            lines = profile_cookies(domain)
        except Exception as e:  # noqa: BLE001
            C.log(f"Вход {site}: не прочитался из окна входа ({e})")
            continue
        if any(l.split("\t")[5] == marker for l in lines):
            C.merge_cookies(lines)
            got.append(site)
    return got


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
    if not result["ok"]:  # окно закрыли раньше, чем вход заметили — забираем из хранилища окна
        result["ok"] = site in import_from_profile([site])
    print("OK" if result["ok"] else "CANCEL", flush=True)
    return result["ok"]


if __name__ == "__main__":
    C.Cfg.load()
    sys.exit(0 if run(sys.argv[1]) else 1)
