package com.ruko.demo.signals

import com.ruko.demo.net.CallDirection
import com.ruko.demo.net.CallKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SignalsTest {

    @Test
    fun charWbMatchesScikitLearn() {
        // sklearn: TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5)).build_analyzer()("hi")
        assertEquals(listOf(" hi", "hi ", " hi "), charWbNgrams("hi", 3, 5))
        val grams = charWbNgrams("arrest", 3, 5)
        assertEquals(" ar", grams.first())
        assertTrue(" arre" in grams && "rest " in grams)
        assertEquals(6 + 5 + 4, grams.size)
    }

    @Test
    fun linearModelPicksTheHeavierClass() {
        val model = LureModel(
            version = "test",
            ngramRange = listOf(3, 5),
            classes = listOf("arrest_threat", "normal"),
            vocabulary = mapOf(" ar" to 0, "arr" to 1, " hi" to 2),
            idf = listOf(1.0, 1.0, 1.0),
            coef = listOf(listOf(4.0, 4.0, -2.0), listOf(-4.0, -4.0, 2.0)),
            intercept = listOf(0.0, 0.0),
        )
        val hit = LinearLureClassifier(model).classify("ARREST warrant")
        assertEquals("arrest_threat", hit.tag)
        assertTrue(hit.conf > 0.9)
        assertEquals("normal", LinearLureClassifier(model).classify("hi").tag)
    }

    @Test
    fun keywordFallbackNeedsMoreThanOneWord() {
        val scam = KeywordLureClassifier.classify("This is CBI. A warrant for your arrest is issued.")
        assertEquals("arrest_threat", scam.tag)
        assertTrue(scam.conf >= LURE_SEND_THRESHOLD)
        assertTrue(KeywordLureClassifier.classify("Your refund is processed").conf < LURE_SEND_THRESHOLD)
        assertEquals("normal", KeywordLureClassifier.classify("Dinner at 8?").tag)
    }

    @Test
    fun onlyStrongScamTagsAreSent() {
        val tags = bestLureTags(
            listOf(LureHit("kyc", 0.7), LureHit("kyc", 0.9), LureHit("refund", 0.5), LureHit("normal", 0.99)),
        )
        assertEquals(listOf("kyc"), tags.map { it.tag })
        assertEquals(0.9, tags.single().conf, 0.0)
    }

    @Test
    fun everythingSwitchedOffSendsNullContext() {
        assertNull(PhoneSignals(call = null, device = null).toRequestContext(emptyList()))
    }

    @Test
    fun switchedOffGroupIsUnknownNotSafe() {
        val context = PhoneSignals(call = null, device = device(screenRecorded = null)).toRequestContext(emptyList())!!
        assertNull(context.call)
        assertNull(context.device!!.remoteAccessActive) // before Android 15 there is no capture callback
    }

    @Test
    fun voipCallerIsUnknownAndNoNumberIsSent() {
        val voip = CallReading(true, CallKind.VOIP, null, null, 38.4, null, callerNumber = null)
        val call = PhoneSignals(voip, null).toRequestContext(emptyList())!!.call!!
        assertEquals(true, call.active)
        assertNull(call.callerInContacts)
        assertNull(call.callerNumber)
        assertEquals(38.4, call.startedMinAgo!!, 0.0)
    }

    @Test
    fun callerNumberOnlyForIncomingSimCallsFromUnknownNumbers() {
        val unknown = CallReading(true, CallKind.SIM, CallDirection.INCOMING, false, 3.0, null, callerNumber = "9812345670")
        assertEquals("+919812345670", unknown.toCallSignal().callerNumber)
        assertNull(unknown.copy(callerInContacts = true).toCallSignal().callerNumber) // family
        assertNull(unknown.copy(callerInContacts = null).toCallSignal().callerNumber) // could not check
        assertNull(unknown.copy(direction = CallDirection.OUTGOING).toCallSignal().callerNumber)
        val ended = unknown.copy(active = false, startedMinAgo = null, endedMinAgo = 4.0)
        assertEquals("+919812345670", ended.toCallSignal().callerNumber)
    }

    @Test
    fun indianNumbersBecomeE164() {
        assertEquals("+919812345670", toE164("98123 45670"))
        assertEquals("+919812345670", toE164("09812345670"))
        assertEquals("+919812345670", toE164("+91 98123-45670"))
        assertNull(toE164("12345"))
    }

    @Test
    fun remoteAccessIsActiveOnlyWhileTheScreenIsCaptured() {
        assertEquals(true, device(remote = true, screenRecorded = true).toDeviceSignal().remoteAccessActive)
        assertEquals(false, device(remote = true, screenRecorded = false).toDeviceSignal().remoteAccessActive)
        assertNull(device(remote = false, screenRecorded = true).toDeviceSignal().remoteAccessActive) // e.g. scrcpy
        assertEquals(25.0, device(remote = true, screenRecorded = null).toDeviceSignal().remoteAppInstalledMinAgo!!, 0.0)
    }

    @Test
    fun luresGoInsideContextWithTheirAge() {
        val context = PhoneSignals(null, null).toRequestContext(listOf(LureHit("kyc", 0.876, 52.04)))!!
        assertEquals("kyc", context.lures.single().tag)
        assertEquals(0.88, context.lures.single().conf, 0.0)
        assertEquals(52.0, context.lures.single().minAgo, 0.0)
    }

    @Test
    fun overridesFillOnlyConsentedGroupsAndAreListed() {
        val o = SignalOverrides(onCall = true, callMinutes = 38, remoteApp = true)
        val real = PhoneSignals(
            call = CallReading(false, null, null, null, null, null),
            device = null, // app checks switched off
        )
        val applied = o.applyTo(real)
        assertEquals(true, applied.call!!.active)
        assertEquals(CallKind.VOIP, applied.call!!.kind)
        assertEquals(38.0, applied.call!!.startedMinAgo!!, 0.0)
        assertNull(applied.device)
        assertEquals(listOf("call.active", "call.started_min_ago", "device.remote_app"), o.active)
    }

    private fun device(remote: Boolean = true, screenRecorded: Boolean?) = DeviceReading(
        remoteAppInstalled = remote,
        remoteAppInstalledMinAgo = if (remote) 25.0 else null,
        untrustedAccessibility = false,
        sideloadedLast24h = false,
        screenRecorded = screenRecorded,
    )
}
