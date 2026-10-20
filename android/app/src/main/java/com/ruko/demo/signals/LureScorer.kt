package com.ruko.demo.signals

import com.ruko.demo.net.LureSignal
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlin.math.exp
import kotlin.math.sqrt

/** One scored message: the best class and its probability. */
data class LureHit(val tag: String, val conf: Double, val minAgo: Double = 0.0)

interface LureClassifier {
    val version: String
    fun classify(text: String): LureHit
}

/** Diwansu's model/artifacts/lure.json (guide format). */
@Serializable
data class LureModel(
    val version: String,
    val analyzer: String = "char_wb",
    @SerialName("ngram_range") val ngramRange: List<Int>,
    val lowercase: Boolean = true,
    val classes: List<String>,
    val vocabulary: Map<String, Int>,
    val idf: List<Double>,
    val coef: List<List<Double>>,
    val intercept: List<Double>,
)

/**
 * Character n-gram TF-IDF + logistic regression, scored the way scikit-learn does, so a
 * message gets the same tag here as in Python:
 * char_wb n-grams → counts × idf → L2 norm → coef · v + intercept → softmax.
 */
class LinearLureClassifier(private val model: LureModel) : LureClassifier {
    override val version: String = model.version

    override fun classify(text: String): LureHit {
        val counts = HashMap<Int, Int>()
        val input = if (model.lowercase) text.lowercase() else text
        for (gram in charWbNgrams(input, model.ngramRange[0], model.ngramRange[1])) {
            val index = model.vocabulary[gram] ?: continue
            counts[index] = (counts[index] ?: 0) + 1
        }
        val weights = counts.mapValues { (index, count) -> count * model.idf[index] }
        val norm = sqrt(weights.values.sumOf { it * it })
        val scores = model.classes.indices.map { k ->
            model.intercept[k] + if (norm == 0.0) 0.0 else weights.entries.sumOf { (i, w) -> model.coef[k][i] * w / norm }
        }
        val probs = softmax(scores)
        val best = probs.indices.maxBy { probs[it] }
        return LureHit(model.classes[best], probs[best])
    }
}

/**
 * Fallback until lure.json arrives (guide: "keyword list per lure tag"). One keyword alone
 * scores below the 0.6 send threshold, so a single word such as "refund" never flags a message.
 */
object KeywordLureClassifier : LureClassifier {
    override val version = "keywords-v1"

    private val keywords = mapOf(
        "arrest_threat" to listOf(
            "arrest", "cbi", "police", "warrant", "money laundering", "narcotics", "customs",
            "court", "fir ", "parcel", "drugs", "ed officer", "giraftar",
        ),
        "kyc" to listOf(
            "kyc", "pan card", "aadhaar", "blocked", "suspended", "update your", "verify your",
            "account will be", "expire", "re-kyc",
        ),
        "task_job" to listOf(
            "task", "part time", "work from home", "daily income", "earn", "like videos",
            "telegram", "salary", "per day", "rate products",
        ),
        "trading_tip" to listOf(
            "stock", "ipo", "trading", "profit", "returns", "shares", "investment", "guaranteed",
            "vip group", "crypto",
        ),
        "refund" to listOf(
            "refund", "cashback", "electricity", "disconnected", "reversal", "scan qr", "receive money",
            "bill unpaid", "power cut", "tonight",
        ),
    )

    override fun classify(text: String): LureHit {
        val lower = text.lowercase()
        val (tag, hits) = keywords
            .map { (tag, words) -> tag to words.count { it in lower } }
            .maxBy { it.second }
        val conf = when (hits) {
            0 -> return LureHit("normal", 1.0)
            1 -> 0.5
            2 -> 0.7
            else -> 0.85
        }
        return LureHit(tag, conf)
    }
}

/** Only scam tags at or above this confidence leave the phone (guide step 5). */
const val LURE_SEND_THRESHOLD = 0.6

/** The tags to send: non-normal hits above the threshold, the strongest one per tag. */
fun bestLureTags(hits: List<LureHit>): List<LureHit> =
    hits.filter { it.tag != "normal" && it.conf >= LURE_SEND_THRESHOLD }
        .groupBy { it.tag }
        .map { (_, sameTag) -> sameTag.maxBy { it.conf } }
        .sortedByDescending { it.conf }

fun LureHit.toLureSignal() = LureSignal(tag = tag, conf = round2(conf), minAgo = round1(minAgo.coerceAtLeast(0.0)))

private fun round2(value: Double) = kotlin.math.round(value * 100) / 100

private fun round1(value: Double) = kotlin.math.round(value * 10) / 10

/**
 * scikit-learn's char_wb analyzer: each whitespace separated word is padded with one space
 * on each side, then cut into n-grams; a word shorter than n is counted once, whole.
 */
fun charWbNgrams(text: String, minN: Int, maxN: Int): List<String> {
    val out = ArrayList<String>()
    for (word in text.split(Regex("\\s+")).filter { it.isNotEmpty() }) {
        val w = " $word "
        for (n in minN..maxN) {
            var offset = 0
            out.add(w.substring(offset, minOf(offset + n, w.length)))
            while (offset + n < w.length) {
                offset++
                out.add(w.substring(offset, offset + n))
            }
            if (offset == 0) break
        }
    }
    return out
}

private fun softmax(scores: List<Double>): List<Double> {
    val max = scores.max()
    val exps = scores.map { exp(it - max) }
    val sum = exps.sum()
    return exps.map { it / sum }
}
