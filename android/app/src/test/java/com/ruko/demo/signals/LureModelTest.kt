package com.ruko.demo.signals

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The phone's lure model must score like Python (ml/model/lure_train.py) on every case in
 * ml/model/artifacts/lure_vectors.json, and the shipped assets must be the trained models.
 */
class LureModelTest {
    private val repo: File = generateSequence(File("").absoluteFile) { it.parentFile }
        .first { File(it, "ml/model/artifacts/lure_vectors.json").exists() }
    private val artifacts = File(repo, "ml/model/artifacts")
    private val assets = File(repo, "android/app/src/main/assets")
    private val classifier = LinearLureClassifier(
        Json { ignoreUnknownKeys = true }.decodeFromString(LureModel.serializer(), File(artifacts, "lure.json").readText()),
    )

    @Test
    fun everyVectorGetsPythonsScamTagAndConfidence() {
        val vectors = Json.parseToJsonElement(File(artifacts, "lure_vectors.json").readText()).jsonObject.getValue("vectors").jsonArray
        assertTrue(vectors.isNotEmpty())
        for (v in vectors) {
            val o = v.jsonObject
            val text = o.getValue("text").jsonPrimitive.content
            val hit = classifier.classify(text)
            assertEquals(text, o.getValue("scam_tag").jsonPrimitive.content, hit.tag)
            assertEquals(text, o.getValue("scam_conf").jsonPrimitive.double, hit.conf, 2e-6)
        }
    }

    @Test
    fun scamHitAddsUpTheScamTypes() {
        val hit = LinearLureClassifier.scamHit(listOf("arrest_threat", "kyc", "normal", "refund"), listOf(0.05, 0.35, 0.3, 0.3))
        assertEquals("kyc", hit.tag)
        assertEquals(0.7, hit.conf, 1e-12)
        assertEquals("normal", LinearLureClassifier.scamHit(listOf("kyc", "normal"), listOf(0.2, 0.8)).tag)
    }

    @Test
    fun shippedAssetsAreTheTrainedModels() {
        assertEquals(File(artifacts, "lure.json").readText(), File(assets, "lure.json").readText())
        assertEquals(File(artifacts, "v0/device_model.json").readText(), File(assets, "device_model.json").readText())
    }
}
