# android: Manjeet Singh (Android app)

Cache Pay, the phone part of RUKO (zone 1). Branches: `android/*`. Kotlin, Jetpack Compose,
Retrofit and kotlinx.serialization. It is a demo only: no real money moves.

## Run it

1. Open this `android` folder in Android Studio and let Gradle sync.
2. Start the backend on the laptop (see `backend/README.md`).
3. Plug in the phone with USB debugging on, then forward the API port:
   `adb reverse tcp:8000 tcp:8000`
4. Press Run, sign up, and tap "Check Scam Shield" on Home to call `GET /health`.

From the command line: `./gradlew assembleDebug testDebugUnitTest`

## What is here

| Area | Files |
|---|---|
| Request and response models, contract **c2-0.1** (`contracts/`) | `net/Models.kt` |
| Request builder (IST timestamps, request id) | `net/RequestBuilder.kt` |
| Retrofit client, 1 s timeout, fail open | `net/RukoApi.kt` |
| Phone signals: calls, contacts, apps, screen recording | `signals/ContextSensor.kt` |
| Mapping to the nested `context` (call, device, lures) | `signals/PhoneSignals.kt` |
| Lure scorer on recent SMS, `assets/lure.json` or keywords | `signals/LureScorer.kt`, `signals/Signals.kt` |
| Sign up, login, encrypted account storage | `ui/auth/`, `data/` |
| Home, Pay, decision, PIN, Success, debug panel | `ui/` |
| Contract tests against `contracts/` | `app/src/test/.../ContractTest.kt` |

Privacy rules the phone follows on top of the contract:
- `caller_number` is sent only for an incoming SIM call from a number not in contacts.
- `remote_access_active` is true only when a remote access app is installed and the screen is being
  captured (Android 15+); false when nothing captures the screen; null before Android 15.
- Account details never leave the phone.

## Not built yet (guide steps 7 to 9)

The T1 to T4 alert screens, feedback calls and Hindi strings.
