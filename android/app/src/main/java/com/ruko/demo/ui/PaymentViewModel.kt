package com.ruko.demo.ui

import android.os.SystemClock
import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ruko.demo.DemoConfig
import com.ruko.demo.data.Consents
import com.ruko.demo.data.PaymentRecord
import com.ruko.demo.net.ApiClient
import com.ruko.demo.net.DecideRequest
import com.ruko.demo.net.DecideResponse
import com.ruko.demo.net.EntryMethod
import com.ruko.demo.net.PayerSays
import com.ruko.demo.net.RequestBuilder
import com.ruko.demo.net.RukoApi
import com.ruko.demo.net.RukoJson
import com.ruko.demo.signals.PastPayment
import com.ruko.demo.signals.SessionTracker
import com.ruko.demo.signals.Signals
import com.ruko.demo.signals.toE164
import retrofit2.HttpException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class ResolveError { BAD_NUMBER, NOT_LINKED, UNREACHABLE }

sealed interface Decision {
    data class Result(val response: DecideResponse, val millis: Long) : Decision

    /** The server was slow, down, refused the request or said it could not decide. The payment proceeds. */
    data object Unavailable : Decision
}

class PaymentViewModel(private val api: RukoApi = ApiClient.api) : ViewModel() {

    var upiId by mutableStateOf("")
    var amountText by mutableStateOf("")

    // TYPED when the UPI ID was typed, CONTACT when it was picked from saved payees.
    var entryMethod by mutableStateOf(EntryMethod.TYPED)

    // Paying by mobile number (c2-0.7): who it is and the number used. Cleared when the UPI ID is edited.
    var payeeName by mutableStateOf<String?>(null)
        private set
    var registeredName by mutableStateOf<String?>(null)
        private set
    var payeeMobile by mutableStateOf<String?>(null)
        private set
    var resolving by mutableStateOf(false)
        private set
    var resolveError by mutableStateOf<ResolveError?>(null)
        private set

    // Optional answer to "Who are you paying?" for a new payee.
    var payerSays by mutableStateOf<PayerSays?>(null)

    // The user's current Scam Shield switches, kept in sync by the app from the account.
    var consents by mutableStateOf(Consents())

    // This user's usual seconds between amount digits (last 10 payments), kept in sync by the app.
    var typingGaps by mutableStateOf<List<Double>>(emptyList())

    // This user's completed payments, for the on-device model (c2-0.8). Never sent.
    var pastPayments by mutableStateOf<List<PastPayment>>(emptyList())

    // How this Pay screen is being used (c2-0.5): typing rhythm, paste, app switches. Memory only.
    private val session = SessionTracker()

    // The typing rhythm of the last Pay, saved to the user's baseline once the payment completes.
    var lastTypingGap: Double? = null
        private set

    // Debug overrides that shaped the last request, shown as SIMULATED.
    var lastSimulated by mutableStateOf<List<String>>(emptyList())
        private set

    var checking by mutableStateOf(false)
        private set
    var decision by mutableStateOf<Decision?>(null)
        private set

    // The payment being made, for the history: its id is the request_id sent to /v1/decide.
    var checkedPayment by mutableStateOf<PaymentRecord?>(null)
        private set
    var serverStatus by mutableStateOf<String?>(null)
        private set

    // The exact JSON sent on the last Pay; the debug panel shows it.
    var lastRequestJson by mutableStateOf("")
        private set

    val amountInr: Double?
        get() = amountText.toDoubleOrNull()?.takeIf { it > 0 }

    val canPay: Boolean
        get() = upiId.trim().contains('@') && amountInr != null && !checking

    /** The question is for new payees above small everyday amounts. */
    val askWhoIsPayee: Boolean
        get() = entryMethod == EntryMethod.TYPED && (amountInr ?: 0.0) >= ASK_PAYEE_FROM_INR

    fun typePayee(text: String) {
        session.onPayeeChange(upiId, text, SystemClock.elapsedRealtime())
        upiId = text
        entryMethod = EntryMethod.TYPED
        clearMobilePayee()
    }

