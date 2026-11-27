package org.unityconnect.health

import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Binder
import android.os.IBinder
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.json.JSONArray
import org.json.JSONObject
import org.unityconnect.health.ipc.IUnityHealth
import java.io.File
import java.security.MessageDigest

/** Bounded read-only IPC, available only to signed Unity companions after opt-in. */
class HealthIpcService : Service() {
    private val pins = mapOf(
        "org.unityconnect.companion" to setOf("7d8b0dd9e14b7b8949cc40f9bd6686c0d0cbb395fa4310d7c7b05ab28ce6e19c", "cea4fb2b54c411ddacdd609b57ccb33207796fa5e104dce62628334d38bb7b57"),
        "org.unityconnect.companion.preview" to setOf("f53f3eb0f8e9f8c671a252211d3f8b2bc94c2467439a18c636162f89d66ae1d3"),
        "org.unityconnect.companion.preview.next" to setOf("cea4fb2b54c411ddacdd609b57ccb33207796fa5e104dce62628334d38bb7b57"),
    )

    private fun authorize() {
        val packages = packageManager.getPackagesForUid(Binder.getCallingUid()).orEmpty()
        check(packages.isNotEmpty() && packages.all { pkg ->
            val allowed = pins[pkg] ?: return@all false
            val signatures = packageManager.getPackageInfo(pkg, PackageManager.GET_SIGNING_CERTIFICATES)
                .signingInfo?.apkContentsSigners.orEmpty()
            signatures.size == 1 && digest(signatures.single().toByteArray()) in allowed
        }) { "Untrusted Unity health caller" }
        check(getSharedPreferences("health", MODE_PRIVATE).getBoolean("unity_sync_enabled", false)) {
            "Direct Unity integration is disabled"
        }
    }

    private fun current(): HealthSnapshot {
        val file = File(filesDir, "last_snapshot.json")
        check(file.exists() && file.length() <= MAX_SNAPSHOT_BYTES) { "No cached health records available" }
        val snapshot = HealthSnapshot.fromJson(file.readText())
        // Permission revocation also applies to cached IPC reads; this does not
        // read health records in the background or require background access.
        val granted = runBlocking { withTimeout(2000) { HealthReader(this@HealthIpcService).grantedPermissions() } }
        val allowed = HealthReader.permissionMetrics.filterKeys { it in granted }.values.toSet()
        val covered = snapshot.coveredMetrics.intersect(allowed)
        check(covered.isNotEmpty()) { "Health read access was revoked" }
        return snapshot.copy(coveredMetrics = covered, records = snapshot.records.filter { it.metric in covered })
    }

    private val binder = object : IUnityHealth.Stub() {
        override fun request(requestJson: String): String {
            authorize()
            val identity = Binder.clearCallingIdentity()
            return try {
                require(requestJson.toByteArray().size <= 1024) { "Request is too large" }
                val request = JSONObject(requestJson)
                require(request.getInt("version") == 1 && request.keys().asSequence().all { it in setOf("version", "method", "args") })
                val method = request.getString("method")
                require(method in setOf("status", "page")) { "Unsupported health method" }
                val args = request.optJSONObject("args") ?: JSONObject()
                val snapshot = current()
                val token = snapshot.snapshotId + ":" + digest(snapshot.coveredMetrics.sorted().joinToString(",").toByteArray()).take(16)
                val result = if (method == "status") {
                    require(args.length() == 0)
                    val header = JSONObject(snapshot.toJson()).apply { remove("records") }
                    JSONObject().put("state", "ready").put("token", token).put("header", header)
                        .put("record_count", snapshot.records.size).put("snapshot_id", snapshot.snapshotId)
                } else {
                    require(args.keys().asSequence().all { it in setOf("token", "offset", "limit") })
                    check(args.getString("token") == token) { "Health records changed; start sync again" }
                    val offset = args.getInt("offset")
                    val limit = args.optInt("limit", 32)
                    require(offset in 0..snapshot.records.size && limit in 1..64)
                    val records = JSONArray()
                    var next = offset
                    while (next < snapshot.records.size && records.length() < limit) {
                        val record = snapshot.records[next].toJson()
                        if (records.toString().toByteArray().size + record.toString().toByteArray().size > 40 * 1024) break
                        records.put(record)
                        next++
                    }
                    check(next > offset || offset == snapshot.records.size) { "A health record is too large" }
                    JSONObject().put("token", token).put("offset", offset).put("records", records)
                        .put("next_offset", next).put("done", next == snapshot.records.size)
                }
                JSONObject().put("version", 1).put("ok", true).put("result", result).toString().also {
                    check(it.toByteArray().size <= 48 * 1024) { "Health response exceeds the message limit" }
                }
            } catch (error: Exception) {
                JSONObject().put("version", 1).put("ok", false)
                    .put("error", error.message?.take(300) ?: "Health records unavailable").toString()
            } finally { Binder.restoreCallingIdentity(identity) }
        }
    }

    override fun onBind(intent: Intent): IBinder? = if (intent.component?.packageName == packageName) binder else null
    private fun digest(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}
