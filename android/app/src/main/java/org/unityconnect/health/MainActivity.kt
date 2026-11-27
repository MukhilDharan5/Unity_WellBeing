package org.unityconnect.health

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.DirectionsWalk
import androidx.compose.material.icons.rounded.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.UUID
import kotlin.math.roundToInt

private val Teal = Color(0xFF168978)
private val Blue = Color(0xFF4389D8)
private val Violet = Color(0xFF9271CC)
private val Rose = Color(0xFFD95F79)
private val Orange = Color(0xFFD39135)
private val Green = Color(0xFF6C9D58)

class MainActivity : ComponentActivity() {
    private val prefs by lazy { getSharedPreferences("health", MODE_PRIVATE) }
    private val reader by lazy { HealthReader(this) }
    private var source by mutableStateOf(HealthSource.GOOGLE_FIT)
    private var snapshot by mutableStateOf<HealthSnapshot?>(null)
    private var busy by mutableStateOf(false)
    private var message by mutableStateOf("")
    private var tab by mutableIntStateOf(SCREEN_TIME_TAB)
    private var resumed by mutableStateOf(false)
    private var permissionCount by mutableIntStateOf(0)
    private var directSync by mutableStateOf(false)
    private val streamId get() = prefs.getString("stream_id", null) ?: UUID.randomUUID().toString().also {
        prefs.edit().putString("stream_id", it).apply()
    }
    private val permissionRequest = registerForActivityResult(
        PermissionController.createRequestPermissionResultContract()
    ) { granted ->
        permissionCount = granted.intersect(HealthReader.permissions).size
        if (permissionCount > 0) refresh() else message = "No read access granted."
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        directSync = prefs.getBoolean("unity_sync_enabled", false)
        source = HealthSource.fromPackage(prefs.getString("provider", "").orEmpty())
        if (intent.action in setOf("androidx.health.ACTION_SHOW_PERMISSIONS_RATIONALE", Intent.ACTION_VIEW_PERMISSION_USAGE)) tab = 2
        snapshot = runCatching {
            val cached = HealthSnapshot.fromJson(File(filesDir, "last_snapshot.json").readText())
            cached.takeIf { it.provider == source }
        }.getOrNull()
        setContent {
            val dark = isSystemInDarkTheme()
            val colors = if (dark) darkColorScheme(primary = Color(0xFF79CFB7),
                background = Color(0xFF151517), surface = Color(0xFF252528), surfaceContainer = Color(0xFF252528))
            else lightColorScheme(primary = Teal, background = Color(0xFFF4F4F7), surface = Color.White,
                surfaceContainer = Color.White)
            MaterialTheme(colorScheme = colors) {
                HealthApp(source, snapshot, busy, message, tab, permissionCount, resumed,
                    onTab = { tab = it }, onConnect = ::connect, onRefresh = ::refresh,
                    onSend = ::send, onSettings = ::openHealthConnect,
                    directSync = directSync, onDirectSync = { enabled ->
                        directSync = enabled
                        prefs.edit().putBoolean("unity_sync_enabled", enabled).apply()
                        runCatching { UnityTransfer.notifyChanged(this) }
                    },
                    onSource = { next ->
                        if (!busy && next != source) {
                            source = next
                            snapshot = null
                            File(filesDir, "last_snapshot.json").delete()
                            prefs.edit().putString("provider", next.packageName).apply()
                            refresh()
                        }
                    }, unityInstalled = UnityTransfer.available(this),
                    onClear = {
                        File(filesDir, "last_snapshot.json").delete()
                        File(cacheDir, "snapshots").deleteRecursively()
                        snapshot = null
                        runCatching { UnityTransfer.notifyChanged(this) }
                        message = "Local cached records removed."
                    })
            }
        }
        refresh()
    }

    override fun onResume() {
        super.onResume()
        resumed = true
    }

    override fun onPause() {
        resumed = false
        super.onPause()
    }

    private fun connect() {
        if (reader.availability() == HealthConnectClient.SDK_AVAILABLE) {
            permissionRequest.launch(HealthReader.permissions)
        } else openHealthConnect()
    }

    private fun refresh() {
        if (busy) return
        if (reader.availability() != HealthConnectClient.SDK_AVAILABLE) {
            message = "Health Connect unavailable."
            return
        }
        busy = true
        message = ""
        val selected = source
        lifecycleScope.launch {
            try {
                permissionCount = reader.grantedPermissions().intersect(HealthReader.permissions).size
                if (permissionCount == 0) {
                    snapshot = null
                    message = "Health Connect read access unavailable."
                    return@launch
                }
                val result = withContext(Dispatchers.IO) { reader.read(selected, streamId) }
                withContext(Dispatchers.IO) {
                    val temporary = File(filesDir, "last_snapshot.pending")
                    temporary.writeText(result.toJson())
                    check(temporary.renameTo(File(filesDir, "last_snapshot.json"))) { "Could not save the local cache" }
                }
                snapshot = result
                if (directSync) runCatching { UnityTransfer.send(this@MainActivity, result) }
                message = if (result.records.isEmpty()) "No ${selected.displayName} records shared."
                    else "Read ${result.records.size} records from ${selected.displayName}."
            } catch (error: Exception) {
                message = "Could not refresh: " + (error.localizedMessage ?: "Health Connect access unavailable")
            } finally { busy = false }
        }
    }

