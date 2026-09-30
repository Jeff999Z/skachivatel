"""
Связь с телефоном по домашней сети (приложение «Скачиватель» для Android).

Безопасность:
  • HTTPS с собственным сертификатом ПК; телефон знает его отпечаток из QR и проверяет (подмена ПК невозможна).
  • Сопряжение: код из 8 символов меняется каждые 30 с, по сети не передаётся — телефон шлёт подпись
    HMAC(код, nonce + ключ + устройство). Новое устройство подтверждают кнопкой на ПК.
  • Каждый запрос подписан ключом телефона: HMAC(ключ, метод, путь, время, nonce, sha256(тела));
    старше 2 минут или повтор nonce — отказ. Отвечаем только адресам домашней сети.
"""
import base64
import datetime
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import core as C
import paths
from core import Cfg, Q, log

PORT = 47822
DISCOVERY_PORT = 47823
CERT, KEY = paths.data("remote_cert.pem"), paths.data("remote_key.pem")
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# ─────────────────────────── Сертификат и отпечаток ───────────────────────────

def ensure_cert():
    if os.path.exists(CERT) and os.path.exists(KEY):
        return
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Skachivatel {socket.gethostname()}")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650)).sign(key, hashes.SHA256()))
    with open(KEY, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    with open(CERT, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


def fingerprint():
    der = ssl.PEM_cert_to_DER_cert(open(CERT, encoding="ascii").read())
    return hashlib.sha256(der).hexdigest()


def pc_id():
    return base64.b32encode(bytes.fromhex(fingerprint())).decode()[:8]


def lan_ips():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # адрес, через который идём в интернет
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.0.1", 9))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    good = [ip for ip in ips if ipaddress.ip_address(ip).is_private and not ip.startswith(("169.254.", "172.19.", "100."))]
    return sorted(good, key=lambda ip: (not ip.startswith("192.168."), ip))


def private_client(ip):
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback
    except ValueError:
        return False


# ─────────────────────────── Сопряжение ───────────────────────────

class Pair:
    lock = threading.Lock()
    secret = None       # на время окна «Подключить телефон»; на диск не пишется
    opened = 0
    fails = 0
    pending = {}        # nonce → {device, name, secret, event, ok}

    @classmethod
    def start(cls):
        with cls.lock:
            cls.secret, cls.opened, cls.fails = secrets.token_bytes(32), time.time(), 0

    @classmethod
    def stop(cls):
        with cls.lock:
            cls.secret = None

    @classmethod
    def active(cls):
        if cls.secret and time.time() - cls.opened > 600:  # окно само закрывается через 10 минут
            cls.stop()
        return bool(cls.secret)

    @classmethod
    def code(cls, slot=None):
        slot = int(time.time() // 30) if slot is None else slot
        d = hmac.new(cls.secret, str(slot).encode(), hashlib.sha256).digest()
        return "".join(ALPHABET[b % len(ALPHABET)] for b in d[:8])

    @classmethod
    def check(cls, nonce, key, device, mac):
        slot = int(time.time() // 30)
        for s in (slot, slot - 1):
            want = hmac.new(cls.code(s).encode(), f"{nonce}\n{key}\n{device}".encode(), hashlib.sha256).hexdigest()
            if hmac.compare_digest(want, mac):
                return True
        cls.fails += 1
        if cls.fails >= 5:
            log("Сопряжение: 5 неверных попыток — окно закрыто")
            cls.stop()
        return False

    @classmethod
    def qr(cls):
        ip = (lan_ips() or ["?"])[0]
        return f"skachivatel://pc?id={pc_id()}&code={cls.code()}&host={ip}&port={PORT}&fp={fingerprint()}"


def devices():
    return Cfg.data.setdefault("devices", {})


# ─────────────────────────── Сервер для телефона ───────────────────────────

SEEN_NONCES = {}


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

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _auth(self, body):
        """Проверка подписи запроса. Возвращает устройство или None."""
        dev = devices().get(self.headers.get("X-Device", ""))
        ts, nonce, sig = self.headers.get("X-Ts", "0"), self.headers.get("X-Nonce", ""), self.headers.get("X-Sig", "")
        if not dev or not nonce or abs(time.time() - int(ts or 0)) > 120:
            return None
        now = time.time()
        for n in [n for n, t in SEEN_NONCES.items() if now - t > 300]:
            del SEEN_NONCES[n]
        if nonce in SEEN_NONCES:
            return None
        path = urlparse(self.path).path + ("?" + urlparse(self.path).query if urlparse(self.path).query else "")
        msg = f"{self.command}\n{path}\n{ts}\n{nonce}\n{hashlib.sha256(body).hexdigest()}"
        want = hmac.new(base64.b64decode(dev["secret"]), msg.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, sig):
            return None
        SEEN_NONCES[nonce] = now
        dev["seen"] = time.strftime("%d.%m %H:%M")
        return dev

    def handle_one_request(self):
        if not private_client(self.client_address[0]):
            self.close_connection = True
            return
        super().handle_one_request()

    # ── сопряжение ──
    def do_POST(self):
        body = self._body()
        u = urlparse(self.path)
        if u.path == "/pair":
            return self.pair(body)
        dev = self._auth(body)
        if not dev:
            return self._json({"error": "нет доступа — подключите телефон заново"}, 401)
        d = json.loads(body or b"{}")
        if u.path == "/api/probe":
            self._json({"info": C.probe(d["url"])})
        elif u.path == "/api/add":
            job = Q.add(url=d["url"], what=d.get("what", "best"), where="pc", folder=int(d.get("folder", 0)),
                        who=f"phone:{dev['id']}", thumb=d.get("thumb"), title=d.get("title"))
            job["to_phone"] = bool(d.get("to_phone"))
            self._json({"ok": True, "id": job["id"]})
        elif u.path == "/api/cancel":
            self._json({"ok": Q.cancel(d["id"])})
        else:
            self._json({"error": "not found"}, 404)

    def pair(self, body):
        if not Pair.active():
            return self._json({"error": "На ПК не открыто окно «Подключить телефон»."}, 403)
        try:
            d = json.loads(body)
            nonce, key, device, mac = d["nonce"], d["key"], d["device"], d["mac"]
            name = str(d.get("name") or device)[:60]
            if len(base64.b64decode(key)) != 32 or nonce in Pair.pending:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self._json({"error": "неверный запрос"}, 400)
        if not Pair.check(nonce, key, device, mac):
            return self._json({"error": "Код не подошёл (он меняется каждые 30 секунд) — отсканируйте QR ещё раз."}, 403)
        ev = threading.Event()
        Pair.pending[nonce] = {"device": device, "name": name, "key": key, "event": ev, "ok": False,
                               "fp": hashlib.sha256(base64.b64decode(key)).hexdigest()[:12].upper()}
        log(f"Телефон «{name}» просит подключиться — подтвердите в окне")
        ev.wait(90)
        p = Pair.pending.pop(nonce, {})
        if not p.get("ok"):
            return self._json({"error": "На ПК не подтвердили подключение."}, 403)
        devices()[device] = {"id": device, "name": name, "secret": key, "added": time.strftime("%d.%m.%Y")}
        Cfg.save()
        Pair.stop()
        log(f"Телефон подключён: {name}")
        self._json({"ok": True, "pc": socket.gethostname(), "folders": [f["name"] for f in Cfg.data["folders"]]})

    # ── чтение ──
    def do_GET(self):
        u = urlparse(self.path)
        dev = self._auth(b"")
        if not dev:
            return self._json({"error": "нет доступа — подключите телефон заново"}, 401)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        who = f"phone:{dev['id']}"
        if u.path == "/api/info":
            self._json({"pc": socket.gethostname(), "folders": [f["name"] for f in Cfg.data["folders"]],
                        "vpn": bool(C.find_proxy())})
        elif u.path == "/api/jobs":
            with Q.lock:
                act = [pub(j) for j in Q.jobs if j["who"] == who]
            hist = [pub(h) for h in Q.history if h["who"] == who][:30]
            self._json({"active": act, "history": hist})
        elif u.path == "/api/file":
            h = next((x for x in Q.history if x["id"] == q.get("job") and x["who"] == who), None)
            if not h:
                return self._json({"error": "не найдено"}, 404)
            try:
                path = h["files"][int(q.get("i", 0))]
            except (IndexError, ValueError):
                return self._json({"error": "не найдено"}, 404)
            self.send_file(path)
        else:
            self._json({"error": "not found"}, 404)

    def send_file(self, path):
        size = os.path.getsize(path)
        start = 0
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            start = int(rng[6:].split("-")[0] or 0)
        self.send_response(206 if start else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size - start))
        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" +
                         __import__("urllib.parse").parse.quote(os.path.basename(path)))
        if start:
            self.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            while chunk := f.read(1 << 20):
                self.wfile.write(chunk)


def pub(j):
    files = [f for f in j.get("files", []) if os.path.exists(f)]
    return {"id": j["id"], "url": j.get("url"), "title": j.get("title"), "what": j.get("what"),
            "status": j.get("status"), "pct": j.get("pct", 100 if j.get("status") == "done" else 0),
            "text": j.get("text") or j.get("error") or "", "size": j.get("size", 0),
            "files": [{"name": os.path.basename(f), "size": os.path.getsize(f)} for f in files]}


# ─────────────────────────── Поиск ПК в сети ───────────────────────────

def discovery():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", DISCOVERY_PORT))
    while True:
        try:
            data, addr = s.recvfrom(256)
            if not private_client(addr[0]):
                continue
            msg = data.decode("ascii", "ignore").strip()
            if msg.upper() != f"SKACHIVATEL? {pc_id()}":
                continue  # отвечаем только на точную метку этого ПК
            host = next((ip for ip in lan_ips() if ip.rsplit(".", 1)[0] == addr[0].rsplit(".", 1)[0]), (lan_ips() or [""])[0])
            reply = {"app": "skachivatel", "id": pc_id(), "name": socket.gethostname(), "host": host,
                     "port": PORT, "fp": fingerprint(), "pairing": Pair.active()}
            s.sendto(json.dumps(reply).encode(), addr)
        except OSError:
            time.sleep(1)


def start():
    ensure_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(CERT, KEY)
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    except OSError as e:
        log(f"Связь с телефоном не запущена: {e}")
        return
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=discovery, daemon=True).start()
    log(f"Связь с телефоном: {', '.join(lan_ips()) or 'нет сети'}:{PORT} · метка ПК {pc_id()}")


def qr_svg(text):
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    return img.to_string(encoding="unicode")
