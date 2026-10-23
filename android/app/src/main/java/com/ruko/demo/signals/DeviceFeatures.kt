package com.ruko.demo.signals

import java.math.BigDecimal
import java.math.RoundingMode
import kotlin.math.abs
import kotlin.math.floor
import kotlin.math.log10

/*
 * The on-device model's inputs, computed on the phone and never sent. A line-by-line port of
 * ml/dataset/device_features.py (the spec: names, units, null rules); DeviceFeaturesTest checks it
 * against Python on real simulated payments (device_feature_vectors.json). null = the phone could
 * not measure it, never "safe" (CLAUDE.md rule 1).
 */

/** The current or just-ended call, as ContextSensor read it (null when call checks are off). */
data class CallFacts(
    val active: Boolean?,         // null without phone permission
    val kind: String?,            // "sim" | "voip" | null when there is no call
    val direction: String?,       // "incoming" | "outgoing" | null (VoIP never says)
    val inContacts: Boolean?,     // null when the number is not visible (VoIP, outgoing SIM)
    val startedMinAgo: Double?,
    val endedMinAgo: Double?,
    val foreign: Boolean?,        // the number is not +91; null when there is no number
)

/** Remote access and app checks (null when app checks are off). */
data class DeviceFacts(
    val remoteInstalled: Boolean?,
    val remoteMinAgo: Double?,
    val screenRecorded: Boolean?, // null before Android 15
    val accessibility: Boolean?,
    val sideloaded24h: Boolean?,
)

/** One scam-tag SMS hit from the lure model, at any confidence. */
data class LureFact(val tag: String, val conf: Double, val minAgo: Double)

/** The watcher's episode summary (contract `EpisodeSignal`); null when the watcher is off. */
data class EpisodeFacts(
    val state: String,
    val ageDays: Double?,
    val baitCredits: Int?,
    val baitInr: Double?,
    val outs: Int?,
    val newPayees: Int?,
    val escalationSteps: Int?,
    val lastOutInr: Double?,
    val outToInRatio: Double?,
    val payeeSeenInEpisode: Boolean?,
)

/** One of the user's own earlier payments (this app's completed payments). */
data class PastPayment(val tsSeconds: Double, val payee: String, val amountInr: Double)

/** Everything the phone knows at the Pay tap. [session] maps contract `session` field -> value. */
data class PayFacts(
    val call: CallFacts?,
    val device: DeviceFacts?,
    val lureHits: List<LureFact>?,           // null when SMS checks are off
    val episode: EpisodeFacts?,
    val session: Map<String, Any?>?,         // null when Session checks are off
    val payeeInContacts: Boolean?,
    val history: List<PastPayment>,
    val amountInr: Double,
    val entryMethod: String,                 // typed | qr | contact | collect
    val payee: String,
    val nowSeconds: Double,
    val anyShield: Boolean,
    val escalationFactor: Double = 1.5,
)

object DeviceFeatures {
    const val COLD_START_PAYMENTS = 10
    private const val DAY_S = 86_400.0
    private const val IST_OFFSET_S = 5.5 * 3600
    private val LURE_TAG = linkedMapOf(
        "arrest_threat" to "dev_lure_arrest", "kyc" to "dev_lure_kyc", "task_job" to "dev_lure_task",
        "trading_tip" to "dev_lure_trading", "refund" to "dev_lure_refund",
    )
    private val SESSION = linkedMapOf(
        "amount_digits" to "dev_sess_digits", "amount_median_gap_s" to "dev_sess_median_gap_s",
        "amount_max_gap_s" to "dev_sess_max_gap_s", "amount_gap_vs_usual" to "dev_sess_gap_vs_usual",
        "amount_edits" to "dev_sess_edits", "payee_pasted" to "dev_sess_pasted",
        "pasted_within_s_of_return" to "dev_sess_paste_after_return_s", "app_switches" to "dev_sess_app_switches",
        "away_s" to "dev_sess_away_s", "pay_screen_s" to "dev_sess_pay_screen_s",
        "abandoned_flows_30m" to "dev_sess_abandoned_30m",
    )
    private val STATE_CODE = mapOf("none" to 0.0, "watching" to 1.0, "active" to 2.0, "dismissed" to 3.0)

