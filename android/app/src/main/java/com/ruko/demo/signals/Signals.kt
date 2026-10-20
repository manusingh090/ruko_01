package com.ruko.demo.signals

import android.Manifest.permission.READ_CALL_LOG
import android.Manifest.permission.READ_CONTACTS
import android.Manifest.permission.READ_PHONE_STATE
import android.Manifest.permission.READ_SMS
import android.content.Context
import android.content.pm.PackageManager
import android.provider.Telephony
import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import com.ruko.demo.data.Consents
import kotlinx.serialization.json.Json
import java.io.FileNotFoundException
import com.ruko.demo.net.Context as RequestContext

/** Runtime permissions each Scam Shield consent needs. App checks need none (manifest <queries>). */
object ShieldPermissions {
    val CALLS = listOf(READ_PHONE_STATE, READ_CONTACTS, READ_CALL_LOG)
    val SMS = listOf(READ_SMS)

    fun wanted(consents: Consents): List<String> = buildList {
        if (consents.callSignals) addAll(CALLS)
        if (consents.smsLure) addAll(SMS)
    }

    fun missing(context: Context, consents: Consents): List<String> = wanted(consents).filter {
        ContextCompat.checkSelfPermission(context, it) != PackageManager.PERMISSION_GRANTED
    }
}

/** One reading taken when the user taps Pay. */
data class SignalReading(
    val phone: PhoneSignals,
    val context: RequestContext?,
    val lures: List<LureHit>,
    val simulated: List<String>,
)

/** App-wide access to the sensor, the lure model and the debug overrides. */
object Signals {
    private lateinit var app: Context
    lateinit var sensor: ContextSensor
        private set
    var classifier: LureClassifier = KeywordLureClassifier
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
        classifier = loadLureModel(app)
    }

    /** Off the main thread: may read SMS and query packages. */
    fun read(consents: Consents): SignalReading {
        val now = System.currentTimeMillis()
        val real = PhoneSignals(
            call = if (consents.callSignals) sensor.readCall(now) else null,
            device = if (consents.appChecks) sensor.readDevice(now) else null,
        )
        val o = overrides
        val phone = o.applyTo(real)
        val lures = buildList {
            if (consents.smsLure) addAll(SmsLure.scan(app, classifier, now))
            pasted?.let(::add)
            o.lureTag?.let { add(LureHit(it, 0.9)) }
        }
        val best = bestLureTags(lures)
        return SignalReading(phone, phone.toRequestContext(best), best, o.active)
    }

    private fun loadLureModel(context: Context): LureClassifier = try {
        val text = context.assets.open(LURE_ASSET).bufferedReader().use { it.readText() }
        LinearLureClassifier(Json { ignoreUnknownKeys = true }.decodeFromString(LureModel.serializer(), text))
    } catch (e: FileNotFoundException) {
        KeywordLureClassifier // lure.json from Diwansu is due at H28
    } catch (e: Exception) {
        Log.w("Signals", "bad $LURE_ASSET, using keywords", e)
        KeywordLureClassifier
    }

    /** Drop Diwansu's model/artifacts/lure.json here to replace the keyword fallback. */
    const val LURE_ASSET = "lure.json"
}

/** Scores SMS from the last 72 h on the phone. Message text never leaves this function. */
object SmsLure {
    private const val WINDOW_MS = 72 * 60 * 60_000L
    private const val MAX_MESSAGES = 200

    fun scan(context: Context, classifier: LureClassifier, now: Long): List<LureHit> {
        if (ContextCompat.checkSelfPermission(context, READ_SMS) != PackageManager.PERMISSION_GRANTED) return emptyList()
        val hits = ArrayList<LureHit>()
        try {
            context.contentResolver.query(
                Telephony.Sms.Inbox.CONTENT_URI,
                arrayOf(Telephony.Sms.BODY, Telephony.Sms.DATE),
                "${Telephony.Sms.DATE} >= ?",
                arrayOf((now - WINDOW_MS).toString()),
                "${Telephony.Sms.DATE} DESC",
            )?.use { cursor ->
                while (cursor.moveToNext() && hits.size < MAX_MESSAGES) {
                    val body = cursor.getString(0) ?: continue
                    val minAgo = (now - cursor.getLong(1)) / 60_000.0
                    hits += classifier.classify(body).copy(minAgo = minAgo)
                }
            }
        } catch (e: SecurityException) {
            return emptyList()
        }
        return hits
    }
}
