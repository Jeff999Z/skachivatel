"""
Telegram-бот «Скачивателя»: принимает ссылки, показывает карточку «что / куда / папка»,
сообщает прогресс, присылает файлы в чат (до 50 МБ обычным ботом, до 2 ГБ — через MTProto).
"""
import json
import os
import random
import re
import shutil
import threading
import time

import requests

import core as C
from core import Cfg, Q, TL, WHAT, WHERE, esc, human, log

BTN_HOW, BTN_MY, BTN_QUEUE, BTN_SET = "📥 Как скачать", "📂 Мои загрузки", "⏳ Очередь", "⚙️ Настройки"
BTN_PEOPLE, BTN_ADD = "👥 Люди", "➕ Добавить человека"

HOW = ("<b>Как скачать</b>\n"
       "Пришлите ссылку (можно несколько) — появится карточка: выберите <b>что</b> скачать, "
       "<b>куда</b> (на компьютер, сюда в чат или оба) и <b>папку</b>, и нажмите «Скачать».\n\n"
       "Можно просто переслать сюда пост из Telegram-канала.\n\n"
       "⚡ В «Настройках» можно включить быстрый режим — тогда ссылка качается сразу, без карточки.\n\n"
       "Откуда: VK, Rutube, Дзен, TikTok, OK, Pinterest, SoundCloud, Bandcamp, Kinescope (без защиты), "
       "Telegram, YouTube и Instagram (при включённом VPN на ПК) и ещё ~1000 сайтов.")


