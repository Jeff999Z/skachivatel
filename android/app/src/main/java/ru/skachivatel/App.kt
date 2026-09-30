package ru.skachivatel

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import com.yausername.ffmpeg.FFmpeg
import com.yausername.youtubedl_android.YoutubeDL
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONObject

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        app = this
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "Загрузки", NotificationManager.IMPORTANCE_LOW)
        )
        // движок (Python + yt-dlp + ffmpeg) распаковывается при первом запуске — в фоне
        scope.launch {
            runCatching {
                YoutubeDL.getInstance().init(this@App)
                FFmpeg.getInstance().init(this@App)
                engineReady.complete(true)
            }.onFailure { engineReady.complete(false); engineError = it.message }
        }
    }

    companion object {
        const val CHANNEL = "downloads"
        lateinit var app: App
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val engineReady = CompletableDeferred<Boolean>()
        var engineError: String? = null
    }
}

/** Настройки и привязка к ПК — в SharedPreferences (только на этом телефоне). */
object Prefs {
    private val p get() = App.app.getSharedPreferences("s", Context.MODE_PRIVATE)

    /** Привязанный ПК: id, host, port, fp (отпечаток сертификата), device, key (base64), name. */
    var pc: JSONObject?
        get() = p.getString("pc", null)?.let { JSONObject(it) }
        set(v) = p.edit().putString("pc", v?.toString()).apply()

    var pcFolders: List<String>
        get() = p.getString("pcFolders", "")!!.split("\n").filter { it.isNotBlank() }
        set(v) = p.edit().putString("pcFolders", v.joinToString("\n")).apply()

    var target: String                 // phone | pc | pc2phone — куда качать по умолчанию
        get() = p.getString("target", "phone")!!
        set(v) = p.edit().putString("target", v).apply()

    var pcFolder: Int
        get() = p.getInt("pcFolder", 0)
        set(v) = p.edit().putInt("pcFolder", v).apply()

    var history: String
        get() = p.getString("history", "[]")!!
        set(v) = p.edit().putString("history", v).apply()

    val deviceId: String
        get() = p.getString("device", null) ?: java.util.UUID.randomUUID().toString().also {
            p.edit().putString("device", it).apply()
        }
}
