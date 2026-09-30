package ru.skachivatel

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/** Задание: что скачать и куда. target: phone — на телефон, pc — на ПК, pc2phone — на ПК и прислать сюда. */
data class Job(
    val id: String = System.currentTimeMillis().toString(36) + (0..999).random(),
    val url: String,
    val title: String,
    val what: String,
    val target: String,
    val folder: Int = 0,
    val thumb: String? = null,
    val status: String = "queued",       // queued | running | done | error | cancelled
    val pct: Float = 0f,
    val text: String = "в очереди",
    val files: List<String> = emptyList(),   // content:// на телефоне или имена файлов на ПК
    val pcId: String? = null,
    val time: Long = System.currentTimeMillis(),
) {
    val active get() = status == "queued" || status == "running"
    fun toJson(): JSONObject = JSONObject().put("id", id).put("url", url).put("title", title).put("what", what)
        .put("target", target).put("status", status).put("text", text).put("time", time).put("thumb", thumb)
        .put("files", JSONArray(files))

    companion object {
        fun fromJson(o: JSONObject) = Job(o.getString("id"), o.getString("url"), o.optString("title"), o.optString("what"),
            o.optString("target"), status = o.optString("status"), text = o.optString("text"), time = o.optLong("time"),
            thumb = o.optString("thumb").ifBlank { null }, pct = 100f,
            files = o.optJSONArray("files")?.let { a -> (0 until a.length()).map { a.getString(it) } } ?: emptyList())
    }
}

object Jobs {
    val list = MutableStateFlow(loadHistory())

    private fun loadHistory(): List<Job> = runCatching {
        val a = JSONArray(Prefs.history)
        (0 until a.length()).map { Job.fromJson(a.getJSONObject(it)) }
    }.getOrDefault(emptyList())

    private fun saveHistory() {
        val done = list.value.filter { !it.active }.take(60)
        Prefs.history = JSONArray(done.map { it.toJson() }).toString()
    }

    fun add(ctx: Context, job: Job) {
        list.update { listOf(job) + it }
        ctx.startForegroundService(Intent(ctx, DownloadService::class.java))
    }

    fun set(id: String, f: (Job) -> Job) {
        list.update { l -> l.map { if (it.id == id) f(it) else it } }
        if (list.value.firstOrNull { it.id == id }?.active == false) saveHistory()
    }

    fun cancel(job: Job) {
        if (job.target == "phone") Engine.cancel(job.id) else job.pcId?.let { App.scope.launch { Pc.cancel(it) } }
        set(job.id) { it.copy(status = "cancelled", text = "отменено") }
    }

    fun remove(job: Job) {
        list.update { l -> l.filter { it.id != job.id } }
        saveHistory()
    }
}

/** Фоновая служба: качает задания телефона по одному и следит за заданиями, отправленными на ПК. */
class DownloadService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var running = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(1, note("Скачиватель", "готовлю…", 0), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        if (!running) {
            running = true
            scope.launch { loop() }
        }
        return START_NOT_STICKY
    }

    private suspend fun loop() {
        while (true) {
            val jobs = Jobs.list.value
            // 1. задания для ПК: отправить новые, опросить идущие
            jobs.filter { it.target != "phone" && it.active }.let { if (it.isNotEmpty()) pcTick(it) }
            // 2. одно задание на телефоне
            val next = Jobs.list.value.firstOrNull { it.target == "phone" && it.status == "queued" }
            if (next != null) runPhone(next)
            if (Jobs.list.value.none { it.active }) break
            if (next == null) delay(2000)
        }
        running = false
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private suspend fun runPhone(job: Job) {
        Jobs.set(job.id) { it.copy(status = "running", text = "начинаю…") }
        try {
            val files = Engine.download(this, job) { p, t ->
                Jobs.set(job.id) { it.copy(pct = p, text = t) }
                update(job.title, t, p.toInt())
            }
            if (Jobs.list.value.firstOrNull { it.id == job.id }?.status == "cancelled") return
            if (files.isEmpty()) throw IllegalStateException("Ничего не скачалось")
            Jobs.set(job.id) { it.copy(status = "done", pct = 100f, files = files, text = "готово · Загрузки/Скачиватель") }
        } catch (e: Exception) {
            if (Jobs.list.value.firstOrNull { it.id == job.id }?.status != "cancelled")
                Jobs.set(job.id) { it.copy(status = "error", text = Engine.explain(e.message ?: "")) }
        }
    }

    private fun pcTick(jobs: List<Job>) {
        try {
            for (j in jobs.filter { it.pcId == null && it.status == "queued" }) {
                val id = Pc.add(j.url, j.what, j.folder, j.target == "pc2phone", j.thumb, j.title)
                Jobs.set(j.id) { it.copy(pcId = id, status = "running", text = "отправлено на ПК") }
            }
            val (active, history) = Pc.jobs()
            val all = (0 until active.length()).map { active.getJSONObject(it) } + (0 until history.length()).map { history.getJSONObject(it) }
            for (j in Jobs.list.value.filter { it.pcId != null && it.active }) {
                val r = all.firstOrNull { it.getString("id") == j.pcId } ?: continue
                when (r.getString("status")) {
                    "done" -> if (j.target == "pc2phone") {
                        val files = r.getJSONArray("files")
                        val got = (0 until files.length()).map { i ->
                            Jobs.set(j.id) { it.copy(text = "получаю с ПК: файл ${i + 1} из ${files.length()}") }
                            update(j.title, "получаю с ПК…", 99)
                            Pc.fetch(this, j.pcId!!, i, files.getJSONObject(i).getString("name"))
                        }
                        Jobs.set(j.id) { it.copy(status = "done", pct = 100f, files = got, text = "готово · Загрузки/Скачиватель/С компьютера") }
                    } else {
                        val names = r.getJSONArray("files").let { a -> (0 until a.length()).map { a.getJSONObject(it).getString("name") } }
                        Jobs.set(j.id) { it.copy(status = "done", pct = 100f, files = names, text = "готово · сохранено на ПК") }
                    }
                    "error" -> Jobs.set(j.id) { it.copy(status = "error", text = r.optString("text")) }
                    "cancelled" -> Jobs.set(j.id) { it.copy(status = "cancelled", text = "отменено") }
                    else -> {
                        val p = r.optDouble("pct", 0.0).toFloat()
                        Jobs.set(j.id) { it.copy(pct = p, text = "на ПК: " + r.optString("text")) }
                        update(j.title, "на ПК: " + r.optString("text"), p.toInt())
                    }
                }
            }
        } catch (e: Exception) {
            for (j in jobs.filter { it.pcId == null }) Jobs.set(j.id) { it.copy(status = "error", text = e.message ?: "ПК не отвечает") }
        }
    }

    private fun note(title: String, text: String, pct: Int): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, App.CHANNEL).setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle(title.take(60)).setContentText(text).setProgress(100, pct, pct <= 0)
            .setContentIntent(open).setOngoing(true).build()
    }

    private fun update(title: String, text: String, pct: Int) {
        getSystemService(android.app.NotificationManager::class.java).notify(1, note(title, text, pct))
    }

    override fun onDestroy() {
        running = false
        super.onDestroy()
    }
}
