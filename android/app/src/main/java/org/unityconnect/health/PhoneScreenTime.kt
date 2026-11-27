package org.unityconnect.health

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Process
import android.provider.Settings
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.rounded.KeyboardArrowRight
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.graphics.drawable.toBitmap
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.time.LocalDate
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private data class AppTime(val packageName: String, val label: String, val milliseconds: Long)

@Composable
internal fun PhoneScreenTime(modifier: Modifier, resumed: Boolean) {
    val context = LocalContext.current
    var today by remember { mutableStateOf(LocalDate.now()) }
    var selected by remember { mutableStateOf<LocalDate?>(null) }
    var apps by remember { mutableStateOf<List<AppTime>>(emptyList()) }
    var combined by remember { mutableStateOf<CombinedScreenTime?>(null) }
    var loading by remember { mutableStateOf(true) }
    var status by remember { mutableStateOf("Loading screen time…") }
    var needsAccess by remember { mutableStateOf(false) }
    var settingsUnavailable by remember { mutableStateOf(false) }
    val day = selected ?: today

    LaunchedEffect(context, resumed, selected) {
        if (!resumed) return@LaunchedEffect
        apps = emptyList()
        combined = null
        loading = true
        status = "Loading screen time…"
        while (true) {
            val currentDate = LocalDate.now()
            val queryDate = selected ?: currentDate
            today = currentDate
            combined = withContext(Dispatchers.IO) { CombinedScreenTimeCache.read(context, queryDate) }
            loading = false
            needsAccess = !hasUsageAccess(context)
            if (needsAccess) {
                apps = emptyList()
                status = "Usage access unavailable"
            } else {
                val result = withContext(Dispatchers.IO) {
                    runCatching { readScreenTime(context, queryDate) }.getOrNull()
                }
                apps = result.orEmpty()
                status = when {
                    result == null -> "Screen time unavailable"
                    result.isEmpty() -> "No app usage for this date"
                    else -> ""
                }
            }
            delay(15_000)
        }
    }

    LazyColumn(modifier, contentPadding = PaddingValues(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = { selected = day.minusDays(1) }, enabled = day > today.minusDays(6)) {
                    Icon(Icons.AutoMirrored.Rounded.KeyboardArrowLeft, "Previous day")
                }
                Text(if (day == today) "Today" else day.format(DateTimeFormatter.ofPattern("EEE, d MMM")),
                    Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
                IconButton(onClick = { selected = day.plusDays(1).takeIf { it < today } }, enabled = day < today) {
                    Icon(Icons.AutoMirrored.Rounded.KeyboardArrowRight, "Next day")
                }
            }
        }
        item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Combined screen time", style = MaterialTheme.typography.titleMedium)
                    val summary = combined
                    if (summary != null) {
                        Text(duration(summary.combined), style = MaterialTheme.typography.headlineMedium)
                        Text("Computer ${duration(summary.desktop)} · Phone ${duration(summary.phone)}")
                        Text("Simultaneous use ${duration(summary.overlap)} · counted once",
                            style = MaterialTheme.typography.bodySmall)
                        val synced = Instant.ofEpochMilli(summary.generatedAt).atZone(ZoneId.systemDefault())
                            .format(DateTimeFormatter.ofPattern("d MMM, HH:mm"))
                        Text("Last synced $synced", style = MaterialTheme.typography.bodySmall)
                    } else {
                        Text(if (loading) "Loading screen time…" else "No combined screen time synced for this date")
                        if (!loading) Text("Enable Screen time in the Unity companion and sync phone screen time in WellBeing on your computer.",
                            style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
        item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("On this phone", style = MaterialTheme.typography.titleMedium)
                    Text(if (status.isEmpty()) duration(apps.sumOf { it.milliseconds }) else status,
                        style = if (status.isEmpty()) MaterialTheme.typography.headlineMedium else MaterialTheme.typography.bodyMedium)
                    if (needsAccess) {
                        TextButton(onClick = {
                            settingsUnavailable = runCatching {
                                context.startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
                            }.isFailure
                        }) { Text("Usage access settings") }
                    }
                    if (settingsUnavailable && needsAccess) Text("Usage access settings unavailable")
                }
            }
        }
        if (apps.isNotEmpty()) item { Text("Time by app on this phone", style = MaterialTheme.typography.titleMedium) }
        items(apps, key = { it.packageName }) { app ->
            val size = with(LocalDensity.current) { 28.dp.roundToPx() }
            val icon = remember(context, app.packageName, size) {
                val manager = context.packageManager
                val drawable = runCatching { manager.getApplicationIcon(app.packageName) }
                    .getOrElse { manager.defaultActivityIcon }
                drawable.toBitmap(size, size).asImageBitmap()
            }
            Card(Modifier.fillMaxWidth()) {
                Row(Modifier.padding(16.dp), horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalAlignment = Alignment.CenterVertically) {
                    Image(icon, contentDescription = null, modifier = Modifier.size(28.dp))
                    Text(app.label, Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(duration(app.milliseconds))
                }
            }
        }
    }
}

