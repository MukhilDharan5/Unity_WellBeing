package org.unityconnect.health

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate
import java.time.OffsetDateTime

class HealthSnapshotTest {
    private fun snapshot(records: List<HealthEntry>) = HealthSnapshot(
        streamId = "test-phone", provider = HealthSource.GOOGLE_FIT,
        start = LocalDate.now().minusDays(27), end = LocalDate.now().plusDays(1),
        coveredMetrics = setOf("steps", "weight"), records = records,
    )

    @Test fun missingValuesStayMissing() {
        val snapshot = snapshot(emptyList())
        assertNull(snapshot.value("steps"))
        assertNull(snapshot.value("weight"))
        assertTrue(snapshot.records.isEmpty())
    }

    @Test fun cumulativeMetricsAreSummedAndMeasurementsUseLatest() {
        val now = OffsetDateTime.now()
        val entries = listOf(
            HealthEntry("one", "steps", 1200.0, now.minusMinutes(10).toString()),
            HealthEntry("two", "steps", 300.0, now.toString()),
            HealthEntry("three", "weight", 72.0, now.minusMinutes(5).toString()),
            HealthEntry("four", "weight", 71.5, now.toString()),
        )
        val snapshot = snapshot(entries)
        assertEquals(1500.0, snapshot.value("steps")!!, .001)
        assertEquals(71.5, snapshot.value("weight")!!, .001)
    }

    @Test fun wireFormatPreservesSourceIdentityPermissionsAndUnits() {
        val snapshot = snapshot(listOf(HealthEntry("steps:today", "steps", 1234.0,
            OffsetDateTime.now().toString(), JSONObject().put("origin", HealthSource.GOOGLE_FIT.packageName))))
        val json = JSONObject(snapshot.toJson())
        assertEquals(SNAPSHOT_SCHEMA, json.getString("schema"))
        assertEquals(1, json.getInt("version"))
        assertEquals(HealthSource.GOOGLE_FIT.packageName, json.getString("provider"))
        assertEquals("test-phone", json.getString("stream_id"))
        assertEquals("steps", json.getJSONArray("records").getJSONObject(0).getString("metric"))
        val decoded = HealthSnapshot.fromJson(snapshot.toJson())
        assertEquals(snapshot.snapshotId, decoded.snapshotId)
        assertEquals(snapshot.coveredMetrics, decoded.coveredMetrics)
        assertEquals(1234.0, decoded.value("steps")!!, .001)
    }

    @Test fun unsupportedProviderCannotBeLoadedFromCache() {
        val json = JSONObject(snapshot(emptyList()).toJson()).put("provider", "unknown.app")
        assertThrows(NoSuchElementException::class.java) { HealthSnapshot.fromJson(json.toString()) }
    }

    @Test fun providerCaloriesAndDistanceStaySeparateFromActiveEnergy() {
        val now = OffsetDateTime.now().toString()
        val original = snapshot(listOf(
            HealthEntry("total", "total_energy", 1820.5, now),
            HealthEntry("active", "active_energy", 200.0, now),
            HealthEntry("distance", "distance", 2.75, now),
        )).copy(coveredMetrics = setOf("total_energy", "active_energy", "distance"))
        val decoded = HealthSnapshot.fromJson(original.toJson())
        assertEquals(1820.5, decoded.value("total_energy")!!, .001)
        assertEquals(200.0, decoded.value("active_energy")!!, .001)
        assertEquals(2.75, decoded.value("distance")!!, .001)
        assertNull(decoded.value("steps"))
    }

    @Test fun permissionsAreReadOnlyAndProviderListExcludesNativeSensors() {
        assertTrue(HealthReader.permissions.contains("android.permission.health.READ_TOTAL_CALORIES_BURNED"))
        assertTrue(HealthReader.permissions.contains("android.permission.health.READ_DISTANCE"))
        assertTrue(HealthReader.permissions.all { it.contains(".READ_") })
        assertFalse(HealthReader.permissions.any { it.contains("WRITE") || it.contains("BACKGROUND") })
        assertEquals("com.google.android.apps.fitness", HealthSource.GOOGLE_FIT.packageName)
        assertFalse(HealthSource.entries.any { it.packageName == "android" })
    }
}