    private fun send() {
        val current = snapshot ?: return
        runCatching { UnityTransfer.send(this, current) }
            .onSuccess { message = "Unity notified." }
            .onFailure { message = it.localizedMessage ?: "Unity connection unavailable" }
    }

    private fun openHealthConnect() {
        val intent = if (reader.availability() == HealthConnectClient.SDK_AVAILABLE)
            Intent(if (Build.VERSION.SDK_INT >= 34) "android.health.connect.action.HEALTH_HOME_SETTINGS"
                else "androidx.health.ACTION_HEALTH_CONNECT_SETTINGS")
        else Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=com.google.android.apps.healthdata"))
        runCatching { startActivity(intent) }.onFailure { message = "Health Connect settings unavailable." }
    }
}

private const val SCREEN_TIME_TAB = 3

@Composable
private fun HealthApp(source: HealthSource, snapshot: HealthSnapshot?, busy: Boolean, message: String,
    tab: Int, permissionCount: Int, resumed: Boolean, onTab: (Int) -> Unit, onConnect: () -> Unit,
    onRefresh: () -> Unit, onSend: () -> Unit, onSettings: () -> Unit,
    onSource: (HealthSource) -> Unit, unityInstalled: Boolean, directSync: Boolean,
    onDirectSync: (Boolean) -> Unit, onClear: () -> Unit) {
    var detail by remember { mutableStateOf<String?>(null) }
    Scaffold(containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                listOf("Summary" to Icons.Rounded.FavoriteBorder, "Browse" to Icons.Rounded.GridView,
                    "Sync" to Icons.Rounded.Sync, "Screen time" to Icons.Rounded.Schedule).forEachIndexed { index, item ->
                    NavigationBarItem(selected = tab == index, onClick = { onTab(index) },
                        icon = { Icon(item.second, contentDescription = item.first) }, label = { Text(item.first) })
                }
            }
        }) { padding ->
        if (tab == SCREEN_TIME_TAB) {
            PhoneScreenTime(Modifier.fillMaxSize().padding(padding), resumed)
        } else LazyColumn(Modifier.fillMaxSize().padding(padding),
            contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(18.dp)) {
            item {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Text("Unity WellBeing", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.weight(1f))
                    IconButton(onClick = onRefresh, enabled = !busy) {
                        Icon(Icons.Rounded.Refresh, contentDescription = "Refresh provider records")
                    }
                }
                Spacer(Modifier.height(14.dp))
                Text(LocalDate.now().format(DateTimeFormatter.ofPattern("EEEE, d MMMM", Locale.getDefault())),
                    color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.labelLarge)
            }
            if (busy) item { LinearProgressIndicator(Modifier.fillMaxWidth()) }
            if (message.isNotBlank()) item {
                Text(message, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            when (tab) {
                0 -> {
                    item {
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Rounded.Link, null, tint = Teal, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(6.dp))
                            Text(source.displayName, style = MaterialTheme.typography.labelLarge,
                                modifier = Modifier.weight(1f))
                            Text("Read only", color = MaterialTheme.colorScheme.onSurfaceVariant,
                                style = MaterialTheme.typography.labelMedium)
                        }
                    }
                    if (snapshot == null || snapshot.records.isEmpty()) {
                        item { HealthCard { SectionTitle("No health records") } }
                    } else {
                        item { ActivityCard(snapshot) }
                        item { SectionTitle("Health highlights") }
                        items(3) { row ->
                            val keys = listOf("total_energy", "distance", "sleep", "heart_rate", "water", "weight")
                            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                keys.subList(row * 2, row * 2 + 2).forEach { metric ->
                                    MetricCard(metric, snapshot, Modifier.weight(1f)) { detail = metric }
                                }
                            }
                        }
                        val workouts = snapshot.records.filter { it.metric == "workout" }.take(3)
                        if (workouts.isNotEmpty()) item {
                            HealthCard {
                                SectionTitle("Recent activity")
                                workouts.forEach { item ->
                                    Row(verticalAlignment = Alignment.CenterVertically,
                                        modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
                                        Icon(Icons.AutoMirrored.Rounded.DirectionsWalk, null, tint = Teal)
                                        Spacer(Modifier.width(12.dp))
                                        Column(Modifier.weight(1f)) {
                                            Text(item.details.optString("name", "Workout"), fontWeight = FontWeight.SemiBold)
                                            Text(item.day.toString(), style = MaterialTheme.typography.bodySmall,
                                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                                        }
                                        Text("${item.value.roundToInt()} min")
                                    }
                                }
                            }
                        }
                        item {
                            Button(onClick = onSend, modifier = Modifier.fillMaxWidth(), enabled = !busy && directSync) {
                                Icon(Icons.Rounded.Sync, null); Spacer(Modifier.width(8.dp)); Text("Sync with laptop")
                            }
                        }
                    }
                }
                1 -> {
                    items(5) { row ->
                        val keys = listOf("steps", "total_energy", "active_energy", "distance", "sleep", "heart_rate", "weight", "water", "food", "workout")
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            keys.drop(row * 2).take(2).forEach { metric ->
                                MetricCard(metric, snapshot, Modifier.weight(1f)) { detail = metric }
                            }
                        }
                    }
                }
                2 -> {
                    item {
                        HealthCard {
                            Icon(Icons.Rounded.HealthAndSafety, null, tint = Teal, modifier = Modifier.size(32.dp))
                            SectionTitle("Health Connect")
                            Text(if (permissionCount > 0) "$permissionCount of ${HealthReader.permissions.size} categories allowed"
                                else "No read access granted", color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Button(onClick = onConnect, enabled = !busy, modifier = Modifier.fillMaxWidth()) { Text("Manage read access") }
                            TextButton(onClick = onSettings) { Text("Open Health Connect") }
                        }
                    }
                    item { SectionTitle("Read records from") }
                    item {
                        HealthCard {
                            HealthSource.entries.forEach { item ->
                                Row(Modifier.fillMaxWidth().clickable(enabled = !busy) { onSource(item) }, verticalAlignment = Alignment.CenterVertically) {
                                    RadioButton(selected = source == item, onClick = { onSource(item) }, enabled = !busy)
                                    Text(item.displayName)
                                }
                            }
                            Text("Only records from the selected provider are read.",
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                    item {
                        HealthCard {
                            Icon(Icons.Rounded.Laptop, null, tint = Blue, modifier = Modifier.size(32.dp))
                            SectionTitle("Sync through Unity")
                            Text(if (unityInstalled) "Unity companion installed" else "Unity companion unavailable",
                                fontWeight = FontWeight.SemiBold)
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                Text("Direct Unity integration", modifier = Modifier.weight(1f))
                                Switch(checked = directSync, onCheckedChange = onDirectSync, enabled = unityInstalled && !busy)
                            }
                            Button(onClick = onSend, enabled = snapshot != null && !busy && unityInstalled && directSync,
                                modifier = Modifier.fillMaxWidth()) { Text("Sync with laptop") }
                            snapshot?.let { Text("Last read: " + OffsetDateTime.parse(it.generatedAt).format(DateTimeFormatter.ofPattern("d MMM, HH:mm")),
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                        }
                    }
                    item {
                        HealthCard {
                            Icon(Icons.Rounded.Lock, null, tint = Green)
                            SectionTitle("Data & privacy")
                            Text("Health Connect access is read only.")
                            Text("Records are cached on this phone and shared with the paired laptop while Direct Unity integration is enabled.")
                            Text("Removing the local cache leaves provider records unchanged.",
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                            TextButton(onClick = onClear, enabled = !busy) { Text("Remove local cached records") }
                        }
                    }
                }
            }
        }
    }
    detail?.let { metric ->
        AlertDialog(onDismissRequest = { detail = null }, title = { Text(metricTitle(metric)) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("Last 7 days · ${source.displayName}")
                    for (offset in 6L downTo 0L) {
                        val day = LocalDate.now().minusDays(offset)
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text(day.format(DateTimeFormatter.ofPattern("EEE, d MMM")))
                            Text(formatValue(metric, snapshot?.value(metric, day)))
                        }
                    }
                }
            }, confirmButton = { TextButton(onClick = { detail = null }) { Text("Done") } })
    }
}

@Composable
private fun HealthCard(content: @Composable ColumnScope.() -> Unit) {
    Surface(shape = RoundedCornerShape(24.dp), color = MaterialTheme.colorScheme.surface, tonalElevation = 0.dp,
        modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp), content = content)
    }
}

