package ru.skachivatel

import android.Manifest
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil3.compose.AsyncImage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {
    private val incoming = mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        handle(intent)
        setContent { SkTheme { Root(incoming) } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    private fun handle(i: Intent?) {
        when (i?.action) {
            Intent.ACTION_SEND -> incoming.value = i.getStringExtra(Intent.EXTRA_TEXT)
            Intent.ACTION_VIEW -> incoming.value = i.dataString
        }
    }
}

private val URL_RE = Regex("https?://[^\\s<>\"']+")

@Composable
private fun Root(incoming: MutableState<String?>) {
    var screen by remember { mutableStateOf("home") }
    var card by remember { mutableStateOf<String?>(null) }
    var pairLink by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(incoming.value) {
        val t = incoming.value ?: return@LaunchedEffect
        incoming.value = null
        if (t.startsWith("skachivatel://")) { pairLink = t; screen = "pc" }
        else URL_RE.find(t)?.value?.let { card = it; screen = "home" }
    }
    BackHandler(screen != "home") { screen = if (screen == "scan") "pc" else "home" }

    when (screen) {
        "home" -> Home(onCard = { card = it }, onSettings = { screen = "pc" })
        "pc" -> PcScreen(pairLink, onScan = { screen = "scan" }, onBack = { screen = "home"; pairLink = null })
        "scan" -> ScanScreen(onResult = { pairLink = it; screen = "pc" }, onBack = { screen = "pc" })
    }
    card?.let { url -> CardSheet(url) { card = null } }
}

// ─────────────────────────── Главный экран ───────────────────────────

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Home(onCard: (String) -> Unit, onSettings: () -> Unit) {
    val ctx = LocalContext.current
    val jobs by Jobs.list.collectAsState()
    var link by remember { mutableStateOf("") }
    var engine by remember { mutableStateOf<Boolean?>(null) }
    LaunchedEffect(Unit) { engine = App.engineReady.await() }

    Scaffold(topBar = {
        TopAppBar(title = { Text("Скачиватель", fontWeight = FontWeight.SemiBold) }, actions = {
            IconButton(onClick = onSettings) {
                Icon(if (Pc.paired) Icons.Outlined.Computer else Icons.Outlined.Settings, "Настройки и ПК")
            }
        })
    }) { pad ->
        LazyColumn(Modifier.padding(pad).fillMaxSize(), contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item {
                Card(shape = RoundedCornerShape(16.dp)) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text("Вставьте ссылку или нажмите «Поделиться → Скачиватель» в любом приложении",
                            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        OutlinedTextField(link, { link = it }, Modifier.fillMaxWidth(), singleLine = true,
                            placeholder = { Text("https://…") }, shape = RoundedCornerShape(12.dp),
                            trailingIcon = {
                                IconButton(onClick = {
                                    val cm = ctx.getSystemService(ClipboardManager::class.java)
                                    cm.primaryClip?.getItemAt(0)?.text?.toString()?.let { t -> URL_RE.find(t)?.value?.let { link = it } }
                                }) { Icon(Icons.Outlined.ContentPaste, "Вставить") }
                            })
                        Button(onClick = { URL_RE.find(link)?.value?.let { onCard(it); link = "" } },
                            enabled = URL_RE.containsMatchIn(link), modifier = Modifier.fillMaxWidth(),
                            shape = RoundedCornerShape(12.dp)) {
                            Icon(Icons.Outlined.Search, null); Spacer(Modifier.width(8.dp)); Text("Найти, что можно скачать")
                        }
                        when (engine) {
                            null -> Text("⏳ Первый запуск: готовлю движок (около минуты)…", style = MaterialTheme.typography.bodySmall)
                            false -> Text("⚠️ Движок не запустился: ${App.engineError}", color = MaterialTheme.colorScheme.error,
                                style = MaterialTheme.typography.bodySmall)
                            else -> {}
                        }
                    }
                }
            }
            if (jobs.isEmpty()) item {
                Text("Здесь появятся загрузки.\nФайлы сохраняются в «Загрузки/Скачиватель».", Modifier.padding(24.dp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            items(jobs, key = { it.id }) { JobCard(it) }
        }
    }
}

@Composable
private fun JobCard(j: Job) {
    val ctx = LocalContext.current
    Card(shape = RoundedCornerShape(14.dp)) {
        Row(Modifier.padding(12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            if (j.thumb != null) AsyncImage(j.thumb, null, Modifier.size(64.dp).clip(RoundedCornerShape(10.dp)),
                contentScale = ContentScale.Crop)
            Column(Modifier.weight(1f)) {
                Text(j.title.ifBlank { j.url }, maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.Medium)
                Text("${label(j.what)} → ${targetLabel(j.target)}", style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (j.active) LinearProgressIndicator(progress = { j.pct / 100f }, Modifier.fillMaxWidth().padding(vertical = 6.dp))
                Text(j.text, style = MaterialTheme.typography.bodySmall,
                    color = if (j.status == "error") MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    if (j.active) TextButton(onClick = { Jobs.cancel(j) }) { Text("Отменить") }
                    if (j.status == "done" && j.files.firstOrNull()?.startsWith("content://") == true)
                        TextButton(onClick = {
                            val u = Uri.parse(j.files.first())
                            ctx.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(u, ctx.contentResolver.getType(u))
                                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION))
                        }) { Text("Открыть") }
                    if (!j.active) TextButton(onClick = { Jobs.remove(j) }) { Text("Убрать") }
                }
            }
        }
    }
}

fun label(w: String) = when {
    w == "best" -> "⭐ Максимум"; w == "mp3" -> "🎵 MP3"; w == "audio" -> "🎵 Оригинал"; w == "thumb" -> "🖼 Обложка"
    w == "photo" -> "🖼 Фото"; w == "video" -> "🎬 Видео"; w == "all" -> "📦 Всё"
    w.startsWith("subs:") -> "📝 Субтитры"; w.toIntOrNull() != null -> "🎬 ${w}p"; else -> w
}

fun targetLabel(t: String) = when (t) { "phone" -> "📱 телефон"; "pc" -> "💻 ПК"; else -> "💻→📱 ПК и сюда" }

// ─────────────────────────── Карточка: что → качество → куда ───────────────────────────

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CardSheet(url: String, onClose: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var info by remember { mutableStateOf<Info?>(null) }
    var step by remember { mutableStateOf("type") }
    var sub by remember { mutableStateOf("") }
    var what by remember { mutableStateOf("best") }
    var target by remember { mutableStateOf(if (Pc.paired) Prefs.target else "phone") }
    var folder by remember { mutableIntStateOf(Prefs.pcFolder) }

    LaunchedEffect(url) {
        info = withContext(Dispatchers.IO) {
            if (Engine.needsPc(url)) {
                if (Pc.paired) runCatching { Pc.probe(url) }.getOrElse { Info("error", error = it.message) }
                else Info("error", error = "Доски Pinterest целиком и Telegram-каналы качает приложение на ПК. Подключите ПК в настройках.")
            } else {
                val local = Engine.probe(url)
                if (local.kind == "error" && Pc.paired) runCatching { Pc.probe(url) }.getOrDefault(local) else local
            }
        }
        if (info?.viaPc == true && target == "phone") target = "pc2phone"
    }

    ModalBottomSheet(onDismissRequest = onClose, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 28.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(10.dp)) {
            val i = info
            if (i == null) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp); Text("Смотрю, что есть по ссылке…")
                }
                Text(url, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2)
                return@Column
            }
            if (i.thumb != null && i.kind in setOf("video", "audio", "playlist"))
                AsyncImage(i.thumb, null, Modifier.fillMaxWidth().aspectRatio(16f / 9).clip(RoundedCornerShape(14.dp)),
                    contentScale = ContentScale.Crop)
            Text(i.title.ifBlank { url }, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            listOfNotNull(i.uploader, i.duration.takeIf { it > 0 }?.let { hms(it) }).joinToString(" · ").takeIf { it.isNotBlank() }
                ?.let { Text(it, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            if (i.kind == "error") {
                Text("❌ ${i.error}", color = MaterialTheme.colorScheme.error)
                OutlinedButton(onClick = onClose, Modifier.fillMaxWidth()) { Text("Закрыть") }
                return@Column
            }
            fun pick(w: String) { what = w; step = "dest" }

            when (step) {
                "type" -> {
                    Section("Что скачать?")
                    when (i.kind) {
                        "video", "audio" -> {
                            if (i.video.isNotEmpty()) Big("🎬 Видео") { sub = "video"; step = "q" }
                            if (i.audio != null) Big("🎵 Только аудио") { sub = "audio"; step = "q" }
                            if (i.subs.isNotEmpty()) Big("📝 Субтитры") { sub = "subs"; step = "q" }
                            if (i.thumb != null && !i.viaPc) Big("🖼 Обложка") { pick("thumb") }
                        }
                        "playlist" -> {
                            Big("🎬 Все видео (${i.count})") { sub = "plvideo"; step = "q" }
                            Big("🎵 Всё аудио (${i.count})") { sub = "plaudio"; step = "q" }
                        }
                        "pinterest" -> {
                            Big("📚 Все доски по папкам (${i.count} пинов)") { pick("all") }
                            Big("🖼 Только фото") { pick("photo") }
                        }
                        "gallery" -> {
                            if (i.photos > 0) Big("🖼 Фото (${i.photos})") { pick("photo") }
                            if (i.videos > 0) Big("🎬 Видео (${i.videos})") { pick("video") }
                            Big("📦 Всё") { pick("all") }
                        }
                        else -> Big("📦 Скачать") { pick("all") }
                    }
                }
                "q" -> {
                    when (sub) {
                        "video" -> { Section("Какое качество? (размер примерный)")
                            i.video.take(9).forEachIndexed { n, v ->
                                val fps = v.fps?.takeIf { it > 31 }?.let { " ${it}fps" } ?: ""
                                Big((if (n == 0) "⭐ " else "") + "${v.h}p$fps" + (if (n == 0) " (макс.)" else "") +
                                    (if (v.size > 0) " · ~${human(v.size)}" else "")) { pick(if (n == 0) "best" else v.h.toString()) }
                            } }
                        "audio" -> { Section("Какой звук?"); val a = i.audio!!
                            Big("🎵 MP3 320 kbps · ~${human(a.mp3)}") { pick("mp3") }
                            Big("🎵 Оригинал (${a.ext} ${a.abr} kbps) · ~${human(a.size)}") { pick("audio") } }
                        "subs" -> { Section("Какие субтитры? (файл .srt)")
                            i.subs.forEach { s -> Big("📝 ${s.name}${if (s.auto) " — автоматические" else ""} (${s.lang})") { pick("subs:${s.lang}") } } }
                        "plvideo" -> { Section("Качество для всех видео")
                            listOf("best" to "⭐ Максимум", "1080" to "1080p", "720" to "720p", "480" to "480p").forEach { (k, t) -> Big(t) { pick(k) } } }
                        "plaudio" -> { Big("🎵 MP3 320") { pick("mp3") }; Big("🎵 Оригинал") { pick("audio") } }
                    }
                    TextButton(onClick = { step = "type" }) { Text("← Назад") }
                }
                "dest" -> {
                    Text("Выбрано: ${label(what)}", fontWeight = FontWeight.Medium)
                    Section("Куда?")
                    val opts = buildList {
                        if (!i.viaPc) add("phone" to "📱 На телефон")
                        if (Pc.paired) { add("pc" to "💻 На ПК"); add("pc2phone" to "💻→📱 На ПК и сюда") }
                    }
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        opts.forEach { (k, t) -> FilterChip(target == k, { target = k }, { Text(t) }) }
                    }
                    if (!Pc.paired) Text("Подключите ПК в настройках — тогда можно качать и на компьютер.",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (target != "phone" && Prefs.pcFolders.isNotEmpty()) {
                        Section("Папка на ПК")
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Prefs.pcFolders.forEachIndexed { n, f -> FilterChip(folder == n, { folder = n }, { Text("📁 $f") }) }
                        }
                    }
                    Button(onClick = {
                        Prefs.target = target; Prefs.pcFolder = folder
                        Jobs.add(ctx, Job(url = url, title = i.title, what = what, target = target, folder = folder, thumb = i.thumb))
                        onClose()
                    }, Modifier.fillMaxWidth().height(52.dp), shape = RoundedCornerShape(14.dp)) { Text("⬇️ Скачать") }
                    TextButton(onClick = { step = if (sub.isNotEmpty()) "q" else "type" }) { Text("← Назад") }
                }
            }
        }
    }
}

@Composable private fun Section(t: String) =
    Text(t, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 6.dp))

