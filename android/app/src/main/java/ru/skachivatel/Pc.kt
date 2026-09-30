package ru.skachivatel

import android.net.Uri
import android.os.Build
import org.json.JSONArray
import org.json.JSONObject
import java.io.InputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.URL
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.X509Certificate
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/**
 * Связь с приложением «Скачиватель» на ПК (протокол — pc/remote.py):
 *  • HTTPS; сертификат ПК проверяется по отпечатку из QR — чужой ПК не подставить;
 *  • сопряжение: код по сети не идёт, отправляется подпись HMAC(код, nonce+ключ+устройство);
 *  • каждый запрос подписан ключом телефона.
 */
object Pc {
    private val rnd = SecureRandom()

    val paired get() = Prefs.pc != null
    val name get() = Prefs.pc?.optString("name") ?: ""

    // ── сопряжение ──
    data class Target(val id: String, val host: String, val port: Int, val fp: String, val code: String)

    /** skachivatel://pc?id=…&code=…&host=…&port=…&fp=… */
    fun parseQr(text: String): Target? {
        val u = runCatching { Uri.parse(text.trim()) }.getOrNull() ?: return null
        if (u.scheme != "skachivatel") return null
        val id = u.getQueryParameter("id") ?: return null
        val fp = u.getQueryParameter("fp") ?: return null
        if (!base32(hex(fp)).startsWith(id.uppercase())) return null   // метка должна соответствовать отпечатку
        return Target(id.uppercase(), u.getQueryParameter("host") ?: return null,
            u.getQueryParameter("port")?.toIntOrNull() ?: 47822, fp, u.getQueryParameter("code")?.uppercase() ?: return null)
    }

    /** Ручной ввод: метка ПК + код → поиск ПК в сети по метке (широковещание UDP 47823). */
    fun discover(id: String, code: String): Target? {
        DatagramSocket().use { s ->
            s.broadcast = true
            s.soTimeout = 1200
            val msg = "SKACHIVATEL? ${id.uppercase().trim()}".toByteArray()
            repeat(3) {
                s.send(DatagramPacket(msg, msg.size, InetAddress.getByName("255.255.255.255"), 47823))
                try {
                    val buf = ByteArray(2048)
                    val p = DatagramPacket(buf, buf.size)
                    s.receive(p)
                    val j = JSONObject(String(p.data, 0, p.length))
                    val fp = j.getString("fp")
                    if (base32(hex(fp)).startsWith(id.uppercase().trim()))
                        return Target(id.uppercase().trim(), j.getString("host"), j.optInt("port", 47822), fp, code.uppercase().trim())
                } catch (_: java.net.SocketTimeoutException) {}
            }
        }
        return null
    }

    fun pair(t: Target): String? {
        val key = ByteArray(32).also { rnd.nextBytes(it) }
        val keyB64 = android.util.Base64.encodeToString(key, android.util.Base64.NO_WRAP)
        val device = Prefs.deviceId
        val nonce = randHex(16)
        val mac = hmacHex(t.code.toByteArray(), "$nonce\n$keyB64\n$device")
        val body = JSONObject().put("nonce", nonce).put("key", keyB64).put("device", device).put("mac", mac)
            .put("name", "${Build.MANUFACTURER} ${Build.MODEL}".trim())
        val c = open("https://${t.host}:${t.port}/pair", t.fp, "POST", body.toString().toByteArray(), readTimeout = 100_000)
        val r = readJson(c)
        if (c.responseCode != 200) return r.optString("error", "Ошибка ${c.responseCode}")
        Prefs.pc = JSONObject().put("id", t.id).put("host", t.host).put("port", t.port).put("fp", t.fp)
            .put("device", device).put("key", keyB64).put("name", r.optString("pc"))
        Prefs.pcFolders = r.optJSONArray("folders")?.let { a -> (0 until a.length()).map { a.getString(it) } } ?: emptyList()
        return null
    }

    fun forget() { Prefs.pc = null }

    // ── запросы ──
    private fun call(method: String, path: String, body: JSONObject? = null, retry: Boolean = true): JSONObject {
        val pc = Prefs.pc ?: throw IllegalStateException("ПК не подключён")
        val bytes = body?.toString()?.toByteArray() ?: ByteArray(0)
        return try {
            val c = open("https://${pc.getString("host")}:${pc.getInt("port")}$path", pc.getString("fp"), method, bytes, sign = pc)
            val j = readJson(c)
            if (c.responseCode == 401) throw IllegalStateException("ПК не узнал телефон — подключите заново")
            if (c.responseCode != 200) throw IllegalStateException(j.optString("error", "Ошибка ${c.responseCode}"))
            j
        } catch (e: java.io.IOException) {
            // адрес ПК мог смениться — ищем по метке и повторяем один раз
            val found = if (retry) runCatching { discover(pc.getString("id"), "-") }.getOrNull() else null
            if (found != null && found.fp == pc.getString("fp")) {
                Prefs.pc = pc.put("host", found.host).put("port", found.port)
                call(method, path, body, retry = false)
            } else throw IllegalStateException("ПК не в сети: включите «Скачиватель» на компьютере и подключитесь к той же Wi-Fi")
        }
    }

    fun info(): JSONObject = call("GET", "/api/info").also { j ->
        j.optJSONArray("folders")?.let { a -> Prefs.pcFolders = (0 until a.length()).map { a.getString(it) } }
    }

    fun probe(url: String): Info = infoFromJson(call("POST", "/api/probe", JSONObject().put("url", url)).getJSONObject("info"))

