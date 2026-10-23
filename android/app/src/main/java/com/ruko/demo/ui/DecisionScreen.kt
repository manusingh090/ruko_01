package com.ruko.demo.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.ruko.demo.BuildConfig
import com.ruko.demo.R

private val TASK_KEYS = setOf("t2_task_job", "t3_task_job")

/** Colour that marks how serious a tier is. */
private fun tierColor(tier: String?): Color = when (tier) {
    "T1" -> CacheColors.Info
    "T2" -> CacheColors.Warning
    "T3" -> CacheColors.Danger
    "T4" -> CacheColors.Info
    "T0" -> CacheColors.Success
    else -> CacheColors.TextSecondary
}

/** The threat level in words (c2-0.8). */
@Composable
private fun threatLevelText(level: String): String = when (level) {
    "low" -> stringResource(R.string.threat_level_low)
    "medium" -> stringResource(R.string.threat_level_medium)
    "high" -> stringResource(R.string.threat_level_high)
    "critical" -> stringResource(R.string.threat_level_critical)
    else -> stringResource(R.string.threat_level_none)
}

/** The known scam the payment matches, in words; null for anything the app does not know. */
@Composable
private fun fraudText(type: String?): String? = when (type) {
    "digital_arrest" -> stringResource(R.string.fraud_digital_arrest)
    "fake_customer_care" -> stringResource(R.string.fraud_fake_customer_care)
    "fake_kyc_apk" -> stringResource(R.string.fraud_fake_kyc_apk)
    "trading_group" -> stringResource(R.string.fraud_trading_group)
    "task_job" -> stringResource(R.string.fraud_task_job)
    "refund_qr" -> stringResource(R.string.fraud_refund_qr)
    else -> null
}

/**
 * The tier, the server's threat analysis (c2-0.8: level, the scam it matches, score, reasons) and
 * the safe next step. The full T1 banner / T3 pause screens can build on this.
 */
@Composable
fun DecisionScreen(vm: PaymentViewModel, onContinue: () -> Unit, onCancel: () -> Unit) {
    val result = vm.decision as? Decision.Result
    val accent = tierColor(result?.response?.tier)

    Screen {
        CacheTopBar(stringResource(R.string.decision_title))

        Column(
            modifier = Modifier.fillMaxWidth().cacheCard(borderColor = accent.copy(alpha = 0.6f)).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                Box(
                    modifier = Modifier.size(52.dp).clip(CircleShape).background(accent.copy(alpha = 0.18f)),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        imageVector = if (result == null) CacheIcons.Shield else CacheIcons.Warning,
                        contentDescription = null,
                        tint = accent,
                        modifier = Modifier.size(28.dp),
                    )
                }
                Text(
                    text = if (result != null) {
                        stringResource(R.string.decision_tier, result.response.tier)
                    } else {
                        stringResource(R.string.decision_unavailable)
                    },
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.Bold,
                )
            }

            if (result != null) {
                result.response.threat?.let { threat ->
                    Text(
                        text = threatLevelText(threat.level),
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                        color = accent,
                    )
                    fraudText(threat.fraudType)?.let {
                        Text(stringResource(R.string.threat_type, it), style = MaterialTheme.typography.bodyLarge)
                    }
                    Text(
                        text = stringResource(R.string.threat_score, threat.score),
                        style = MaterialTheme.typography.bodyMedium,
                        color = CacheColors.TextSecondary,
                    )
                    Box(
                        Modifier
                            .fillMaxWidth()
                            .height(8.dp)
                            .clip(CircleShape)
                            .background(accent.copy(alpha = 0.18f)),
                    ) {
                        Box(
                            Modifier
                                .fillMaxWidth(threat.score.coerceIn(0, 100) / 100f)
                                .height(8.dp)
                                .clip(CircleShape)
                                .background(accent),
                        )
                    }
                }
                if (result.response.messageKey in TASK_KEYS) {
                    Text(stringResource(R.string.decision_task_job), style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold)
                }
                val reasons = result.response.reasons.take(3).mapNotNull { reasonText(it) }
                if (reasons.isNotEmpty()) {
                    Text(
                        text = stringResource(R.string.decision_why),
                        style = MaterialTheme.typography.titleMedium,
                        color = CacheColors.TextSecondary,
                    )
                    reasons.forEach { reason ->
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Box(
                                Modifier
                                    .padding(top = 8.dp)
                                    .size(8.dp)
                                    .clip(CircleShape)
                                    .background(accent),
                            )
                            Text(reason, style = MaterialTheme.typography.bodyLarge)
                        }
                    }
                }
                if (BuildConfig.DEBUG) {
                    Text(
                        text = "debug: ${result.millis} ms, message_key=${result.response.messageKey}",
                        style = MaterialTheme.typography.bodySmall,
                        color = CacheColors.TextSecondary,
                    )
                }
            } else {
                Text(
                    text = stringResource(R.string.decision_unavailable_body),
                    style = MaterialTheme.typography.bodyLarge,
                    color = CacheColors.TextSecondary,
                )
            }
        }

        val next = when (result?.response?.threat?.level) {
            "high", "critical" -> R.string.threat_next_high
            "medium" -> R.string.threat_next_medium
            "low", "none" -> R.string.threat_next_low
            else -> null
        }
        next?.let {
            Text(text = stringResource(it), style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold)
        }
        PrimaryButton(stringResource(R.string.decision_continue), onContinue)
        SecondaryButton(stringResource(R.string.decision_cancel), onCancel)
    }
}
