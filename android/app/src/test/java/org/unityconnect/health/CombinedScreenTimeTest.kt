package org.unityconnect.health

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate

class CombinedScreenTimeTest {
    private fun summary() = JSONObject().put("version", 1).put("day", "2026-11-27")
        .put("start_ms", 1_795_737_600_000L).put("end_ms", 1_795_824_000_000L)
        .put("generated_at_ms", 1_795_737_620_000L)
        .put("combined_ms", 15_000).put("desktop_ms", 10_000).put("phone_ms", 10_000).put("overlap_ms", 5_000)

    @Test fun displaysUnionWithoutAddingSimultaneousUseTwice() {
        val result = CombinedScreenTime.parse(summary().toString())
        assertEquals(LocalDate.of(2026, 11, 27), result.day)
        assertEquals(15_000L, result.combined)
        assertEquals(10_000L, result.phone)
        assertEquals(10_000L, result.desktop)
        assertEquals(5_000L, result.overlap)
    }

    @Test fun emptyAndSingleDeviceDaysAreValid() {
        val empty = summary().put("combined_ms", 0).put("desktop_ms", 0).put("phone_ms", 0).put("overlap_ms", 0)
        assertEquals(0L, CombinedScreenTime.parse(empty.toString()).combined)
        empty.put("desktop_ms", 10_000).put("combined_ms", 10_000)
        assertEquals(10_000L, CombinedScreenTime.parse(empty.toString()).combined)
    }

    @Test fun invalidTotalsAndMalformedPayloadsAreRejected() {
        val invalid = listOf(
            summary().put("overlap_ms", 0), summary().put("combined_ms", 9_000),
            summary().put("combined_ms", 21_000), summary().put("phone_ms", -1),
            summary().put("phone_ms", true), summary().put("phone_ms", "10000"),
            summary().put("desktop_ms", 1.5), summary().put("version", 2),
            summary().put("extra", "unsupported"), summary().put("day", "2026-02-30"),
            summary().put("generated_at_ms", 1), summary().put("end_ms", Long.MAX_VALUE),
            summary().apply { remove("phone_ms") },
        )
        for (json in invalid) assertThrows(Exception::class.java) { CombinedScreenTime.parse(json.toString()) }
        assertThrows(Exception::class.java) { CombinedScreenTime.parse(" ".repeat(1025)) }
    }

    @Test fun daylightSavingDaysAndPartialCurrentDaysAreBounded() {
        val json = summary()
        val start = json.getLong("start_ms")
        for (hours in listOf(23, 25)) {
            json.put("end_ms", start + hours * 3_600_000L)
            assertNotNull(CombinedScreenTime.parse(json.toString()))
        }
        json.put("generated_at_ms", start + 5_000)
        assertThrows(Exception::class.java) { CombinedScreenTime.parse(json.toString()) }
    }
}
