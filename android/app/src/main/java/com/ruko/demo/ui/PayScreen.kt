package com.ruko.demo.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.rememberScrollState
import com.ruko.demo.net.EntryMethod
import com.ruko.demo.net.PayerSays
import com.ruko.demo.signals.Signals
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.ruko.demo.R

@Composable
fun PayScreen(
    vm: PaymentViewModel,
    recentPayees: List<String>,
    onBack: () -> Unit,
    onDecided: (Decision) -> Unit,
) {
    Screen {
        CacheTopBar(stringResource(R.string.pay_title), onBack)
        SimulatedPill(Signals.overrides.active)

        if (recentPayees.isNotEmpty()) {
            Text(stringResource(R.string.pay_recent), style = MaterialTheme.typography.titleSmall, color = CacheColors.TextSecondary)
            Row(
                modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                recentPayees.forEach { payee ->
                    Row(
                        modifier = Modifier
                            .cacheCard(radius = 50.dp)
                            .clickable { vm.pickSavedPayee(payee) }
                            .padding(start = 6.dp, end = 14.dp, top = 6.dp, bottom = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        InitialAvatar(payee, size = 32.dp)
                        Text(payee, style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }
        }

        // Payee card, like a chat header in UPI apps.
        Row(
            modifier = Modifier.fillMaxWidth().cacheCard().padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            InitialAvatar(vm.upiId)
            Column {
                Text(
                    text = vm.upiId.trim().ifEmpty { stringResource(R.string.pay_upi_hint) },
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                )
                Text(
                    text = stringResource(if (vm.entryMethod == EntryMethod.TYPED) R.string.pay_typed else R.string.pay_saved),
                    style = MaterialTheme.typography.bodySmall,
                    color = CacheColors.TextSecondary,
                )
            }
        }

        OutlinedTextField(
            value = vm.upiId,
            onValueChange = vm::typePayee,
            label = { Text(stringResource(R.string.pay_upi_label)) },
            singleLine = true,
            shape = RoundedCornerShape(14.dp),
            colors = cacheFieldColors(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
            modifier = Modifier.fillMaxWidth(),
        )

        OutlinedTextField(
            value = vm.amountText,
            onValueChange = { text -> vm.amountText = text.filter { it.isDigit() || it == '.' } },
            label = { Text(stringResource(R.string.pay_amount_label)) },
            prefix = { Text("₹", style = MaterialTheme.typography.headlineMedium) },
            textStyle = MaterialTheme.typography.headlineMedium.copy(fontWeight = FontWeight.Bold),
            singleLine = true,
            shape = RoundedCornerShape(14.dp),
            colors = cacheFieldColors(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            modifier = Modifier.fillMaxWidth(),
        )

        if (vm.askWhoIsPayee) WhoIsPayee(vm)

        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Icon(CacheIcons.Shield, contentDescription = null, tint = CacheColors.PrimaryLight, modifier = Modifier.size(20.dp))
            Text(
                text = stringResource(R.string.pay_shield_note),
                style = MaterialTheme.typography.bodyMedium,
                color = CacheColors.TextSecondary,
                textAlign = TextAlign.Start,
            )
        }

        PrimaryButton(
            text = stringResource(R.string.pay_button),
            onClick = { vm.pay(onDecided) },
            enabled = vm.canPay,
            loading = vm.checking,
        )
    }
}

/** "Who are you paying?" Optional; tapping the chosen answer again clears it. */
@Composable
private fun WhoIsPayee(vm: PaymentViewModel) {
    val options = listOf(
        PayerSays.FAMILY to R.string.payer_family,
        PayerSays.SHOP to R.string.payer_shop,
        PayerSays.COMPANY to R.string.payer_company,
        PayerSays.GOVERNMENT to R.string.payer_government,
        PayerSays.INVESTMENT to R.string.payer_investment,
        PayerSays.OTHER to R.string.payer_other,
    )
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(stringResource(R.string.payer_question), style = MaterialTheme.typography.titleMedium)
        options.chunked(3).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                row.forEach { (value, label) ->
                    val selected = vm.payerSays == value
                    Text(
                        text = stringResource(label),
                        style = MaterialTheme.typography.labelLarge,
                        textAlign = TextAlign.Center,
                        modifier = Modifier
                            .weight(1f)
                            .cacheCard(radius = 12.dp, borderColor = if (selected) CacheColors.PrimaryLight else CacheColors.CardBorder)
                            .clickable { vm.payerSays = if (selected) null else value }
                            .padding(vertical = 12.dp),
                    )
                }
            }
        }
    }
}
