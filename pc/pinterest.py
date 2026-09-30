"""
Pinterest: весь профиль или доска — по папкам, в оригинальном качестве.
    <папка>\\Pinterest\\<пользователь>\\<доска>\\[<раздел>]\\<название пина>.jpg
"""
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote, urlparse

import requests

SITE = "https://www.pinterest.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
VIDEO_PREF = ["V_EXP7", "V_1080P", "V_720P", "V_EXP6", "V_EXP5", "V_EXP4", "V_480P", "V_360P"]
RESERVED = {"pin", "search", "ideas", "today", "settings", "business", "_", "resource", "explore"}


def parse(url):
    """→ (пользователь, доска|None) для ссылок на профиль/доску; None — не профиль/доска (например, пин)."""
    host = urlparse(url).netloc.lower()
    if "pinterest." not in host:
        return None
    parts = [unquote(p) for p in urlparse(url).path.strip("/").split("/") if p]
    if not parts or parts[0].lower() in RESERVED:
        return None
    if len(parts) >= 2 and parts[1] in ("_created", "_saved", "pins", "boards", "_shop"):
        return parts[0], None
    return parts[0], (parts[1] if len(parts) > 1 else None)


class API:
    def __init__(self, proxy=None):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        if proxy:
            px = proxy.replace("socks5://", "socks5h://")
            self.s.proxies = {"http": px, "https": px}
        self.s.get(SITE + "/", timeout=30)

    def _get(self, resource, options, source_url):
        headers = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*, q=0.01",
                   "X-Pinterest-AppState": "active", "X-CSRFToken": self.s.cookies.get("csrftoken", ""),
                   "X-Pinterest-Source-Url": source_url,
                   "X-Pinterest-PWS-Handler": "www/[username].js" if source_url.strip("/").count("/") == 0
                   else "www/[username]/[slug].js"}
        params = {"source_url": source_url, "data": json.dumps({"options": options, "context": {}})}
        for attempt in range(5):
            try:
                r = self.s.get(f"{SITE}/resource/{resource}/get/", params=params, headers=headers, timeout=30)
                if r.status_code == 429 or r.status_code >= 500:
                    time.sleep(3 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()["resource_response"]
            except (requests.RequestException, ValueError):
                if attempt == 4:
                    raise
                time.sleep(3 * (attempt + 1))

    def _paged(self, resource, options, source_url):
        bookmark = None
        while True:
            opts = dict(options)
            if bookmark:
                opts["bookmarks"] = [bookmark]
            resp = self._get(resource, opts, source_url)
            data = resp.get("data") or []
            data = [data] if isinstance(data, dict) else data
            yield from data
            bookmark = resp.get("bookmark")
            if not bookmark or bookmark == "-end-" or not data:
                break

    def boards(self, user):
        opts = {"username": user, "page_size": 50, "privacy_filter": "all", "sort": "custom",
                "field_set_key": "profile_grid_item"}
        return [b for b in self._paged("BoardsResource", opts, f"/{user}/") if b.get("type") == "board" or "pin_count" in b]

    def pins(self, board):
        opts = {"board_id": board["id"], "board_url": board["url"], "page_size": 50, "field_set_key": "react_grid_pin",
                "filter_section_pins": True, "sort": "default", "layout": "default", "redux_normalize_feed": True}
        return [p for p in self._paged("BoardFeedResource", opts, board["url"]) if p.get("type") == "pin"]

    def sections(self, board):
        if not board.get("section_count"):
            return []
        return list(self._paged("BoardSectionsResource", {"board_id": board["id"]}, board["url"]))

    def section_pins(self, board, section):
        opts = {"section_id": section["id"], "page_size": 50, "redux_normalize_feed": True}
        return [p for p in self._paged("BoardSectionPinsResource", opts, board["url"]) if p.get("type") == "pin"]


def _best_video(vl):
    if not vl:
        return None
    for k in VIDEO_PREF:
        if (vl.get(k) or {}).get("url", "").endswith(".mp4"):
            return vl[k]["url"]
    mp4 = [v for v in vl.values() if v.get("url", "").endswith(".mp4")]
    return max(mp4, key=lambda v: v.get("width", 0) * v.get("height", 0))["url"] if mp4 else None


def _best_image(images):
    if not images:
        return None
    for k in ("orig", "originals"):
        if images.get(k):
            return images[k]["url"]
    return max(images.values(), key=lambda i: (i.get("width") or 0) * (i.get("height") or 0)).get("url")


def pin_media(pin):
    urls = []
    v = _best_video((pin.get("videos") or {}).get("video_list"))
    if v:
        urls.append(v)
    pages = (pin.get("story_pin_data") or {}).get("pages") or []
    for page in pages if len(pages) > 1 else []:
        for block in page.get("blocks") or []:
            u = _best_video((block.get("video") or {}).get("video_list")) or _best_image((block.get("image") or {}).get("images"))
            if u:
                urls.append(u)
    for slot in (pin.get("carousel_data") or {}).get("carousel_slots") or []:
        u = _best_image(slot.get("images"))
        if u:
            urls.append(u)
    if not urls:
        u = _best_image(pin.get("images"))
        if u:
            urls.append(u)
    return list(dict.fromkeys(urls))


def _safe(name, limit=100):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "").replace("\n", " ")
    return re.sub(r"\s+", " ", name).strip(" .")[:limit].rstrip(" .,—-") or "_"