    val ALL: List<String> = listOf(
        "dev_call_active", "dev_call_voip", "dev_call_unknown", "dev_call_incoming", "dev_call_minutes",
        "dev_call_ended_min_ago", "dev_call_foreign", "dev_remote_installed", "dev_remote_min_ago",
        "dev_screen_recorded", "dev_accessibility", "dev_sideloaded_24h",
        "dev_lure_max", *LURE_TAG.values.toTypedArray(), "dev_lure_count_72h", "dev_lure_min_ago",
        "dev_ep_state", "dev_ep_age_days", "dev_ep_bait_credits", "dev_ep_bait_inr", "dev_ep_outs",
        "dev_ep_new_payees", "dev_ep_escalation", "dev_ep_out_to_in", "dev_ep_payee_seen",
        "dev_amount_log10", "dev_entry_typed", "dev_entry_qr", "dev_first_time_payee", "dev_payee_in_contacts",
        "dev_hist_n", "dev_amount_to_p95", "dev_amount_to_max", "dev_amount_to_median", "dev_out_1h",
        "dev_out_24h", "dev_hour_share", *SESSION.values.toTypedArray(),
    )

    private fun b(x: Boolean?): Double? = x?.let { if (it) 1.0 else 0.0 }

    /** Python's round(x, n): round half to even on the exact binary value. */
    private fun round(x: Double, n: Int): Double = BigDecimal(x).setScale(n, RoundingMode.HALF_EVEN).toDouble()

