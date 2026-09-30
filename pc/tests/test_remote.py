"""
Проверка связи «телефон ↔ ПК» без телефона: поиск ПК, сопряжение по коду, подписанные запросы, защита.
    python tests/test_remote.py
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import ssl
import sys
import tempfile
import threading
import time
import urllib.request

os.environ["APPDATA"] = tempfile.mkdtemp()  # отдельная папка данных — рабочие настройки не трогаем
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core as C  # noqa: E402
import remote  # noqa: E402

C.Cfg.load()
remote.start()
time.sleep(1)
ok = lambda cond, text: print(("✅ " if cond else "❌ ") + text)  # noqa: E731

# 1. поиск ПК в сети по метке
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(3)
s.sendto(f"SKACHIVATEL? {remote.pc_id()}".encode(), ("127.0.0.1", remote.DISCOVERY_PORT))
reply = json.loads(s.recvfrom(2048)[0])
ok(reply["fp"] == remote.fingerprint(), f"ПК найден по метке {reply['id']}, отпечаток совпадает")
s.sendto(b"SKACHIVATEL? *", ("127.0.0.1", remote.DISCOVERY_PORT))
try:
    s.recvfrom(2048)
    ok(False, "на «*» ответил (не должен)")
except socket.timeout:
    ok(True, "на чужую метку «*» молчит")

# HTTPS с проверкой отпечатка (как телефон)
ctx = ssl.create_default_context()
ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE


def req(method, path, body=b"", headers=None):
    conn = __import__("http.client").client.HTTPSConnection("127.0.0.1", remote.PORT, context=ctx, timeout=100)
    conn.request(method, path, body=body, headers=headers or {})
    der = conn.sock.getpeercert(binary_form=True)
    assert hashlib.sha256(der).hexdigest() == reply["fp"], "подмена ПК!"
    r = conn.getresponse()
    return r.status, json.loads(r.read() or b"{}")


# 2. сопряжение: без открытого окна — отказ
key = base64.b64encode(secrets.token_bytes(32)).decode()
device = "test-phone-1"


def pair_body(code):
    nonce = secrets.token_hex(16)
    mac = hmac.new(code.encode(), f"{nonce}\n{key}\n{device}".encode(), hashlib.sha256).hexdigest()
    return json.dumps({"nonce": nonce, "key": key, "device": device, "mac": mac, "name": "Тестовый телефон"}).encode()


st, r = req("POST", "/pair", pair_body("AAAAAAAA"))
ok(st == 403, f"без окна «Подключить телефон» отказ: {r.get('error')}")
remote.Pair.start()
st, r = req("POST", "/pair", pair_body("WRONGCOD"))
ok(st == 403, "неверный код — отказ")


def approve():  # человек нажимает «Разрешить» на ПК
    for _ in range(50):
        for n, p in list(remote.Pair.pending.items()):
            p["ok"] = True
            p["event"].set()
            return
        time.sleep(0.1)


threading.Thread(target=approve).start()
st, r = req("POST", "/pair", pair_body(remote.Pair.code()))
ok(st == 200 and r.get("ok"), f"сопряжение с верным кодом и подтверждением: {r}")
ok(not remote.Pair.active(), "окно сопряжения закрылось само после успеха")


# 3. подписанные запросы
def signed(method, path, body=b""):
    ts, nonce = str(int(time.time())), secrets.token_hex(12)
    msg = f"{method}\n{path}\n{ts}\n{nonce}\n{hashlib.sha256(body).hexdigest()}"
    sig = hmac.new(base64.b64decode(key), msg.encode(), hashlib.sha256).hexdigest()
    h = {"X-Device": device, "X-Ts": ts, "X-Nonce": nonce, "X-Sig": sig, "Content-Type": "application/json"}
    return h, body


h, b = signed("GET", "/api/info")
st, r = req("GET", "/api/info", b, h)
ok(st == 200 and r.get("folders"), f"подписанный запрос: ПК {r.get('pc')}, папки {r.get('folders')}")
st, r = req("GET", "/api/info", b, h)
ok(st == 401, "повтор того же запроса (перехват) — отказ")
st, r = req("GET", "/api/info", b"", {"X-Device": device})
ok(st == 401, "запрос без подписи — отказ")
bad = dict(h, **{"X-Nonce": "zzz", "X-Sig": "0" * 64})
st, r = req("GET", "/api/info", b"", bad)
ok(st == 401, "поддельная подпись — отказ")

body = json.dumps({"url": "https://rutube.ru/video/029d7bc2db277cef1ed580060e02c426/"}).encode()
h, b = signed("POST", "/api/probe", body)
st, r = req("POST", "/api/probe", b, h)
info = r.get("info", {})
ok(st == 200 and info.get("video"), f"разбор ссылки через ПК: «{info.get('title')}», качеств: {len(info.get('video') or [])}")
print("готово")
os._exit(0)