    fun add(url: String, what: String, folder: Int, toPhone: Boolean, thumb: String?, title: String?): String =
        call("POST", "/api/add", JSONObject().put("url", url).put("what", what).put("folder", folder)
            .put("to_phone", toPhone).put("thumb", thumb).put("title", title)).getString("id")

    fun cancel(id: String) = runCatching { call("POST", "/api/cancel", JSONObject().put("id", id)) }

    fun jobs(): Pair<JSONArray, JSONArray> = call("GET", "/api/jobs").let { it.getJSONArray("active") to it.getJSONArray("history") }

    /** Готовый файл с ПК → «Загрузки/Скачиватель/С компьютера». */
    fun fetch(ctx: android.content.Context, job: String, i: Int, name: String): String {
        val pc = Prefs.pc!!
        val path = "/api/file?job=$job&i=$i"
        val c = open("https://${pc.getString("host")}:${pc.getInt("port")}$path", pc.getString("fp"), "GET", ByteArray(0),
            sign = pc, readTimeout = 600_000)
        if (c.responseCode != 200) throw IllegalStateException("ПК не отдал файл (${c.responseCode})")
        return Engine.save(ctx, c.inputStream, name, "Скачиватель/С компьютера")
    }

    fun infoFromJson(j: JSONObject): Info {
        val v = j.optJSONArray("video") ?: JSONArray()
        val a = j.optJSONObject("audio")
        val s = j.optJSONArray("subs") ?: JSONArray()
        return Info(
            kind = j.optString("kind"), title = j.optString("title"), uploader = j.optString("uploader").ifBlank { null },
            duration = j.optDouble("duration", 0.0).toLong(), thumb = j.optString("thumb").ifBlank { null },
            video = (0 until v.length()).map { v.getJSONObject(it).let { q -> Quality(q.getInt("h"), q.optLong("size"), q.optInt("fps").takeIf { f -> f > 0 }) } },
            audio = a?.let { AudioInfo(it.optInt("abr"), it.optString("ext"), it.optLong("size"), it.optLong("mp3")) },
            subs = (0 until s.length()).map { s.getJSONObject(it).let { x -> Sub(x.getString("lang"), x.optString("name"), x.optBoolean("auto")) } },
            count = j.optInt("count", j.optInt("pins", 0)), photos = j.optInt("photos"), videos = j.optInt("videos"),
            error = j.optString("error").ifBlank { null }, viaPc = true,
        )
    }

    // ── HTTPS с проверкой отпечатка ──
    private fun open(url: String, fp: String, method: String, body: ByteArray, sign: JSONObject? = null,
                     readTimeout: Int = 60_000): HttpsURLConnection {
        val c = URL(url).openConnection() as HttpsURLConnection
        c.sslSocketFactory = pinned(fp)
        c.hostnameVerifier = javax.net.ssl.HostnameVerifier { _, _ -> true }  // личность ПК — по отпечатку, не по имени
        c.requestMethod = method
        c.connectTimeout = 5000
        c.readTimeout = readTimeout
        c.setRequestProperty("Content-Type", "application/json")
        if (sign != null) {
            val u = URL(url)
            val p = u.path + (u.query?.let { "?$it" } ?: "")
            val ts = (System.currentTimeMillis() / 1000).toString()
            val nonce = randHex(12)
            val key = android.util.Base64.decode(sign.getString("key"), android.util.Base64.NO_WRAP)
            c.setRequestProperty("X-Device", sign.getString("device"))
            c.setRequestProperty("X-Ts", ts)
            c.setRequestProperty("X-Nonce", nonce)
            c.setRequestProperty("X-Sig", hmacHex(key, "$method\n$p\n$ts\n$nonce\n${sha256Hex(body)}"))
        }
        if (method == "POST") {
            c.doOutput = true
            c.outputStream.use { it.write(body) }
        }
        return c
    }

    private fun pinned(fp: String): javax.net.ssl.SSLSocketFactory {
        val tm = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) = Unit
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                if (sha256Hex(chain[0].encoded) != fp.lowercase())
                    throw java.security.cert.CertificateException("Отпечаток ПК не совпадает — это не ваш компьютер")
            }
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        }
        return SSLContext.getInstance("TLS").apply { init(null, arrayOf(tm), rnd) }.socketFactory
    }

    private fun readJson(c: HttpsURLConnection): JSONObject {
        val s: InputStream? = if (c.responseCode in 200..299) c.inputStream else c.errorStream
        val t = s?.bufferedReader()?.readText().orEmpty()
        return runCatching { JSONObject(t) }.getOrDefault(JSONObject())
    }

    private fun hmacHex(key: ByteArray, msg: String): String =
        Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(key, "HmacSHA256")) }.doFinal(msg.toByteArray()).hex()

    private fun sha256Hex(b: ByteArray) = MessageDigest.getInstance("SHA-256").digest(b).hex()
    private fun randHex(n: Int) = ByteArray(n).also { rnd.nextBytes(it) }.hex()
    private fun ByteArray.hex() = joinToString("") { "%02x".format(it) }
    private fun hex(s: String) = s.chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    /** RFC 4648 base32 без «=» — как base64.b32encode на ПК. */
    private fun base32(b: ByteArray): String {
        val abc = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
        val sb = StringBuilder()
        var buf = 0; var bits = 0
        for (x in b) {
            buf = (buf shl 8) or (x.toInt() and 0xff); bits += 8
            while (bits >= 5) { sb.append(abc[(buf shr (bits - 5)) and 31]); bits -= 5 }
        }
        if (bits > 0) sb.append(abc[(buf shl (5 - bits)) and 31])
        return sb.toString()
    }
}
