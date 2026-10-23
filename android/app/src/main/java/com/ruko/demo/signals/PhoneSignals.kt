package com.ruko.demo.signals

import com.ruko.demo.net.CallDirection
import com.ruko.demo.net.CallKind
import com.ruko.demo.net.CallSignal
import com.ruko.demo.net.DeviceSignal
import com.ruko.demo.net.DeviceVerdict
import com.ruko.demo.net.EpisodeSignal
import com.ruko.demo.net.SessionSignal
import com.ruko.demo.net.Context as RequestContext

/**
 * What the phone knows about calls. Null means "unknown" (no permission, or the phone does
 * not say), which Scam Shield must never read as "safe".
 */
data class CallReading(
    val active: Boolean?,
    val kind: CallKind?,
    val direction: CallDirection?,
    val callerInContacts: Boolean?,
    val startedMinAgo: Double?,
    val endedMinAgo: Double?, // a call that finished within the last 30 minutes
    val callerNumber: String? = null, // in memory only; see toCallSignal for when it is sent
    val speakerOn: Boolean? = null, // only read while a call is active
) {
    /**
     * The caller's number is sent only for an incoming SIM call from a number that is not in
     * the user's contacts, which is when the FRI and Suspect Registry lookups matter. Calls
     * from family or saved contacts never send a number.
     */
    fun toCallSignal() = CallSignal(
        active = active,
        kind = kind,
        direction = direction,
        callerNumber = callerNumber
            ?.takeIf { kind == CallKind.SIM && direction == CallDirection.INCOMING && callerInContacts == false }
            ?.let(::toE164),
        callerInContacts = callerInContacts,
        startedMinAgo = startedMinAgo,
        endedMinAgo = endedMinAgo,
        speakerOn = speakerOn,
    )
}

data class DeviceReading(
    val remoteAppInstalled: Boolean?,
    val remoteAppInstalledMinAgo: Double?,
    val untrustedAccessibility: Boolean?,
    val sideloadedLast24h: Boolean?,
    val screenRecorded: Boolean?, // Android 15 and later only
) {
    /**
     * The phone sees that a remote access app is installed, not that it is in use. It counts
     * as active only when the screen is also being captured; with no capture it is not active,
     * and before Android 15 (no capture callback) it is unknown.
     */
    fun toDeviceSignal() = DeviceSignal(
        remoteAccessActive = when {
            screenRecorded == true && remoteAppInstalled == true -> true
            screenRecorded == false -> false
            else -> null
        },
        remoteAppInstalledMinAgo = remoteAppInstalledMinAgo,
        untrustedAccessibility = untrustedAccessibility,
        sideloadedLast24h = sideloadedLast24h,
    )
}

/** Everything the sensor read. [call] is null when call signals are off, [device] when app checks are. */
data class PhoneSignals(val call: CallReading?, val device: DeviceReading?) {

    /**
     * The `context` object of contract c2-0.2. A switched-off group is left null ("unknown");
     * with every group off and no lure, `context` itself is null (the user opted out).
     */
    fun toRequestContext(
        lures: List<LureHit>,
        episode: EpisodeSignal? = null,
        session: SessionSignal? = null,
        payeeInContacts: Boolean? = null,
        deviceVerdict: DeviceVerdict? = null,
    ): RequestContext? {
        if (call == null && device == null && lures.isEmpty() && episode == null && session == null &&
            payeeInContacts == null && deviceVerdict == null
        ) {
            return null
        }
        return RequestContext(
            call = call?.toCallSignal(),
            device = device?.toDeviceSignal(),
            lures = lures.map { it.toLureSignal() },
            episode = episode,
            session = session,
            payeeInContacts = payeeInContacts,
            deviceVerdict = deviceVerdict,
        )
    }
}

/** Indian numbers to +91XXXXXXXXXX; anything unrecognisable is not sent. */
fun toE164(raw: String): String? {
    val digits = raw.filter(Char::isDigit)
    return when {
        raw.trim().startsWith("+") && digits.length in 8..15 -> "+$digits"
        digits.length == 10 -> "+91$digits"
        digits.length == 11 && digits.startsWith("0") -> "+91${digits.drop(1)}"
        digits.length == 12 && digits.startsWith("91") -> "+$digits"
        else -> null
    }
}

