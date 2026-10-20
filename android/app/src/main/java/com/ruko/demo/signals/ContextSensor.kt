package com.ruko.demo.signals

import android.Manifest.permission.READ_CONTACTS
import android.Manifest.permission.READ_PHONE_STATE
import android.accessibilityservice.AccessibilityServiceInfo
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ApplicationInfo
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import android.provider.ContactsContract
import android.telephony.PhoneStateListener
import android.telephony.TelephonyCallback
import android.telephony.TelephonyManager
import android.view.accessibility.AccessibilityManager
import androidx.annotation.RequiresApi
import androidx.core.content.ContextCompat
import com.ruko.demo.net.CallDirection
import com.ruko.demo.net.CallKind
import kotlin.math.round

/**
 * Reads the situation on the phone (guide step 6). Call numbers are kept in memory only, used
 * for the contacts lookup and forgotten 30 minutes after the call; nothing here is stored or sent.
 */
class ContextSensor(private val app: Context) {

    private val telephony = app.getSystemService(TelephonyManager::class.java)
    private val audio = app.getSystemService(AudioManager::class.java)
    private val packages = app.packageManager
    private val mainExecutor = ContextCompat.getMainExecutor(app)

    @Volatile private var simCallSince = 0L
    @Volatile private var voipCallSince = 0L
    @Volatile private var ringing = false
    @Volatile private var direction: CallDirection? = null
    @Volatile private var callNumber: String? = null
    @Volatile private var lastCallEndedAt = 0L
    @Volatile private var lastCallNumber: String? = null
    @Volatile private var lastCallDirection: CallDirection? = null
    @Volatile private var lastCallWasVoip = false

    /** Set by MainActivity from the Android 15 screen recording callback. */
    @Volatile var screenRecorded: Boolean? = null

    private var callListening = false
    private var receiverRegistered = false
    private var audioListening = false

    /** Starts the listeners whose permissions are granted; safe to call again after a grant. Main thread. */
    fun start() {
        if (granted(READ_PHONE_STATE)) {
            if (!callListening) callListening = listenCallState()
            if (!receiverRegistered) {
                ContextCompat.registerReceiver(
                    app,
                    phoneStateReceiver,
                    IntentFilter(TelephonyManager.ACTION_PHONE_STATE_CHANGED),
                    // A protected system broadcast: other apps cannot send it.
                    ContextCompat.RECEIVER_EXPORTED,
                )
                receiverRegistered = true
            }
        }
        if (!audioListening && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            audio.addOnModeChangedListener(mainExecutor) { mode -> onAudioMode(mode) }
            audioListening = true
        }
    }

    fun readCall(now: Long = System.currentTimeMillis()): CallReading {
        val phoneAllowed = granted(READ_PHONE_STATE)
        val simActive = phoneAllowed && currentCallState() != TelephonyManager.CALL_STATE_IDLE
        // WhatsApp and other VoIP calls put the audio in communication mode.
        val voipActive = audio.mode == AudioManager.MODE_IN_COMMUNICATION

        // Catch up with anything the listeners missed, such as opening the app mid-call.
        if (simActive && simCallSince == 0L) simCallSince = now
        if (voipActive && voipCallSince == 0L) voipCallSince = now
        if (!voipActive && voipCallSince != 0L) endCall(now, voip = true)
        if (lastCallEndedAt != 0L && now - lastCallEndedAt > CALL_MEMORY_MS) {
            lastCallEndedAt = 0L
            lastCallNumber = null
            lastCallDirection = null
        }

        val active = simActive || voipActive
        val since = listOf(simCallSince, voipCallSince).filter { it > 0 }.minOrNull()
        val endedRecently = !active && lastCallEndedAt != 0L
        val endedSim = endedRecently && !lastCallWasVoip
        val number = when {
            simActive -> callNumber
            endedSim -> lastCallNumber
            else -> null // VoIP calls never expose the caller's number
        }
        return CallReading(
            // Without phone permission a SIM call cannot be seen, so "no call" is unknown.
            active = if (phoneAllowed || voipActive) active else null,
            kind = when {
                simActive -> CallKind.SIM
                voipActive -> CallKind.VOIP
                endedRecently -> if (lastCallWasVoip) CallKind.VOIP else CallKind.SIM
                else -> null
            },
            direction = when {
                simActive -> direction
                endedSim -> lastCallDirection
                else -> null
            },
            callerInContacts = number?.let(::inContacts),
            startedMinAgo = if (active) since?.let { minutes(now - it) } else null,
            endedMinAgo = if (endedRecently) minutes(now - lastCallEndedAt) else null,
            callerNumber = number,
        )
    }

