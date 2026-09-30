package ru.skachivatel

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.provider.MediaStore
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.InputStream
import java.net.URI

/** Что есть по ссылке — то же, что показывает бот и приложение на ПК. */
data class Quality(val h: Int, val size: Long, val fps: Int?)
data class AudioInfo(val abr: Int, val ext: String, val size: Long, val mp3: Long)
data class Sub(val lang: String, val name: String, val auto: Boolean)
data class Info(
    val kind: String,              // video | audio | playlist | gallery | pinterest | telegram | error
    val title: String = "",
    val uploader: String? = null,
    val duration: Long = 0,
    val thumb: String? = null,
    val video: List<Quality> = emptyList(),
    val audio: AudioInfo? = null,
    val subs: List<Sub> = emptyList(),
    val count: Int = 0,
    val photos: Int = 0,
    val videos: Int = 0,
    val error: String? = null,
    val viaPc: Boolean = false,    // разобрано на ПК (Pinterest-доски, Telegram…)
)

object Engine {
    private val LANG = mapOf("ru" to "Русские", "en" to "Английские", "uk" to "Украинские", "de" to "Немецкие",
        "es" to "Испанские", "fr" to "Французские")

    /** Сайты, которые целиком умеет только ПК (доски Pinterest, Telegram-каналы). */
    fun needsPc(url: String): Boolean {
        val u = runCatching { URI(url) }.getOrNull() ?: return false
        val host = u.host.orEmpty().lowercase()
        val parts = u.path.orEmpty().trim('/').split('/').filter { it.isNotBlank() }
        if (host.contains("t.me") || host.contains("telegram.me")) return true
        if (host.contains("pinterest.") && parts.isNotEmpty() && parts[0] != "pin") return true
        return false
    }

    suspend fun probe(url: String): Info {
        if (!App.engineReady.await()) return Info("error", error = "Движок не запустился: ${App.engineError}")
        return try {
            val req = YoutubeDLRequest(url).apply {
                addOption("-J"); addOption("--flat-playlist"); addOption("--no-warnings")
            }
            parse(JSONObject(YoutubeDL.getInstance().execute(req).out))
        } catch (e: Exception) {
            Info("error", title = url, error = explain(e.message ?: ""))
        }
    }

    fun parse(j: JSONObject): Info {
        if (j.optString("_type") == "playlist")
            return Info("playlist", j.optString("title"), j.optString("uploader").ifBlank { null },
                count = j.optJSONArray("entries")?.length() ?: 0, thumb = thumb(j))
        val dur = j.optDouble("duration", 0.0).toLong()
        val fmts = j.optJSONArray("formats") ?: JSONArray()
        var bestA: JSONObject? = null
        val heights = HashMap<Int, Pair<Double, Quality>>()
        for (i in 0 until fmts.length()) {
            val f = fmts.getJSONObject(i)
            val v = f.optString("vcodec", "none"); val a = f.optString("acodec", "none")
            if (v == "none" && a != "none" && (bestA == null || f.optDouble("abr", 0.0) > bestA.optDouble("abr", 0.0))) bestA = f
        }
        val aSize = bestA?.let { size(it, dur) } ?: 0L
        for (i in 0 until fmts.length()) {
            val f = fmts.getJSONObject(i)
            val h = f.optInt("height", 0)
            if (h <= 0 || f.optString("vcodec", "none") == "none") continue
            var s = size(f, dur)
            if (f.optString("acodec", "none") == "none") s += aSize
            val tbr = f.optDouble("tbr", 0.0)
            val cur = heights[h]
            if (cur == null || tbr > cur.first) heights[h] = tbr to Quality(h, s, f.optDouble("fps", 0.0).toInt().takeIf { it > 0 })
        }
        val subs = mutableListOf<Sub>()
        for ((key, auto) in listOf("subtitles" to false, "automatic_captions" to true)) {
            val o = j.optJSONObject(key) ?: continue
            for (lang in o.keys()) {
                val base = lang.substringBefore('-')
                if (auto && base !in setOf("ru", "en", "uk")) continue
                subs += Sub(lang, LANG[base] ?: lang, auto)
            }
        }
        val abr = bestA?.optDouble("abr", 128.0)?.toInt() ?: 128
        val video = heights.values.map { it.second }.sortedByDescending { it.h }
        return Info(
            kind = if (video.isNotEmpty()) "video" else "audio",
            title = j.optString("title"), uploader = j.optString("uploader").ifBlank { j.optString("channel").ifBlank { null } },
            duration = dur, thumb = thumb(j), video = video,
            audio = AudioInfo(abr, bestA?.optString("ext") ?: "m4a", if (aSize > 0) aSize else abr * 125L * dur, 320 * 125L * dur),
            subs = subs.take(12),
        )
    }

    private fun size(f: JSONObject, dur: Long): Long {
        val s = f.optLong("filesize", 0).takeIf { it > 0 } ?: f.optLong("filesize_approx", 0)
        return if (s > 0) s else (f.optDouble("tbr", 0.0) * 125 * dur).toLong()
    }

    private fun thumb(j: JSONObject): String? {
        val t = j.optJSONArray("thumbnails")
        var best: JSONObject? = null
        if (t != null) for (i in 0 until t.length()) {
            val o = t.getJSONObject(i)
            if (best == null || o.optInt("width") * o.optInt("height") > best.optInt("width") * best.optInt("height")) best = o
        }
        return best?.optString("url")?.ifBlank { null } ?: j.optString("thumbnail").ifBlank { null }
    }