/**
 * Debug panel values that replace real readings. Anything set here is shown as "simulated"
 * on screen, so the demo never passes off a toggle as a real signal.
 */
data class SignalOverrides(
    val onCall: Boolean? = null,
    val callerInContacts: Boolean? = null,
    val callMinutes: Int? = null,
    val remoteApp: Boolean? = null,
    val accessibility: Boolean? = null,
    val sideload: Boolean? = null,
    val screenRecorded: Boolean? = null,
    val lureTag: String? = null,
    val speakerOn: Boolean? = null,
    val coachedSession: Boolean? = null, // true: a coached Pay screen, false: a calm one
    val payeeInContacts: Boolean? = null,
) {
    val active: List<String>
        get() = buildList {
            if (onCall != null) add("call.active")
            if (callerInContacts != null) add("call.caller_in_contacts")
            if (callMinutes != null) add("call.started_min_ago")
            if (remoteApp != null) add("device.remote_app")
            if (accessibility != null) add("device.untrusted_accessibility")
            if (sideload != null) add("device.sideloaded_last_24h")
            if (screenRecorded != null) add("screen_recorded")
            if (lureTag != null) add("lures")
            if (speakerOn != null) add("call.speaker_on")
            if (coachedSession != null) add("session")
            if (payeeInContacts != null) add("payee_in_contacts")
        }

    /** Overrides only fill in groups the user consented to; a switched-off group stays null. */
    fun applyTo(real: PhoneSignals): PhoneSignals {
        val call = real.call?.let { c ->
            val simulatedCall = onCall == true && c.active != true
            c.copy(
                active = onCall ?: c.active,
                kind = if (simulatedCall) CallKind.VOIP else c.kind,
                callerInContacts = callerInContacts ?: c.callerInContacts,
                startedMinAgo = callMinutes?.toDouble() ?: c.startedMinAgo ?: if (onCall == true) 0.0 else null,
                endedMinAgo = if (onCall == true) null else c.endedMinAgo,
                speakerOn = speakerOn ?: c.speakerOn,
            )
        }
        val device = real.device?.let { d ->
            val remote = remoteApp ?: d.remoteAppInstalled
            d.copy(
                remoteAppInstalled = remote,
                // A simulated install is "just now", like the demo's freshly installed AnyDesk.
                remoteAppInstalledMinAgo = when (remoteApp) {
                    true -> d.remoteAppInstalledMinAgo ?: 25.0
                    false -> null
                    null -> d.remoteAppInstalledMinAgo
                },
                untrustedAccessibility = accessibility ?: d.untrustedAccessibility,
                sideloadedLast24h = sideload ?: d.sideloadedLast24h,
                screenRecorded = screenRecorded ?: d.screenRecorded,
            )
        }
        return PhoneSignals(call, device)
    }

    /** The session the debug panel stands in for, so a demo does not depend on slow typing on stage. */
    fun applyTo(real: SessionSignal?): SessionSignal? = when (coachedSession) {
        null -> real
        true -> COACHED_SESSION.copy(abandonedFlows30m = real?.abandonedFlows30m ?: 1)
        false -> CALM_SESSION
    }

    companion object {
        val COACHED_SESSION = SessionSignal(
            amountDigits = 5, amountMedianGapS = 2.4, amountMaxGapS = 6.1, amountGapVsUsual = 4.8,
            amountEdits = 1, payeePasted = true, pastedWithinSOfReturn = 6.0, appSwitches = 3,
            awayS = 41.0, payScreenS = 212.0, abandonedFlows30m = 1,
        )
        val CALM_SESSION = SessionSignal(
            amountDigits = 4, amountMedianGapS = 0.4, amountMaxGapS = 0.9, amountGapVsUsual = 1.0,
            amountEdits = 0, payeePasted = false, pastedWithinSOfReturn = null, appSwitches = 0,
            awayS = 0.0, payScreenS = 25.0, abandonedFlows30m = 0,
        )
    }
}