class Bot:
    def __init__(self):
        self.s = requests.Session()
        self.running = False
        self.username = None
        self.cards = {}      # key → карточка выбора
        self.last_edit = {}  # job id → время последнего обновления сообщения

    @property
    def url(self):
        return f"https://api.telegram.org/bot{Cfg.data['token']}/"

    # ── API ──
    def call(self, method, files=None, http_timeout=60, **params):
        for k, v in list(params.items()):
            if isinstance(v, (dict, list)):
                params[k] = json.dumps(v, ensure_ascii=False)
        for attempt in range(4):
            try:
                j = self.s.post(self.url + method, data=params, files=files, timeout=http_timeout).json()
                if not j.get("ok"):
                    ra = j.get("parameters", {}).get("retry_after")
                    if ra:
                        time.sleep(ra)
                        continue
                    if "not modified" not in j.get("description", ""):
                        log(f"   Telegram {method}: {j.get('description')}")
                return j
            except (requests.RequestException, ValueError):
                time.sleep(3 * (attempt + 1))
        return {"ok": False}

    def send(self, chat, text, kb=None, reply_kb=None):
        p = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if kb is not None:
            p["reply_markup"] = {"inline_keyboard": kb}
        elif reply_kb is not None:
            p["reply_markup"] = reply_kb
        return self.call("sendMessage", **p).get("result", {}).get("message_id")

    def edit(self, chat, mid, text, kb=None):
        self.call("editMessageText", chat_id=chat, message_id=mid, text=text, parse_mode="HTML",
                  disable_web_page_preview="true", reply_markup={"inline_keyboard": kb or []})

    def answer(self, cid, text=""):
        self.call("answerCallbackQuery", callback_query_id=cid, text=text)

    def send_file(self, chat, path, caption="", on_progress=None):
        size = os.path.getsize(path)
        low = path.lower()
        if size > C.BOT_SEND_LIMIT:
            if TL.bot and size <= C.MT_SEND_LIMIT:
                TL.send_big(chat, path, caption, on_progress)
                return True
            return False
        if low.endswith((".mp3", ".m4a", ".opus", ".ogg", ".flac")):
            kind, field = "sendAudio", "audio"
        elif low.endswith(".mp4"):
            kind, field = "sendVideo", "video"
        elif low.endswith((".jpg", ".jpeg", ".png", ".webp")) and size < 9_500_000:
            kind, field = "sendPhoto", "photo"
        else:
            kind, field = "sendDocument", "document"
        with open(path, "rb") as fh:
            extra = {"supports_streaming": "true"} if kind == "sendVideo" else {}
            j = self.call(kind, files={field: (os.path.basename(path), fh)}, http_timeout=900,
                          chat_id=chat, caption=caption[:1000], **extra)
        if not j.get("ok") and kind != "sendDocument":
            with open(path, "rb") as fh:
                j = self.call("sendDocument", files={"document": (os.path.basename(path), fh)},
                              http_timeout=900, chat_id=chat, caption=caption[:1000])
        return j.get("ok", False)

    # ── клавиатуры ──
    def main_kb(self, uid):
        rows = [[BTN_HOW, BTN_MY], [BTN_QUEUE, BTN_SET]]
        if Cfg.is_owner(uid):
            rows.append([BTN_PEOPLE, BTN_ADD])
        return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True,
                "input_field_placeholder": "Вставьте ссылку…"}

    def options_kb(self, prefix, sel, with_go=True):
        """Кнопки выбора: что / куда / папка. prefix — «c:<key>» для карточки или «d» для настроек."""
        def b(text, on, data):
            return {"text": ("✅ " if on else "") + text, "callback_data": f"{prefix}:{data}"}
        rows = [[b(WHAT[k], sel["what"] == k, f"what:{k}") for k in ("best", "1080", "720", "480")],
                [b(WHAT[k], sel["what"] == k, f"what:{k}") for k in ("mp3", "audio", "photo", "subs")],
                [b(WHERE[k], sel["where"] == k, f"where:{k}") for k in ("pc", "tg", "both")]]
        if sel["where"] in ("pc", "both"):
            fl = Cfg.data["folders"]
            row = []
            for i, f in enumerate(fl[:8]):
                row.append(b("📁 " + f["name"], sel["folder"] == i, f"folder:{i}"))
                if len(row) == 3:
                    rows.append(row)
                    row = []
            if row:
                rows.append(row)
        if with_go:
            rows.append([{"text": "⬇️ Скачать", "callback_data": f"{prefix}:go"},
                         {"text": "✖️ Отмена", "callback_data": f"{prefix}:x"}])
        return rows

    # ── карточки: тип → качество → куда ──
    def card_msg(self, card, text, kb, new=False):
        preview = {"is_disabled": True}
        thumb = (card.get("info") or {}).get("thumb")
        if thumb and card.get("info", {}).get("kind") in ("video", "audio", "playlist"):
            preview = {"url": thumb, "prefer_large_media": True, "show_above_text": True}
        p = {"chat_id": card["chat"], "text": text, "parse_mode": "HTML", "link_preview_options": preview,
             "reply_markup": {"inline_keyboard": kb}}
        if new:
            return self.call("sendMessage", **p).get("result", {}).get("message_id")
        self.call("editMessageText", message_id=card["mid"], **p)

    def new_card(self, chat, uid, urls=None, tg=None):
        key = f"{random.randint(0, 10**9)}"
        u = Cfg.user(uid)
        d = u.get("defaults") or {}
        card = {"chat": chat, "uid": str(uid), "urls": urls or [], "tg": tg, "info": None, "step": "wait",
                "what": None, "sel": {"where": d.get("where", "pc"), "folder": d.get("folder", 0)}}
        self.cards[key] = card
        multi = len(card["urls"]) > 1
        card["mid"] = self.card_msg(card, f"🔎 Смотрю, что есть по {'ссылкам' if multi else 'ссылке'}…",
                                    [[self.btn(key, "x", "", "✖️ Отмена")]], new=True)

        def fill():
            if multi:
                card["info"] = {"kind": "multi", "title": f"Ссылок: {len(card['urls'])}"}
            elif tg:
                card["info"] = C.probe_tg(tg=tg)
            else:
                card["info"] = C.probe(card["urls"][0])
            card["step"] = "type"
            if key in self.cards:
                self.show(key)
        threading.Thread(target=fill, daemon=True).start()

    @staticmethod
    def btn(key, act, arg, text):
        return {"text": text, "callback_data": f"c:{key}:{act}:{arg}"}

    def show(self, key):
        card = self.cards[key]
        info, step, b = card["info"], card["step"], self.btn
        text = C.describe(info) if info.get("kind") != "multi" else f"<b>{info['title']}</b>"
        rows = []
        k = info.get("kind")
        if step == "type":
            text += "\n\n<b>Что скачать?</b>"
            if k in ("video", "audio"):
                r = []
                if info.get("video"):
                    r.append(b(key, "t", "video", "🎬 Видео"))
                if info.get("audio"):
                    r.append(b(key, "t", "audio", "🎵 Только аудио"))
                rows.append(r)
                r = []
                if info.get("subs"):
                    r.append(b(key, "t", "subs", "📝 Субтитры"))
                if info.get("thumb"):
                    r.append(b(key, "q", "thumb", "🖼 Обложка"))
                if r:
                    rows.append(r)
            elif k == "playlist":
                n = info.get("count", "?")
                rows.append([b(key, "t", "plvideo", f"🎬 Все видео ({n})"), b(key, "t", "plaudio", f"🎵 Всё аудио ({n})")])
            elif k == "pinterest":
                if info.get("board"):
                    rows.append([b(key, "q", "all", f"📋 Вся доска ({info['pins']} пинов)")])
                else:
                    rows.append([b(key, "q", "all", f"📚 Все доски по папкам ({info['boards']} досок · {info['pins']} пинов)")])
                rows.append([b(key, "q", "photo", "🖼 Только фото"), b(key, "q", "video", "🎬 Только видео")])
            elif k == "gallery":
                r = []
                if info.get("photos"):
                    r.append(b(key, "q", "photo", f"🖼 Фото ({info['photos']})"))
                if info.get("videos"):
                    r.append(b(key, "q", "video", f"🎬 Видео ({info['videos']})"))
                if r:
                    rows.append(r)
                total = info.get("photos", 0) + info.get("videos", 0) + info.get("other", 0)
                rows.append([b(key, "q", "all", f"📦 Всё{f' ({total})' if total else ''}")])
            elif k == "telegram":
                if info.get("need_login"):
                    text += "\n\n🔑 Для постов из каналов нужен вход в Telegram: приложение на ПК → Настройки."
                elif info.get("channel"):
                    rows.append([b(key, "q", "all", f"📦 Весь канал ({info.get('count')} файлов)")])
                else:
                    files = info.get("files", [])
                    size = sum(f["size"] for f in files)
                    rows.append([b(key, "q", "all", f"⬇️ {'Всё' if len(files) > 1 else 'Скачать'} · {human(size)}")])
                    kinds = {f["kind"] for f in files}
                    if len(files) > 1 and len(kinds) > 1:
                        r = []
                        if "video" in kinds:
                            r.append(b(key, "q", "video", "🎬 Только видео"))
                        if "photo" in kinds:
                            r.append(b(key, "q", "photo", "🖼 Только фото"))
                        if "audio" in kinds:
                            r.append(b(key, "q", "mp3", "🎵 Только аудио"))
                        rows.append(r)
            elif k == "multi":
                rows += [[b(key, "q", "best", "⭐ Максимум"), b(key, "q", "1080", "1080p"), b(key, "q", "720", "720p")],
                         [b(key, "q", "mp3", "🎵 MP3"), b(key, "q", "photo", "🖼 Фото"), b(key, "q", "all", "📦 Всё")]]
            rows.append([b(key, "x", "", "✖️ Отмена")])
        elif step == "q":
            sub = card["sub"]
            if sub == "video":
                text += "\n\n<b>Какое качество?</b> (размер примерный)"
                for i, v in enumerate(info["video"][:9]):
                    fps = f" {round(v['fps'])}fps" if (v.get("fps") or 0) > 31 else ""
                    size = f" · ~{human(v['size'])}" if v.get("size") else ""
                    rows.append([b(key, "q", "best" if i == 0 else str(v["h"]),
                                   f"{'⭐ ' if i == 0 else ''}{v['h']}p{fps}{' (макс.)' if i == 0 else ''}{size}")])
            elif sub == "audio":
                a = info["audio"]
                text += "\n\n<b>Какой звук?</b>"
                rows.append([b(key, "q", "mp3", f"🎵 MP3 320 kbps · ~{human(a['mp3'])}")])
                rows.append([b(key, "q", "audio", f"🎵 Оригинал ({a['ext']} {a['abr']} kbps) · ~{human(a['size'])}")])
            elif sub == "subs":
                text += "\n\n<b>Какие субтитры?</b> (придут файлом .srt)"
                for s_ in info["subs"]:
                    rows.append([b(key, "q", f"subs:{s_['lang']}", f"📝 {s_['name']}{' — автоматические' if s_['auto'] else ''} ({s_['lang']})")])
            elif sub == "plvideo":
                text += "\n\n<b>Качество для всех видео:</b>"
                rows += [[b(key, "q", "best", "⭐ Максимум"), b(key, "q", "1080", "1080p")],
                         [b(key, "q", "720", "720p"), b(key, "q", "480", "480p")]]
            elif sub == "plaudio":
                rows += [[b(key, "q", "mp3", "🎵 MP3 320"), b(key, "q", "audio", "🎵 Оригинал")]]
            rows.append([b(key, "back", "", "← Назад")])
        elif step == "dest":
            sel = card["sel"]
            size = self.estimate(card)
            text += f"\n\nВыбрано: <b>{C.label(card['what'])}</b>" + (f" · ~{human(size)}" if size else "")
            text += "\n<b>Куда сохранить?</b>"
            if sel["where"] in ("tg", "both") and size > C.BOT_SEND_LIMIT and not TL.bot:
                text += ("\n\n⚠️ Файл больше 50 МБ: в чат пришлю только после входа в Telegram в приложении на ПК. "
                         "Пока сохраню его на ПК.")
            rows.append([b(key, "w", w, ("✅ " if sel["where"] == w else "") + C.WHERE[w]) for w in ("pc", "tg", "both")])
            if sel["where"] in ("pc", "both"):
                row = []
                for i, f in enumerate(Cfg.data["folders"][:9]):
                    row.append(b(key, "f", str(i), ("✅ " if sel["folder"] == i else "") + "📁 " + f["name"]))
                    if len(row) == 3:
                        rows.append(row)
                        row = []
                if row:
                    rows.append(row)
            rows.append([b(key, "go", "", "⬇️ Скачать"), b(key, "back", "", "← Назад")])
        self.card_msg(card, text, rows)

    def estimate(self, card):
        info, what = card["info"], card["what"]
        if what == "best" and info.get("video"):
            return info["video"][0].get("size", 0)
        if what and what.isdigit() and info.get("video"):
            return next((v["size"] for v in info["video"] if str(v["h"]) == what), 0)
        if what == "mp3" and info.get("audio"):
            return info["audio"]["mp3"]
        if what == "audio" and info.get("audio"):
            return info["audio"]["size"]
        if info.get("kind") == "telegram":
            files = info.get("files", [])
            if what == "video":
                files = [f for f in files if f["kind"] == "video"]
            elif what == "photo":
                files = [f for f in files if f["kind"] == "photo"]
            return sum(f["size"] for f in files)
        return 0

    def card_action(self, key, act, arg, cb_id):
        card = self.cards.get(key)
        if not card:
            return False
        if act == "x":
            self.cards.pop(key, None)
            self.card_msg(card, "✖️ Отменено", [])
        elif act == "t":
            card["sub"], card["step"] = arg, "q"
            self.show(key)
        elif act == "q":
            card["what"], card["step"] = arg, "dest"
            card["prev"] = "q" if card.get("sub") else "type"
            self.show(key)
        elif act == "w":
            card["sel"]["where"] = arg
            self.show(key)
        elif act == "f":
            card["sel"]["folder"] = int(arg)
            self.show(key)
        elif act == "back":
            card["step"] = card.get("prev", "type") if card["step"] == "dest" else "type"
            if card["step"] == "type":
                card["sub"] = None
            self.show(key)
        elif act == "go":
            self.start_card(key)
        return True

    def start_card(self, key):
        card = self.cards.pop(key, None)
        if not card:
            return
        sel, info = card["sel"], card["info"] or {}
        u = Cfg.user(card["uid"])
        u["defaults"]["where"], u["defaults"]["folder"] = sel["where"], sel["folder"]
        Cfg.save()
        first = True
        for url in card["urls"] or [None]:
            mid = card["mid"] if first else self.send(card["chat"], "⏳ В очереди")
            Q.add(url=url, what=card["what"], where=sel["where"], folder=sel["folder"], who=card["uid"],
                  chat=card["chat"], tg=card["tg"], mid=mid, thumb=info.get("thumb"),
                  title=info.get("title") if first and info.get("kind") != "multi" else None)
            first = False

    # ── прогресс заданий → сообщения ──
    def on_job(self, job):
        if not job.get("chat") or not job.get("mid"):
            return
        st = job["status"]
        head = f"🔗 {esc(job.get('title') or job.get('url') or 'Пост Telegram')[:120]}\n" \
               f"{WHAT.get(job['what'])} → {WHERE.get(job['where'])}"
        stop = [[{"text": "⏹ Отменить", "callback_data": f"stop:{job['id']}"}]]
        if st == "queued":
            ahead = sum(1 for j in Q.jobs if j["status"] in ("queued", "running")) - 1
            self.edit(job["chat"], job["mid"], f"{head}\n\n⏳ В очереди{f' (впереди {ahead})' if ahead > 0 else ''}", stop)
        elif st == "running":
            now = time.time()
            if now - self.last_edit.get(job["id"], 0) < 4:
                return
            self.last_edit[job["id"]] = now
            bar = "▓" * int(job["pct"] / 10) + "░" * (10 - int(job["pct"] / 10))
            self.edit(job["chat"], job["mid"], f"{head}\n\n⬇️ {bar} {job['text']}", stop)
        elif st == "cancelled":
            self.edit(job["chat"], job["mid"], f"{head}\n\n⏹ Отменено")
        elif st == "error":
            self.edit(job["chat"], job["mid"], f"{head}\n\n❌ {esc(job['error'])}",
                      [[{"text": "🔁 Ещё раз", "callback_data": f"retry:{job['id']}"}]])
        elif st == "done":
            threading.Thread(target=self.finish, args=(job,), daemon=True).start()

    def finish(self, job):
        head = f"🔗 {esc(job.get('title') or '')[:120]}\n{WHAT.get(job['what'])} → {WHERE.get(job['where'])}"
        files = job["files"]
        lines = [f"✅ Готово: {len(files)} файл(ов), {human(job['size'])}"]
        if job["where"] in ("pc", "both"):
            lines.append(f"💻 {esc(os.path.dirname(files[0]))}")
        not_sent = []
        if job.get("to_send"):
            self.edit(job["chat"], job["mid"], f"{head}\n\n📤 Отправляю в чат…")
            for n, f in enumerate(files[:20], 1):
                def prog(done, total, n=n):
                    now = time.time()
                    if now - self.last_edit.get(job["id"] + "s", 0) > 4:
                        self.last_edit[job["id"] + "s"] = now
                        self.edit(job["chat"], job["mid"], f"{head}\n\n📤 Отправляю {n}/{min(len(files), 20)}: "
                                                           f"{done * 100 // max(total, 1)}% из {human(total)}")
                try:
                    if not self.send_file(job["chat"], f, os.path.basename(f), prog):
                        not_sent.append(f)
                except Exception as e:  # noqa: BLE001
                    log(f"   отправка: {e}")
                    not_sent.append(f)
            if not_sent:
                lines.append(f"⚠️ Не отправлено в чат ({len(not_sent)}) — слишком большие"
                             + ("" if TL.bot else " (нужен вход в Telegram в приложении на ПК)")
                             + (". Они сохранены на ПК." if job["where"] != "tg" else "."))
            if job.get("tmp"):  # «только в Telegram» — временные файлы убираем, крупные оставляем
                if not_sent:
                    keep = os.path.join(C.BASE, "Не влезло в Telegram")
                    os.makedirs(keep, exist_ok=True)
                    for f in not_sent:
                        shutil.move(f, os.path.join(keep, os.path.basename(f)))
                    lines.append(f"💻 Сохранил на ПК: {esc(keep)}")
                shutil.rmtree(job["tmp"], ignore_errors=True)
        kb = []
        vids = any(f.lower().endswith(C.VIDEO_EXT) for f in files)
        if vids and job.get("url") and job["what"] not in ("mp3", "audio"):
            kb.append({"text": "🎵 Ещё и MP3", "callback_data": f"again:{job['id']}:mp3"})
        if job["where"] == "pc" and not job.get("incoming"):
            kb.append({"text": "📤 Прислать сюда", "callback_data": f"send:{job['id']}"})
        self.edit(job["chat"], job["mid"], f"{head}\n\n" + "\n".join(lines), [kb] if kb else None)

    # ── входящие ──
    def handle_message(self, msg):
        chat, uid = msg["chat"]["id"], msg["from"]["id"]
        text = (msg.get("text") or msg.get("caption") or "").strip()
        name = " ".join(filter(None, [msg["from"].get("first_name"), msg["from"].get("last_name")])) or str(uid)
        if not Cfg.user(uid):
            if Cfg.use_code(text):
                Cfg.add_user(uid, name)
                self.send(chat, f"✅ {esc(name)}, доступ открыт!" +
                          (" Вы — владелец: добавляете и удаляете людей." if Cfg.is_owner(uid) else ""),
                          reply_kb=self.main_kb(uid))
                self.send(chat, HOW)
            else:
                self.send(chat, "🔒 Это личный бот. Если вам дали код из 6 цифр — отправьте его сюда.")
                log(f"чужой: {name} ({uid})")
            return
        u = Cfg.user(uid)
        origin = msg.get("forward_origin") or {}
        media = incoming_media(msg)
        if media and not (origin.get("type") == "channel" and TL.user):
            # файл прислали или переслали боту — сохраняем на ПК (без входа в Telegram — до 20 МБ)
            d = u.get("defaults") or {}
            mid = self.send(chat, f"📥 Сохраняю на ПК: {esc(media['name'])} ({human(media['size'])})")
            Q.add(what="incoming", where="pc", folder=d.get("folder", 0), who=uid, chat=chat, mid=mid,
                  title=media["name"], incoming=dict(media, chat=chat, msg=msg["message_id"]))
            return
        if origin.get("type") == "channel" and any(msg.get(k) for k in ("video", "document", "audio", "photo", "voice")):
            ch = origin["chat"]
            tg = {"peer": ch.get("username") or ch["id"], "msg": origin["message_id"]}
            if u.get("quick"):
                d = u["defaults"]
                mid = self.send(chat, "⏳ В очереди")
                Q.add(what=d["what"], where=d["where"], folder=d["folder"], who=uid, chat=chat, tg=tg, mid=mid)
            else:
                self.new_card(chat, uid, tg=tg)
            return

        if text == BTN_HOW or text.startswith(("/start", "/help")):
            self.send(chat, HOW, reply_kb=self.main_kb(uid))
        elif text == BTN_MY:
            mine = [h for h in Q.history if h["who"] == str(uid) and h["status"] == "done"][:10]
            if not mine:
                return self.send(chat, "Пока ничего не скачано.")
            kb = [[{"text": f"📤 {h['finished']} · {(h.get('title') or os.path.basename(h['files'][0]))[:32]}",
                    "callback_data": f"send:{h['id']}"}] for h in mine]
            self.send(chat, "<b>Последние загрузки</b> — нажмите, чтобы прислать сюда:", kb)
        elif text == BTN_QUEUE:
            self.send(chat, queue_text())
        elif text == BTN_SET:
            self.send(chat, *self.settings(uid))
        elif text in (BTN_PEOPLE, BTN_ADD) or text.startswith("/add"):
            if not Cfg.is_owner(uid):
                return self.send(chat, "Добавлять и удалять людей может только владелец.")
            if text == BTN_PEOPLE:
                self.send(chat, *self.people())
            else:
                self.send(chat, f"Код для нового человека: <b>{Cfg.new_code()}</b>\n\nПусть откроет бота "
                                f"@{self.username}, нажмёт «Запустить» и отправит код. Действует 10 минут, один раз.")
        else:
            urls = C.URL_RE.findall(text)
            for e in msg.get("entities", []) + msg.get("caption_entities", []):
                if e.get("type") == "text_link":
                    urls.append(e["url"])
            urls = [x.rstrip(").,") for x in dict.fromkeys(urls)]
            if not urls:
                return self.send(chat, "Не вижу ссылки. Нажмите «📥 Как скачать».", reply_kb=self.main_kb(uid))
            if u.get("quick"):
                d = u["defaults"]
                for x in urls:
                    mid = self.send(chat, "⏳ В очереди")
                    Q.add(url=x, what=d["what"], where=d["where"], folder=d["folder"], who=uid, chat=chat, mid=mid)
            else:
                self.new_card(chat, uid, urls=urls)

    def settings(self, uid):
        u = Cfg.user(uid)
        kb = [[{"text": ("✅" if u.get("quick") else "⬜") + " ⚡ Быстрый режим — качать сразу, без карточки",
                "callback_data": "set:quick"}]]
        kb += self.options_kb("d", u["defaults"], with_go=False)
        return ("<b>Настройки</b>\nВыбор по умолчанию (он же — для быстрого режима). "
                "Карточка тоже запоминает ваш последний выбор.", kb)

    def people(self):
        lines, kb = [], []
        for k, u in Cfg.data["users"].items():
            lines.append(f"• {esc(u['name'])}{' 👑' if Cfg.is_owner(k) else ''} — с {u.get('added', '?')}")
            if not Cfg.is_owner(k):
                kb.append([{"text": f"🗑 Удалить: {u['name']}", "callback_data": f"rm:{k}"}])
        return "<b>Подключены</b>\n" + "\n".join(lines), kb

    def handle_callback(self, cb):
        uid, data = cb["from"]["id"], cb.get("data", "")
        chat, mid = cb["message"]["chat"]["id"], cb["message"]["message_id"]
        if not Cfg.user(uid):
            return self.answer(cb["id"], "Нет доступа")
        parts = data.split(":")
        kind = parts[0]
        if kind == "c":  # карточка: c:<ключ>:<действие>:<значение (может содержать «:»)>
            _, key, act, arg = (data.split(":", 3) + ["", ""])[:4]
            if key not in self.cards:
                self.answer(cb["id"], "Карточка устарела")
                return self.edit(chat, mid, "Карточка устарела — пришлите ссылку ещё раз.")
            if self.cards[key].get("step") == "wait" and act != "x":
                return self.answer(cb["id"], "Ещё смотрю ссылку…")
            self.answer(cb["id"], "Скачиваю" if act == "go" else "")
            self.card_action(key, act, arg, cb["id"])
        elif kind == "d":  # настройки по умолчанию
            u = Cfg.user(uid)
            u["defaults"][parts[1]] = int(parts[2]) if parts[1] == "folder" else parts[2]
            Cfg.save()
            self.answer(cb["id"], "Сохранено")
            self.edit(chat, mid, *self.settings(uid))
        elif kind == "set" and parts[1] == "quick":
            u = Cfg.user(uid)
            u["quick"] = not u.get("quick")
            Cfg.save()
            self.answer(cb["id"], "Сохранено")
            self.edit(chat, mid, *self.settings(uid))
        elif kind == "stop":
            Q.cancel(parts[1])
            self.answer(cb["id"], "Отменяю")
        elif kind in ("send", "again", "retry"):
            h = next((x for x in Q.history if x["id"] == parts[1]), None)
            if not h or (h["who"] != str(uid) and not Cfg.is_owner(uid)):
                return self.answer(cb["id"], "Не найдено")
            if kind == "send":
                self.answer(cb["id"], "Отправляю…")
                job = {"id": h["id"], "chat": chat, "mid": self.send(chat, "📤 Отправляю…"), "files":
                       [f for f in h["files"] if os.path.exists(f)], "size": h["size"], "title": h.get("title"),
                       "what": h["what"], "where": "tg", "to_send": True, "url": h.get("url")}
                job["where_label"] = "pc"
                threading.Thread(target=self.finish, args=(job,), daemon=True).start()
            else:
                what = parts[2] if kind == "again" else h["what"]
                self.answer(cb["id"], "Добавил в очередь")
                Q.add(url=h.get("url"), what=what, where=h["where"], folder=0, who=uid, chat=chat,
                      mid=self.send(chat, "⏳ В очереди"))
        elif kind == "rm" and Cfg.is_owner(uid):
            u = Cfg.user(parts[1])
            self.answer(cb["id"])
            self.edit(chat, mid, f"Удалить доступ для «{esc(u['name']) if u else parts[1]}»?",
                      [[{"text": "Да, удалить", "callback_data": f"rmok:{parts[1]}"},
                        {"text": "Нет", "callback_data": "rmno"}]])
        elif kind == "rmok" and Cfg.is_owner(uid):
            Cfg.remove_user(parts[1])
            self.answer(cb["id"], "Удалено")
            self.edit(chat, mid, *self.people())
        elif kind == "rmno":
            self.answer(cb["id"])
            self.edit(chat, mid, *self.people())
        else:
            self.answer(cb["id"])

    # ── цикл ──
    def run(self):
        if not Cfg.data.get("token"):
            log("Бот не запущен: нет ключа (Настройки → Бот)")
            return
        me = self.call("getMe")
        if not me.get("ok"):
            log("Бот не запущен: ключ не подходит")
            return
        self.username = me["result"]["username"]
        self.running = True
        self.call("setMyCommands", commands=[{"command": "start", "description": "Меню и помощь"}])
        Q.listeners.append(self.on_job)
        log(f"Бот @{self.username} работает")
        offset = 0
        while self.running:
            j = self.call("getUpdates", http_timeout=70, offset=offset, timeout=50,
                          allowed_updates=["message", "callback_query"])
            for upd in j.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    if "message" in upd:
                        self.handle_message(upd["message"])
                    elif "callback_query" in upd:
                        self.handle_callback(upd["callback_query"])
                except Exception as e:  # noqa: BLE001
                    log(f"бот: ошибка обработки: {e}")
            if not j.get("ok"):
                time.sleep(5)


