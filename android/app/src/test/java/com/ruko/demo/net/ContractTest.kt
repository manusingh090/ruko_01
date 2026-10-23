package com.ruko.demo.net

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Checks Models.kt against contracts/ in the repo (published from backend/schema/events.py),
 * so a contract change on the server breaks this test instead of the demo.
 */
class ContractTest {

    private val contracts: File = generateSequence(File("").absoluteFile) { it.parentFile }
        .map { File(it, "contracts") }
        .first { File(it, "c2_decide.schema.json").exists() }

    private val schema = Json.parseToJsonElement(File(contracts, "c2_decide.schema.json").readText()).jsonObject
    private val request = schema.getValue("request").jsonObject
    private val defs = request.getValue("\$defs").jsonObject

    // Strict: fails on any field the app does not model.
    private val strict = Json { ignoreUnknownKeys = false }

    private fun example(name: String) = File(contracts, "examples/$name").readText()

    @Test
    fun contractVersionMatches() {
        assertEquals(schema.getValue("contract_version").jsonPrimitive.content, CONTRACT_VERSION)
    }

    @Test
    fun everyExampleDecodesWithNoUnknownFields() {
        File(contracts, "examples").listFiles()!!.sortedBy { it.name }.forEach { file ->
            when {
                file.name.startsWith("decide_request") -> strict.decodeFromString(DecideRequest.serializer(), file.readText())
                file.name.startsWith("decide_response") -> strict.decodeFromString(DecideResponse.serializer(), file.readText())
                file.name.startsWith("episode_event_response") -> strict.decodeFromString(EpisodeEventResponse.serializer(), file.readText())
                file.name.startsWith("episode_event") -> strict.decodeFromString(EpisodeEvent.serializer(), file.readText())
                file.name.startsWith("payment_outcome_response") -> strict.decodeFromString(PaymentOutcomeResponse.serializer(), file.readText())
                file.name.startsWith("payment_outcome") -> strict.decodeFromString(PaymentOutcome.serializer(), file.readText())
            }
        }
    }

    @Test
    fun paymentOutcomeMatchesItsSchema() {
        val outcome = Json.parseToJsonElement(File(contracts, "c2_payment_outcome.schema.json").readText()).jsonObject
        assertEquals(outcome.getValue("contract_version").jsonPrimitive.content, CONTRACT_VERSION)
        val req = outcome.getValue("request").jsonObject
        val built = PaymentOutcome("0b6e4d2f-8c1a-4f3e-b7d9-5a2c1e8f0d47", "u_demo_meena", "shreeganesh.kirana@okaxis",
            240.0, PaymentStatus.COMPLETED, RequestBuilder.nowIst())
        assertFieldsAllowed(RukoJson.encodeToJsonElement(PaymentOutcome.serializer(), built).jsonObject, req)
        assertSubset(PaymentStatus.entries.map(::wire), enumValues(req.getValue("properties").jsonObject.getValue("status")))
        val example = Json.parseToJsonElement(example("payment_outcome_completed.json"))
        val decoded = RukoJson.decodeFromString(PaymentOutcome.serializer(), example.toString())
        assertEquals(normalise(example), normalise(RukoJson.encodeToJsonElement(PaymentOutcome.serializer(), decoded)))
    }

    @Test
    fun meenaRequestRoundTripsExactly() {
        val original = Json.parseToJsonElement(example("decide_request_meena.json"))
        val decoded = RukoJson.decodeFromString(DecideRequest.serializer(), original.toString())
        val encoded = RukoJson.parseToJsonElement(RukoJson.encodeToString(DecideRequest.serializer(), decoded))
        assertEquals(normalise(original), normalise(encoded))
    }

    @Test
    fun taskEpisodeRequestRoundTripsExactly() {
        val original = Json.parseToJsonElement(example("decide_request_task_episode.json"))
        val decoded = RukoJson.decodeFromString(DecideRequest.serializer(), original.toString())
        val encoded = RukoJson.parseToJsonElement(RukoJson.encodeToString(DecideRequest.serializer(), decoded))
        assertEquals(normalise(original), normalise(encoded))
        assertFieldsAllowed(
            encoded.jsonObject.getValue("context").jsonObject.getValue("episode").jsonObject,
            defs.getValue("EpisodeSignal").jsonObject,
        )
        val states = enumValues(defs.getValue("EpisodeSignal").jsonObject.getValue("properties").jsonObject.getValue("state"))
        assertSubset(com.ruko.demo.shield.EpisodeState.entries.map { it.wire }, states)
    }

    @Test
    fun deviceVerdictRequestRoundTripsExactly() {
        val original = Json.parseToJsonElement(example("decide_request_device_verdict.json"))
        val decoded = RukoJson.decodeFromString(DecideRequest.serializer(), original.toString())
        val encoded = RukoJson.parseToJsonElement(RukoJson.encodeToString(DecideRequest.serializer(), decoded))
        assertEquals(normalise(original), normalise(encoded))
        val verdictDef = defs.getValue("DeviceVerdict").jsonObject
        assertFieldsAllowed(encoded.jsonObject.getValue("context").jsonObject.getValue("device_verdict").jsonObject, verdictDef)
        assertEquals(setOf("low", "medium", "high"), enumValues(verdictDef.getValue("properties").jsonObject.getValue("level")))
        val classes = enumValues(verdictDef.getValue("properties").jsonObject.getValue("fraud_class"))
        assertTrue(decoded.context!!.deviceVerdict!!.fraudClass in classes)
    }

