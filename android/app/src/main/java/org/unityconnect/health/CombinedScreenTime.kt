package org.unityconnect.health

import android.content.ContentProvider
import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.net.Uri
import android.os.Bundle
import org.json.JSONObject
import java.time.LocalDate

/** A display-only copy of the desktop's overlap-aware daily totals. */
internal data class CombinedScreenTime(
    val day: LocalDate, val start: Long, val end: Long, val generatedAt: Long,
    val combined: Long, val desktop: Long, val phone: Long, val overlap: Long,
) {
    companion object {
        fun parse(raw: String): CombinedScreenTime {
            require(raw.toByteArray(Charsets.UTF_8).size <= 1024) { "Screen time summary is too large" }
            val json = JSONObject(raw)
            require(json.keys().asSequence().toSet() == setOf("version", "day", "start_ms", "end_ms",
                "generated_at_ms", "combined_ms", "desktop_ms", "phone_ms", "overlap_ms"))
            fun integer(key: String): Long {
                val value = json.get(key)
                require(value is Int || value is Long) { "Expected integer: $key" }
                return (value as Number).toLong()
            }
            require(integer("version") == 1L)
            val day = LocalDate.parse(json.getString("day"))
            require(day.toString() == json.getString("day"))
            val start = integer("start_ms")
            val end = integer("end_ms")
            val generated = integer("generated_at_ms")
            require(start > 0 && end > start && end < 32_503_680_000_000L && end - start <= 26 * 3_600_000L)
            require(generated in start until 32_503_680_000_000L)
            val combined = integer("combined_ms")
            val desktop = integer("desktop_ms")
            val phone = integer("phone_ms")
            val overlap = integer("overlap_ms")
            require(listOf(combined, desktop, phone, overlap).all { it in 0..(minOf(end, generated) - start) })
            require(combined >= maxOf(desktop, phone) && combined <= desktop + phone && overlap == desktop + phone - combined)
            return CombinedScreenTime(day, start, end, generated, combined, desktop, phone, overlap)
        }
    }
}

internal object CombinedScreenTimeCache {
    private fun prefs(context: Context) = context.getSharedPreferences("combined_screen_time", Context.MODE_PRIVATE)

    fun read(context: Context, day: LocalDate): CombinedScreenTime? = runCatching {
        prefs(context).getString(day.toString(), null)?.let(CombinedScreenTime::parse)
    }.getOrNull()

    @Synchronized fun store(context: Context, raw: String): CombinedScreenTime {
        val summary = CombinedScreenTime.parse(raw)
        require(summary.generatedAt <= System.currentTimeMillis() + 120_000) { "Check the computer's clock" }
        val previous = read(context, summary.day)
        check(previous == null || summary.generatedAt >= previous.generatedAt) { "An older screen time summary was received" }
        val preferences = prefs(context)
        val update = preferences.edit().putString(summary.day.toString(), raw)
        preferences.all.keys.filter { runCatching { LocalDate.parse(it) < summary.day.minusDays(30) }.getOrDefault(true) }
            .forEach(update::remove)
        check(update.commit()) { "Screen time summary could not be saved" }
        return summary
    }
}

/** Only signed Unity companions may update the app's private display cache. */
class CombinedScreenTimeProvider : ContentProvider() {
    override fun onCreate() = true

    override fun call(method: String, arg: String?, extras: Bundle?): Bundle {
        val context = requireNotNull(context)
        context.enforceCallingPermission("org.unityconnect.health.permission.READ_HEALTH", "Trusted Unity companion required")
        require(method == "summary" && arg != null && extras == null) { "Unsupported screen time request" }
        val summary = CombinedScreenTimeCache.store(context, arg)
        return Bundle().apply { putBoolean("stored", true); putString("day", summary.day.toString()) }
    }

    override fun query(uri: Uri, projection: Array<out String>?, selection: String?, selectionArgs: Array<out String>?, sortOrder: String?): Cursor? = null
    override fun getType(uri: Uri): String? = null
    override fun insert(uri: Uri, values: ContentValues?): Uri? = throw UnsupportedOperationException()
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?) = throw UnsupportedOperationException()
    override fun update(uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<out String>?) = throw UnsupportedOperationException()
}
