package com.ruko.demo.net

import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.UUID

/** Builds the one request the phone sends on Pay. Only derived flags go in `context`. */
object RequestBuilder {

    private val IST: ZoneOffset = ZoneOffset.ofHoursMinutes(5, 30)

    /** For example 2026-11-07T10:40:00+05:30 */
    fun nowIst(): String =
        OffsetDateTime.now(IST)
            .truncatedTo(ChronoUnit.SECONDS)
            .format(DateTimeFormatter.ISO_OFFSET_DATE_TIME)

    fun build(
        userId: String,
        upiId: String,
        amountInr: Double,
        entryMethod: EntryMethod,
        context: Context?,
        payerSays: PayerSays? = null,
        ts: String = nowIst(),
        requestId: String = UUID.randomUUID().toString(),
    ): DecideRequest = DecideRequest(
        requestId = requestId,
        userId = userId,
        payeeUpiId = upiId.trim().lowercase(),
        amountInr = amountInr,
        entryMethod = entryMethod,
        payerSays = payerSays,
        ts = ts,
        context = context,
    )
}