    @Test
    fun threatAnalysisMatchesTheResponseSchema() {
        val response = schema.getValue("response").jsonObject
        val threatDef = response.getValue("\$defs").jsonObject.getValue("Threat").jsonObject
        val decoded = RukoJson.decodeFromString(DecideResponse.serializer(), example("decide_response_t3.json"))
        val threat = decoded.threat!!
        assertEquals(91, threat.score)
        assertFieldsAllowed(RukoJson.encodeToJsonElement(Threat.serializer(), threat).jsonObject, threatDef)
        assertTrue(threat.level in enumValues(threatDef.getValue("properties").jsonObject.getValue("level")))
        assertEquals(null, RukoJson.decodeFromString(DecideResponse.serializer(), example("decide_response_fail_open.json")).threat)
    }

    @Test
    fun builtRequestUsesOnlySchemaFieldsAndHasTheRequiredOnes() {
        val built = RequestBuilder.build(
            userId = "u_demo_meena",
            upiId = " Secure.Settlement.Desk@okaxis ",
            amountInr = 95000.0,
            entryMethod = EntryMethod.TYPED,
            payerSays = PayerSays.GOVERNMENT,
            context = Context(
                call = CallSignal(true, CallKind.SIM, CallDirection.INCOMING, "+919812345670", false, 38.0, null),
                device = DeviceSignal(null, 25.0, false, null),
                lures = listOf(LureSignal("arrest_threat", 0.85, 52.0)),
            ),
        )
        val json = RukoJson.parseToJsonElement(RukoJson.encodeToString(DecideRequest.serializer(), built)).jsonObject
        assertFieldsAllowed(json, request)
        val context = json.getValue("context").jsonObject
        assertFieldsAllowed(context, defs.getValue("Context").jsonObject)
        assertFieldsAllowed(context.getValue("call").jsonObject, defs.getValue("CallSignal").jsonObject)
        assertFieldsAllowed(context.getValue("device").jsonObject, defs.getValue("DeviceSignal").jsonObject)
        assertFieldsAllowed(context.getValue("lures").jsonArray.first().jsonObject, defs.getValue("LureSignal").jsonObject)
        assertEquals("secure.settlement.desk@okaxis", json.getValue("payee_upi_id").jsonPrimitive.content)
    }

    @Test
    fun enumValuesAreOnesTheServerAccepts() {
        val props = request.getValue("properties").jsonObject
        val callProps = defs.getValue("CallSignal").jsonObject.getValue("properties").jsonObject
        assertSubset(EntryMethod.entries.map(::wire), enumValues(props.getValue("entry_method")))
        assertSubset(PayerSays.entries.map(::wire), enumValues(props.getValue("payer_says")))
        assertSubset(CallKind.entries.map(::wire), enumValues(callProps.getValue("kind")))
        assertSubset(CallDirection.entries.map(::wire), enumValues(callProps.getValue("direction")))
    }

    @Test
    fun optedOutRequestMatchesTheMinimalExample() {
        val built = RequestBuilder.build(
            userId = "u_demo_meena",
            upiId = "shreeganesh.kirana@okaxis",
            amountInr = 240.0,
            entryMethod = EntryMethod.QR,
            context = null,
            ts = "2026-11-07T09:15:00+05:30",
            requestId = "0b6e4d2f-8c1a-4f3e-b7d9-5a2c1e8f0d47",
        )
        val encoded = RukoJson.parseToJsonElement(RukoJson.encodeToString(DecideRequest.serializer(), built))
        assertEquals(normalise(Json.parseToJsonElement(example("decide_request_minimal.json"))), normalise(encoded))
    }

    @Test
    fun responseKeepsWorkingWhenTheServerAddsAField() {
        val response = RukoJson.decodeFromString(
            DecideResponse.serializer(),
            """{"request_id":"r1","tier":"T3","reasons":["R1_live_unknown_call"],"message_key":"t3_digital_arrest","new_field":1}""",
        )
        assertEquals("T3", response.tier)
        assertEquals(0, response.pauseMinutes)
    }

    @Test
    fun timestampIsIstIso8601() {
        assertTrue(Regex("""\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+05:30""").matches(RequestBuilder.nowIst()))
    }

    // ---- helpers ----

    private fun assertFieldsAllowed(json: JsonObject, def: JsonObject) {
        val allowed = def.getValue("properties").jsonObject.keys
        assertTrue("not in schema: ${json.keys - allowed}", allowed.containsAll(json.keys))
        def["required"]?.jsonArray?.map { it.jsonPrimitive.content }?.forEach {
            assertTrue("missing required $it", it in json.keys)
        }
    }

    private fun assertSubset(ours: List<String>, theirs: Set<String>) =
        assertTrue("$ours not all in $theirs", theirs.containsAll(ours))

    /** Every "enum" list anywhere under a schema property (handles anyOf with null). */
    private fun enumValues(element: JsonElement): Set<String> = when (element) {
        is JsonObject -> element.entries.flatMap { (key, value) ->
            if (key == "enum") value.jsonArray.map { it.jsonPrimitive.content } else enumValues(value)
        }.toSet()
        is JsonArray -> element.flatMap(::enumValues).toSet()
        else -> emptySet()
    }

    private inline fun <reified T : Enum<T>> wire(value: T): String =
        RukoJson.encodeToString(kotlinx.serialization.serializer<T>(), value).trim('"')

    /** Drop null fields and compare numbers by value, so 38 and 38.0 are equal. */
    private fun normalise(element: JsonElement): JsonElement = when (element) {
        is JsonObject -> JsonObject(element.filterValues { it !is kotlinx.serialization.json.JsonNull }.mapValues { normalise(it.value) })
        is JsonArray -> JsonArray(element.map(::normalise))
        is JsonPrimitive -> if (!element.isString && element.content.toDoubleOrNull() != null) {
            JsonPrimitive(element.content.toDouble())
        } else {
            element
        }
    }
}
