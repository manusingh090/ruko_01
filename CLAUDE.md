# CLAUDE.md: working in ruko_01

RUKO (रुको) is a UPI scam workflow shield for the Amazon AI Cyber Security Hackathon, Track 02:
pause the scam, not the payment. All data is synthetic or public. Finale around 2026-11-07.
Start with `README.md` (owners, layout, run commands); this file is the rules and the map.

## Who owns what

| Folder | Owner | What lives there |
|---|---|---|
| `android/` | Manjeet Singh | Cache Pay, the Kotlin + Compose app (`com.ruko.demo`) |
| `backend/` | Jagdeep Singh | Risk API, stage tracker, features, policy, evidence vault, mock intel, payment graph, shared schema |
| `console/` | Jagdeep Singh | Analyst console (React + TypeScript), served by the API at `/console/` |
| `ml/` | Diwansu Pilania | Training data replay, server model (`model/predict.py`), on-device model (`device_model.json`), lure classifier (`lure.json`); plan and status in `ml/PLAN.md` |
| `data/` | Nikunj Santosh Tangle | Simulator (`sim/`), evaluation (`eval/`), generated datasets |
| `contracts/` | shared | Published contract, generated from `backend/schema/events.py` |
| `docs/` | shared | Design briefs, e.g. `task-scam-watcher.md` (built; build notes in its §12) |

Branches: `android/*`, `backend/*`, `ml/*`, `data/*`, or `feature/*` for cross-folder work.
Merge to `main` by pull request. Keep `main` runnable.

## Rules

The rule numbers are cited elsewhere (`docs/`, `contracts/README.md`); keep them stable.

1. **Unknown is never safe.** A signal the phone or a service could not measure is `null` (or left
   out), never `false` or `0`. Applies to the request, features, scorer and evaluation.
2. **Only conclusions leave the phone.** No SMS text, audio, contacts or screen content is sent.
   The phone sends derived flags and counts. SMS text is parsed on the phone and thrown away.
3. **Fail open.** `/v1/decide` answers in 200 ms or less or returns `fail_open` (T0). The app gives
   up after its own 1 s timeout and treats a timeout, an error or a 422 as "safety check
   unavailable". A payment is never hard-blocked by RUKO failing.
4. **The score only explains; the tier decides.** Since c2-0.8 the response carries `threat`: a
   0–100 score (`round(100 × p_scam)`), a level that always follows the tier, the scam type and the
   lit families. The app shows the level and scam type in words (English and Hindi) and the score
   next to them, never as a "safe below N" hint (a scammer holding the phone could probe it). Raw
   `p_scam` and family scores stay on the server.
5. **Two independent families before interrupting.** Families are C context, L lure, P payee,
   B behaviour. T2 or higher needs at least two lit families (`api/policy.yaml`
   `corroboration_min_families`). One source of evidence must never light two families: the whole
   phone episode (`context.episode`) is one L signal, and graph / crowd signals are P only.
6. **Thresholds live in config, not code.** Policy numbers in `backend/api/policy.yaml`; episode
   numbers in `contracts/episode_rules.json` (the phone copies it to
   `android/app/src/main/assets/`, and `EpisodeTrackerTest` checks the copy).
7. **Privacy by default.** Phone data is AES-256-GCM encrypted with an Android Keystore key in
   `noBackupFilesDir`, excluded from backup; "Delete my data" removes the files and the key. RUKO
   users are pseudonymised on the server (`evidence/store.py` `pseudonym()`); payees, bait senders
   and cash-out accounts stay plain because they must be looked up. New consents default to off,
   with one exception: "Session checks" (`Consents.sessionSignals`, c2-0.5) is on by default,
   because it sends only counts and rounded times measured inside RUKO's own Pay screen (never
   keys typed or which other app was open) and the user can switch it off.
8. **A contract change is one change everywhere.** Edit `backend/schema/events.py`, bump
   `CONTRACT_VERSION`, run `python -m schema.export`, update `contracts/examples/` and
   `contracts/README.md` (changelog), and mirror it in `android/.../net/Models.kt` and
   `ContractTest.kt`, all in the same PR. Unknown fields get HTTP 422 (`extra="forbid"`).
