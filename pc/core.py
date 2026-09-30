"""
Движок «Скачивателя»: настройки, люди, папки, очередь, скачивание (yt-dlp, gallery-dl, Telegram),
отправка больших файлов в Telegram (MTProto, до 2 ГБ). Используется приложением (app.py) и ботом (tgbot.py).
"""
import asyncio
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from urllib.parse import urlparse

import paths

HERE = paths.DATA_DIR                      # все личные данные — в %APPDATA%\Скачиватель
CONFIG = paths.data("app_config.json")
HISTORY = paths.data("app_history.json")
QUEUE_FILE = paths.data("app_queue.json")
TG_USER_SESSION = paths.data("tg_user")
TG_BOT_SESSION = paths.data("tg_bot")
FFMPEG_DIR = paths.FFMPEG_DIR
HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, "Downloads", "Скачано")
STARTUP_LNK = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup",
                           "Скачиватель.lnk")
BOT_SEND_LIMIT = 49 * 1024 * 1024      # Bot API: до 50 МБ
MT_SEND_LIMIT = 1990 * 1024 * 1024     # MTProto-бот: до 2 ГБ

URL_RE = re.compile(r"https?://[^\s<>\"']+")
GALLERY_FIRST = ("instagram.com", "pinterest.", "pin.it", "twitter.com", "x.com", "reddit.com",
                 "deviantart.com", "tumblr.com", "artstation.com", "behance.net")
VK_PHOTO = re.compile(r"vk\.(com|ru)/(album|photo|wall)")
TG_POST = re.compile(r"(?:t\.me|telegram\.me)/(?:s/)?(c/)?([\w\d_]+)/(\d+)")
TG_CHANNEL = re.compile(r"(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z][\w\d_]{3,})/?$")
PROGRESS = re.compile(r"\[download\]\s+([\d.]+)% of\s+~?\s*([\d.]+\s*\w+)(?:.*?at\s+([\d.]+\s*\w+/s))?(?:.*?ETA\s+([\d:]+))?")
ITEM = re.compile(r"Downloading item (\d+) of (\d+)")

WHAT = {"best": "⭐ Максимум", "1080": "1080p", "720": "720p", "480": "480p",
        "mp3": "🎵 MP3", "audio": "🎵 Оригинал", "photo": "🖼 Фото", "subs": "📝 Субтитры",
        "incoming": "📥 Файл из Telegram"}
WHERE = {"pc": "💻 На ПК", "tg": "📱 В Telegram", "both": "💻+📱 Оба"}
VIDEO_EXT = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v")


