package com.ruko.demo.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.ruko.demo.data.PaymentState
import com.ruko.demo.signals.Signals
import com.ruko.demo.ui.auth.AuthState
import com.ruko.demo.ui.auth.AuthViewModel
import com.ruko.demo.ui.auth.LoginScreen
import com.ruko.demo.ui.auth.ProfileScreen
import com.ruko.demo.ui.auth.SignUpFlow
import com.ruko.demo.signals.PastPayment

private object Routes {
    const val HOME = "home"
    const val PAY = "pay"
    const val DECISION = "decision"
    const val PIN = "pin"
    const val SUCCESS = "success"
    const val PROFILE = "profile"
    const val DEBUG = "debug"
    const val HISTORY = "history"
    const val CONTACTS = "contacts"
}

/** Sign up or login first; the payment app only exists once the account is unlocked. */
@Composable
fun RukoApp() {
    val auth: AuthViewModel = viewModel()
    // One view model for the whole payment flow, scoped to the activity.
    val vm: PaymentViewModel = viewModel()

    // Leaving the app mid-payment (for the call, WhatsApp, a chat) is part of the Pay session.
    // Kept here, not in PaymentApp, so it still counts when the app locks while the user is away.
    LifecycleEventEffect(Lifecycle.Event.ON_STOP) {
        auth.onBackground()
        vm.onAppBackground()
    }
    LifecycleEventEffect(Lifecycle.Event.ON_START) {
        auth.onForeground()
        vm.onAppForeground()
    }

    when (auth.state) {
        AuthState.Loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator(color = CacheColors.Primary)
        }
        AuthState.NeedsSignUp -> SignUpFlow(auth)
        AuthState.Locked -> LoginScreen(auth)
        AuthState.Unlocked -> PaymentApp(auth, vm)
    }
}

/** Home, Pay, decision, PIN, Success (guide step 5), plus the profile. */
@Composable
private fun PaymentApp(auth: AuthViewModel, vm: PaymentViewModel) {
    val nav = rememberNavController()
    // Signals are read with the user's current consents at the moment of Pay.
    SideEffect {
        vm.consents = auth.consents
        vm.typingGaps = auth.typingGaps
        vm.pastPayments = auth.history.filter { it.state == PaymentState.COMPLETED }
            .map { PastPayment(it.ts / 1000.0, it.payee.trim().lowercase(), it.amountInr) }
    }

    NavHost(navController = nav, startDestination = Routes.HOME) {
        composable(Routes.HOME) {
            HomeScreen(
                vm = vm,
                firstName = auth.profile?.firstName.orEmpty(),
                consents = auth.consents,
                trustedName = auth.profile?.trustedName,
                trustedMobile = auth.profile?.trustedMobile,
                onPay = {
                    auth.closeAbandonedPayments()
                    vm.reset()
                    nav.navigate(Routes.PAY)
                },
                onProfile = { nav.navigate(Routes.PROFILE) },
                onDebug = { nav.navigate(Routes.DEBUG) },
                onHistory = { nav.navigate(Routes.HISTORY) },
                onPayContact = { nav.navigate(Routes.CONTACTS) },
            )
        }
        composable(Routes.PAY) {
            PayScreen(
                vm = vm,
                recentPayees = auth.recentPayees,
                onBack = { nav.popBackStack() },
                onDecided = { decision ->
                    // Every check enters the history, with its payment id (the request_id).
                    vm.checkedPayment?.let(auth::recordCheck)
                    // Silence is a feature: T0 goes straight to the PIN with nothing shown.
                    val silent = decision is Decision.Result && decision.response.tier == "T0"
                    nav.navigate(if (silent) Routes.PIN else Routes.DECISION)
                },
            )
        }
        composable(Routes.DECISION) {
            DecisionScreen(
                vm = vm,
                onContinue = { nav.navigate(Routes.PIN) },
                onCancel = {
                    vm.checkedPayment?.let { auth.settlePayment(it.id, PaymentState.CANCELLED) }
                    nav.popBackStack(Routes.HOME, inclusive = false)
                },
            )
        }
        composable(Routes.PIN) {
            PinScreen(vm, onConfirm = {
                auth.rememberPayee(vm.upiId)
                auth.rememberPayment(vm.upiId, vm.amountInr)
                auth.rememberTypingGap(vm.lastTypingGap)
                vm.checkedPayment?.let { auth.settlePayment(it.id, PaymentState.COMPLETED) }
                nav.navigate(Routes.SUCCESS) { popUpTo(Routes.HOME) }
            })
        }
        composable(Routes.SUCCESS) {
            SuccessScreen(vm, onDone = { nav.popBackStack(Routes.HOME, inclusive = false) })
        }
        composable(Routes.PROFILE) {
            ProfileScreen(auth, onBack = { nav.popBackStack() })
        }
        composable(Routes.CONTACTS) {
            ContactsScreen(vm, onBack = { nav.popBackStack() }, onReady = {
                auth.closeAbandonedPayments()
                nav.navigate(Routes.PAY) { popUpTo(Routes.HOME) }
            })
        }
        composable(Routes.HISTORY) {
            HistoryScreen(auth.history, onOpen = auth::reportOutcomes, onBack = { nav.popBackStack() })
        }
        composable(Routes.DEBUG) {
            DebugPanel(vm, auth.consents, onBack = { nav.popBackStack() })
        }
    }
}