9. **The engine only sees what RUKO could really see.** It never reads the simulator's ground
   truth (`is_mule`, `label_*`, journeys, full ledger) at decision time. The payment graph is built
   only from `/v1/decide`, `/v1/episode` and bank flows the visibility setting allows. Ground truth
   is for scoring in `data/eval/` only. User baselines (`features/user_baseline.py`,
   `Baselines.get(user, at=ts)`) use only payments before the one being judged; keep it that way.
   `eval/session_eval.py` still hides scam payments from the engine's ledger (an old workaround)
   until it is re-run without it.
10. **Warn, never block, on the phone.** Phone-side alerts (watcher notifications, the Home card)
    only warn. Interruptions at payment time come from the engine. They appear only once there is a
    pattern (episode ACTIVE, or the server answered `high`); a WATCHING episode (one small credit
    from a stranger) is recorded and reported, but not shown.
11. **Humans approve discovered mules.** A graph-discovered suspect affects live scoring only after
    two different analysts approve it (`graph/discover.py`).
12. **Commits and PRs carry only the human author.** No AI co-author or attribution lines.
13. **No LLM in the decision path.** Rules (and later LightGBM) decide tiers. An LLM may only draft
    analyst summaries or playbook descriptions from structured evidence.
14. **Age, gender and language are never model or rule inputs.** They are used only to audit
    outcomes (e.g. false alarms per age band in `eval/session_eval.py`).

## Conventions
- Features are defined once, in `backend/features/build.py` (with its `FAMILY` map). Training,
  replay and evaluation import it; never re-implement a feature. Phone-side episode logic is defined
  once in `contracts/episode_rules.json`; Pay-screen session fields are defined in
  `contracts/README.md` and the simulator (`sim/session.py`) must measure them the same way.
- Every decision writes an evidence record, off the latency path.
- Every user-facing message has English and Hindi strings.
- Add a test with every rule, feature or reason code.
- Python: type hints; Pydantic models mirror `contracts/`.

## Contract (current: c2-0.8)
- `POST /v1/decide`: request `request_id, user_id, payee_upi_id (plain), amount_inr, entry_method,
  payer_says?, ts (+05:30), context {call, device, lures, episode, session, payee_in_contacts, device_verdict}`; response tier T0 to
  T4, reasons, `message_key`, `pause_minutes`, `playbook`, `stage`, `evidence_id`, `fail_open`,
  `threat` (c2-0.8).
- `context.device_verdict` (c2-0.8): the phone's own model verdict (`model_version`, `fraud_class`,
  `level`, family scores `c`/`l`/`b`, `reasons`). The phone runs `device_model.json` on everything
  it measured (its own payment history and every lure hit too) and sends only this conclusion.
  `c`/`l`/`b` are features of the same families (`device_c` C, `device_l` L, `device_b` B, never a
  reason), so the verdict cannot light a family on its own; `fraud_class` names the scam only when
  the server's playbook matcher found none.
- Response `threat` (c2-0.8): `score` 0–100, `level` (`none`/`low`/`medium`/`high`/`critical` for
  T0 to T4), `fraud_type` (the playbook; `null` for T0), `families` (lit families). `null` on fail
  open. See rule 4.
- `context.session` (c2-0.5): how the Pay screen of this payment was used (digit rhythm, paste
  after an app switch, app switches, time away, abandoned tries), counts and rounded seconds only;
  `null` when "Session checks" is off. All its signs are family B, so they can never interrupt on
  their own. `call.speaker_on` (c2-0.5) only adds weight to the C call signal. Field meanings are
  in `contracts/README.md`; phone and simulator must measure them the same way.
- `context.payee_in_contacts` (c2-0.7): yes/no/null, the payee (paid by mobile number, or a
  mobile-style UPI ID) is a saved contact. Never names or numbers. It only softens "new payee" (R6);
  a payee is *known* when the bank's history or a completed payment here (c2-0.6) says so.
- `POST /v1/payment/outcome` (c2-0.6): after the PIN or Cancel, the phone reports `completed` or
  `cancelled` for a payment, in the background. The payment id is the `/v1/decide` `request_id`;
  it is kept in the phone's history, the payment graph (`GET /v1/payments/{id}`) and the console.
- `POST /v1/episode`: the phone's task-scam watcher reports a state change with at most 10
  accounts, only with "Share suspicious accounts" on; reply `episode_risk` in words.
- Reason codes R1 to R12 (`R10_bait_then_pay`, `R11_ring_link`, `R12_coached_session`).
  `context.money` (c2-0.2) was removed in c2-0.4 and now gets 422; c2-0.1 and c2-0.3 to c2-0.7
  requests still validate.
