package com.ruko.demo.signals

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.ln

/**
 * The on-device model: ml/model/artifacts/v0/device_model.json (copy it to assets/device_model.json),
 * scored on the phone. A line-by-line port of ml/model/device/score.py; DeviceModelTest checks that
 * it gives Python's answers for every case in device_vectors.json.
 *
 * Input: the features defined in ml/dataset/device_features.py, name -> value, or null when the phone
 * could not measure it (never 0 for "unknown"). Everything stays on the phone. The local warning only
 * ever warns, it never blocks a payment (CLAUDE.md rule 10).
 */
class DeviceModel private constructor(
    val version: String,
    private val order: List<String>,
    private val families: Map<String, Family>,
    private val prior: Double,
    private val familyK: Double,
    private val litAt: Double,
    private val coef: DoubleArray,
    private val intercept: Double,
    private val reasonOf: Map<String, String>,
    private val maxReasons: Int,
    private val warnMinFamilies: Int,
    private val warnMinP: Double,
) {
    data class Result(
        val raw: Map<String, Double>,           // per family: sum of the leaves reached
        val calibrated: Map<String, Double>,    // per family: P(scam | this family's evidence)
        val familyScores: Map<String, Double>,  // per family: lit when >= 0.5
        val p: Double,                          // the device's overall estimate; never shown to the user
        val lit: List<String>,                  // lit families, strongest first
        val reasons: List<String>,              // reason codes, at most maxReasons
        val warn: Boolean,                      // show the gentle local warning
    )

    private class Tree(
        val splitFeature: IntArray,
        val threshold: DoubleArray,
        val defaultLeft: BooleanArray,
        val missing: IntArray,          // 0 none, 1 zero, 2 NaN (LightGBM missing types)
        val left: IntArray,             // >= 0: split node; < 0: leaf number inv()
        val right: IntArray,
        val nodeValue: DoubleArray,
        val leafValue: DoubleArray,
    )

    private class Family(val features: List<String>, val trees: List<Tree>, val isoX: DoubleArray, val isoY: DoubleArray)

    /** The families in scoring order (C, L, B) and the features each one reads. */
    val familyOrder: List<String> get() = order

    fun familyFeatures(family: String): List<String> = families.getValue(family).features

    fun score(features: Map<String, Double?>): Result {
        val priorOdds = prior / (1 - prior)
        val raw = LinkedHashMap<String, Double>()
        val cal = LinkedHashMap<String, Double>()
        val strength = LinkedHashMap<String, Double>()
        val credits = HashMap<String, DoubleArray>()
        for (f in order) {
            val fam = families.getValue(f)
            val row = fam.features.map { features[it] }
            var total = 0.0
            val perFeature = DoubleArray(fam.features.size)
            for (tree in fam.trees) total += walk(tree, row, perFeature)
            raw[f] = total
            val c = interp(sigmoid(total), fam.isoX, fam.isoY)
            cal[f] = c
            val p = c.coerceIn(CLIP, 1 - CLIP)
            val lr = (p / (1 - p)) / priorOdds
            strength[f] = lr / (lr + familyK)
            credits[f] = perFeature
        }
        var sum = 0.0
        order.forEachIndexed { i, f -> sum += coef[i] * logit(cal.getValue(f)) }
        val pDevice = sigmoid(intercept + sum)
        val lit = order.sortedByDescending { strength.getValue(it) }.filter { strength.getValue(it) >= litAt }
        val warn = lit.size >= warnMinFamilies && pDevice >= warnMinP
        return Result(raw, cal, strength, pDevice, lit, reasons(lit, credits), warn)
    }

    /** Adds the leaf reached to the score, and credits each split's feature with the change in node value. */
    private fun walk(t: Tree, row: List<Double?>, credits: DoubleArray): Double {
        if (t.splitFeature.isEmpty()) return t.leafValue[0]
        var node = 0
        while (true) {
            val f = t.splitFeature[node]
            val child = if (goLeft(t, node, row[f])) t.left[node] else t.right[node]
            val childValue = if (child >= 0) t.nodeValue[child] else t.leafValue[child.inv()]
            credits[f] += childValue - t.nodeValue[node]
            if (child < 0) return childValue
            node = child
        }
    }

    /** LightGBM's numerical split, including where missing values go. */
    private fun goLeft(t: Tree, node: Int, value: Double?): Boolean {
        val missing = t.missing[node]
        var v = value ?: Double.NaN
        if (v.isNaN() && missing != MISSING_NAN) v = 0.0
        if ((missing == MISSING_ZERO && abs(v) <= ZERO) || (missing == MISSING_NAN && v.isNaN())) return t.defaultLeft[node]
        return v <= t.threshold[node]
    }

    private fun reasons(lit: List<String>, credits: Map<String, DoubleArray>): List<String> {
        val byCode = compareBy<Map.Entry<String, Double>>({ -it.value }, { it.key })
        val ranked = lit.map { f ->
            val perCode = LinkedHashMap<String, Double>()
            families.getValue(f).features.forEachIndexed { i, name ->
                val code = reasonOf[name]
                val c = credits.getValue(f)[i]
                if (code != null && c > 0) perCode[code] = (perCode[code] ?: 0.0) + c
            }
            perCode.entries.sortedWith(byCode)
        }
        val out = ArrayList<String>()
        for (codes in ranked) {
            if (codes.isNotEmpty() && codes[0].key !in out && out.size < maxReasons) out.add(codes[0].key)
        }
        for (e in ranked.flatMap { it.drop(1) }.sortedWith(byCode)) {
            if (e.key !in out && out.size < maxReasons) out.add(e.key)
        }
        return out
    }

    companion object {
        private const val ZERO = 1e-35            // LightGBM kZeroThreshold
        private const val CLIP = 1e-4
        private const val MISSING_ZERO = 1
        private const val MISSING_NAN = 2

        private fun sigmoid(z: Double) = 1.0 / (1.0 + exp(-z))

        private fun logit(p: Double): Double {
            val q = p.coerceIn(CLIP, 1 - CLIP)
            return ln(q / (1 - q))
        }

        private fun interp(x: Double, xs: DoubleArray, ys: DoubleArray): Double {
            if (x <= xs[0]) return ys[0]
            if (x >= xs[xs.size - 1]) return ys[ys.size - 1]
            var lo = 0
            var hi = xs.size - 1
            while (hi - lo > 1) {  // xs[lo] <= x < xs[hi]
                val mid = (lo + hi) / 2
                if (xs[mid] <= x) lo = mid else hi = mid
            }
            return ys[lo] + (x - xs[lo]) * (ys[hi] - ys[lo]) / (xs[hi] - xs[lo])
        }

        fun parse(text: String): DeviceModel {
            val o = Json.parseToJsonElement(text).jsonObject
            fun JsonObject.d(key: String) = getValue(key).jsonPrimitive.double
            fun JsonObject.doubles(key: String) = getValue(key).jsonArray.map { it.jsonPrimitive.double }.toDoubleArray()
            fun JsonObject.ints(key: String) = getValue(key).jsonArray.map { it.jsonPrimitive.int }.toIntArray()
            val order = o.getValue("family_order").jsonArray.map { it.jsonPrimitive.content }
            val families = o.getValue("families").jsonObject.mapValues { (_, fam) ->
                val f = fam.jsonObject
                Family(
                    features = f.getValue("features").jsonArray.map { it.jsonPrimitive.content },
                    trees = f.getValue("trees").jsonArray.map { t ->
                        val tr = t.jsonObject
                        Tree(
                            splitFeature = tr.ints("split_feature"), threshold = tr.doubles("threshold"),
                            defaultLeft = tr.getValue("default_left").jsonArray.map { it.jsonPrimitive.boolean }.toBooleanArray(),
                            missing = tr.ints("missing"), left = tr.ints("left"), right = tr.ints("right"),
                            nodeValue = tr.doubles("node_value"), leafValue = tr.doubles("leaf_value"),
                        )
                    },
                    isoX = f.doubles("iso_x"), isoY = f.doubles("iso_y"),
                )
            }
            val comb = o.getValue("combiner").jsonObject
            val warn = o.getValue("local_warning").jsonObject
            return DeviceModel(
                version = o.getValue("version").jsonPrimitive.content, order = order, families = families,
                prior = o.d("prior"), familyK = o.d("family_k"), litAt = o.d("lit"),
                coef = comb.doubles("coef"), intercept = comb.d("intercept"),
                reasonOf = o.getValue("reasons").jsonObject.mapValues { it.value.jsonPrimitive.content },
                maxReasons = o.getValue("max_reasons").jsonPrimitive.int,
                warnMinFamilies = warn.getValue("min_families").jsonPrimitive.int, warnMinP = warn.d("min_p"),
            )
        }
    }
}
