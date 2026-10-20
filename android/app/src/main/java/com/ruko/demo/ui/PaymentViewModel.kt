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
import com.ruko.demo.net.ApiClient
import com.ruko.demo.net.DecideRequest
import com.ruko.demo.net.DecideResponse
import com.ruko.demo.net.EntryMethod
import com.ruko.demo.net.PayerSays
import com.ruko.demo.net.RequestBuilder
import com.ruko.demo.net.RukoApi
import com.ruko.demo.net.RukoJson
import com.ruko.demo.signals.Signals
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

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

    // Optional answer to "Who are you paying?" for a new payee.
    var payerSays by mutableStateOf<PayerSays?>(null)

    // The user's current Scam Shield switches, kept in sync by the app from the account.
    var consents by mutableStateOf(Consents())

    // Debug overrides that shaped the last request, shown as SIMULATED.
    var lastSimulated by mutableStateOf<List<String>>(emptyList())
        private set

    var checking by mutableStateOf(false)
        private set
    var decision by mutableStateOf<Decision?>(null)
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
        upiId = text
        entryMethod = EntryMethod.TYPED
    }

    fun pickSavedPayee(id: String) {
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

        viewModelScope.launch {
            val reading = try {
                withContext(Dispatchers.IO) { Signals.read(consents) }
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
            checking = false
            onDecided(result)
        }
    }

    fun reset() {
        upiId = ""
        amountText = ""
        entryMethod = EntryMethod.TYPED
        payerSays = null
        decision = null
    }

    private companion object {
        const val TAG = "RukoPay"
        const val ASK_PAYEE_FROM_INR = 2_000.0
    }
}
