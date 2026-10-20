package com.ruko.demo.net

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

// Mirrors contract c2-0.1: backend/schema/events.py, published in contracts/.
// The server rejects unknown fields with HTTP 422, so keys must match exactly; ContractTest
// checks these classes against contracts/c2_decide.schema.json and the example files.
// A null field means "unknown", never "safe".

const val CONTRACT_VERSION = "c2-0.1"

@Serializable
enum class CallKind {
    @SerialName("sim") SIM,
    @SerialName("voip") VOIP,
}

@Serializable
enum class CallDirection {
    @SerialName("incoming") INCOMING,
    @SerialName("outgoing") OUTGOING,
}

@Serializable
enum class EntryMethod {
    @SerialName("typed") TYPED,
    @SerialName("qr") QR,
    @SerialName("contact") CONTACT,
    @SerialName("collect") COLLECT,
}

/** Answer to "Who are you paying?", asked for new payees above a small amount. */
@Serializable
enum class PayerSays {
    @SerialName("government") GOVERNMENT,
    @SerialName("company") COMPANY,
    @SerialName("family") FAMILY,
    @SerialName("shop") SHOP,
    @SerialName("investment") INVESTMENT,
    @SerialName("other") OTHER,
}

@Serializable
data class CallSignal(
    val active: Boolean? = null,
    val kind: CallKind? = null,
    val direction: CallDirection? = null,
    @SerialName("caller_number") val callerNumber: String? = null, // only for FRI / registry lookups
    @SerialName("caller_in_contacts") val callerInContacts: Boolean? = null,
    @SerialName("started_min_ago") val startedMinAgo: Double? = null,
    @SerialName("ended_min_ago") val endedMinAgo: Double? = null, // a call that just finished
)

@Serializable
data class DeviceSignal(
    @SerialName("remote_access_active") val remoteAccessActive: Boolean? = null,
    @SerialName("remote_app_installed_min_ago") val remoteAppInstalledMinAgo: Double? = null,
    @SerialName("untrusted_accessibility") val untrustedAccessibility: Boolean? = null,
    @SerialName("sideloaded_last_24h") val sideloadedLast24h: Boolean? = null,
)

@Serializable
data class LureSignal(val tag: String, val conf: Double, @SerialName("min_ago") val minAgo: Double)

@Serializable
data class Context(
    val call: CallSignal? = null,
    val device: DeviceSignal? = null,
    val lures: List<LureSignal> = emptyList(),
)

@Serializable
data class DecideRequest(
    @SerialName("request_id") val requestId: String, // a new UUID per Pay tap, echoed back
    @SerialName("user_id") val userId: String,
    @SerialName("payee_upi_id") val payeeUpiId: String,
    @SerialName("amount_inr") val amountInr: Double,
    @SerialName("entry_method") val entryMethod: EntryMethod,
    @SerialName("payer_says") val payerSays: PayerSays? = null,
    val ts: String, // ISO 8601 with +05:30
    val context: Context? = null, // null when the user opted out of every phone signal
)

@Serializable
data class DecideResponse(
    @SerialName("request_id") val requestId: String,
    val tier: String,
    val reasons: List<String>,
    @SerialName("message_key") val messageKey: String? = null,
    @SerialName("pause_minutes") val pauseMinutes: Int = 0,
    val playbook: String? = null,
    val stage: String? = null,
    @SerialName("evidence_id") val evidenceId: String? = null,
    @SerialName("fail_open") val failOpen: Boolean = false,
    @SerialName("contract_version") val contractVersion: String? = null,
)

// explicitNulls sends unknown fields as null; ignoreUnknownKeys keeps the app working when
// the server adds an optional response field before the app catches up.
val RukoJson = Json {
    encodeDefaults = true
    explicitNulls = true
    ignoreUnknownKeys = true
}
