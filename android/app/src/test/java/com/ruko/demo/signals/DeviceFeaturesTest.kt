package com.ruko.demo.signals

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.double
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The phone's features and verdict must equal Python's (ml/dataset/device_features.py,
 * ml/model/device/verdict.py) on real simulated payments: device_feature_vectors.json.
 */
class DeviceFeaturesTest {
    private val artifacts: File = generateSequence(File("").absoluteFile) { it.parentFile }
        .map { File(it, "ml/model/artifacts/v0") }
        .first { File(it, "device_feature_vectors.json").exists() }
    private val model = DeviceModel.parse(File(artifacts, "device_model.json").readText())
    private val cases = Json.parseToJsonElement(File(artifacts, "device_feature_vectors.json").readText()).jsonObject
    private val tol = cases.getValue("tolerance").jsonPrimitive.double

    private fun JsonObject.d(k: String): Double? = (get(k) as? JsonElement)?.takeIf { it !is JsonNull }?.jsonPrimitive?.doubleOrNull
    private fun JsonObject.bool(k: String): Boolean? = (get(k) as? JsonElement)?.takeIf { it !is JsonNull }?.jsonPrimitive?.booleanOrNull
    private fun JsonObject.s(k: String): String? = (get(k) as? JsonElement)?.takeIf { it !is JsonNull }?.jsonPrimitive?.content
    private fun JsonElement.objOrNull(): JsonObject? = if (this is JsonNull) null else jsonObject

    private fun facts(i: JsonObject): PayFacts {
        val call = i.getValue("call").objOrNull()?.let {
            CallFacts(it.bool("active"), it.s("kind"), it.s("direction"), it.bool("in_contacts"),
                it.d("started_min_ago"), it.d("ended_min_ago"), it.bool("foreign"))
        }
        val device = i.getValue("device").objOrNull()?.let {
            DeviceFacts(it.bool("remote_installed"), it.d("remote_min_ago"), it.bool("screen_recorded"),
                it.bool("accessibility"), it.bool("sideloaded_24h"))
        }
        val hits = (i.getValue("lure_hits") as? JsonElement)?.takeIf { it !is JsonNull }?.jsonArray?.map {
            val h = it.jsonObject
            LureFact(h.s("tag")!!, h.d("conf")!!, h.d("min_ago")!!)
        }
        val episode = i.getValue("episode").objOrNull()?.let {
            EpisodeFacts(it.s("state")!!, it.d("age_days"), it.d("bait_credits")?.toInt(), it.d("bait_inr"),
                it.d("outs")?.toInt(), it.d("new_payees")?.toInt(), it.d("escalation_steps")?.toInt(),
                it.d("last_out_inr"), it.d("out_to_in_ratio"), it.bool("payee_seen_in_episode"))
        }
        val session = i.getValue("session").objOrNull()?.mapValues { (_, v) ->
            if (v is JsonNull) null else v.jsonPrimitive.booleanOrNull ?: v.jsonPrimitive.double
        }
        val history = i.getValue("history").jsonArray.map {
            val h = it.jsonObject
            PastPayment(h.d("ts")!!, h.s("payee")!!, h.d("amount")!!)
        }
        return PayFacts(call, device, hits, episode, session, i.bool("payee_in_contacts"), history, i.d("amount")!!,
            i.s("entry")!!, i.s("payee")!!, i.d("now")!!, i.bool("any_shield")!!)
    }

    private fun checkVerdict(name: String, want: JsonElement, got: Verdict?) {
        if (want is JsonNull) {
            assertTrue("$name: no verdict expected", got == null)
            return
        }
        val w = want.jsonObject
        assertTrue("$name: verdict expected", got != null)
        assertEquals("$name: model", w.s("model_version"), got!!.modelVersion)
        assertEquals("$name: class", w.s("fraud_class"), got.fraudClass)
        assertEquals("$name: level", w.s("level"), got.level)
        assertEquals("$name: c", w.d("c"), got.c)
        assertEquals("$name: l", w.d("l"), got.l)
        assertEquals("$name: b", w.d("b"), got.b)
        assertEquals("$name: reasons", w.getValue("reasons").jsonArray.map { it.jsonPrimitive.content }, got.reasons)
    }

    @Test
    fun featuresAndVerdictMatchPythonOnRealPayments() {
        val all = cases.getValue("vectors").jsonArray
        assertTrue("no cases", all.isNotEmpty())
        for (v in all) {
            val c = v.jsonObject
            val name = c.s("case")!!
            val inputs = c.getValue("inputs").jsonObject
            val got = DeviceFeatures.compute(facts(inputs))
            val want = c.getValue("features").jsonObject
            assertEquals("$name: feature names", want.keys, got.keys)
            for ((k, x) in want) {
                val g = got[k]
                if (x is JsonNull) assertEquals("$name: $k", null, g)
                else assertEquals("$name: $k", x.jsonPrimitive.double, g ?: Double.NaN, tol)
            }
            val verdict = if (inputs.bool("any_shield") == true) DeviceVerdicts.of(model, got) else null
            checkVerdict(name, c.getValue("verdict"), verdict)
        }
    }

    @Test
    fun everyFraudClassIsNamedLikePython() {
        for (v in cases.getValue("verdict_cases").jsonArray) {
            val c = v.jsonObject
            val features = c.getValue("features").jsonObject.mapValues { (_, x) -> if (x is JsonNull) null else x.jsonPrimitive.double }
            checkVerdict(c.s("case")!!, c.getValue("verdict"), DeviceVerdicts.of(model, features))
        }
    }

    @Test
    fun scamShieldOffMeansNoFeatures() {
        val off = PayFacts(null, null, null, null, null, null, emptyList(), 500.0, "qr", "shop@okaxis", 0.0, anyShield = false)
        assertTrue(DeviceFeatures.compute(off).values.all { it == null })
        assertEquals(DeviceFeatures.ALL.toSet(), DeviceFeatures.compute(off).keys)
    }
}
