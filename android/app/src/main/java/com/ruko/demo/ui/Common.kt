package com.ruko.demo.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextFieldColors
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.ruko.demo.R
import java.text.NumberFormat
import java.util.Locale

/** Cache Pay palette: near black surfaces with a violet accent. */
object CacheColors {
    val Background = Color(0xFF0E0B16)
    val Surface = Color(0xFF17131F)
    val Card = Color(0xFF221D2E)
    val CardBorder = Color(0xFF342C45)
    val Primary = Color(0xFF7C3AED)
    val PrimaryDeep = Color(0xFF9333EA)
    val PrimaryLight = Color(0xFFA78BFA)
    val HeroTop = Color(0xFF240760)
    val HeroBottom = Color(0xFF5B21B6)
    val TextPrimary = Color(0xFFF5F3FA)
    val TextSecondary = Color(0xFFB4ADC6)
    val Success = Color(0xFF22C55E)
    val Warning = Color(0xFFF59E0B)
    val Danger = Color(0xFFEF4444)
    val Info = Color(0xFF60A5FA)
    val Accent = Color(0xFFF97316)
    val Gold = Color(0xFFFBBF24)
}

@Composable
fun CachePayTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = CacheColors.Primary,
            onPrimary = Color.White,
            background = CacheColors.Background,
            onBackground = CacheColors.TextPrimary,
            surface = CacheColors.Background,
            onSurface = CacheColors.TextPrimary,
            surfaceVariant = CacheColors.Card,
            onSurfaceVariant = CacheColors.TextSecondary,
            secondaryContainer = CacheColors.Card,
            onSecondaryContainer = CacheColors.TextPrimary,
            outline = CacheColors.CardBorder,
            error = CacheColors.Danger,
        ),
        content = content,
    )
}

/** Standard inner page: padded, scrollable, safe from system bars. */
@Composable
fun Screen(content: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        content = content,
    )
}

@Composable
fun CacheTopBar(title: String, onBack: (() -> Unit)? = null) {
    Row(
        modifier = Modifier.fillMaxWidth().height(56.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (onBack != null) {
            IconButton(onClick = onBack) {
                Icon(CacheIcons.ArrowBack, contentDescription = stringResource(R.string.back))
            }
        }
        Text(
            text = title,
            style = MaterialTheme.typography.titleLarge,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(start = if (onBack == null) 4.dp else 0.dp),
        )
    }
}

@Composable
fun PrimaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    loading: Boolean = false,
) {
    Button(
        onClick = onClick,
        enabled = enabled && !loading,
        shape = RoundedCornerShape(16.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = CacheColors.Primary,
            contentColor = Color.White,
            disabledContainerColor = CacheColors.Primary.copy(alpha = 0.35f),
            disabledContentColor = Color.White.copy(alpha = 0.6f),
        ),
        modifier = modifier.fillMaxWidth().height(56.dp),
    ) {
        if (loading) {
            CircularProgressIndicator(modifier = Modifier.size(22.dp), color = Color.White, strokeWidth = 2.dp)
        } else {
            Text(text, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
        }
    }
}

@Composable
fun SecondaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier) {
    OutlinedButton(
        onClick = onClick,
        shape = RoundedCornerShape(16.dp),
        border = BorderStroke(1.dp, CacheColors.CardBorder),
        modifier = modifier.fillMaxWidth().height(52.dp),
    ) {
        Text(text, color = CacheColors.TextPrimary, style = MaterialTheme.typography.titleMedium)
    }
}

@Composable
fun cacheFieldColors(): TextFieldColors = OutlinedTextFieldDefaults.colors(
    focusedContainerColor = CacheColors.Card,
    unfocusedContainerColor = CacheColors.Card,
    focusedBorderColor = CacheColors.PrimaryLight,
    unfocusedBorderColor = CacheColors.CardBorder,
    focusedLabelColor = CacheColors.PrimaryLight,
    cursorColor = CacheColors.PrimaryLight,
)

/** Rounded dark card used across screens. */
fun Modifier.cacheCard(radius: Dp = 16.dp, borderColor: Color = CacheColors.CardBorder): Modifier =
    this
        .clip(RoundedCornerShape(radius))
        .background(CacheColors.Card)
        .border(1.dp, borderColor, RoundedCornerShape(radius))

@Composable
fun DemoBanner(modifier: Modifier = Modifier) {
    Row(
        modifier = modifier.fillMaxWidth().cacheCard(radius = 14.dp).padding(14.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Icon(CacheIcons.Shield, contentDescription = null, tint = CacheColors.PrimaryLight, modifier = Modifier.size(22.dp))
        Text(
            text = stringResource(R.string.demo_banner),
            style = MaterialTheme.typography.bodyMedium,
            color = CacheColors.TextSecondary,
        )
    }
}

/** Round avatar with the first letter of a name or UPI ID. */
@Composable
fun InitialAvatar(text: String, size: Dp = 48.dp) {
    Box(
        modifier = Modifier.size(size).clip(CircleShape).background(CacheColors.Primary),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = text.trim().firstOrNull()?.uppercase() ?: "?",
            color = Color.White,
            style = MaterialTheme.typography.titleLarge,
            fontWeight = FontWeight.Bold,
        )
    }
}

private val inr: NumberFormat = NumberFormat.getCurrencyInstance(Locale.forLanguageTag("en-IN"))

fun formatInr(amount: Double): String = inr.format(amount)

/** Plain words for a reason code (contract table). Unknown codes are skipped, never shown raw. */
@Composable
fun reasonText(code: String): String? = when (code) {
    "R1_live_unknown_call" -> stringResource(R.string.reason_R1_live_unknown_call)
    "R2_remote_control" -> stringResource(R.string.reason_R2_remote_control)
    "R3_mule_like_payee" -> stringResource(R.string.reason_R3_mule_like_payee)
    "R4_unusual_amount" -> stringResource(R.string.reason_R4_unusual_amount)
    "R5_lure_message" -> stringResource(R.string.reason_R5_lure_message)
    "R6_new_payee_typed" -> stringResource(R.string.reason_R6_new_payee_typed)
    "R7_tranche_pattern" -> stringResource(R.string.reason_R7_tranche_pattern)
    "R8_intel_hit" -> stringResource(R.string.reason_R8_intel_hit)
    "R9_story_mismatch" -> stringResource(R.string.reason_R9_story_mismatch)
    else -> null // includes ruko_unavailable, which is not shown as a reason
}