    fun compute(p: PayFacts): Map<String, Double?> {
        val f = LinkedHashMap<String, Double?>()
        if (!p.anyShield) {
            ALL.forEach { f[it] = null }
            return f
        }
        // C: calls (null when call checks are off) and remote control (null when app checks are off)
        val call = p.call
        f["dev_call_active"] = call?.active?.let { b(it) }
        f["dev_call_voip"] = call?.kind?.let { b(it == "voip") }
        f["dev_call_unknown"] = call?.inContacts?.let { b(!it) }
        f["dev_call_incoming"] = call?.direction?.let { b(it == "incoming") }
        f["dev_call_minutes"] = call?.startedMinAgo
        f["dev_call_ended_min_ago"] = call?.endedMinAgo
        f["dev_call_foreign"] = call?.foreign?.let { b(it) }
        val dev = p.device
        f["dev_remote_installed"] = dev?.remoteInstalled?.let { b(it) }
        f["dev_remote_min_ago"] = dev?.remoteMinAgo
        f["dev_screen_recorded"] = dev?.screenRecorded?.let { b(it) }
        f["dev_accessibility"] = dev?.accessibility?.let { b(it) }
        f["dev_sideloaded_24h"] = dev?.sideloaded24h?.let { b(it) }

        // L: every scam-tag SMS in 72 h at any confidence, and the episode
        val hits = p.lureHits
        if (hits == null) {
            listOf("dev_lure_max", *LURE_TAG.values.toTypedArray(), "dev_lure_count_72h", "dev_lure_min_ago").forEach { f[it] = null }
        } else {
            f["dev_lure_max"] = hits.maxOfOrNull { it.conf } ?: 0.0
            for ((tag, name) in LURE_TAG) f[name] = hits.filter { it.tag == tag }.maxOfOrNull { it.conf } ?: 0.0
            f["dev_lure_count_72h"] = hits.size.toDouble()
            f["dev_lure_min_ago"] = hits.minOfOrNull { it.minAgo }
        }
        val ep = p.episode
        if (ep == null) {
            listOf("dev_ep_state", "dev_ep_age_days", "dev_ep_bait_credits", "dev_ep_bait_inr", "dev_ep_outs",
                "dev_ep_new_payees", "dev_ep_escalation", "dev_ep_out_to_in", "dev_ep_payee_seen").forEach { f[it] = null }
        } else {
            f["dev_ep_state"] = STATE_CODE.getValue(ep.state)
            f["dev_ep_age_days"] = ep.ageDays
            f["dev_ep_bait_credits"] = ep.baitCredits?.toDouble()
            f["dev_ep_bait_inr"] = ep.baitInr
            f["dev_ep_outs"] = ep.outs?.toDouble()
            f["dev_ep_new_payees"] = ep.newPayees?.toDouble()
            val last = ep.lastOutInr
            f["dev_ep_escalation"] = ep.escalationSteps?.let { steps ->
                // Does this payment continue the run? (same rule as features.build)
                (if (last != null && last != 0.0 && p.amountInr >= p.escalationFactor * last) steps + 1 else 0).toDouble()
            }
            f["dev_ep_out_to_in"] = ep.outToInRatio
            f["dev_ep_payee_seen"] = b(ep.payeeSeenInEpisode)
        }

        // B: this payment against the user's own history (90 days), and the Pay screen
        val before = p.history.filter { it.tsSeconds < p.nowSeconds }
        val recent = before.filter { it.tsSeconds >= p.nowSeconds - 90 * DAY_S }
        val amounts = recent.map { it.amountInr }
        val n = amounts.size
        val warm = n >= COLD_START_PAYMENTS
        f["dev_amount_log10"] = log10(p.amountInr)
        f["dev_entry_typed"] = b(p.entryMethod == "typed")
        f["dev_entry_qr"] = b(p.entryMethod == "qr")
        f["dev_first_time_payee"] = b(before.none { it.payee == p.payee })
        f["dev_payee_in_contacts"] = b(p.payeeInContacts)
        f["dev_hist_n"] = n.toDouble()
        f["dev_amount_to_p95"] = if (warm) round(p.amountInr / quantile(amounts, 0.95), 3) else null
        f["dev_amount_to_max"] = if (warm) round(p.amountInr / amounts.max(), 3) else null
        f["dev_amount_to_median"] = if (warm) round(p.amountInr / median(amounts), 3) else null
        f["dev_out_1h"] = recent.count { it.tsSeconds >= p.nowSeconds - 3600 }.toDouble()
        f["dev_out_24h"] = recent.count { it.tsSeconds >= p.nowSeconds - DAY_S }.toDouble()
        f["dev_hour_share"] = if (warm) hourShare(recent.map { it.tsSeconds }, p.nowSeconds) else null
        val ses = p.session
        for ((key, name) in SESSION) {
            f[name] = when (val v = ses?.get(key)) {
                null -> null
                is Boolean -> b(v)
                is Number -> v.toDouble()
                else -> null
            }
        }
        return f
    }

    /** Linear interpolation between sorted values, as ml/dataset/phone_view.py `_quantile`. */
    private fun quantile(xs: List<Double>, q: Double): Double {
        val s = xs.sorted()
        val k = (s.size - 1) * q
        val lo = floor(k).toInt()
        val hi = minOf(lo + 1, s.size - 1)
        return s[lo] + (s[hi] - s[lo]) * (k - lo)
    }

    /** Python's statistics.median. */
    private fun median(xs: List<Double>): Double {
        val s = xs.sorted()
        val m = s.size / 2
        return if (s.size % 2 == 1) s[m] else (s[m - 1] + s[m]) / 2
    }

    /** Share of earlier payments made within 90 minutes (clock time, IST) of this one. */
    private fun hourShare(past: List<Double>, now: Double): Double {
        fun clock(x: Double) = ((x + IST_OFFSET_S) % 86_400) / 3600
        val c = clock(now)
        val near = past.count { val d = abs(clock(it) - c); minOf(d, 24 - d) <= 1.5 }
        return round(near.toDouble() / past.size, 3)
    }
}
