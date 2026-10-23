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
| Request and response models, contract **c2-0.8** (`contracts/`) | `net/Models.kt` |
| Request builder (IST timestamps, request id) | `net/RequestBuilder.kt` |
| Retrofit client, 1 s timeout, fail open | `net/RukoApi.kt` |
| Phone signals: calls, contacts, apps, screen recording | `signals/ContextSensor.kt` |
| Mapping to the nested `context` (call, device, lures) | `signals/PhoneSignals.kt` |
| Lure scorer on recent SMS, `assets/lure.json` (keywords if missing) | `signals/LureScorer.kt`, `signals/Signals.kt` |
| On-device model (c2-0.8): readings → device features → verdict (fraud class, level, family scores) | `signals/DeviceFeatures.kt`, `signals/DeviceModel.kt`, `signals/DeviceVerdict.kt`, `assets/device_model.json` |
| Decision screen: tier, threat level, scam type, score bar, reasons, next step (c2-0.8 `threat`) | `ui/DecisionScreen.kt` |
| Sign up, login, encrypted account storage | `ui/auth/`, `data/` |
| Home, Pay, decision, PIN, Success, debug panel | `ui/` |
| Background task-scam watcher: SMS receiver, workers, episode, notifications, event queue | `shield/` |
| Home episode card, watcher switches in Profile and sign-up, Hindi strings | `ui/EpisodeCard.kt`, `ui/auth/`, `res/values-hi/` |
| Contract tests against `contracts/`, shared episode vectors | `app/src/test/.../ContractTest.kt`, `shield/EpisodeTrackerTest.kt` |
| Model tests against `ml/model/artifacts/` (scores, features, verdicts and lure scores match Python; assets are the trained files) | `signals/DeviceModelTest.kt`, `signals/DeviceFeaturesTest.kt`, `signals/LureModelTest.kt` |

Privacy rules the phone follows on top of the contract:
- `caller_number` is sent only for an incoming SIM call from a number not in contacts.
- `remote_access_active` is true only when a remote access app is installed and the screen is being
  captured (Android 15+); false when nothing captures the screen; null before Android 15.
- Account details never leave the phone.
- The on-device model runs at every Pay tap when at least one Scam Shield switch is on. It reads the
  call, device, lure hits, episode, Pay-screen session, payee-in-contacts and this app's completed
  payments; only its verdict (`context.device_verdict`: model version, fraud class, level, three
  family scores, up to 3 reasons) is sent. A family whose inputs were all unmeasured is `null`.

### Updating the models
`assets/lure.json` and `assets/device_model.json` are copies of `ml/model/artifacts/lure.json` and
`ml/model/artifacts/v0/device_model.json`; `LureModelTest` fails if they drift. After a retrain in
`ml/` (see `ml/PLAN.md`), copy both files and run `testDebugUnitTest`. Device feature names, units
and null rules are defined in `ml/dataset/device_features.py`; `DeviceFeaturesTest` replays real
simulated payments from `ml/model/artifacts/v0/device_feature_vectors.json`.

## Not built yet (guide steps 7 to 9)

The full T1 banner / T3 pause / T4 review screens (the decision screen shows the threat analysis
but has no trusted-contact or 1930 flow yet) and feedback calls.