def jload(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jsave(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def safe(name, limit=80):
    return (re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .") or "_")[:limit].rstrip(" .")


def human(n):
    n = float(n or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit in ("Б", "КБ") else f"{n:.1f} {unit}"
        n /= 1024


def hms(sec):
    sec = int(sec or 0)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


# ─────────────────────────── Журнал ───────────────────────────

LOG = deque(maxlen=400)


def log(msg):
    line = f"{time.strftime('%d.%m %H:%M:%S')}  {msg}"
    LOG.append(line)
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        pass


# ─────────────────────────── Настройки ───────────────────────────

class Cfg:
    lock = threading.RLock()
    data = {}
    codes = {}

    DEFAULT_FOLDERS = [
        {"name": "Скачано", "path": BASE},
        {"name": "Видео", "path": os.path.join(HOME, "Videos", "Скачано")},
        {"name": "Музыка", "path": os.path.join(HOME, "Music", "Скачано")},
        {"name": "Курсы", "path": os.path.join(HOME, "Videos", "Курсы")},
    ]

    @classmethod
    def load(cls):
        if not os.path.exists(CONFIG):
            migrate_legacy()
        d = jload(CONFIG, {})
        d.setdefault("users", {})
        d.setdefault("owner", None)
        if not d["owner"] and d["users"]:  # старый бот не знал про владельца — им становится первый
            d["owner"] = next(iter(d["users"]))
        d.setdefault("token", "")
        d.setdefault("api", {})
        d.setdefault("folders", cls.DEFAULT_FOLDERS)
        d.setdefault("pc_defaults", {"what": "best", "folder": 0})
        for u in d["users"].values():
            u.setdefault("defaults", {"what": u.pop("quality", "best") if u.get("quality") != "ask" else "best",
                                      "where": "pc", "folder": 0})
            u.setdefault("quick", False)
        cls.data = d
        cls.save()

    @classmethod
    def save(cls):
        with cls.lock:
            jsave(CONFIG, cls.data)

    @classmethod
    def user(cls, uid):
        return cls.data["users"].get(str(uid))

    @classmethod
    def is_owner(cls, uid):
        return uid is not None and str(uid) == str(cls.data.get("owner"))

    @classmethod
    def add_user(cls, uid, name):
        with cls.lock:
            cls.data["users"][str(uid)] = {"name": name, "added": time.strftime("%d.%m.%Y"),
                                           "defaults": {"what": "best", "where": "pc", "folder": 0}, "quick": False}
            if not cls.data.get("owner"):
                cls.data["owner"] = str(uid)
        cls.save()
        log(f"добавлен: {name}")

    @classmethod
    def remove_user(cls, uid):
        with cls.lock:
            u = cls.data["users"].pop(str(uid), None)
        cls.save()
        log(f"удалён: {u and u['name']}")

    @classmethod
    def new_code(cls):
        code = f"{random.SystemRandom().randint(0, 999999):06d}"
        cls.codes[code] = time.time() + 600
        return code

    @classmethod
    def use_code(cls, text):
        now = time.time()
        for c in [c for c, e in cls.codes.items() if e < now]:
            del cls.codes[c]
        code = re.sub(r"\D", "", text or "")
        if len(code) == 6 and code in cls.codes:
            del cls.codes[code]
            return True
        return False

    @classmethod
    def folder(cls, idx):
        f = cls.data["folders"]
        return f[idx]["path"] if 0 <= int(idx) < len(f) else f[0]["path"]


def migrate_legacy():
    """Первая установка: забираем настройки из прежней «портативной» папки, если она есть."""
    # SKACHIVATEL_LEGACY задаёт папку явно (пустое значение — не переносить ничего)
    legacy = os.environ.get("SKACHIVATEL_LEGACY")
    for old in ([legacy] if legacy is not None else [os.path.join(HOME, "Desktop", "PinterestDownloader")]):
        if not old or not os.path.exists(os.path.join(old, "app_config.json")):
            continue
        for name in ("app_config.json", "app_history.json", "cookies.txt", "tg_user.session", "tg_bot.session"):
            src = os.path.join(old, name)
            if os.path.exists(src) and not os.path.exists(paths.data(name)):
                shutil.copy2(src, paths.data(name))
        log(f"Настройки перенесены из {old}")
        return


def autostart_on():
    return os.path.exists(STARTUP_LNK)


def set_autostart(on):
    if not on:
        if os.path.exists(STARTUP_LNK):
            os.remove(STARTUP_LNK)
        return
    target, argv = paths.launch_command(hidden=True)
    argv = argv.replace("'", "''")
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{STARTUP_LNK}');"
          f"$s.TargetPath='{target}';$s.Arguments='{argv}';"
          f"$s.WorkingDirectory='{paths.INSTALL_DIR}';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=subprocess.CREATE_NO_WINDOW)


# ─────────────────────────── Telegram (MTProto): вход и большие файлы ───────────────────────────

class TL:
    """Отдельный поток с asyncio: клиент-пользователь (каналы) и клиент-бот (отправка до 2 ГБ)."""
    loop = None
    user = None
    bot = None
    login_state = {}

    @classmethod
    def start(cls):
        cls.loop = asyncio.new_event_loop()
        threading.Thread(target=cls.loop.run_forever, daemon=True).start()
        cls.run(cls._connect(), timeout=60)

    @classmethod
    def run(cls, coro, timeout=None):
        return asyncio.run_coroutine_threadsafe(coro, cls.loop).result(timeout)

    @classmethod
    def api(cls):
        a = Cfg.data.get("api") or {}
        return (int(a["api_id"]), a["api_hash"]) if a.get("api_id") and a.get("api_hash") else None

    @classmethod
    async def _connect(cls):
        from telethon import TelegramClient
        api = cls.api()
        if not api:
            return
        try:
            if os.path.exists(TG_USER_SESSION + ".session"):
                c = TelegramClient(TG_USER_SESSION, *api)
                await c.connect()
                if await c.is_user_authorized():
                    cls.user = c
                    me = await c.get_me()
                    log(f"Telegram-аккаунт: {me.first_name}")
                else:
                    await c.disconnect()
            if Cfg.data.get("token"):
                b = TelegramClient(TG_BOT_SESSION, *api)
                await b.start(bot_token=Cfg.data["token"])
                cls.bot = b
                log("Отправка больших файлов (до 2 ГБ): включена")
        except Exception as e:  # noqa: BLE001
            log(f"Telegram MTProto: {e}")

    # — вход из окна приложения —
    @classmethod
    def login_send_code(cls, api_id, api_hash, phone):
        from telethon import TelegramClient
        Cfg.data["api"] = {"api_id": int(api_id), "api_hash": api_hash.strip()}
        Cfg.save()

        async def go():
            c = TelegramClient(TG_USER_SESSION, int(api_id), api_hash.strip())
            await c.connect()
            sent = await c.send_code_request(phone)
            cls.login_state = {"client": c, "phone": phone, "hash": sent.phone_code_hash}
        cls.run(go(), 60)

    @classmethod
    def login_code(cls, code):
        from telethon.errors import SessionPasswordNeededError
        st = cls.login_state

        async def go():
            try:
                await st["client"].sign_in(st["phone"], code, phone_code_hash=st["hash"])
                return "ok"
            except SessionPasswordNeededError:
                return "password"
        r = cls.run(go(), 60)
        if r == "ok":
            cls._finish_login()
        return r

    @classmethod
    def login_password(cls, pwd):
        cls.run(cls.login_state["client"].sign_in(password=pwd), 60)
        cls._finish_login()

    @classmethod
    def _finish_login(cls):
        cls.user = cls.login_state.pop("client")
        cls.login_state = {}
        if not cls.bot:
            cls.run(cls._connect(), 60)
        log("Вход в Telegram-аккаунт выполнен")

    @classmethod
    def logout(cls):
        if cls.user:
            cls.run(cls.user.log_out(), 30)
            cls.user = None

    @classmethod
    def status(cls):
        info = {"api": bool(cls.api()), "user": None, "big": bool(cls.bot)}
        if cls.user:
            try:
                me = cls.run(cls.user.get_me(), 15)
                info["user"] = f"{me.first_name or ''} {('@' + me.username) if me.username else ''}".strip()
            except Exception:  # noqa: BLE001
                info["user"] = "?"
        return info

    # — отправка файла в чат ботом (MTProto, до 2 ГБ) —
    @classmethod
    def send_big(cls, chat, path, caption, progress=None):
        async def go():
            def cb(done, total):
                if progress:
                    progress(done, total)
            await cls.bot.send_file(int(chat), path, caption=caption[:1000], progress_callback=cb,
                                    supports_streaming=path.lower().endswith(".mp4"),
                                    force_document=not path.lower().endswith((".mp4", ".mp3", ".m4a", ".jpg", ".png")))
        cls.run(go())

    # — скачивание из каналов —
    @classmethod
    def download(cls, job, out_root, on_progress):
        return cls.run(cls._download(job, out_root, on_progress))

    @classmethod
    async def _download(cls, job, out_root, on_progress):
        from telethon.tl.types import MessageMediaWebPage
        c = cls.user
        src = job.get("tg")
        if src:
            peer = src["peer"]
            entity = await c.get_entity(int(peer) if str(peer).lstrip("-").isdigit() else peer)
            ids, whole = [int(src["msg"])], False
        else:
            m = TG_POST.search(job["url"])
            if m:
                entity = await c.get_entity(int("-100" + m.group(2)) if m.group(1) else m.group(2))
                ids, whole = [int(m.group(3))], False
            else:
                entity = await c.get_entity(TG_CHANNEL.search(job["url"]).group(1))
                ids, whole = None, True
        title = safe(getattr(entity, "title", None) or getattr(entity, "username", None) or "Telegram")
        job["title"] = job.get("title") or title
        out = os.path.join(out_root, "Telegram", title)
        os.makedirs(out, exist_ok=True)
        if whole:
            msgs = [m async for m in c.iter_messages(entity) if m.media and not isinstance(m.media, MessageMediaWebPage)]
        else:
            msgs = [m for m in await c.get_messages(entity, ids=ids) if m]
            if msgs and msgs[0].grouped_id:
                around = await c.get_messages(entity, ids=list(range(ids[0] - 10, ids[0] + 11)))
                msgs = [m for m in around if m and m.grouped_id == msgs[0].grouped_id]
        msgs = [m for m in msgs if m.media and not isinstance(m.media, MessageMediaWebPage)]
        what = job["what"]
        if what in ("mp3", "audio"):
            msgs = [m for m in msgs if m.audio or m.voice or m.video]
        elif what == "photo":
            msgs = [m for m in msgs if m.photo]
        elif what == "video":
            msgs = [m for m in msgs if m.video]
        if not msgs:
            return [], "В посте нет подходящих файлов."
        files = []
        for n, m in enumerate(msgs, 1):
            if job.get("cancel"):
                break
            name = m.file.name or f"{m.id}{m.file.ext or ''}"
            path = os.path.join(out, safe(f"{m.id}_{name}", 150))
            if not (os.path.exists(path) and os.path.getsize(path) == (m.file.size or -1)):
                def cb(done, total, n=n):
                    if job.get("cancel"):
                        raise asyncio.CancelledError()
                    on_progress(done * 100 / total if total else 0, f"{human(done)} из {human(total)}"
                                + (f" · файл {n} из {len(msgs)}" if len(msgs) > 1 else ""))
                await c.download_media(m, file=path, progress_callback=cb)
            files.append(path)
        return files, ""


# ─────────────────────────── Разбор ссылки (для карточки) ───────────────────────────

def is_tg(url):
    return bool(url and (TG_POST.search(url) or TG_CHANNEL.search(url)))


LANG_NAMES = {"ru": "Русские", "en": "Английские", "uk": "Украинские", "de": "Немецкие", "es": "Испанские",
              "fr": "Французские", "it": "Итальянские", "zh": "Китайские", "ja": "Японские", "ko": "Корейские"}


# ─────────────────────────── Прокси VPN (для заблокированных сайтов) ───────────────────────────

BLOCKED = ("youtube.com", "youtu.be", "ytimg.com", "googlevideo.com", "instagram.com", "facebook.com", "fb.watch", "twitter.com", "x.com",
           "twimg.com", "linkedin.com", "discord", "patreon.com", "vimeo.com", "dailymotion.com", "twitch.tv")
# типичные локальные прокси VPN-клиентов: Happ/v2rayN/xray, Clash, sing-box/Hiddify, прочие
PROXY_CANDIDATES = [("socks5", 10808), ("http", 10809), ("http", 7890), ("socks5", 7891), ("socks5", 2080),
                    ("http", 2081), ("socks5", 1080), ("http", 8080), ("socks5", 12334)]
_proxy_cache = {"at": 0, "url": None}


def find_proxy():
    """Прокси из настроек, иначе — найденный локальный прокси VPN (проверка раз в минуту)."""
    manual = (Cfg.data.get("proxy") or "auto").strip()
    if manual == "off":
        return None
    if manual != "auto":
        return manual
    if time.time() - _proxy_cache["at"] < 60:
        return _proxy_cache["url"]
    import socket
    found = None
    for scheme, port in PROXY_CANDIDATES:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                found = f"{scheme}://127.0.0.1:{port}"
                break
    _proxy_cache.update(at=time.time(), url=found)
    return found


def proxy_for(module, proxy):
    """gallery-dl сам ищет адрес сайта, если прокси socks5:// — а заблокированный адрес не находится.
    socks5h:// — адрес узнаёт VPN. yt-dlp делает так и с socks5://."""
    if proxy and module == "gallery_dl" and proxy.startswith("socks5://"):
        return "socks5h://" + proxy[len("socks5://"):]
    return proxy


def needs_proxy(url):
    host = urlparse(url or "").netloc.lower()
    return any(host == d or host.endswith("." + d) or d in host for d in BLOCKED)


def network_error(text):
    t = (text or "").lower()
    return any(w in t for w in ("getaddrinfo", "failed to resolve", "timed out", "connection reset",
                                "connection refused", "remote end closed", "unable to connect", "10060", "10054"))


COOKIES = os.path.join(HERE, "cookies.txt")
LOGIN_ONLY = {"instagram": ("instagram.com",), "twitter": ("twitter.com", "x.com"), "reddit": ("reddit.com",)}


def cookie_args(tool):
    """Вход на сайты (Instagram, X, Reddit, закрытые VK…) — файл cookies.txt из расширения браузера."""
    if not os.path.exists(COOKIES) or os.path.getsize(COOKIES) < 50:
        return []
    return ["--cookies", COOKIES] if tool == "yt" else ["-C", COOKIES]


def cookie_sites():
    """Для каких сайтов есть вход в cookies.txt."""
    if not os.path.exists(COOKIES):
        return []
    sites = set()
    with open(COOKIES, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                sites.add(line.split("\t")[0].lstrip("."))
    keys = {"instagram": "instagram", "twitter": "twitter", "x.com": "twitter", "reddit": "reddit", "vk.com": "vk",
            "vkvideo": "vk", "tiktok": "tiktok", "facebook": "facebook", "youtube": "youtube", "pinterest": "pinterest",
            "ok.ru": "ok", "dzen": "dzen", "boosty": "boosty", "patreon": "patreon"}
    return sorted({v for s in sites for k, v in keys.items() if k in s})


def merge_cookies(new_lines):
    """Добавить вход в cookies.txt: новые строки заменяют старые того же сайта и имени."""
    old = []
    if os.path.exists(COOKIES):
        old = [l for l in open(COOKIES, encoding="utf-8").read().splitlines() if l.strip() and not l.startswith("# ")]
    merged = {}
    for l in old + list(new_lines):
        f = l.split("\t")
        if len(f) >= 7:
            merged[(f[0].replace("#HttpOnly_", "").lstrip("."), f[5])] = l
    with open(COOKIES, "w", encoding="utf-8") as fh:
        fh.write("# Netscape HTTP Cookie File\n" + "\n".join(merged.values()) + "\n")
    log("Вход на сайты: " + (", ".join(cookie_sites()) or "нет"))
    return cookie_sites()


def _run_json(args, timeout=45, proxy=None):
    if proxy:
        args = args[:1] + ["--proxy", proxy_for(args[0], proxy)] + args[1:]
    r = subprocess.run(paths.cmd(args), capture_output=True, timeout=timeout,
                       creationflags=paths.NO_WINDOW, env=paths.tool_env())
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def _fsize(f, duration):
    s = f.get("filesize") or f.get("filesize_approx")
    if not s and f.get("tbr") and duration:
        s = f["tbr"] * 125 * duration  # кбит/с → байты
    return s or 0


def probe(url):
    """
    Что есть по ссылке. kind: video | audio | playlist | gallery | telegram | error.
    Для видео — список качеств с примерным размером, аудио, субтитры, обложка.
    """
    if is_tg(url):
        return probe_tg(url)
    import pinterest
    if pinterest.parse(url):  # профиль или доска — «все доски по папкам»
        try:
            return pinterest.probe(url, find_proxy() if needs_proxy(url) else None)
        except Exception as e:  # noqa: BLE001
            return {"kind": "error", "title": "Pinterest", "error": f"Pinterest не ответил: {e}"}
    host = urlparse(url).netloc.lower()
    gallery = any(d in host for d in GALLERY_FIRST) or VK_PHOTO.search(url)
    # сайты, которые без входа ничего не отдают — не ждём минуту, а сразу подсказываем
    need = next((k for k, hosts in LOGIN_ONLY.items() if any(h in host for h in hosts)), None)
    # отдельные посты и Reels часто открываются и без входа (видео) — пробуем; профили целиком — нет
    single = re.search(r"/(p|reel|reels|tv|status)/[\w-]+", urlparse(url).path)
    if need and not single and need not in cookie_sites():
        return {"kind": "error", "title": host, "error": explain(["login"])}
    if "threads." in host:
        return {"kind": "error", "title": host, "error": "Threads пока не поддерживается ни одним из движков скачивания."}
    info = None
    proxy = find_proxy() if needs_proxy(url) else None
    if not ("pinterest" in host or "pin.it" in host):
        try:
            args = ["yt_dlp", "-J", "--flat-playlist", "--no-warnings"] + cookie_args("yt") + [url]
            code, out, err = _run_json(args, proxy=proxy)
            if code and not proxy and network_error(err) and find_proxy():  # напрямую не пускают — через VPN
                proxy = find_proxy()
                code, out, err = _run_json(args, proxy=proxy)
            if code == 0:
                info = json.loads(out)
            elif not gallery:  # yt-dlp не справился (бывает при поломках сайта) — пробуем gallery-dl
                g = probe_gallery(url, host)
                if g.get("kind") != "error":
                    return g
                return {"kind": "error", "title": host, "error": explain(err.splitlines()[-3:], proxy)}
        except (subprocess.TimeoutExpired, ValueError):
            pass
    if info and info.get("_type") == "playlist":
        return {"kind": "playlist", "title": info.get("title") or host, "uploader": info.get("uploader"),
                "count": len(info.get("entries") or []), "thumb": _thumb(info)}
    if info and (info.get("formats") or info.get("url")):
        return _video_info(info, host)
    if gallery or not info:
        return probe_gallery(url, host)
    return {"kind": "error", "title": host, "error": "Не нашёл, что тут скачивать."}


def _thumb(j):
    thumbs = [t for t in j.get("thumbnails") or [] if t.get("url")]
    if thumbs:
        best = max(thumbs, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0) or t.get("preference") or 0)
        return best["url"]
    return j.get("thumbnail")


def _video_info(j, host):
    dur = j.get("duration") or 0
    fmts = j.get("formats") or [j]
    audios = [f for f in fmts if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    best_a = max(audios, key=lambda f: (f.get("abr") or f.get("tbr") or 0), default=None)
    a_size = _fsize(best_a, dur) if best_a else 0
    heights = {}
    for f in fmts:
        h = f.get("height")
        if not h or f.get("vcodec") in (None, "none"):
            continue
        size = _fsize(f, dur)
        if f.get("acodec") in (None, "none"):
            size += a_size  # видео без звука — звук докачается отдельно
        cur = heights.get(h)
        if not cur or (f.get("tbr") or 0) > cur["tbr"]:
            heights[h] = {"h": h, "size": size, "tbr": f.get("tbr") or 0, "fps": f.get("fps")}
    video = sorted(heights.values(), key=lambda x: -x["h"])
    subs = []
    for key, auto in (("subtitles", False), ("automatic_captions", True)):
        langs = j.get(key) or {}
        for lang in sorted(langs):
            base = lang.split("-")[0]
            if auto and base not in ("ru", "en", "uk") or lang.endswith("-orig") and auto and base != "ru":
                continue  # автоперевод на все языки — показываем только основные
            subs.append({"lang": lang, "name": LANG_NAMES.get(base, lang), "auto": auto})
    audio = None
    if best_a or video:
        abr = (best_a or {}).get("abr") or 128
        audio = {"abr": round(abr), "ext": (best_a or {}).get("ext") or "m4a",
                 "size": a_size or abr * 125 * dur, "mp3": 320 * 125 * dur}
    return {"kind": "video" if video else "audio", "title": j.get("title") or host,
            "uploader": j.get("uploader") or j.get("channel") or j.get("creator"), "duration": dur,
            "thumb": _thumb(j), "video": video, "audio": audio, "subs": subs[:12],
            "site": j.get("extractor_key") or host}


def probe_gallery(url, host):
    proxy = find_proxy() if needs_proxy(url) else None
    # -G — раскрыть промежуточные ссылки (профиль → посты → файлы); первые 200 для подсчёта
    args = ["gallery_dl", "-G", "--range", "1-200", "-R", "1", "--no-colors"] + cookie_args("gallery") + [url]
    try:
        code, out, err = _run_json(args, timeout=60, proxy=proxy)
        if code and not proxy and (network_error(err) or "blocked" in err.lower()) and find_proxy():
            code, out, err = _run_json(args, timeout=60, proxy=find_proxy())
    except subprocess.TimeoutExpired:
        return {"kind": "error", "title": host,
                "error": "Сайт не ответил за минуту — похоже, без входа в аккаунт он ничего не отдаёт. " + explain(["login"])}
    urls = [l.strip() for l in out.splitlines() if l.strip().startswith(("http", "ytdl:"))]
    if not urls:
        return {"kind": "error", "title": host, "error": explain(err.splitlines()[-3:]) if err else "Не нашёл файлов по ссылке."}
    urls = [u for u in urls if not re.match(r"https?://[^/]+/[^/]+/(posts|timeline|reels|tagged|stories)/?$", u)]
    if not urls:  # только ссылки на разделы профиля — значит, сайт не пустил дальше (нужен вход)
        return {"kind": "error", "title": host, "error": explain(err.splitlines()[-3:] + ["login"])}
    photos = sum(1 for u in urls if re.search(r"\.(jpe?g|png|webp|gif|heic)(\?|$)", u, re.I))
    videos = sum(1 for u in urls if re.search(r"\.(mp4|webm|mov|m3u8)(\?|$)|/video/tos/|/aweme/v1/play/|video\.twimg|"
                                              r"/v/t\d+\.\d+-\d+/.+\.mp4", u, re.I) or u.startswith("ytdl:"))
    return {"kind": "gallery", "title": host.replace("www.", ""), "photos": photos, "videos": videos,
            "other": len(urls) - photos - videos, "thumb": next((u for u in urls if re.search(r"\.(jpe?g|png|webp)", u, re.I)), None)}


def probe_tg(url=None, tg=None):
    if not TL.user:
        return {"kind": "telegram", "title": "Пост из Telegram", "need_login": True, "files": []}

    async def go():
        c = TL.user
        if tg:
            peer = tg["peer"]
            entity = await c.get_entity(int(peer) if str(peer).lstrip("-").isdigit() else peer)
            ids = [int(tg["msg"])]
        else:
            m = TG_POST.search(url)
            if not m:
                entity = await c.get_entity(TG_CHANNEL.search(url).group(1))
                n = 0
                async for msg in c.iter_messages(entity, limit=3000):
                    if msg.file:
                        n += 1
                return {"kind": "telegram", "title": getattr(entity, "title", "Канал"), "channel": True, "count": n, "files": []}
            entity = await c.get_entity(int("-100" + m.group(2)) if m.group(1) else m.group(2))
            ids = [int(m.group(3))]
        msgs = [x for x in await c.get_messages(entity, ids=ids) if x]
        if msgs and msgs[0].grouped_id:
            around = await c.get_messages(entity, ids=list(range(ids[0] - 10, ids[0] + 11)))
            msgs = [x for x in around if x and x.grouped_id == msgs[0].grouped_id]
        files = []
        for x in msgs:
            if x.file:
                kind = "video" if x.video else "audio" if (x.audio or x.voice) else "photo" if x.photo else "file"
                files.append({"name": x.file.name or f"{x.id}{x.file.ext or ''}", "size": x.file.size or 0,
                              "kind": kind, "dur": getattr(x.file, "duration", None)})
        text = (msgs[0].message or "").split("\n")[0][:100] if msgs else ""
        return {"kind": "telegram", "title": getattr(entity, "title", "Telegram"), "text": text, "files": files}
    try:
        return TL.run(go(), 60)
    except Exception as e:  # noqa: BLE001
        return {"kind": "error", "title": "Telegram", "error": f"Не открылся пост: {e}"}


def label(what):
    """Подпись к выбранному режиму."""
    if what in WHAT:
        return WHAT[what]
    if what.isdigit():
        return f"🎬 {what}p"
    if what.startswith("subs:"):
        return f"📝 Субтитры ({what[5:]})"
    return {"thumb": "🖼 Обложка", "video": "🎬 Все видео", "all": "📦 Всё"}.get(what, what)


def describe(info):
    if not info:
        return "🔎 Смотрю, что есть по ссылке…"
    if info.get("kind") == "error":
        return f"❌ {esc(info.get('error', 'Не получилось открыть ссылку'))}"
    parts = [f"<b>{esc(info.get('title', ''))[:150]}</b>"]
    meta = [esc(x) for x in (info.get("uploader"), hms(info["duration"]) if info.get("duration") else None) if x]
    if meta:
        parts.append(" · ".join(meta))
    avail = []
    k = info.get("kind")
    if info.get("video"):
        avail.append(f"видео до {info['video'][0]['h']}p")
    if info.get("audio"):
        avail.append("аудио")
    if info.get("subs"):
        avail.append("субтитры (" + ", ".join(dict.fromkeys(s["lang"].split("-")[0] for s in info["subs"])) + ")")
    if info.get("thumb") and k in ("video", "audio"):
        avail.append("обложка")
    if k == "playlist":
        avail.append(f"плейлист: {info.get('count', '?')} шт.")
    if k == "pinterest":
        avail.append(f"{info['pins']} пинов" + ("" if info.get("board") else f" на {info['boards']} досках")
                     + " — в оригинальном качестве, по папкам")
    if k == "gallery":
        if info.get("photos"):
            avail.append(f"фото: {info['photos']}")
        if info.get("videos"):
            avail.append(f"видео: {info['videos']}")
    if k == "telegram":
        if info.get("need_login"):
            avail.append("нужен вход в Telegram в приложении на ПК")
        elif info.get("channel"):
            avail.append(f"канал, файлов: {info.get('count')}")
        else:
            if info.get("text"):
                parts.append(esc(info["text"]))
            for f in info.get("files", [])[:6]:
                avail.append(f"{esc(f['name'])[:50]} ({human(f['size'])})")
    if avail:
        parts.append("Доступно: " + "; ".join(avail))
    return "\n".join(parts)


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ─────────────────────────── Очередь и скачивание ───────────────────────────

# ─────────────────────────── Защита аккаунта Instagram ───────────────────────────
# Ведём себя как человек: паузы между запросами, не больше IG_LIMIT_HOUR ссылок в час,
# а если Instagram просит подождать — перерыв IG_COOLDOWN (остальные сайты качаются как обычно).
IG_LIMIT_HOUR = 40
IG_COOLDOWN = 2 * 3600
IG = {"times": deque(), "cool_until": 0}
IG_GALLERY_OPTS = ["-o", "extractor.instagram.sleep-request=6.0-12.0", "--sleep", "2-5"]
IG_YT_OPTS = ["--sleep-requests", "3", "--sleep-interval", "2", "--max-sleep-interval", "6"]
IG_STOP = ("please wait a few minutes", "429", "too many requests", "challenge_required", "checkpoint",
           "feedback_required", "rate limit", "ratelimit")


def ig_host(url):
    return "instagram.com" in urlparse(url or "").netloc.lower()


def ig_delay():
    """Сколько секунд ещё ждать, прежде чем брать следующую ссылку Instagram."""
    now = time.time()
    while IG["times"] and now - IG["times"][0] > 3600:
        IG["times"].popleft()
    d = max(IG["cool_until"] - now, 0)
    if len(IG["times"]) >= IG_LIMIT_HOUR:
        d = max(d, 3600 - (now - IG["times"][0]))
    return d


class Q:
    lock = threading.RLock()
    jobs = []            # очередь + текущее (status queued/running)
    history = []
    wake = threading.Event()
    listeners = []       # функции(job) — бот обновляет сообщения
    proc = None

    @classmethod
    def load(cls):
        cls.jobs = [j for j in jload(QUEUE_FILE, []) if j.get("status") in ("queued", "running")]
        for j in cls.jobs:
            j["status"] = "queued"
        cls.history = jload(HISTORY, [])

    @classmethod
    def save(cls):
        with cls.lock:
            jsave(QUEUE_FILE, cls.jobs)

    @classmethod
    def add(cls, url=None, what="best", where="pc", folder=0, who="pc", chat=None, tg=None, title=None, mid=None,
            thumb=None, incoming=None):
        job = {"id": f"{time.time():.4f}".replace(".", ""), "url": url, "what": what, "where": where,
               "folder": int(folder), "who": str(who), "chat": chat, "tg": tg, "title": title, "mid": mid,
               "thumb": thumb, "incoming": incoming,
               "status": "queued", "pct": 0, "text": "в очереди", "files": [], "size": 0, "error": "",
               "created": time.strftime("%d.%m %H:%M")}
        with cls.lock:
            cls.jobs.append(job)
            cls.save()
        cls.wake.set()
        cls.notify(job)
        log(f"в очередь: {url or 'пост Telegram'} [{what} → {where}] от {who_name(who)}")
        return job

    @classmethod
    def cancel(cls, jid):
        with cls.lock:
            for j in cls.jobs:
                if j["id"] == jid:
                    j["cancel"] = True
                    if j["status"] == "running" and cls.proc:
                        cls.proc.kill()
                    elif j["status"] == "queued":
                        j["status"] = "cancelled"
                        j["text"] = "отменено"
                        cls.jobs.remove(j)
                        cls.save()
                        cls.notify(j)
                    return True
        return False

    @classmethod
    def notify(cls, job):
        for f in list(cls.listeners):
            try:
                f(job)
            except Exception as e:  # noqa: BLE001
                log(f"уведомление: {e}")

    @classmethod
    def progress(cls, job, pct, text):
        job["pct"], job["text"] = round(pct, 1), text
        cls.notify(job)

    @classmethod
    def worker(cls):
        while True:
            cls.wake.wait(5)
            cls.wake.clear()
            while True:
                with cls.lock:
                    now, job = time.time(), None
                    for j in cls.jobs:
                        if j["status"] != "queued" or j.get("not_before", 0) > now:
                            continue
                        d = ig_delay() if ig_host(j.get("url")) else 0
                        if d > 0:  # Instagram ждёт — берём следующую ссылку с другого сайта
                            j["not_before"] = now + d
                            j["text"] = f"пауза для Instagram (защита аккаунта) — начну через {int(d // 60) + 1} мин"
                            cls.notify(j)
                            continue
                        job = j
                        break
                    if not job:
                        break
                    job["status"] = "running"
                    if ig_host(job.get("url")):
                        IG["times"].append(now)
                try:
                    run_job(job)
                except Exception as e:  # noqa: BLE001
                    job["status"], job["error"] = "error", str(e)
                    log(f"ошибка: {e}")
                with cls.lock:
                    if job in cls.jobs:
                        cls.jobs.remove(job)
                    cls.save()
                    if job["status"] in ("done", "error"):
                        h = {k: job[k] for k in ("id", "url", "what", "where", "who", "title", "files", "size",
                                                 "status", "error", "created")}
                        h["finished"] = time.strftime("%d.%m %H:%M")
                        cls.history.insert(0, h)
                        del cls.history[500:]
                        jsave(HISTORY, cls.history)
                cls.notify(job)


def who_name(who):
    if who in ("pc", None):
        return "ПК"
    if str(who).startswith("phone:"):
        d = Cfg.data.get("devices", {}).get(str(who)[6:])
        return f"📱 {d['name']}" if d else "📱 телефон"
    u = Cfg.user(who)
    return u["name"] if u else str(who)


def decode_line(raw):
    """Строка вывода движка: UTF-8 или (gallery-dl.exe так печатает пути) кодировка Windows."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        import locale
        return raw.decode(locale.getpreferredencoding(False) or "cp1251", "replace")


def run_tool(args, on_line):
    p = subprocess.Popen(paths.cmd(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         env=paths.tool_env(), creationflags=paths.NO_WINDOW)
    Q.proc = p
    tail = []
    for raw in p.stdout:
        line = decode_line(raw).rstrip()
        tail = (tail + [line])[-40:]
        on_line(line)
    code = p.wait()
    Q.proc = None
    return code, tail


TEMP_DIR = ".загрузка"   # скрытая служебная папка для кусков (видео и звук отдельно) до склейки


def ytdlp_args(url, folder, what):
    # во время скачивания id в имени нужен (чтобы разные видео с одинаковым названием не путались),
    # после — переименовываем в чистое название (clean_names)
    tpl = os.path.join(folder, "%(extractor_key)s", "%(title).150B [%(id)s].%(ext)s")
    tmp = os.path.join(folder, TEMP_DIR)
    os.makedirs(tmp, exist_ok=True)
    subprocess.run(["attrib", "+h", tmp], creationflags=subprocess.CREATE_NO_WINDOW)
    a = ["yt_dlp", url, "-o", tpl, "-P", f"temp:{tmp}", "--newline", "--no-colors", "--windows-filenames"] + cookie_args("yt") + [
         "--ffmpeg-location", FFMPEG_DIR, "--print", "after_move:ФАЙЛ:%(filepath)s", "--no-quiet",
         "--progress", "--retries", "10", "--fragment-retries", "10", "--concurrent-fragments", "4"]
    if what == "subs" or what.startswith("subs:"):
        langs = what[5:] if what.startswith("subs:") else "ru.*,en.*,ru,en"
        return a[:a.index("--print")] + ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", langs,
                                         "--convert-subs", "srt", "--no-quiet"]
    a += ["--embed-metadata", "--embed-thumbnail"]
    if what == "mp3":
        return a + ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0"]
    if what == "audio":
        return a + ["-f", "ba/b", "-x"]
    if what.isdigit():  # точное качество: сначала ровно такая высота, иначе ближайшая ниже
        h = what
        return a + ["-f", f"bv*[height={h}]+ba/b[height={h}]/bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b",
                    "--merge-output-format", "mp4/mkv"]
    return a + ["-f", "bv*+ba/b", "-S", "res,fps,vbr,abr", "--merge-output-format", "mp4/mkv"]


WIDE = {"｜": "-", "：": " -", "？": "", "＂": "'", "／": "-", "＼": "-", "＊": "", "＜": "(", "＞": ")", "⧸": "-", "⧹": "-"}


def clean_names(files):
    """«Название [id].mp4» → «Название.mp4»; широкие символы → обычные; при совпадении — «(2)»."""
    out = []
    for f in files:
        folder, name = os.path.split(f)
        stem, ext = os.path.splitext(name)
        # « [id]» в конце — или перед языком субтитров: «Название [id].ru» → «Название.ru»
        new = re.sub(r"\s*\[[\w-]{6,}\](?=(\.[\w-]{2,12})?$)", "", stem)
        for a, b in WIDE.items():
            new = new.replace(a, b)
        new = re.sub(r"\s{2,}", " ", new).strip(" .-") or stem
        target = os.path.join(folder, new + ext)
        n = 2
        while os.path.exists(target) and os.path.normcase(target) != os.path.normcase(f):
            target = os.path.join(folder, f"{new} ({n}){ext}")
            n += 1
        try:
            if os.path.normcase(target) != os.path.normcase(f):
                os.replace(f, target)
            out.append(target)
        except OSError:
            out.append(f)
    return out


def download_thumb(job, base):
    """Обложка в максимальном размере — прямо по ссылке из разбора."""
    import requests
    url = job.get("thumb")
    if not url:
        return []
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
    except requests.RequestException:
        px = find_proxy()
        if not px:
            raise
        px = px.replace("socks5://", "socks5h://")
        r = requests.get(url, timeout=60, proxies={"http": px, "https": px})
        r.raise_for_status()
    ext = {"image/png": ".png", "image/webp": ".webp"}.get(r.headers.get("content-type", "").split(";")[0], ".jpg")
    out = os.path.join(base, "Обложки")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, safe(job.get("title") or "обложка", 120) + ext)
    with open(path, "wb") as f:
        f.write(r.content)
    return [path]


def explain(tail, proxy=None):
    t = " ".join(tail).lower()
    if "drm" in t:
        return "Видео защищено от копирования (DRM) — такое не скачиваю."
    if network_error(t):
        if proxy:
            return "Сайт не открылся даже через VPN — проверьте, что VPN подключён, и повторите."
        return "Сайт заблокирован, а VPN на ПК не найден. Включите VPN (Happ и т.п.) и повторите."
    if any(w in t for w in ("login", "cookies", "private", "sign in", "authorization", "logged-in", "authrequired",
                            "requested user could not be found", "blocked by network security", "rate-limit")):
        return ("Сайт отдаёт это только после входа в аккаунт. Добавьте вход: приложение на ПК → "
                "Настройки → «Вход на сайты» (файл cookies.txt).")
    if "unsupported url" in t or "no suitable" in t:
        return "Этот сайт или тип ссылки не поддерживается."
    if "404" in t or "not found" in t or "unavailable" in t or "removed" in t:
        return "Страница не найдена или удалена."
    return tail[-1][:300] if tail else "неизвестная ошибка"


BOT_GETFILE_LIMIT = 20 * 1024 * 1024   # Bot API: бот скачивает присланные файлы до 20 МБ


def receive_file(job, base):
    """Файл, который прислали боту. С входом в Telegram (MTProto) — любого размера, без — до 20 МБ."""
    import requests
    inc = job["incoming"]
    out = os.path.join(base, "Telegram", "Присланные боту")
    os.makedirs(out, exist_ok=True)
    stem, ext = os.path.splitext(safe(inc.get("name") or f"{inc['msg']}{inc.get('ext', '')}", 150))
    path, n = os.path.join(out, stem + ext), 2
    while os.path.exists(path):
        path, n = os.path.join(out, f"{stem} ({n}){ext}"), n + 1
    size = inc.get("size") or 0

    def prog(done, total):
        if job.get("cancel"):
            raise asyncio.CancelledError()
        Q.progress(job, done * 100 / total if total else 0, f"{human(done)} из {human(total or size)}")

    if TL.bot:
        async def go():
            msg = await TL.bot.get_messages(int(inc["chat"]), ids=int(inc["msg"]))
            await TL.bot.download_media(msg, file=path, progress_callback=prog)
        try:
            TL.run(go())
        except asyncio.CancelledError:
            return [], ""
        return ([path] if os.path.exists(path) else []), ""
    if size > BOT_GETFILE_LIMIT:
        return [], (f"Файл {human(size)} — больше 20 МБ. Такие бот сможет забирать после входа в Telegram "
                    "(ключи с my.telegram.org → Настройки → Вход в Telegram-аккаунт).")
    token = Cfg.data.get("token")
    r = requests.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": inc["file_id"]}, timeout=60).json()
    if not r.get("ok"):
        return [], "Telegram не отдал файл: " + str(r.get("description"))
    with requests.get(f"https://api.telegram.org/file/bot{token}/{r['result']['file_path']}", stream=True, timeout=300) as resp:
        resp.raise_for_status()
        done = 0
        with open(path, "wb") as f:
            for chunk in resp.iter_content(1 << 16):
                f.write(chunk)
                done += len(chunk)
                prog(done, size)
    return [path], ""


def run_job(job):
    what, where = job["what"], job["where"]
    to_pc = where in ("pc", "both")
    base = Cfg.folder(job["folder"]) if to_pc else tempfile.mkdtemp(prefix="dl_")
    if to_pc and job["who"] not in ("pc", str(Cfg.data.get("owner"))):
        base = os.path.join(base, safe(who_name(job["who"])))   # у мамы своя подпапка
    os.makedirs(base, exist_ok=True)
    files, tail, reason = [], [], ""
    Q.progress(job, 0, "начинаю…")

    if job.get("incoming"):  # файл, присланный/пересланный боту
        files, reason = receive_file(job, base)
    elif job.get("tg") or is_tg(job.get("url")):
        if not TL.user:
            raise RuntimeError("Для Telegram-каналов нужен вход в аккаунт: приложение → Настройки → Telegram.")
        try:
            files, reason = TL.download(job, base, lambda p, t: Q.progress(job, p, t))
        except asyncio.CancelledError:
            pass
    elif __import__("pinterest").parse(job.get("url") or "") and what in ("all", "photo", "video", "best"):
        import pinterest
        url = job["url"]
        files = pinterest.download(url, base, lambda p, t: Q.progress(job, p, t), lambda: job.get("cancel"),
                                   find_proxy() if needs_proxy(url) else None, what)
        if not files:
            reason = "Не нашёл пинов (доска пустая или закрыта)."
    else:
        url = job["url"]
        host = urlparse(url).netloc.lower()
        item = {"t": ""}

        def on_line(line):
            s = line.strip()
            if s.startswith("ФАЙЛ:"):
                p = s[5:]
                if p and p != "NA" and os.path.exists(p):
                    files.append(p)
                return
            if s.startswith("# "):  # gallery-dl: файл уже был скачан раньше — считаем готовым
                s = s[2:].strip()
            if os.path.isabs(s) and os.path.exists(s):
                files.append(s)
                Q.progress(job, 0, f"скачано файлов: {len(files)}")
                return
            m = ITEM.search(line)
            if m:
                item["t"] = f" · {m[1]} из {m[2]}"
            m = PROGRESS.search(line)
            if m:
                Q.progress(job, float(m[1]), f"{float(m[1]):.0f}% из {m[2]}" + (f" · {m[3]}" if m[3] else "")
                           + (f" · осталось {m[4]}" if m[4] else "") + item["t"])
            if "[download] Destination" in line and not job.get("title"):
                job["title"] = os.path.splitext(os.path.basename(line.split("Destination:", 1)[1].strip()))[0]

        gallery = any(d in host for d in GALLERY_FIRST) or VK_PHOTO.search(url)
        if what == "thumb":
            tools = ["thumb"]
        elif what in ("mp3", "audio", "subs") or what.isdigit() or what.startswith("subs:"):
            tools = ["yt"]
        elif what in ("photo", "all") or (what == "video" and gallery):
            tools = ["gallery", "yt"] if what != "photo" else ["gallery"]
        else:
            tools = ["gallery", "yt"] if gallery else ["yt", "gallery"]
        before = set()
        if what.startswith("subs"):  # субтитры yt-dlp не печатает путём — ищем новые .srt
            for dp, _, fn in os.walk(base):
                before.update(os.path.join(dp, f) for f in fn)
        proxy = find_proxy() if needs_proxy(url) else None
        tails = []
        for t in tools:
            for attempt in range(2):  # вторая попытка — через VPN, если напрямую сеть не пускает
                if job.get("cancel"):
                    break
                if t == "thumb":
                    files += download_thumb(job, base)
                    break
                if t == "yt":
                    args = ytdlp_args(url, base, what)
                else:
                    args = ["gallery_dl", url, "-d", base, "--no-colors", "-o", "skip=true"] + cookie_args("gallery")
                    if what == "photo":
                        args += ["--filter", "extension in ('jpg','jpeg','png','webp','gif','heic')"]
                    elif what == "video":
                        args += ["--filter", "extension in ('mp4','webm','mov','mkv','m4v')"]
                if ig_host(url):  # паузы между запросами — как у человека
                    args += IG_YT_OPTS if t == "yt" else IG_GALLERY_OPTS
                if proxy:
                    args = args[:1] + ["--proxy", proxy_for(args[0], proxy)] + args[1:]
                    Q.progress(job, 0, "через VPN…")
                code, tail = run_tool(args, on_line)
                tails.append((t, tail))
                if files or proxy or not network_error(" ".join(tail)) or not find_proxy():
                    break
                proxy = find_proxy()
            if files or job.get("cancel"):
                break
        # причина ошибки — от yt-dlp, если он пробовал (gallery-dl на видео обычно пишет «не поддерживается»)
        tail = next((tl for tt, tl in tails if tt == "yt"), tails[-1][1] if tails else [])
        if not files and ig_host(url) and any(w in " ".join(tl for _, t in tails for tl in t).lower() for w in IG_STOP):
            IG["cool_until"] = time.time() + IG_COOLDOWN
            log("Instagram попросил паузу — ссылки Instagram подождут 2 часа")
            reason = ("Instagram попросил сделать паузу. Чтобы не рисковать аккаунтом, ссылки Instagram "
                      "подождут 2 часа — остальные сайты качаются как обычно. Пришлите ссылку позже.")
        reason = reason or (explain(tail, proxy) if not files else "")
        if what.startswith("subs"):
            for dp, _, fn in os.walk(base):
                files += [os.path.join(dp, f) for f in fn
                          if f.lower().endswith((".srt", ".vtt")) and os.path.join(dp, f) not in before]

    if job.get("cancel"):
        job["status"], job["text"] = "cancelled", "отменено"
        return
    files = [f for f in dict.fromkeys(files) if os.path.exists(f)]
    if not files:
        job["status"] = "error"
        job["error"] = reason or explain(tail)
        job["text"] = job["error"]
        log(f"   не удалось: {job['error']}")
        return
    files = clean_names(files)
    job["files"], job["size"] = files, sum(os.path.getsize(f) for f in files)
    if not job.get("title"):
        job["title"] = os.path.splitext(os.path.basename(files[0]))[0]
    job["status"], job["pct"] = "done", 100
    job["text"] = f"готово: {len(files)} файл(ов), {human(job['size'])}"
    job["to_send"] = where in ("tg", "both") and job.get("chat") is not None
    job["tmp"] = None if to_pc else base
    log(f"   {job['text']}")
