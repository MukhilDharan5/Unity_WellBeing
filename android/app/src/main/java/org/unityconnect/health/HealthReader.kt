package org.unityconnect.health

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.aggregate.AggregateMetric
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.*
import androidx.health.connect.client.records.metadata.DataOrigin
import androidx.health.connect.client.request.AggregateRequest
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import org.json.JSONObject
import java.security.MessageDigest
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** Only reads provider-authored data. It never subscribes to sensors or writes records. */
class HealthReader(private val context: Context) {
    private val client get() = HealthConnectClient.getOrCreate(context)
    private val zone = ZoneId.systemDefault()

    companion object {
        val permissionMetrics = linkedMapOf(
            HealthPermission.getReadPermission(StepsRecord::class) to "steps",
            HealthPermission.getReadPermission(ActiveCaloriesBurnedRecord::class) to "active_energy",
            HealthPermission.getReadPermission(TotalCaloriesBurnedRecord::class) to "total_energy",
            HealthPermission.getReadPermission(ExerciseSessionRecord::class) to "workout",
            HealthPermission.getReadPermission(SleepSessionRecord::class) to "sleep",
            HealthPermission.getReadPermission(RestingHeartRateRecord::class) to "heart_rate",
            HealthPermission.getReadPermission(WeightRecord::class) to "weight",
            HealthPermission.getReadPermission(HydrationRecord::class) to "water",
            HealthPermission.getReadPermission(NutritionRecord::class) to "food",
            HealthPermission.getReadPermission(DistanceRecord::class) to "distance",
        )
        val permissions get() = permissionMetrics.keys
    }

    fun availability() = HealthConnectClient.getSdkStatus(context)
    suspend fun grantedPermissions() = client.permissionController.getGrantedPermissions()

    private fun stamp(instant: Instant) = instant.atZone(zone).format(DateTimeFormatter.ISO_OFFSET_DATE_TIME)
    private fun key(metric: String, id: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(id.toByteArray())
        return metric + ":" + digest.joinToString("") { "%02x".format(it) }.take(48)
    }

    private suspend inline fun <reified T : Record> records(
        source: HealthSource, from: Instant, until: Instant,
    ): List<T> {
        val output = mutableListOf<T>()
        val seenTokens = mutableSetOf<String>()
        var token: String? = null
        do {
            val page = client.readRecords(ReadRecordsRequest(
                recordType = T::class, timeRangeFilter = TimeRangeFilter.between(from, until),
                dataOriginFilter = setOf(DataOrigin(source.packageName)), pageSize = 1000, pageToken = token,
            ))
            output += page.records
            check(output.size <= 30000) { "Too many records. Choose a shorter health history." }
            token = page.pageToken?.takeIf { it.isNotBlank() }
            token?.let { check(seenTokens.add(it)) { "Health Connect repeated a page token; refresh again" } }
        } while (token != null)
        return output
    }

