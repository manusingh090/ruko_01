package com.ruko.demo.signals

import kotlin.math.floor

/**
 * The phone's verdict, contract c2-0.8 `context.device_verdict`: the only thing the phone sends about
 * what it saw. A line-by-line port of ml/model/device/verdict.py.
 *
 * - c / l / b: the device model's family scores, 2 decimals; null when every input of that family was
 *   unmeasured (a model's guess about nothing must not look like a measurement).
 * - level: high when the local warning fires, medium when any family is lit, low otherwise.
 * - fraudClass: the known scam the phone's own signals point to (the engine's playbook names).
 */
data class Verdict(
    val modelVersion: String,
    val fraudClass: String?,
    val level: String,
    val c: Double?,
    val l: Double?,
    val b: Double?,
    val reasons: List<String>,
    val warn: Boolean,
)

object DeviceVerdicts {
    private const val LURE_SENT = 0.6            // LURE_SEND_THRESHOLD
    private const val RECENT_INSTALL_MIN = 120.0 // features.build: a recent remote install is control

    /** Half up, the same in Python (math.floor(x * 100 + 0.5) / 100). */
    fun round2(x: Double): Double = floor(x * 100 + 0.5) / 100

    fun fraudClass(f: Map<String, Double?>): String? {
        fun on(k: String): Boolean = f[k]?.let { it >= 0.5 } ?: false
        fun lure(k: String) = (f[k] ?: 0.0) >= LURE_SENT
        val callNow = on("dev_call_active") || f["dev_call_ended_min_ago"] != null
        val unknown = on("dev_call_unknown") || (on("dev_call_voip") && f["dev_call_unknown"] == null)
        val remoteAgo = f["dev_remote_min_ago"]
        val remote = on("dev_screen_recorded") || (remoteAgo != null && remoteAgo <= RECENT_INSTALL_MIN)
        val incoming = f["dev_call_incoming"]
        val outgoing = incoming != null && incoming < 0.5
        return when {
            (f["dev_ep_state"] ?: 0.0) == 2.0 -> "task_job"
            on("dev_sideloaded_24h") && (lure("dev_lure_kyc") || on("dev_accessibility")) -> "fake_kyc_apk"
            callNow && outgoing && remote -> "fake_customer_care"
            lure("dev_lure_arrest") || (callNow && unknown && remote) -> "digital_arrest"
            lure("dev_lure_trading") -> "trading_group"
            lure("dev_lure_task") -> "task_job"
            callNow && unknown && on("dev_entry_qr") -> "refund_qr"
            lure("dev_lure_refund") -> "refund_qr"
            lure("dev_lure_kyc") -> "fake_kyc_apk"
            else -> null
        }
    }

    fun of(model: DeviceModel, features: Map<String, Double?>): Verdict {
        val r = model.score(features)
        val fam = model.familyOrder.associateWith { family ->
            val measured = model.familyFeatures(family).any { features[it] != null }
            if (measured) round2(r.familyScores.getValue(family)) else null
        }
        val level = if (r.warn) "high" else if (r.lit.isNotEmpty()) "medium" else "low"
        return Verdict(model.version, fraudClass(features), level, fam["C"], fam["L"], fam["B"], r.reasons, r.warn)
    }
}