    fun readDevice(now: Long = System.currentTimeMillis()): DeviceReading {
        val remote = REMOTE_ACCESS_PACKAGES.mapNotNull(::packageInfo)
        return DeviceReading(
            remoteAppInstalled = remote.isNotEmpty(),
            remoteAppInstalledMinAgo = remote.maxOfOrNull { it.firstInstallTime }?.let { minutes(now - it) },
            untrustedAccessibility = untrustedAccessibility(),
            sideloadedLast24h = sideloadedSince(now - DAY_MS),
            screenRecorded = screenRecorded,
        )
    }

    // ---------------- calls ----------------

    private fun onCallState(state: Int, number: String?) {
        val now = System.currentTimeMillis()
        number?.takeIf { it.isNotBlank() }?.let { callNumber = it }
        when (state) {
            TelephonyManager.CALL_STATE_RINGING -> {
                ringing = true
                direction = CallDirection.INCOMING
            }
            TelephonyManager.CALL_STATE_OFFHOOK -> {
                if (simCallSince == 0L) simCallSince = now
                if (!ringing) direction = CallDirection.OUTGOING
            }
            TelephonyManager.CALL_STATE_IDLE -> if (simCallSince != 0L || ringing) endCall(now, voip = false)
        }
    }

    private fun onAudioMode(mode: Int) {
        val now = System.currentTimeMillis()
        if (mode == AudioManager.MODE_IN_COMMUNICATION) {
            if (voipCallSince == 0L) voipCallSince = now
        } else if (voipCallSince != 0L) {
            endCall(now, voip = true)
        }
    }

    private fun endCall(now: Long, voip: Boolean) {
        lastCallEndedAt = now
        lastCallWasVoip = voip
        if (voip) {
            voipCallSince = 0L
        } else {
            lastCallNumber = callNumber
            lastCallDirection = direction
            simCallSince = 0L
            ringing = false
            direction = null
            callNumber = null
        }
    }