    suspend fun read(source: HealthSource, streamId: String): HealthSnapshot {
        check(availability() == HealthConnectClient.SDK_AVAILABLE) { "Health Connect unavailable" }
        val granted = grantedPermissions()
        val covered = permissionMetrics.filterKeys { it in granted }.values.toSet()
        check(covered.isNotEmpty()) { "Allow read access to at least one health category" }
        val today = LocalDate.now(zone)
        val start = today.minusDays(27)
        val end = today.plusDays(1)
        val from = start.atStartOfDay(zone).toInstant()
        val until = Instant.now()
        val output = mutableListOf<HealthEntry>()
        fun entry(metric: String, id: String, value: Double, at: Instant, details: JSONObject = JSONObject()) {
            check(value.isFinite() && value >= 0) { "The provider returned an invalid value" }
            details.put("origin", source.packageName)
            output += HealthEntry(key(metric, id), metric, value, stamp(at), details)
            check(output.size <= 30000) { "Too many health records in one snapshot" }
        }

        // Aggregate cumulative metrics to avoid double counting overlapping records.
        // Filtering by the selected app excludes Health Connect's own phone-step source.
        val metrics = mutableSetOf<AggregateMetric<*>>()
        if ("steps" in covered) metrics += StepsRecord.COUNT_TOTAL
        if ("active_energy" in covered) metrics += ActiveCaloriesBurnedRecord.ACTIVE_CALORIES_TOTAL
        if ("total_energy" in covered) metrics += TotalCaloriesBurnedRecord.ENERGY_TOTAL
        if ("distance" in covered) metrics += DistanceRecord.DISTANCE_TOTAL
        if (metrics.isNotEmpty()) {
            for (offset in 0L..27L) {
                val day = start.plusDays(offset)
                val dayStart = day.atStartOfDay(zone).toInstant()
                val dayEnd = minOf(day.plusDays(1).atStartOfDay(zone).toInstant(), until)
                if (dayEnd <= dayStart) continue
                val values = client.aggregate(AggregateRequest(metrics = metrics,
                    timeRangeFilter = TimeRangeFilter.between(dayStart, dayEnd),
                    dataOriginFilter = setOf(DataOrigin(source.packageName))))
                if ("steps" in covered) values[StepsRecord.COUNT_TOTAL]?.let { entry("steps", day.toString(), it.toDouble(), dayStart) }
                if ("active_energy" in covered) values[ActiveCaloriesBurnedRecord.ACTIVE_CALORIES_TOTAL]?.let { entry("active_energy", day.toString(), it.inKilocalories, dayStart) }
                if ("total_energy" in covered) values[TotalCaloriesBurnedRecord.ENERGY_TOTAL]?.let { entry("total_energy", day.toString(), it.inKilocalories, dayStart) }
                if ("distance" in covered) values[DistanceRecord.DISTANCE_TOTAL]?.let { entry("distance", day.toString(), it.inKilometers, dayStart) }
            }
        }
        if ("workout" in covered) records<ExerciseSessionRecord>(source, from, until).forEach {
            val name = it.title?.takeIf(String::isNotBlank) ?: exerciseName(it.exerciseType)
            entry("workout", it.metadata.id, Duration.between(it.startTime, it.endTime).seconds / 60.0,
                it.startTime, JSONObject().put("name", name).put("type", exerciseName(it.exerciseType))
                    .put("start_time", stamp(it.startTime)).put("end_time", stamp(it.endTime)))
        }
        if ("sleep" in covered) records<SleepSessionRecord>(source, from.minusSeconds(86400), until).forEach {
            if (it.endTime.atZone(zone).toLocalDate() < start) return@forEach
            val awake = it.stages.filter { stage -> stage.stage in setOf(
                SleepSessionRecord.STAGE_TYPE_AWAKE, SleepSessionRecord.STAGE_TYPE_OUT_OF_BED,
                SleepSessionRecord.STAGE_TYPE_AWAKE_IN_BED,
            ) }.sumOf { stage -> Duration.between(stage.startTime, stage.endTime).seconds }
            val seconds = (Duration.between(it.startTime, it.endTime).seconds - awake).coerceAtLeast(0)
            entry("sleep", it.metadata.id, seconds / 3600.0, it.endTime,
                JSONObject().put("bedtime", it.startTime.atZone(zone).toLocalTime().toString().take(5))
                    .put("wake_time", it.endTime.atZone(zone).toLocalTime().toString().take(5)))
        }
        if ("heart_rate" in covered) records<RestingHeartRateRecord>(source, from, until).forEach {
            entry("heart_rate", it.metadata.id, it.beatsPerMinute.toDouble(), it.time)
        }
        if ("weight" in covered) records<WeightRecord>(source, from, until).forEach {
            entry("weight", it.metadata.id, it.weight.inKilograms, it.time)
        }
        if ("water" in covered) records<HydrationRecord>(source, from, until).forEach {
            entry("water", it.metadata.id, it.volume.inMilliliters, it.startTime)
        }
        if ("food" in covered) records<NutritionRecord>(source, from, until).forEach {
            val energy = it.energy ?: return@forEach
            val details = JSONObject().put("name", it.name ?: "Nutrition record")
            it.protein?.let { value -> details.put("protein", value.inGrams) }
            it.totalCarbohydrate?.let { value -> details.put("carbs", value.inGrams) }
            it.totalFat?.let { value -> details.put("fat", value.inGrams) }
            entry("food", it.metadata.id, energy.inKilocalories, it.startTime, details)
        }
        return HealthSnapshot(streamId = streamId, provider = source, start = start, end = end,
            coveredMetrics = covered, records = output.sortedByDescending { it.recordedAt })
    }

    private fun exerciseName(type: Int) = when (type) {
        ExerciseSessionRecord.EXERCISE_TYPE_WALKING -> "Walking"
        ExerciseSessionRecord.EXERCISE_TYPE_RUNNING -> "Running"
        ExerciseSessionRecord.EXERCISE_TYPE_BIKING -> "Cycling"
        ExerciseSessionRecord.EXERCISE_TYPE_YOGA -> "Yoga"
        ExerciseSessionRecord.EXERCISE_TYPE_STRENGTH_TRAINING -> "Strength training"
        ExerciseSessionRecord.EXERCISE_TYPE_SWIMMING_POOL -> "Swimming"
        else -> "Workout"
    }
}