def probe(url, proxy=None):
    """Сколько досок и пинов — для карточки."""
    user, slug = parse(url)
    api = API(proxy)
    boards = api.boards(user)
    if slug:
        boards = [b for b in boards if (unquote(b.get("url", "")).strip("/").split("/") + [""])[1].lower() == slug.lower()]
        if not boards:
            return {"kind": "error", "title": "Pinterest", "error": "Доска не найдена (или она закрыта)."}
    return {"kind": "pinterest", "title": f"Pinterest · {user}" + (f" · {boards[0]['name']}" if slug else ""),
            "user": user, "board": slug, "boards": len(boards), "pins": sum(b.get("pin_count", 0) for b in boards)}


def download(url, base, progress, cancelled, proxy=None, what="all"):
    """Скачивает профиль/доску по папкам. Возвращает список файлов."""
    user, slug = parse(url)
    api = API(proxy)
    boards = api.boards(user)
    if slug:
        boards = [b for b in boards if (unquote(b.get("url", "")).strip("/").split("/") + [""])[1].lower() == slug.lower()]
    root = os.path.join(base, "Pinterest", _safe(user))
    jobs = []  # (url, путь)
    for bi, b in enumerate(boards, 1):
        if cancelled():
            break
        progress(0, f"собираю список: доска {bi} из {len(boards)} — {b.get('name')}")
        bdir = os.path.join(root, _safe(b.get("name")))
        groups = [(bdir, api.pins(b))] + [(os.path.join(bdir, _safe(s.get("title"))), api.section_pins(b, s))
                                          for s in api.sections(b)]
        for folder, pins in groups:
            used = set()
            for pin in pins:
                media = pin_media(pin)
                title = _safe(pin.get("title") or pin.get("grid_title") or pin["id"], 90)
                for i, u in enumerate(media):
                    if what == "photo" and u.endswith(".mp4") or what == "video" and not u.endswith(".mp4"):
                        continue
                    ext = os.path.splitext(urlparse(u).path)[1].lower() or ".jpg"
                    name = title + (f" ({i + 1})" if len(media) > 1 else "")
                    n, cand = 2, name
                    while cand.lower() in used:  # одинаковые названия пинов — с номером
                        cand, n = f"{name} ({n})", n + 1
                    used.add(cand.lower())
                    jobs.append((u, os.path.join(folder, cand + ext)))
    files, lock, done = [], threading.Lock(), [0]

    def one(u, path):
        if cancelled():
            return None
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        for attempt in range(4):
            try:
                with api.s.get(u, stream=True, timeout=60) as r:
                    if r.status_code == 404 and "/originals/" in u:
                        u = u.replace("/originals/", "/736x/")
                        continue
                    r.raise_for_status()
                    with open(path + ".part", "wb") as f:
                        for chunk in r.iter_content(1 << 16):
                            f.write(chunk)
                os.replace(path + ".part", path)
                return path
            except requests.RequestException:
                time.sleep(2 * (attempt + 1))
        return None

    with ThreadPoolExecutor(8) as ex:
        futs = [ex.submit(one, u, p) for u, p in jobs]
        for fut in as_completed(futs):
            r = fut.result()
            with lock:
                done[0] += 1
                if r:
                    files.append(r)
                progress(done[0] * 100 / max(len(jobs), 1), f"{done[0]} из {len(jobs)} файлов")
    return files