- Schemas: `contracts/c2_decide.schema.json`, `contracts/c2_episode.schema.json`,
  `contracts/c2_payment_outcome.schema.json`. Shared episode
  test cases: `contracts/examples/episode_vectors.json` (run by Python and Kotlin tests).

## Run it (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -BaitPump   # whole stack + demo data
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Stop
```

The script makes `.venv`, generates `data/*.parquet` if missing (`-Regenerate` to rebuild), builds
the console if `console/dist/` is missing (`-BuildConsole` to rebuild; needs Node 18+), starts mock
intel on 8001 and the API with `--demo-clock`, pumps the demo mule (and with `-BaitPump` the bait
account), runs `adb reverse` (8000, and 8001 for the mock NPCI's UPI number mapper) and seeds
Meena's contacts into the phone's address book (`python -m sim.contacts --data data --adb`). If laptop port 8000 is taken, the API moves to the next free port
(8002 on Manjeet's laptop) and the phone's 8000 is mapped to it. macOS / Linux: `sh scripts/dev.sh`.

- Analyst console: `http://127.0.0.1:<api port>/console/` (dev server: `cd console && npm run dev`,
  port 5173). `/console/graph` redirects there.
- Python: `PYTHONPATH=backend;ml;data` (PowerShell) or `backend:ml:data`; set
  `PYTHONIOENCODING=utf-8` on Windows (the simulator prints ₹).
- Use `127.0.0.1`, not `localhost`, between services on Windows (IPv6 first adds ~200 ms).

## Tests

```bash
.venv/Scripts/python -m pytest backend/tests -q          # 157 tests (with PYTHONPATH set)
.venv/Scripts/python -m pytest ml/tests -q               # ML (needs: pip install -r ml/requirements.txt)
cd android && JAVA_HOME="/c/Program Files/Android/Android Studio/jbr" sh ./gradlew assembleDebug testDebugUnitTest   # 62 tests
```

Android needs Android Studio's JBR (Java 25): AGP 9.4.1 with built-in Kotlin, Gradle 9.8.1,
compileSdk 37, targetSdk 34, minSdk 26. The system `java` (Java 8) does not work.
`android/local.properties` (not committed) needs `sdk.dir` and optionally `API_BASE_URL`
(or set `ANDROID_HOME` to the SDK folder instead).

Kotlin tests that only need plain Kotlin (`ContractTest`, `SignalsTest`, `DeviceModelTest`,
`DeviceFeaturesTest`, `LureModelTest`) read `contracts/` and `ml/model/artifacts/` from the repo, so
a retrained model or a contract change breaks them instead of the demo.

Functional backend tests that need the payee to be recognised should not depend on machine speed:
use generous time limits like the `engine` fixture in `test_session.py` (intel budget and
`api.main.DEADLINE_S` raised). Keep the real 150 ms / 200 ms limits only in timing tests. With the
real limits, a busy machine turns the demo mule into "unknown payee" and a T3 test sees T1.

Evaluation (slow, minutes): `python -m eval.episode_eval`, `python -m eval.graph_eval`,
`python -m eval.session_eval` write `data/eval/*_results.json` and `data/eval/charts/*.svg`
(results are committed). Latest episode run (history-only baselines), fresh mules, scam rupees
interrupted: 54.2% / 65.3% / 90.5% across its setups. The coached-session result (fresh mules
62.5% → 67.8%, no new false alarms) predates that fix. `data/eval/realism/` compares the simulator
with an older prototype, PaySim and Xente (`FINDINGS.md`): the team data is too easy (e.g. mule
account age alone gives AUC 0.994) and has few scam payments, so read eval numbers with that in
mind. Offline episode features for training:
`python -m features.offline_episode --data data/`.

## Map

**Backend** (`backend/`)
- `api/main.py`: decide path tracker → intel (150 ms, parallel) → features → score → policy →
  evidence; also `/v1/episode`, `/v1/cases`, `/v1/alerts`, `/v1/eval`, `/v1/graph/*`,
  `/v1/trusted`, `/v1/evidence/*`, `/sim/reset`, and the console mount.
- Scorer: `python -m api.main --scorer ml` (default) runs the trained model `ml/model/predict.py`
  (`lgbm-*`, needs LightGBM from `ml/requirements.txt`; the dev scripts install it) and falls back
  to `api/rule_scorer.py` (`rules-0.7`, hand-set) if it cannot load; `--scorer rules` forces the
  rules. `GET /health` reports which one is running.
- `api/policy.py` + `policy.yaml` (incl. the `session:` thresholds; the alert budget never
  applies when the payee family is lit), `api/intel.py` (a slow lookup finishes in the background
  and its answer is reused for 30 min as `cached:<status>`, so a slow source never flips the tier),
  `api/episode_events.py`.
- `features/`: `build.py` (one feature builder; `FAMILY` map), `tracker.py` (stages S0 to S5),
  `episode.py` + `bank_sms.py` (same state machine as the phone), `session.py` (Pay-screen numbers
  → coached-session signs), `user_baseline.py` (history before the payment only),
  `offline_episode.py`.
- `graph/store.py` (crowd signal, money loop, story for the console, one `Payment` per payment id
  with its decision and outcome), `graph/discover.py`.
- `intel_mock/`: NPCI, banks (`payee-risk`, `payee-flows`, visibility none / partner / dpip),
  DoT FRI, I4C registry, with real lags and blind spots.
- `schema/events.py` is the source of truth for the contract; `evidence/store.py` is the
  hash-chained vault.

**Android** (`android/app/src/main/java/com/ruko/demo/`)
- `net/`: models, request builder, Retrofit client. `signals/`: calls, device, lure scorer (`assets/lure.json`), bank
  SMS parser, `DeviceModel.kt` + `DeviceFeatures.kt` + `DeviceVerdict.kt` (on-device model,
  `assets/device_model.json`, c2-0.8; must match `ml/model/artifacts/`), `ContactBook.kt` (payee in contacts, c2-0.7), `SessionTracker.kt` (Pay-screen session, c2-0.5; tested by `SessionTrackerTest`). `shield/`: background task-scam watcher (SMS receiver, workers, episode, notifier,
  encrypted event queue). `data/`: encrypted account store, `PaymentHistory.kt` (payment history: id, decision, outcome; 90 days). `ui/`: screens incl. `HistoryScreen.kt` (bottom-bar History), `ContactsScreen.kt` (To Mobile Number), debug panel (long press
  "Cache Pay" on Home), `EpisodeCard.kt`. Hindi strings in `res/values-hi/`.

**Data** (`data/`)
- `sim/generate.py` builds the world (`sim/extras.py` adds hard negatives on a separate random
  stream so the core world and demo identities never shift; `sim/session.py` adds Pay-screen
  sessions the same way). `sim/mule_pump.py`, `sim/bait_pump.py` prepare live demos; `sim/contacts.py` builds a demo user's
  address book (people really paid, plus a few never paid) and seeds it on the emulator.
- `eval/`: `episode_eval.py`, `graph_eval.py`, `session_eval.py` (ground truth used for scoring
  only), `realism/` (`compare.py`, `FINDINGS.md`); `redteam/` is empty. `data/manifest.json` and `data/sample_v0/` are committed;
  `data/*.parquet` is git ignored.

## Demo identities
- User `u_demo_meena` (Cache Pay passcode on the emulator: 482915). Contacts: Sai Das, Aarav Yadav,
  Rahul Kumar, Aarav Mishra (paid before), Arjun Pandey (sends her money), Kiran Sinha, Sunil Yadav,
  Nikhil Singh (never paid).
- Demo mule `secure.settlement.desk@okaxis`; kirana `shreeganesh.kirana@okaxis`.
- Meena scenario (call + remote access + ₹95,000, "government") → T3 `t3_digital_arrest`.
- Bait pump `--check` → T3 `t3_task_job` with R10, R3, R9, R11.
- Coached example (`contracts/examples/decide_request_coached.json`) → T3 or T4 with R12 and R1.

## Not built yet
Full T1 to T4 alert screens (the decision screen shows the tier, threat level, scam type, score,
reasons and the next step, but not the T3 pause or trusted-contact flow), feedback endpoint and
buttons, new-pattern discovery (only the six known playbooks are named), red-team tests (`data/eval/redteam/` is empty), `docker-compose.yml` / `Makefile`,
Redis / Postgres, signed release APK. SMS permissions are a demo-build feature only (Play Store
does not allow them for a payment app). `ml/PLAN.md` tracks what is done in `ml/` and what it needs
from other folders (its §12).

`.claude/SPEC.md` (local, not in git) is the original long spec. Parts of it are older than the
code (e.g. its endpoint names and repo layout); when they disagree, this file and the code win.