@Composable private fun Big(text: String, onClick: () -> Unit) =
    OutlinedButton(onClick, Modifier.fillMaxWidth().heightIn(min = 50.dp), shape = RoundedCornerShape(12.dp)) {
        Text(text, Modifier.fillMaxWidth())
    }

// ─────────────────────────── ПК и настройки ───────────────────────────

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PcScreen(link: String?, onScan: () -> Unit, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var paired by remember { mutableStateOf(Pc.paired) }
    var status by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var id by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var engineMsg by remember { mutableStateOf<String?>(null) }

    fun connect(t: Pc.Target?) {
        if (t == null) { status = "❌ ПК не найден. Проверьте, что телефон и ПК в одной Wi-Fi и на ПК открыто «Подключить телефон»."; return }
        busy = true; status = "⏳ Подтвердите подключение на компьютере…"
        scope.launch {
            val err = withContext(Dispatchers.IO) { runCatching { Pc.pair(t) }.getOrElse { it.message } }
            busy = false; paired = Pc.paired
            status = if (err == null) "✅ Подключено к «${Pc.name}»" else "❌ $err"
        }
    }
    LaunchedEffect(link) { link?.let { l -> connect(Pc.parseQr(l)) } }

    Scaffold(topBar = {
        TopAppBar(title = { Text("ПК и настройки") },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.Outlined.ArrowBack, "Назад") } })
    }) { pad ->
        Column(Modifier.padding(pad).padding(16.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Card(shape = RoundedCornerShape(16.dp)) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("💻 Компьютер", style = MaterialTheme.typography.titleMedium)
                    if (paired) {
                        Text("Подключено к «${Pc.name}». Можно качать на ПК и получать готовые файлы сюда.")
                        OutlinedButton(onClick = { Pc.forget(); paired = false; status = null }) { Text("Отключить") }
                    } else {
                        Text("На ПК откройте «Скачиватель» → вкладка «📱 Телефон» → «Подключить телефон».",
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Button(onClick = onScan, enabled = !busy, modifier = Modifier.fillMaxWidth()) {
                            Icon(Icons.Outlined.QrCodeScanner, null); Spacer(Modifier.width(8.dp)); Text("Сканировать QR")
                        }
                        Text("или вручную:", style = MaterialTheme.typography.bodySmall)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedTextField(id, { id = it.uppercase() }, Modifier.weight(1f), label = { Text("Метка ПК") }, singleLine = true)
                            OutlinedTextField(code, { code = it.uppercase() }, Modifier.weight(1f), label = { Text("Код") }, singleLine = true)
                        }
                        OutlinedButton(onClick = {
                            busy = true; status = "⏳ Ищу ПК в сети…"
                            scope.launch { val t = withContext(Dispatchers.IO) { runCatching { Pc.discover(id, code) }.getOrNull() }; busy = false; connect(t) }
                        }, enabled = !busy && id.length == 8 && code.length == 8, modifier = Modifier.fillMaxWidth()) { Text("Подключить") }
                    }
                    status?.let { Text(it) }
                }
            }
            Card(shape = RoundedCornerShape(16.dp)) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("⚙️ Движок скачивания", style = MaterialTheme.typography.titleMedium)
                    Text("Если какой-то сайт перестал качаться — обновите движок (yt-dlp).",
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    OutlinedButton(onClick = {
                        engineMsg = "⏳ Обновляю…"
                        scope.launch { engineMsg = withContext(Dispatchers.IO) { Engine.update(ctx) } }
                    }) { Text("Обновить движок") }
                    engineMsg?.let { Text(it) }
                }
            }
            Card(shape = RoundedCornerShape(16.dp)) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("ℹ️ О программе", style = MaterialTheme.typography.titleMedium)
                    Text("Скачиватель ${BuildConfig.VERSION_NAME}. Файлы — в «Загрузки/Скачиватель».")
                    Text("YouTube и Instagram в России открываются только с VPN на телефоне.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
                    TextButton(onClick = {
                        ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/Jeff999Z/skachivatel")))
                    }) { Text("Страница проекта на GitHub") }
                }
            }
        }
    }
}