    fun explain(msg: String): String {
        val t = msg.lowercase()
        return when {
            "drm" in t -> "Видео защищено от копирования (DRM) — такое не скачивается."
            "unable to resolve" in t || "failed to resolve" in t || "timed out" in t ->
                "Сайт не открывается — возможно, он заблокирован. Включите VPN на телефоне."
            "login" in t || "cookies" in t || "private" in t || "sign in" in t ->
                "Сайт отдаёт это только после входа в аккаунт — скачайте через приложение на ПК (там можно добавить вход)."
            "unsupported url" in t -> "Этот сайт или тип ссылки не поддерживается."
            else -> msg.lines().lastOrNull { it.isNotBlank() }?.take(200) ?: "неизвестная ошибка"
        }
    }

    /** Опции yt-dlp для выбранного «что». Совпадают с ПК-версией (core.py). */
    fun options(req: YoutubeDLRequest, what: String) {
        when {
            what == "mp3" -> { req.addOption("-f", "ba/b"); req.addOption("-x"); req.addOption("--audio-format", "mp3"); req.addOption("--audio-quality", "0") }
            what == "audio" -> { req.addOption("-f", "ba/b"); req.addOption("-x") }
            what.startsWith("subs:") -> {
                req.addOption("--skip-download"); req.addOption("--write-subs"); req.addOption("--write-auto-subs")
                req.addOption("--sub-langs", what.removePrefix("subs:")); req.addOption("--convert-subs", "srt")
            }
            what.toIntOrNull() != null -> {
                req.addOption("-f", "bv*[height=$what]+ba/b[height=$what]/bv*[height<=$what]+ba/b[height<=$what]/bv*+ba/b")
                req.addOption("--merge-output-format", "mp4/mkv")
            }
            else -> { req.addOption("-f", "bv*+ba/b"); req.addOption("-S", "res,fps,vbr,abr"); req.addOption("--merge-output-format", "mp4/mkv") }
        }
    }

    /** Скачать на телефон: во временную папку, потом — в «Загрузки/Скачиватель/<сайт>». */
    suspend fun download(ctx: Context, job: Job, onProgress: (Float, String) -> Unit): List<String> {
        if (!App.engineReady.await()) throw IllegalStateException("Движок не запустился: ${App.engineError}")
        val tmp = File(ctx.cacheDir, "dl/${job.id}").apply { deleteRecursively(); mkdirs() }
        if (job.what == "thumb") {
            val f = File(tmp, "${safe(job.title)}.jpg")
            java.net.URL(job.thumb).openStream().use { i -> f.outputStream().use { i.copyTo(it) } }
        } else {
            val req = YoutubeDLRequest(job.url).apply {
                addOption("-o", "${tmp.absolutePath}/%(extractor_key)s/%(title).150B.%(ext)s")
                addOption("--no-mtime"); addOption("--embed-metadata")
                addOption("--retries", "10"); addOption("--concurrent-fragments", "4")
            }
            options(req, job.what)
            YoutubeDL.getInstance().execute(req, job.id) { progress, eta, _ ->
                onProgress(progress, if (progress > 0) "${progress.toInt()}%" + (if (eta > 0) " · осталось ${eta / 60}:${"%02d".format(eta % 60)}" else "") else "готовлю…")
            }
        }
        val out = mutableListOf<String>()
        tmp.walkTopDown().filter { it.isFile && !it.name.endsWith(".part") && !it.name.endsWith(".ytdl") }.forEach { f ->
            val site = f.parentFile?.takeIf { it != tmp }?.name ?: "Разное"
            out += save(ctx, f.inputStream(), f.name, "Скачиватель/$site")
        }
        tmp.deleteRecursively()
        return out
    }

    fun cancel(id: String) = runCatching { YoutubeDL.getInstance().destroyProcessById(id) }

    /** В общую папку «Загрузки» — видно в Проводнике, Галерее и на ПК по кабелю. Одинаковые имена — «(1)». */
    fun save(ctx: Context, input: InputStream, name: String, sub: String): String {
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.RELATIVE_PATH, "${Environment.DIRECTORY_DOWNLOADS}/$sub")
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val uri: Uri = ctx.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: throw IllegalStateException("Не получилось создать файл в «Загрузках»")
        input.use { i -> ctx.contentResolver.openOutputStream(uri)!!.use { i.copyTo(it, 1 shl 16) } }
        ctx.contentResolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
        return uri.toString()
    }

    fun safe(s: String) = s.replace(Regex("[<>:\"/\\\\|?*\\u0000-\\u001f]"), "_").trim().take(120).ifBlank { "файл" }

    suspend fun update(ctx: Context): String = try {
        App.engineReady.await()
        val st = YoutubeDL.getInstance().updateYoutubeDL(ctx, YoutubeDL.UpdateChannel._NIGHTLY)
        "Движок: ${YoutubeDL.getInstance().versionName(ctx) ?: "?"} (${if (st == YoutubeDL.UpdateStatus.DONE) "обновлён" else "уже свежий"})"
    } catch (e: Exception) {
        "Обновить не получилось: ${e.message}"
    }
}