@Suppress("DEPRECATION")
private fun hasUsageAccess(context: Context): Boolean {
    val appOps = context.getSystemService(AppOpsManager::class.java)
    val op = AppOpsManager.OPSTR_GET_USAGE_STATS
    val uid = Process.myUid()
    val mode = if (Build.VERSION.SDK_INT >= 29) appOps.unsafeCheckOpNoThrow(op, uid, context.packageName)
        else appOps.checkOpNoThrow(op, uid, context.packageName)
    return mode == AppOpsManager.MODE_ALLOWED
}

private fun readScreenTime(context: Context, day: LocalDate): List<AppTime>? {
    val zone = ZoneId.systemDefault()
    val start = day.atStartOfDay(zone).toInstant().toEpochMilli()
    val end = minOf(day.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli(), System.currentTimeMillis())
    if (start >= end) return emptyList()
    val queryStart = day.minusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
    val events = context.getSystemService(UsageStatsManager::class.java).queryEvents(queryStart, end) ?: return null
    val totals = HashMap<String, Long>()
    var foreground: String? = null
    var activity: String? = null
    var began = start
    var interactive = true
    var locked = false

    fun finish(at: Long) {
        val app = foreground
        val elapsed = minOf(at, end) - maxOf(began, start)
        if (!app.isNullOrBlank() && app != "com.android.systemui" && elapsed > 0) {
            totals[app] = totals.getOrDefault(app, 0L) + elapsed
        }
        foreground = null
        activity = null
    }

    val event = UsageEvents.Event()
    while (events.getNextEvent(event)) {
        when (event.eventType) {
            UsageEvents.Event.ACTIVITY_RESUMED -> {
                finish(event.timeStamp)
                if (interactive && !locked) {
                    foreground = event.packageName
                    activity = event.className
                    began = event.timeStamp
                }
            }
            UsageEvents.Event.ACTIVITY_PAUSED, UsageEvents.Event.ACTIVITY_STOPPED -> {
                if (foreground == event.packageName && activity == event.className) finish(event.timeStamp)
            }
            UsageEvents.Event.SCREEN_NON_INTERACTIVE, UsageEvents.Event.DEVICE_SHUTDOWN,
            UsageEvents.Event.DEVICE_STARTUP -> {
                finish(event.timeStamp)
                interactive = false
            }
            UsageEvents.Event.SCREEN_INTERACTIVE -> interactive = true
            UsageEvents.Event.KEYGUARD_SHOWN -> {
                finish(event.timeStamp)
                locked = true
            }
            UsageEvents.Event.KEYGUARD_HIDDEN -> locked = false
        }
    }
    finish(end)

    return totals.entries.sortedByDescending { it.value }.map { (app, time) ->
        val label = runCatching {
            val info = context.packageManager.getApplicationInfo(app, 0)
            context.packageManager.getApplicationLabel(info).toString()
        }.getOrDefault(app)
        AppTime(app, label, time)
    }
}

private fun duration(milliseconds: Long): String {
    val minutes = milliseconds / 60_000
    return when {
        minutes >= 60 -> "${minutes / 60} h ${minutes % 60} min"
        minutes > 0 -> "$minutes min"
        milliseconds > 0 -> "<1 min"
        else -> "0 min"
    }
}
