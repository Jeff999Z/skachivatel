package ru.skachivatel

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val Light = lightColorScheme(
    primary = Color(0xFF3B5BDB), onPrimary = Color.White,
    primaryContainer = Color(0xFFEAEFFF), onPrimaryContainer = Color(0xFF1C2D6B),
    background = Color(0xFFF5F6F8), surface = Color.White, surfaceVariant = Color(0xFFEFF1F4),
    onSurface = Color(0xFF1C2130), onSurfaceVariant = Color(0xFF687083), outline = Color(0xFFE5E7EB),
    error = Color(0xFFE03131),
)
private val Dark = darkColorScheme(
    primary = Color(0xFF7B93FF), onPrimary = Color(0xFF0F1320),
    primaryContainer = Color(0xFF232B48), onPrimaryContainer = Color(0xFFDCE3FF),
    background = Color(0xFF13151A), surface = Color(0xFF1B1E25), surfaceVariant = Color(0xFF222630),
    onSurface = Color(0xFFE6E8EE), onSurfaceVariant = Color(0xFF9AA2B1), outline = Color(0xFF2A2F39),
    error = Color(0xFFFF6B6B),
)

@Composable
fun SkTheme(content: @Composable () -> Unit) =
    MaterialTheme(colorScheme = if (isSystemInDarkTheme()) Dark else Light, content = content)

fun human(n: Long): String {
    var v = n.toDouble()
    for (u in listOf("Б", "КБ", "МБ", "ГБ")) {
        if (v < 1024 || u == "ГБ") return if (u == "Б" || u == "КБ") "${v.toInt()} $u" else "%.1f $u".format(v)
        v /= 1024
    }
    return "$n Б"
}

fun hms(s: Long) = if (s >= 3600) "%d:%02d:%02d".format(s / 3600, s % 3600 / 60, s % 60) else "%d:%02d".format(s / 60, s % 60)