    /**
     * Pay a mobile number: a saved contact ([name] set, [picked] true) or a number typed by hand.
     * The UPI number mapper (the mock NPCI) gives the UPI ID linked to it and its registered name.
     */
    fun payMobile(name: String?, mobile: String, picked: Boolean, onReady: () -> Unit) {
        val e164 = toE164(mobile) ?: run { resolveError = ResolveError.BAD_NUMBER; return }
        resolveError = null
        resolving = true
        viewModelScope.launch {
            val mapped = try {
                withContext(Dispatchers.IO) { ApiClient.npci.mapper(e164) }.takeIf { it.mapped && it.upiId != null }
            } catch (e: CancellationException) {
                throw e
            } catch (e: HttpException) {
                null // 404: no UPI ID is linked to this number
            } catch (e: Exception) {
                Log.w(TAG, "UPI number mapper not reachable", e)
                resolving = false
                resolveError = ResolveError.UNREACHABLE
                return@launch
            }
            resolving = false
            if (mapped == null) {
                resolveError = ResolveError.NOT_LINKED
                return@launch
            }
            reset()
            upiId = mapped.upiId!!
            entryMethod = if (picked) EntryMethod.CONTACT else EntryMethod.TYPED
            payeeName = name ?: mapped.registeredName
            registeredName = mapped.registeredName
            payeeMobile = e164
            onReady()
        }
    }

    private fun clearMobilePayee() {
        payeeName = null
        registeredName = null
        payeeMobile = null
    }

    fun typeAmount(text: String) {
        val clean = text.filter { it.isDigit() || it == '.' }
        session.onAmountChange(amountText, clean, SystemClock.elapsedRealtime())
        amountText = clean
    }

    /** The app went to the background or came back (another app, the call, WhatsApp). */
    fun onAppBackground() = session.onBackground(SystemClock.elapsedRealtime())

    fun onAppForeground() = session.onForeground(SystemClock.elapsedRealtime())

    fun pickSavedPayee(id: String) {
        session.onPayeePicked()
        clearMobilePayee()
        upiId = id
        entryMethod = EntryMethod.CONTACT
        payerSays = null
    }

    fun checkServer() {
        viewModelScope.launch {
            serverStatus = try {
                val body = withContext(Dispatchers.IO) { api.health().string() }
                "OK: ${body.take(80)}"
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                "Not reachable: ${e.message}"
            }
        }
    }

    fun pay(onDecided: (Decision) -> Unit) {
        val amount = amountInr ?: return
        checking = true
        session.markPaid()
        val sessionSignal = session.toSignal(SystemClock.elapsedRealtime(), typingGaps)
        lastTypingGap = session.medianGapSeconds()

        viewModelScope.launch {
            val reading = try {
                withContext(Dispatchers.IO) {
                    Signals.read(consents, upiId, sessionSignal, payeeMobile, amount, entryMethod, pastPayments)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // A broken sensor must not block the payment; null context means "unknown".
                Log.w(TAG, "reading signals failed", e)
                null
            }
            val request = RequestBuilder.build(
                userId = DemoConfig.USER_ID,
                upiId = upiId,
                amountInr = amount,
                entryMethod = entryMethod,
                payerSays = payerSays.takeIf { askWhoIsPayee },
                context = reading?.context,
            )
            lastRequestJson = RukoJson.encodeToString(DecideRequest.serializer(), request)
            lastSimulated = reading?.simulated.orEmpty()
            Signals.pasted = null // a pasted message counts for one payment only

            val start = SystemClock.elapsedRealtime()
            val result = try {
                val response = api.decide(request)
                if (response.requestId != request.requestId) Log.w(TAG, "response for another request")
                if (response.failOpen) Decision.Unavailable else Decision.Result(response, SystemClock.elapsedRealtime() - start)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Includes HTTP 422 for a request the server does not accept: fail open.
                Log.w(TAG, "decide failed, failing open", e)
                Decision.Unavailable
            }
            decision = result
            val answer = (result as? Decision.Result)?.response
            checkedPayment = PaymentRecord(
                id = request.requestId,
                ts = System.currentTimeMillis(),
                payee = request.payeeUpiId,
                payeeName = payeeName,
                amountInr = amount,
                entryMethod = request.entryMethod.name.lowercase(),
                tier = answer?.tier,
                reasons = answer?.reasons.orEmpty(),
                evidenceId = answer?.evidenceId,
            )
            checking = false
            onDecided(result)
        }
    }

    /** Opening the Pay screen starts a new session; one left unpaid counts as abandoned. */
    fun reset() {
        session.start(SystemClock.elapsedRealtime())
        lastTypingGap = null
        upiId = ""
        amountText = ""
        entryMethod = EntryMethod.TYPED
        payerSays = null
        decision = null
        checkedPayment = null
        clearMobilePayee()
        resolveError = null
    }

    private companion object {
        const val TAG = "RukoPay"
        const val ASK_PAYEE_FROM_INR = 2_000.0
    }
}