    private fun listenCallState(): Boolean = try {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            telephony.registerTelephonyCallback(mainExecutor, CallCallback())
        } else {
            @Suppress("DEPRECATION")
            telephony.listen(LegacyCallListener(), PhoneStateListener.LISTEN_CALL_STATE)
        }
        true
    } catch (e: SecurityException) {
        false
    }

    @RequiresApi(Build.VERSION_CODES.S)
    private inner class CallCallback : TelephonyCallback(), TelephonyCallback.CallStateListener {
        override fun onCallStateChanged(state: Int) = onCallState(state, null)
    }

    @Suppress("DEPRECATION")
    private inner class LegacyCallListener : PhoneStateListener() {
        @Deprecated("Deprecated in Java")
        override fun onCallStateChanged(state: Int, phoneNumber: String?) = onCallState(state, phoneNumber)
    }

    /** Carries the incoming number when READ_CALL_LOG is granted; TelephonyCallback does not. */
    private val phoneStateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val state = when (intent.getStringExtra(TelephonyManager.EXTRA_STATE)) {
                TelephonyManager.EXTRA_STATE_RINGING -> TelephonyManager.CALL_STATE_RINGING
                TelephonyManager.EXTRA_STATE_OFFHOOK -> TelephonyManager.CALL_STATE_OFFHOOK
                TelephonyManager.EXTRA_STATE_IDLE -> TelephonyManager.CALL_STATE_IDLE
                else -> return
            }
            @Suppress("DEPRECATION")
            onCallState(state, intent.getStringExtra(TelephonyManager.EXTRA_INCOMING_NUMBER))
        }
    }

    private fun currentCallState(): Int = try {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            telephony.callStateForSubscription
        } else {
            @Suppress("DEPRECATION")
            telephony.callState
        }
    } catch (e: SecurityException) {
        TelephonyManager.CALL_STATE_IDLE
    }

    /** Null when contacts cannot be read: the caller is unknown, not known-safe. */
    private fun inContacts(number: String): Boolean? {
        if (!granted(READ_CONTACTS)) return null
        val uri = Uri.withAppendedPath(ContactsContract.PhoneLookup.CONTENT_FILTER_URI, Uri.encode(number))
        return try {
            app.contentResolver.query(uri, arrayOf(ContactsContract.PhoneLookup._ID), null, null, null)
                ?.use { it.moveToFirst() } ?: false
        } catch (e: SecurityException) {
            null
        }
    }

    // ---------------- apps ----------------

    /** An enabled accessibility service from an app that did not ship with the phone. */
    private fun untrustedAccessibility(): Boolean {
        val manager = app.getSystemService(AccessibilityManager::class.java)
        return manager.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK).any { info ->
            val pkg = info.resolveInfo?.serviceInfo?.packageName ?: return@any true
            pkg != app.packageName && packageInfo(pkg)?.applicationInfo?.isSystem() != true
        }
    }

    /** A user app installed after [since] by something other than the Play Store. */
    private fun sideloadedSince(since: Long): Boolean = installedPackages().any { info ->
        info.packageName != app.packageName &&
            info.firstInstallTime > since &&
            info.applicationInfo?.isSystem() != true &&
            installerOf(info.packageName) !in TRUSTED_INSTALLERS
    }

    private fun installerOf(pkg: String): String? = try {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            packages.getInstallSourceInfo(pkg).installingPackageName
        } else {
            @Suppress("DEPRECATION")
            packages.getInstallerPackageName(pkg)
        }
    } catch (e: PackageManager.NameNotFoundException) {
        null
    } catch (e: IllegalArgumentException) {
        null
    }

    private fun packageInfo(pkg: String): PackageInfo? = try {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            packages.getPackageInfo(pkg, PackageManager.PackageInfoFlags.of(0))
        } else {
            packages.getPackageInfo(pkg, 0)
        }
    } catch (e: PackageManager.NameNotFoundException) {
        null
    }

    private fun installedPackages(): List<PackageInfo> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            packages.getInstalledPackages(PackageManager.PackageInfoFlags.of(0))
        } else {
            packages.getInstalledPackages(0)
        }

    private fun ApplicationInfo.isSystem() =
        flags and (ApplicationInfo.FLAG_SYSTEM or ApplicationInfo.FLAG_UPDATED_SYSTEM_APP) != 0

    private fun granted(permission: String) =
        ContextCompat.checkSelfPermission(app, permission) == PackageManager.PERMISSION_GRANTED

    private fun minutes(ms: Long): Double = round(ms.coerceAtLeast(0) / 6_000.0) / 10.0

    companion object {
        /** Must match the <queries> block in the manifest. */
        val REMOTE_ACCESS_PACKAGES = listOf(
            "com.anydesk.anydeskandroid",
            "com.teamviewer.quicksupport.market",
            "com.carriez.flutter_hbb", // RustDesk
            "com.sand.airdroid",
        )
        private val TRUSTED_INSTALLERS = setOf("com.android.vending")
        private const val CALL_MEMORY_MS = 30 * 60_000L
        private const val DAY_MS = 24 * 60 * 60_000L
    }
}
