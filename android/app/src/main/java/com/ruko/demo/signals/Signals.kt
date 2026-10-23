package com.ruko.demo.signals

import android.Manifest.permission.READ_CALL_LOG
import android.Manifest.permission.READ_CONTACTS
import android.Manifest.permission.READ_PHONE_STATE
import android.Manifest.permission.POST_NOTIFICATIONS
import android.Manifest.permission.READ_SMS
import android.Manifest.permission.RECEIVE_SMS
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Telephony
import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import com.ruko.demo.BuildConfig
import com.ruko.demo.data.Consents
import com.ruko.demo.net.CallDirection
import com.ruko.demo.net.CallKind
import com.ruko.demo.net.DeviceVerdict
import com.ruko.demo.net.EntryMethod
import com.ruko.demo.net.EpisodeSignal
import com.ruko.demo.net.SessionSignal
import com.ruko.demo.shield.ShieldController
import kotlinx.serialization.json.Json
import java.io.FileNotFoundException
import com.ruko.demo.net.Context as RequestContext

/** Runtime permissions each Scam Shield consent needs. App checks need none (manifest <queries>). */
object ShieldPermissions {
    val CALLS = listOf(READ_PHONE_STATE, READ_CONTACTS, READ_CALL_LOG)
    val SMS = listOf(READ_SMS)
    val WATCH = buildList {
        add(RECEIVE_SMS)
        add(READ_SMS)
        if (Build.VERSION.SDK_INT >= 33) add(POST_NOTIFICATIONS)
    }

    fun wanted(consents: Consents): List<String> = buildList {
        if (consents.callSignals) addAll(CALLS)
        if (consents.smsLure) addAll(SMS)
        if (consents.moneyWatch) addAll(WATCH)
    }

    fun missing(context: Context, consents: Consents): List<String> = wanted(consents).distinct().filter {
        ContextCompat.checkSelfPermission(context, it) != PackageManager.PERMISSION_GRANTED
    }
}

/** One reading taken when the user taps Pay. */
data class SignalReading(
    val phone: PhoneSignals,
    val context: RequestContext?,
    val lures: List<LureHit>,
    val episode: EpisodeSignal?,
    val simulated: List<String>,
)

/** App-wide access to the sensor, the lure model and the debug overrides. */
object Signals {
    private lateinit var app: Context
    lateinit var sensor: ContextSensor
        private set
    lateinit var contacts: ContactBook
        private set
    var classifier: LureClassifier = KeywordLureClassifier
        private set

    /** The on-device model (assets/device_model.json); null when missing, and then no verdict is sent. */
    var deviceModel: DeviceModel? = null
        private set

    var overrides by mutableStateOf(SignalOverrides())

    /** A message the user pasted into the check box; counts for the next payment only. */
    var pasted by mutableStateOf<LureHit?>(null)

    /** The automatic permission prompt is shown once per app start; after that the Home card asks. */
    var askedForPermissions = false

    fun init(context: Context) {
        if (::app.isInitialized) return
        app = context.applicationContext
        sensor = ContextSensor(app)
        contacts = ContactBook(app)
        classifier = loadLureModel(app)
        deviceModel = try {
            DeviceModel.parse(app.assets.open(DEVICE_MODEL_ASSET).bufferedReader().use { it.readText() })
        } catch (e: Exception) {
            Log.w("Signals", "no $DEVICE_MODEL_ASSET, sending no device verdict", e)
            null
        }
    }

    /**
     * Off the main thread: may read SMS and query packages. [payee] is the UPI ID being paid;
     * [session] is how the Pay screen was used for this payment (c2-0.5), sent only with
     * "Session checks" on. [payeeMobile] is the mobile number the payee was paid by, if any; it is
     * only matched against the phone's contacts (c2-0.7), never sent. With [amountInr],
     * [entryMethod] and [history] (this app's completed payments), the on-device model also runs and
     * its verdict goes in the request (c2-0.8); the readings behind it stay on the phone.
     */
    fun read(
        consents: Consents,
        payee: String? = null,
        session: SessionSignal? = null,
        payeeMobile: String? = null,
        amountInr: Double? = null,
        entryMethod: EntryMethod? = null,
        history: List<PastPayment> = emptyList(),
    ): SignalReading {
        val now = System.currentTimeMillis()
        val real = PhoneSignals(
            call = if (consents.callSignals) sensor.readCall(now) else null,
            device = if (consents.appChecks) sensor.readDevice(now) else null,
        )
        val o = overrides
        val phone = o.applyTo(real)
        val sms = if (consents.smsLure) SmsScan.scan(app, classifier, now) else SmsScan.Result.EMPTY
        val lures = buildList {
            addAll(sms.lures)
            pasted?.let(::add)
            o.lureTag?.let { add(LureHit(it, 0.9)) }
        }
        val best = bestLureTags(lures)
        // The background watcher's task-scam episode (c2-0.3); null while it is switched off.
        val episode = runCatching { ShieldController.summary(app, consents, payee) }.getOrNull()
        val ses = if (consents.sessionSignals) o.applyTo(session) else null
        // Contacts are read under the call-checks consent, which asked for that permission.
        val inContacts = when {
            !consents.callSignals -> null
            o.payeeInContacts != null -> o.payeeInContacts
            payee != null -> runCatching { contacts.payeeInContacts(payee, payeeMobile) }.getOrNull()
            else -> null
        }
        val anyShield = consents.callSignals || consents.appChecks || consents.smsLure || consents.moneyWatch ||
            consents.sessionSignals
        val model = deviceModel
        val verdict = if (model != null && anyShield && payee != null && amountInr != null && entryMethod != null) {
            runCatching {
                val facts = PayFacts(
                    call = phone.call?.toFacts(),
                    device = phone.device?.toFacts(),
                    lureHits = if (consents.smsLure) lures.filter { it.tag != "normal" }.map { LureFact(it.tag, it.conf, it.minAgo) } else null,
                    episode = episode?.toFacts(),
                    session = ses?.toMap(),
                    payeeInContacts = inContacts,
                    history = history,
                    amountInr = amountInr,
                    entryMethod = entryMethod.name.lowercase(),
                    payee = payee.trim().lowercase(),
                    nowSeconds = now / 1000.0,
                    anyShield = true,
                )
                val v = DeviceVerdicts.of(model, DeviceFeatures.compute(facts))
                DeviceVerdict(v.modelVersion, v.fraudClass, v.level, v.c, v.l, v.b, v.reasons)
            }.onFailure { Log.w("Signals", "device model failed; no verdict", it) }.getOrNull()
        } else {
            null
        }
        return SignalReading(phone, phone.toRequestContext(best, episode, ses, inContacts, verdict), best, episode, o.active)
    }

