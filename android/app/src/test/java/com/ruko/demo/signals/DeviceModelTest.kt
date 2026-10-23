package com.ruko.demo.signals

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The phone must score exactly like Python (ml/model/device/score.py). The model and the cases are
 * read straight from ml/model/artifacts/v0, like ContractTest reads contracts/.
 */
class DeviceModelTest {
    private val artifacts: File = generateSequence(File("").absoluteFile) { it.parentFile }
        .map { File(it, "ml/model/artifacts/v0") }
        .first { File(it, "device_model.json").exists() }
    private val model = DeviceModel.parse(File(artifacts, "device_model.json").readText())
    private val cases = Json.parseToJsonElement(File(artifacts, "device_vectors.json").readText()).jsonObject

    @Test
    fun versionMatchesTheVectors() {
        assertEquals(cases.getValue("version").jsonPrimitive.content, model.version)
    }

    @Test
    fun everyCaseScoresLikePython() {
        val tol = cases.getValue("tolerance").jsonPrimitive.double
        val all = cases.getValue("vectors").jsonArray
        assertTrue("no cases", all.isNotEmpty())
        for (v in all) {
            val c = v.jsonObject
            val name = c.getValue("case").jsonPrimitive.content
            val features = c.getValue("features").jsonObject.mapValues { (_, x) -> if (x is JsonNull) null else x.jsonPrimitive.double }
            val want = c.getValue("expected").jsonObject
            val got = model.score(features)
            assertEquals("$name: p", want.getValue("p").jsonPrimitive.double, got.p, tol)
            for ((key, map) in listOf("raw" to got.raw, "calibrated" to got.calibrated, "family_scores" to got.familyScores)) {
                for ((family, x) in want.getValue(key).jsonObject) {
                    assertEquals("$name: $key $family", x.jsonPrimitive.double, map.getValue(family), tol)
                }
            }
            assertEquals("$name: lit", want.getValue("lit").jsonArray.map { it.jsonPrimitive.content }, got.lit)
            assertEquals("$name: reasons", want.getValue("reasons").jsonArray.map { it.jsonPrimitive.content }, got.reasons)
            assertEquals("$name: warn", want.getValue("warn").jsonPrimitive.boolean, got.warn)
        }
    }

    @Test
    fun unknownIsNotTheSameAsZero() {
        // Missing values take LightGBM's learned default side; they are never silently read as 0
        // where the model learned a separate path for "unknown".
        val nothing = model.score(emptyMap())
        val zeros = model.score(cases.getValue("vectors").jsonArray.first().jsonObject.getValue("features").jsonObject
            .mapValues { 0.0 })
        assertTrue(nothing.raw != zeros.raw)
    }
}
