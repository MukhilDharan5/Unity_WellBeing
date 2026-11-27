package org.unityconnect.health

import android.content.Context
import android.content.Intent

/** Notify the authenticated Unity app; health records travel only over Binder/TLS. */
object UnityTransfer {
    const val ACTION = "org.unityconnect.health.SNAPSHOT_UPDATED"
    val packages = listOf("org.unityconnect.companion", "org.unityconnect.companion.preview", "org.unityconnect.companion.preview.next")
    fun available(context: Context) = packages.any { name ->
        runCatching { context.packageManager.getPackageInfo(name, 0) }.isSuccess
    }
    fun send(context: Context, snapshot: HealthSnapshot) {
        check(context.getSharedPreferences("health", Context.MODE_PRIVATE).getBoolean("unity_sync_enabled", false)) {
            "Direct Unity integration is disabled"
        }
        notifyChanged(context)
    }
    fun notifyChanged(context: Context) {
        val installed = packages.filter { name -> runCatching { context.packageManager.getPackageInfo(name, 0) }.isSuccess }
        check(installed.isNotEmpty()) { "Unity companion unavailable" }
        installed.forEach { target ->
            context.sendBroadcast(Intent(ACTION).setPackage(target),
                "org.unityconnect.health.permission.READ_HEALTH")
        }
    }
}
