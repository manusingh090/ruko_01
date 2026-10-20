package com.ruko.demo.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.ruko.demo.R
import com.ruko.demo.data.Consents
import com.ruko.demo.net.RukoJson
import com.ruko.demo.signals.LureHit
import com.ruko.demo.signals.SignalOverrides
import com.ruko.demo.signals.SignalReading
import com.ruko.demo.signals.Signals
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.serialization.builtins.nullable
import kotlinx.serialization.json.Json
import com.ruko.demo.net.Context as RequestContext

private val prettyJson = Json(from = RukoJson) { prettyPrint = true }

private val LURE_TAGS = listOf("arrest_threat", "kyc", "task_job", "trading_tip", "refund")

/**
 * Guide step 6 debug panel: a switch for every context field, the outgoing JSON and a lure
 * paste box. Every real signal keeps a toggle here as its backup during the demo.
 */
@Composable
fun DebugPanel(vm: PaymentViewModel, consents: Consents, onBack: () -> Unit) {
    var reading by remember { mutableStateOf<SignalReading?>(null) }
    LaunchedEffect(consents) {
        while (true) {
            reading = withContext(Dispatchers.IO) { runCatching { Signals.read(consents) }.getOrNull() }
            delay(1_500)
        }
    }
    val o = Signals.overrides
    val set = { new: SignalOverrides -> Signals.overrides = new }

    Screen {
        CacheTopBar(stringResource(R.string.debug_title), onBack)
        Text(stringResource(R.string.debug_note), style = MaterialTheme.typography.bodyMedium, color = CacheColors.Warning)
        SimulatedPill(o.active)

        SectionTitle(R.string.debug_live)
        val r = reading
        if (r != null && r.phone.call == null) {
            Text(stringResource(R.string.debug_context_off), color = CacheColors.TextSecondary)
        }
        CodeBlock(
            "\"context\": " + (r?.let { prettyJson.encodeToString(RequestContext.serializer().nullable, it.context) } ?: "…")
        )

        SectionTitle(R.string.debug_overrides)
        BoolOverride("on_call", o.onCall) { set(o.copy(onCall = it)) }
        BoolOverride("caller_in_contacts", o.callerInContacts) { set(o.copy(callerInContacts = it)) }
        ChoiceOverride("call_minutes", o.callMinutes?.toString(), listOf("5", "38")) { set(o.copy(callMinutes = it?.toInt())) }
        BoolOverride("remote_access_app", o.remoteApp) { set(o.copy(remoteApp = it)) }
        BoolOverride("accessibility_on", o.accessibility) { set(o.copy(accessibility = it)) }
        BoolOverride("sideload_last_24h", o.sideload) { set(o.copy(sideload = it)) }
        BoolOverride("screen_recorded", o.screenRecorded) { set(o.copy(screenRecorded = it)) }
        ChoiceOverride("lure_tags", o.lureTag, LURE_TAGS) { set(o.copy(lureTag = it)) }
        SecondaryButton(stringResource(R.string.debug_clear), onClick = { set(SignalOverrides()) })

        SectionTitle(R.string.debug_paste)
        PasteBox()
        Text(stringResource(R.string.debug_model, Signals.classifier.version), style = MaterialTheme.typography.bodySmall, color = CacheColors.TextSecondary)

        SectionTitle(R.string.debug_last_request)
        CodeBlock(
            vm.lastRequestJson.takeIf { it.isNotEmpty() }
                ?.let { prettyJson.encodeToString(prettyJson.parseToJsonElement(it)) }
                ?: stringResource(R.string.debug_none)
        )
    }
}

@Composable
private fun PasteBox() {
    var text by remember { mutableStateOf("") }
    var result by remember { mutableStateOf<LureHit?>(null) }
    OutlinedTextField(
        value = text,
        onValueChange = {
            text = it
            result = null
        },
        placeholder = { Text(stringResource(R.string.debug_paste_hint)) },
        shape = RoundedCornerShape(14.dp),
        colors = cacheFieldColors(),
        modifier = Modifier.fillMaxWidth().heightIn(min = 110.dp),
    )
    SecondaryButton(stringResource(R.string.debug_paste_check), onClick = { result = Signals.classifier.classify(text) })
    result?.let { hit ->
        Text(
            stringResource(R.string.debug_paste_result, hit.tag, "%.2f".format(hit.conf)),
            style = MaterialTheme.typography.titleMedium,
        )
        if (hit.tag != "normal") {
            PrimaryButton(stringResource(R.string.debug_paste_use), onClick = {
                Signals.pasted = hit
                text = ""
                result = null
            })
        }
    }
}

@Composable
private fun SectionTitle(title: Int) {
    Text(stringResource(title), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 8.dp))
}

@Composable
private fun CodeBlock(text: String) {
    Text(
        text = text,
        fontFamily = FontFamily.Monospace,
        style = MaterialTheme.typography.bodySmall,
        modifier = Modifier.fillMaxWidth().cacheCard(radius = 12.dp).padding(12.dp),
    )
}

@Composable
private fun BoolOverride(field: String, value: Boolean?, onChange: (Boolean?) -> Unit) {
    val yes = stringResource(R.string.debug_yes)
    val no = stringResource(R.string.debug_no)
    ChoiceOverride(field, value?.let { if (it) yes else no }, listOf(yes, no)) { onChange(it?.let { choice -> choice == yes }) }
}

/** "Real" plus fixed choices; picking Real removes the override. */
@Composable
private fun ChoiceOverride(field: String, selected: String?, choices: List<String>, onChange: (String?) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(field, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodyMedium)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Chip(stringResource(R.string.debug_real), selected == null, Modifier.weight(1f)) { onChange(null) }
            choices.forEach { choice ->
                Chip(choice, selected == choice, Modifier.weight(1f)) { onChange(choice) }
            }
        }
    }
}

@Composable
private fun Chip(label: String, selected: Boolean, modifier: Modifier, onClick: () -> Unit) {
    Box(
        modifier = modifier
            .height(40.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(if (selected) CacheColors.Primary.copy(alpha = 0.3f) else CacheColors.Card)
            .border(1.dp, if (selected) CacheColors.PrimaryLight else CacheColors.CardBorder, RoundedCornerShape(10.dp))
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, style = MaterialTheme.typography.labelMedium, maxLines = 1)
    }
}