def incoming_media(msg):
    """Файл из сообщения: {file_id, size, name, ext} или None."""
    for kind, ext in (("document", ""), ("video", ".mp4"), ("audio", ".mp3"), ("voice", ".ogg"),
                      ("video_note", ".mp4"), ("animation", ".mp4")):
        f = msg.get(kind)
        if f:
            name = f.get("file_name")
            if not name and kind == "audio" and f.get("title"):
                name = f"{f.get('performer') + ' - ' if f.get('performer') else ''}{f['title']}{ext}"
            return {"file_id": f["file_id"], "size": f.get("file_size") or 0,
                    "name": name or f"{kind}_{msg['message_id']}{ext}", "ext": ext}
    if msg.get("photo"):
        p = msg["photo"][-1]  # самый большой размер
        return {"file_id": p["file_id"], "size": p.get("file_size") or 0, "name": f"photo_{msg['message_id']}.jpg", "ext": ".jpg"}
    return None


def queue_text():
    with Q.lock:
        act = [j for j in Q.jobs if j["status"] in ("queued", "running")]
    if not act:
        return "Очередь пуста."
    out = []
    for j in act:
        mark = "▶️" if j["status"] == "running" else "⏳"
        out.append(f"{mark} {esc((j.get('title') or j.get('url') or 'пост')[:50])} — {esc(C.who_name(j['who']))}"
                   + (f" · {j['text']}" if j["status"] == "running" else ""))
    return "<b>Очередь</b>\n" + "\n".join(out)


BOT = Bot()