    private fun CallReading.toFacts() = CallFacts(
        active = active,
        kind = kind?.let { if (it == CallKind.VOIP) "voip" else "sim" },
        direction = direction?.let { if (it == CallDirection.INCOMING) "incoming" else "outgoing" },
        inContacts = callerInContacts,
        startedMinAgo = startedMinAgo,
        endedMinAgo = endedMinAgo,
        foreign = callerNumber?.let(::toE164)?.let { !it.startsWith("+91") },
    )

    private fun DeviceReading.toFacts() = DeviceFacts(
        remoteInstalled = remoteAppInstalled,
        remoteMinAgo = remoteAppInstalledMinAgo,
        screenRecorded = screenRecorded,
        accessibility = untrustedAccessibility,
        sideloaded24h = sideloadedLast24h,
    )

    private fun EpisodeSignal.toFacts() = EpisodeFacts(
        state, ageDays, baitCredits, baitInr, outs, newPayees, escalationSteps, lastOutInr, outToInRatio, payeeSeenInEpisode,
    )

    private fun SessionSignal.toMap(): Map<String, Any?> = mapOf(
        "amount_digits" to amountDigits, "amount_median_gap_s" to amountMedianGapS, "amount_max_gap_s" to amountMaxGapS,
        "amount_gap_vs_usual" to amountGapVsUsual, "amount_edits" to amountEdits, "payee_pasted" to payeePasted,
        "pasted_within_s_of_return" to pastedWithinSOfReturn, "app_switches" to appSwitches, "away_s" to awayS,
        "pay_screen_s" to payScreenS, "abandoned_flows_30m" to abandonedFlows30m,
    )

    private fun loadLureModel(context: Context): LureClassifier = try {
        val text = context.assets.open(LURE_ASSET).bufferedReader().use { it.readText() }
        LinearLureClassifier(Json { ignoreUnknownKeys = true }.decodeFromString(LureModel.serializer(), text))
    } catch (e: FileNotFoundException) {
        KeywordLureClassifier // lure.json from Diwansu is due at H28
    } catch (e: Exception) {
        Log.w("Signals", "bad $LURE_ASSET, using keywords", e)
        KeywordLureClassifier
    }

    /** Diwansu's ml/model/artifacts/lure.json; the keyword fallback is used when it is missing. */
    const val LURE_ASSET = "lure.json"

    /** ml/model/artifacts/v0/device_model.json (c2-0.8 device verdict). */
    const val DEVICE_MODEL_ASSET = "device_model.json"
}

/**
 * One pass over SMS from the last 72 h, on the phone: scores each message for scam scripts and
 * reads bank UPI alerts into [MoneyTxn]s. Message text never leaves this object.
 */
object SmsScan {
    private const val WINDOW_MS = 72 * 60 * 60_000L
    private const val MAX_MESSAGES = 200

    data class Result(val lures: List<LureHit>, val txns: List<MoneyTxn>) {
        companion object {
            val EMPTY = Result(emptyList(), emptyList())
        }
    }

    fun scan(context: Context, classifier: LureClassifier, now: Long): Result {
        if (ContextCompat.checkSelfPermission(context, READ_SMS) != PackageManager.PERMISSION_GRANTED) return Result.EMPTY
        val hits = ArrayList<LureHit>()
        val txns = ArrayList<MoneyTxn>()
        try {
            context.contentResolver.query(
                Telephony.Sms.Inbox.CONTENT_URI,
                arrayOf(Telephony.Sms.BODY, Telephony.Sms.DATE, Telephony.Sms.ADDRESS),
                "${Telephony.Sms.DATE} >= ?",
                arrayOf((now - WINDOW_MS).toString()),
                "${Telephony.Sms.DATE} DESC",
            )?.use { cursor ->
                var seen = 0
                while (cursor.moveToNext() && seen++ < MAX_MESSAGES) {
                    val body = cursor.getString(0) ?: continue
                    val ts = cursor.getLong(1)
                    // Debug builds accept numbered senders, so the emulator's `sms send` can play a bank.
                    val bank = if (BankSms.trustedSender(cursor.getString(2), allowNumbers = BuildConfig.DEBUG)) {
                        BankSms.parse(body, ts)
                    } else {
                        null
                    }
                    if (bank != null) txns += bank else hits += classifier.classify(body).copy(minAgo = (now - ts) / 60_000.0)
                }
            }
        } catch (e: SecurityException) {
            return Result.EMPTY
        }
        return Result(hits, txns)
    }
}