@Composable
private fun SectionTitle(title: String) {
    Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
}

@Composable
private fun ActivityCard(snapshot: HealthSnapshot) {
    val steps = snapshot.value("steps")
    val minutes = snapshot.value("workout")
    val energy = snapshot.value("active_energy")
    HealthCard {
        SectionTitle("Daily activity")
        Box(Modifier.fillMaxWidth().height(205.dp), contentAlignment = Alignment.Center) {
            Canvas(Modifier.size(200.dp)) {
                listOf(Triple(steps, 8000.0, Teal), Triple(minutes, 45.0, Blue), Triple(energy, 400.0, Orange))
                    .forEachIndexed { index, item ->
                        val inset = 11.dp.toPx() + index * 21.dp.toPx()
                        val width = size.width - inset * 2
                        val stroke = Stroke(14.dp.toPx(), cap = StrokeCap.Round)
                        drawArc(item.third.copy(alpha = .12f), 0f, 360f, false,
                            topLeft = Offset(inset, inset), size = Size(width, width), style = stroke)
                        item.first?.takeIf { it > 0 }?.let { value ->
                            drawArc(item.third, -90f, (value / item.second * 360).coerceAtMost(360.0).toFloat(), false,
                                topLeft = Offset(inset, inset), size = Size(width, width), style = stroke)
                        }
                    }
            }
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("Today", style = MaterialTheme.typography.labelLarge)
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            listOf(Triple("Steps", steps, Teal), Triple("Minutes", minutes, Blue), Triple("Active kcal", energy, Orange)).forEach { item ->
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(item.second?.let { "%,.0f".format(it) } ?: "—", fontSize = 24.sp, fontWeight = FontWeight.Bold, color = item.third)
                    Text(item.first, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        Text("Goals: 8,000 steps · 45 min · 400 kcal", style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun MetricCard(metric: String, snapshot: HealthSnapshot?, modifier: Modifier, onClick: () -> Unit) {
    val color = metricColor(metric)
    Surface(modifier = modifier.clickable(onClick = onClick), shape = RoundedCornerShape(22.dp),
        color = MaterialTheme.colorScheme.surface) {
        Column(Modifier.padding(16.dp).heightIn(min = 145.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(metricIcon(metric), null, tint = color, modifier = Modifier.size(17.dp))
                Spacer(Modifier.width(6.dp))
                Text(metricTitle(metric), style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
            }
            Text(formatValue(metric, snapshot?.value(metric)), fontSize = 25.sp, fontWeight = FontWeight.Bold,
                letterSpacing = (-.7).sp)
            val points = (6L downTo 0L).map { offset -> snapshot?.value(metric, LocalDate.now().minusDays(offset)) }
            Canvas(Modifier.fillMaxWidth().height(32.dp)) {
                val maximum = (points.filterNotNull().maxOrNull() ?: 1.0).coerceAtLeast(1.0) * 1.15
                var previous: Offset? = null
                points.forEachIndexed { index, value ->
                    if (value == null) { previous = null; return@forEachIndexed }
                    val point = Offset(size.width * index / 6f, size.height * (1 - value / maximum).toFloat())
                    previous?.let { drawLine(color, it, point, strokeWidth = 2.dp.toPx(), cap = StrokeCap.Round) }
                    drawCircle(color, 2.dp.toPx(), point)
                    previous = point
                }
            }
            Text(if (snapshot?.value(metric) != null) "Shared by provider" else "No record shared",
                style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

private fun metricTitle(metric: String) = mapOf("steps" to "Steps", "active_energy" to "Active energy", "total_energy" to "Calories burned", "sleep" to "Sleep",
    "heart_rate" to "Resting heart", "weight" to "Weight", "water" to "Water", "food" to "Nutrition",
    "distance" to "Distance", "workout" to "Workout time")[metric] ?: metric

private fun metricColor(metric: String) = when (metric) {
    "sleep" -> Violet; "heart_rate" -> Rose; "water", "distance" -> Blue
    "weight", "active_energy", "total_energy" -> Orange; "food" -> Green; else -> Teal
}

private fun metricIcon(metric: String): ImageVector = when (metric) {
    "sleep" -> Icons.Rounded.Bedtime; "heart_rate" -> Icons.Rounded.Favorite
    "water" -> Icons.Rounded.WaterDrop; "weight" -> Icons.Rounded.MonitorWeight
    "food" -> Icons.Rounded.Restaurant; "active_energy", "total_energy" -> Icons.Rounded.LocalFireDepartment
    "distance" -> Icons.Rounded.Route; else -> Icons.AutoMirrored.Rounded.DirectionsWalk
}

private fun formatValue(metric: String, value: Double?): String {
    if (value == null) return "—"
    return when (metric) {
        "sleep" -> { val mins = (value * 60).roundToInt(); "${mins / 60}h ${mins % 60}m" }
        "water" -> "%.2f L".format(value / 1000)
        "weight" -> "%.1f kg".format(value)
        "heart_rate" -> "${value.roundToInt()} bpm"
        "active_energy", "total_energy", "food" -> "${value.roundToInt()} kcal"
        "distance" -> if (value < 1) "${(value * 1000).roundToInt()} m" else "%.2f km".format(value)
        "workout" -> "${value.roundToInt()} min"
        else -> "%,.0f".format(value)
    }
}
