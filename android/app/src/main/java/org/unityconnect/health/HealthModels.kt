package org.unityconnect.health

import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import java.util.UUID

const val SNAPSHOT_SCHEMA = "org.unityconnect.health.snapshot"
const val MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024

enum class HealthSource(val packageName: String, val displayName: String) {
    GOOGLE_FIT("com.google.android.apps.fitness", "Google Fit"),
    SAMSUNG_HEALTH("com.sec.android.app.shealth", "Samsung Health"),
    GOOGLE_HEALTH("com.fitbit.FitbitMobile", "Google Health / Fitbit");
    companion object {
        fun fromPackage(value: String) = entries.firstOrNull { it.packageName == value } ?: GOOGLE_FIT
    }
}

data class HealthEntry(
    val id: String,
    val metric: String,
    val value: Double,
    val recordedAt: String,
    val details: JSONObject = JSONObject(),
) {
    val day: LocalDate get() = OffsetDateTime.parse(recordedAt).toLocalDate()
    fun toJson() = JSONObject().put("id", id).put("metric", metric).put("value", value)
        .put("recorded_at", recordedAt).put("details", details)
}

data class HealthSnapshot(
    val streamId: String,
    val snapshotId: String = UUID.randomUUID().toString(),
    val generatedAt: String = OffsetDateTime.now().toString(),
    val provider: HealthSource,
    val start: LocalDate,
    val end: LocalDate,
    val coveredMetrics: Set<String>,
    val records: List<HealthEntry>,
) {
    fun toJson(): String {
        val json = JSONObject().put("schema", SNAPSHOT_SCHEMA).put("version", 1)
            .put("stream_id", streamId).put("snapshot_id", snapshotId)
            .put("generated_at", generatedAt).put("provider", provider.packageName)
            .put("range", JSONObject().put("start", start.toString()).put("end", end.toString()))
            .put("covered_metrics", JSONArray(coveredMetrics.sorted()))
            .put("records", JSONArray(records.map { it.toJson() })).toString()
        require(json.toByteArray(Charsets.UTF_8).size <= MAX_SNAPSHOT_BYTES) { "Snapshot is too large to sync" }
        return json
    }

    fun value(metric: String, day: LocalDate = LocalDate.now()): Double? {
        val rows = records.filter { it.metric == metric && it.day == day }
        if (rows.isEmpty()) return null
        return if (metric in setOf("weight", "heart_rate")) rows.maxBy { it.recordedAt }.value else rows.sumOf { it.value }
    }

    companion object {
        fun fromJson(value: String): HealthSnapshot {
            require(value.toByteArray(Charsets.UTF_8).size <= MAX_SNAPSHOT_BYTES)
            val data = JSONObject(value)
            require(data.getString("schema") == SNAPSHOT_SCHEMA && data.getInt("version") == 1)
            val range = data.getJSONObject("range")
            val metrics = data.getJSONArray("covered_metrics")
            val entries = data.getJSONArray("records")
            require(entries.length() <= 30000)
            val provider = HealthSource.entries.first { it.packageName == data.getString("provider") }
            return HealthSnapshot(
                streamId = data.getString("stream_id"), snapshotId = data.getString("snapshot_id"),
                generatedAt = data.getString("generated_at"), provider = provider,
                start = LocalDate.parse(range.getString("start")), end = LocalDate.parse(range.getString("end")),
                coveredMetrics = (0 until metrics.length()).map { metrics.getString(it) }.toSet(),
                records = (0 until entries.length()).map { index ->
                    val item = entries.getJSONObject(index)
                    HealthEntry(item.getString("id"), item.getString("metric"), item.getDouble("value"),
                        item.getString("recorded_at"), item.optJSONObject("details") ?: JSONObject())
                },
            )
        }
    }
}
