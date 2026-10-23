# ml/ build plan: server model, on-device model, synthetic data

Owner: Diwansu Pilania. Everything here lives in `ml/`. Changes other folders need are in §12.

## 0. Status
Branch `feature/threat-analysis` on top of `main` 5fc81a7: contract c2-0.8, engine rules-0.7, server model lgbm-0.2, device model device-0.1.

| | Done | Check |
|---|---|---|
| M0 | `.venv` + `ml/requirements.txt` | `pytest ml/tests backend/tests`: 50 ML + 166 backend passed, pandas 3.0; Kotlin model and contract tests (33) compiled and run here with IntelliJ's kotlinc |
| M2 | `dataset/phone_view.py` (the app's exact readings: call, device, lures, session, payee in contacts), `dataset/episodes.py` (the backend's own episode state machine + episode events), `dataset/graph.py`, `dataset/replay.py` (incl. payment outcomes, c2-0.6), `dataset/worlds.py` | replay == live engine, feature for feature, with episode events, outcomes and the payment graph (`test_replay_parity.py`) |
| M3 | `model/metrics.py` (deck slide 7), `model/experiments.py` (ablation incl. the current rules engine) | runs; the world is still too easy to rank models (§12) |
| M4a | `model/lure_corpus.py` (3,643 synthetic EN / HI / Hinglish SMS), `model/lure_train.py` → `artifacts/lure.json` + `lure_vectors.json` | unseen templates: 84% scams caught, 0 false alarms (with the §12 Manjeet rule) |
| M5 | `model/train.py` (LightGBM per family C/L/P/B + isotonic + logistic combiner), `model/predict.py` (`score(features)`, drop-in for `api/rule_scorer.score`), `model/evaluate.py`, `serve.py`, `artifacts/v0/server_model.json` (lgbm-0.2: also reads the phone's verdict scores `device_c/l/b`, hidden on 30% of training rows so a request without a verdict is still judged on its raw signals) | `test_predict.py`: Meena T3/T4, kirana T0, rule 1 (unknown never safer), p95 < 10 ms; held-out world below |
| M4b | `model/device/train.py` (3 small LightGBM families C/L/B on the phone-side features + isotonic + combiner + local warning), `model/device/score.py` (pure-Python JSON scorer), `model/device/verdict.py` (fraud class + level), `model/device/feature_vectors.py`, `artifacts/v0/device_model.json` (device-0.1, trained on `dev_device`) + `device_vectors.json` + `device_feature_vectors.json`; the Kotlin side now lives in the app (`android/.../signals/DeviceModel.kt`, `DeviceFeatures.kt`, `DeviceVerdict.kt`) | JSON == LightGBM to 1e-12 incl. all missing-value modes (`test_device_model.py`); Kotlin features, scores and verdicts equal Python's on 56 real payments and 13 verdict cases; held out: PR-AUC 0.980, local warning on 64% of scam payments, 0 per 1,000 legit |
| M4c | Threat analysis (c2-0.8): the phone sends only its verdict (`context.device_verdict`: fraud class, level, C/L/B scores, reasons); the server adds intel, MuleHunter, FRI, the registry and the payment graph and answers `threat` (score 0-100, level, scam type, lit families); the app shows it | `backend/tests/test_threat.py`, `ContractTest`, `test_replay_parity.py` (replayed requests carry the verdict) |
| M6 | `model/tune.py` -> `artifacts/v0/policy_proposal_rules-0.7.yaml`, `policy_proposal_lgbm-0.2.yaml` | tuned on `dev_calib`, checked on `dev_test` (below); `test_tune.py` |

**On a held-out world** (`dev_test`, seed 12, never used in training; all through backend/api/policy.py
with today's thresholds; `ml/runs/evaluate_dev_test.json`):

| | rules-0.7 | lgbm-0.1 | lgbm-0.2 | lgbm-0.2, no phone sends a verdict | deck target |
|---|---|---|---|---|---|
| Scam payments interrupted (T2+) | 85.1% | 94.7% | 93.7% | 94.7% | |
| Scam rupees interrupted | 90.5% | 98.5% | 98.3% | 98.5% | |
| Paused (T3+) | 66.7% | 93.9% | 93.5% | 93.5% | |
| Caught at the journey's first payment | 83.4% | 92.1% | 89.4% | 92.1% | >= 70% |
| Legit interruptions per 1,000 | 6.09 | 0.00 | 0.00 | 0.00 | <= 5 |
| T3+ precision | 49.5% | 100% | 100% | 100% | >= 1 in 4 |

The phone's verdict adds nothing measurable here (on `dev_calib` too: 93.6% vs 94.1%), because in the
simulator it is computed from the same readings the server already gets. On a real phone it also
carries what never leaves it (the phone's own payment history, every lure hit, sub-threshold lures).
Without verdict dropout, lgbm-0.2 gave Meena's digital arrest T0 when the request had no verdict.

Read these with `data/eval/realism/FINDINGS.md` in mind: the simulated world is too easy (mule account
age alone separates mules), so the ML numbers mostly show that the pipeline works end to end. They
will drop when the world gets realistic; retrain with the same commands then.

**Threshold tuning** (`python -m model.tune`, legit interruptions <= 4 per 1,000, T3+ precision >= 1 in 4;
tuned on `dev_calib`, checked on `dev_test`; `ml/runs/tune_dev_calib.json`):

| Held-out world | T1/T2/T3 min_p | Scam rupees interrupted | Caught at 1st payment | Legit interruptions / 1,000 | T3+ precision |
|---|---|---|---|---|---|
| rules-0.7, today's policy.yaml | 0.30 / 0.50 / 0.75 | 90.5% | 83.4% | **6.09** (over the deck's 5) | 49.5% |
| rules-0.7, proposed | 0.20 / 0.60 / 0.70 | 82.7% | 76.8% | **3.93** | 37.4% |
| lgbm-0.2, today's policy.yaml | 0.30 / 0.50 / 0.75 | 98.3% | 89.4% | 0.00 | 100% |
| lgbm-0.2, proposed | 0.20 / 0.30 / 0.50 | 98.4% | 92.1% | 0.00 | 100% |

The API now runs lgbm-0.2 by default (`--scorer ml`), so today's thresholds already stay inside the
alert budget; the rules proposal matters only when the engine falls back to rules. The lgbm-0.2 proposal
only reflects how easy the world still is; re-tune on the realistic world before anyone adopts it.

Model notes:
- A family score is the family's likelihood ratio squashed as LR / (LR + 10): the family is lit (>= 0.5)
  when its evidence alone makes a scam at least 10x likelier. Out of fold, families light on 0.2-0.6% of
  legit payments.
- `story_mismatch` and the caller lookups only run in some situations ("who are you paying?" for typed
  payments from Rs 2,000; caller checks for incoming SIM calls from strangers). Unknown and "no finding"
  are both 0 for them (`model.data.POSITIVE_ONLY`), as in the rules engine. Without this, the payee model
  learned "not asked = safe" (40% of rows, up to 0.98 lower p_scam).
- Reasons: taken in turns across lit families (strongest family first): each family's best reason, then each one's second best, and so on (max 4).
- The playbook still comes from the rules engine's matcher (`api/rule_scorer._playbook`); when it finds
  none, the phone's `fraud_class` names the scam (c2-0.8).

Synthetic worlds come from `data/sim` (Nikunj; pay-screen sessions by Jagdeep). `ml/` only adds what
the world does not say: which Scam Shield groups each phone has on (`dataset/assumptions.yaml`) and the
"who are you paying?" answers, plus the lure texts.

Known issue: replaying a 5,000-user world (360k payments) takes about 6 minutes, and it slows as the
payment graph grows (`GraphStore.account_signals` scans every bait sender and every payer of busy shops).

Commands (repo root, `PYTHONPATH=backend;ml;data`, `.venv\Scripts\python`):
```
python -m dataset.worlds dev                      # world -> ml/data/worlds/dev
python -m dataset.replay --data ml/data/worlds/dev --out ml/data/tables/dev.parquet
python -m model.experiments --table ml/data/tables/dev.parquet
python -m model.train --table ml/data/tables/dev.parquet --out ml/model/artifacts/v0
python -m model.evaluate --table ml/data/tables/dev_test.parquet   # a world the model never saw
python -m serve --data data --port 8000 --demo-clock                # the engine with the ML scorer
python -m dataset.worlds dev_device && python -m dataset.replay --data ml/data/worlds/dev_device --out ml/data/tables/dev_device.parquet
python -m model.device.train --table ml/data/tables/dev_device.parquet --test ml/data/tables/dev_test.parquet
python -m model.device.feature_vectors             # Kotlin parity cases; then copy device_model.json + lure.json to android assets
python -m model.tune --table ml/data/tables/dev_calib.parquet --test ml/data/tables/dev_test.parquet
python -m model.lure_corpus && python -m model.lure_train
python -m pytest ml/tests
```

## 1. What we build

| | Server model | On-device model |
|---|---|---|
| Question | Is this payment going to a scam? | Is someone working on this person right now? |
| Runs | Risk engine, at `POST /v1/decide` | Phone, at the Pay tap and in the background |
| Sees | Payee intel (NPCI, bank, MuleHunter, registry, FRI), the payer's history at the PSP, the request, the phone's summary | SMS (lures, bank alerts), calls, apps, the user's own payments in every UPI app, behaviour on the pay screen |
| Gives | `p_scam` + family scores C/L/P/B + reasons + playbook (same shape as `api/rule_scorer.score`) | Device family scores C/L/B + reason codes + local warnings |
| Format | LightGBM + isotonic calibration, Python | JSON weights scored in plain Kotlin (no TFLite) |

Plus: the lure classifier (`lure.json`), weekly pattern discovery, threshold tuning, and the
evaluation the Round 1 deck promised (slide 7).

**Rules that never change** (deck slides 5–8, contracts/README):
- Raw SMS text, call audio, contacts and per-transaction history never leave the phone. Only scores,
  reason codes and the existing derived flags are sent.
- `None` means unknown, never safe. A missing input must never lower the score (tested, §9).
- Interruptions need 2+ independent families. The phone can raise risk but never vouch for safety;
  the server alone (P + B) must still reach T3 for a mule.
- Device B and server B are **one** family B (combined, never counted twice).
- Age, gender and language are never model inputs; they are used only to audit outcomes.
- No LLM in the decision path.

## 2. Folder layout

```
ml/
├── PLAN.md                this file
├── requirements.txt       lightgbm, scikit-learn, pandas, pyarrow, numpy, scipy, pyyaml, pytest
├── synth/                 synthetic worlds on top of data/sim (data/sim itself is not edited)
│   ├── assumptions.yaml   every invented number, with a comment
│   ├── world.py           build_world() wrapper: sim builders + fixes + extra situations
│   ├── situations.py      extra playbooks and hard negatives
│   ├── device.py          what each phone would see: consents, sessions, SMS coverage
│   ├── lure_corpus.py     -> ml/data/lure_texts.csv (EN / HI / Hinglish, synthetic)
│   └── sms_render.py      bank SMS text from ledger rows (parser tests for Android)
├── dataset/
│   └── replay.py          point-in-time replay -> training table (server + device features, labels)
├── model/
│   ├── server/            payee.py, families.py, combine.py, calibrate.py, reasons.py
│   ├── device/            features.py, train.py, export.py (-> JSON), score_json.py (Python reference)
│   ├── lure_train.py      -> artifacts/lure.json
│   ├── playbooks.py       config-driven matcher (playbooks.yaml), not a trained classifier
│   ├── discovery.py       sequence n-grams + HDBSCAN -> candidate playbooks
│   ├── tune.py            thresholds -> proposed policy.yaml values
│   ├── predict.py         score(features) -> same dict as api/rule_scorer.score
│   ├── MODEL_CARD.md
│   └── artifacts/         v0/, v1/ (committed, small JSON/text models)
├── eval/                  metrics, ablation, fairness, red team, latency -> results.json
├── serve.py               runs the backend engine with the ML scorer (no backend edits)
├── data/                  generated worlds and tables (git ignored except lure_texts.csv)
└── tests/
```

`PYTHONPATH=backend;ml;data` as everywhere else. `.venv` at the repo root (same as `scripts/dev.ps1`).

## 3. Synthetic data (`ml/synth`)

### 3.1 Problems in the current world (checked on `data/sample_v0`)
| Problem | Evidence | Fix in `ml/synth` |
|---|---|---|
| Too few scams | 23 journeys in the 2,000-user world | Scale + boosted-rate training worlds (§3.3) |
| Giveaways | Scam payments: 72% round to ₹100 vs 1% normal; 100% typed vs 3% | Make normal amounts round where people really round (rent, family, fees); some scams non-round, some by QR or saved contact |
| Baseline leak | `Baselines.from_data` uses the whole window: every scam payee is already "known", and the victim's 90-day max is the scam payment | Replay computes every feature as of the payment (§4). Live engine is unaffected (it runs at the data end) |

**Giveaway audit (release gate):** a model trained only on `amount % 100 == 0`, hour and entry
method must score PR-AUC close to the base rate. If not, the world still leaks the label.

### 3.2 Situations to add (on top of the 6 existing playbooks)
- **Emerging playbooks, never in the library or training** (for discovery, §7):
  `relative_emergency` (voice-clone "son in trouble": call from a new number, family story, new
  person payee, urgency), `collect_request` ("enter PIN to receive"), `marketplace_buyer`
  (small test payment then a large one, QR).
- **Hard negatives** promised in the deck but missing: festival gifting (Diwali window, more and
  bigger family/friend transfers), community collections (mandal / society accounts: many payers,
  low repeat, money forwarded to vendors), buying second-hand from a stranger while on the phone with
  them, new landlord deposit, real IT support on AnyDesk while paying a bill, gig workers and small
  sellers getting many small credits, merchant cashback / referral credits, urgent family emergencies.
- **Scammer adaptation** (deck A3, 9 red-team moves): tranche splitting, ending the call before
  paying, brand-new mule, coaching to ignore / tap "I know this person" / answer "family", probing
  with test payments, lure by image or voice note (no text), mass false reports, tampered app
  sending "all clear", new script. Each becomes a journey file every model version must pass.
- **Out of scope** (deck slide 2 / 10): SIM swap, account takeover, card fraud, loan-app
  harassment, federated training.

### 3.3 Worlds
| World | Size | Scam rate | Use |
|---|---|---|---|
| `tiny` | 400 users × 20 days | 5% | unit tests (seconds) |
| `train_*` | 20k × 60, several seeds, knobs randomised | 4% | training (more positives) |
| `test` | 50k × 60 (deck slide 7), unseen seed and knobs | 1% | all reported metrics |
| `heldout` | 20k × 60 | 3% | one library playbook removed + the emerging ones, for discovery |

Generation is about 6 s per 60k user-days today, so `test` takes minutes. It holds every row as a
Python dict, so watch RAM; fall back to 20k × 60 if needed.

### 3.4 What each phone sees (`device.py`)
Per user, fixed for the run (assumptions in `assumptions.yaml`, independent of the scam label):
consents (call signals, app checks, SMS: off by default, so most users have no L), Android ≥ 15
(screen-capture callback), personal pace (some people are always slow).
Per payment attempt: pay-screen session (seconds on screen, UPI ID pasted, amount edits, app sent to
background mid-flow, cancelled attempts in the last 10 min), drawn from coached / not-coached tables.
Bank SMS coverage: which ledger rows reach the phone as parseable alerts (some formats fail to parse).

### 3.5 Lure corpus (`lure_corpus.py` → `ml/data/lure_texts.csv`)
Six tags (`arrest_threat`, `kyc`, `task_job`, `trading_tip`, `refund`, `normal`) × templates ×
slot fillers × English / Hindi (Devanagari) / Hinglish × tricks (spacing, look-alike letters, short
links, emoji). Scam texts are fluent, as AI-written lures are. `normal` includes look-alikes: real
bank OTPs, genuine KYC reminders, courier updates, real job and broker messages. Written from public
I4C / RBI advisories, labelled synthetic. Train/test split **by template**. UCI SMS Spam adds
English `normal` and generic spam if licence allows.

## 4. Training table (`dataset/replay.py`)

One row per payment attempt by an app user, in time order, using **only what existed at that moment**:
- Server features: the same `features.build.build_features()` the live engine uses, fed by
  `StageTracker`, a point-in-time baseline, and the mock intel functions called directly with the
  store's clock set to the payment time (the exact code behind ports 8001/8000).
- Device features: from the user's own earlier events and payments (§6.2), respecting consents.
- Labels: `is_scam`, `stage`, `playbook`, `journey_id`; payee `is_mule` for §5.1.
- Groups for splitting: `user_id`, payee, mule, journey, week.
- Normal payments are subsampled with weights; every scam payment is kept.

**Done when:** for 200 sampled payments, the replayed features equal what the live engine computes
for the same request (parity test).

## 5. Server model (`model/server`, `model/predict.py`)

1. **Payee model** "does this account behave like a mule now?" Rows: (account, time) snapshots,
   label `is_mule`. Many more positives than scam payments. Trained with bank fields blanked at random
   (visibility `none` / `partner` / `dpip`).
2. **Family models** C, L, P, B: one small LightGBM each on its own features only, so each output is
   the family score the policy's corroboration gate needs. C and L use the contract flags plus the
   device scores; B uses PSP history plus the device B score. Device scores are produced out-of-fold
   so the server never trains on device predictions of its own training rows.
3. **Combiner:** logistic regression over family scores (+ a few interactions) → `p_scam`, then
   isotonic calibration.
4. **Reasons:** top contributions inside lit families mapped to R1–R10; **playbook:** config matcher.
5. **Baseline for the ablation:** same LightGBM and tuning, single-transaction features only
   (amount, amount vs history, new payee, entry method, hour, payee age).

`score(features)` returns `{p_scam, family_scores, reasons, playbook, model_version}`, exactly
like `api/rule_scorer.score`. `ml/serve.py` starts the backend app and sets
`app.state.score = model.predict.score` (the swap point at `backend/api/main.py:79`).
Budget: < 10 ms per score.

## 6. On-device model (`model/device`; Kotlin in `android/.../signals/`)

### 6.1 Parts
| Part | Type | Output |
|---|---|---|
| Lure classifier | char n-gram TF-IDF + logistic regression → `lure.json` (format already in `LureScorer.kt`) | tag + confidence |
| C model | small GBDT (≤ 50 trees, depth ≤ 4) | C_dev |
| L model | small GBDT over lure hits + bait credits | L_dev |
| B model | small GBDT over own money history + pay-screen session | B_dev |
| Combiner | logistic regression | device `p` + top reasons, local warning level |

### 6.2 Features (computed on the phone, never sent)
- **C:** call active / unknown / VoIP / incoming, minutes on call, minutes since call ended, foreign
  number (SIM calls), remote app installed minutes ago, screen captured, untrusted accessibility,
  sideloaded in 24 h.
- **L:** best confidence per scam tag in 72 h, minutes since best lure, scam messages in 72 h, bait
  credits and bait ₹ in 72 h and in the 30-day episode.
- **B:** amount ÷ own 95th percentile and ÷ own max (90 days, all UPI apps via bank SMS), never paid
  this payee, typed, payments in last 1 h / 24 h, new payees in 72 h, escalation steps including this
  one, sent ÷ received in 72 h, how unusual this hour is for this user, history size (cold start:
  under 10 payments → personal features null); seconds on pay screen, pasted UPI ID, amount edits,
  app backgrounded mid-flow, cancelled attempts in 10 min.

### 6.3 Export and parity
- LightGBM `dump_model()` → compact JSON trees; logistic layers as weights. One file:
  `artifacts/v1/device_model.json` (target < 500 KB, < 20 ms on a mid-range phone).
- `model/device/score_json.py` scores from the JSON only; it must equal the trained model.
- `DeviceModel.kt` + `device_vectors.json`, `DeviceFeatures.kt` + `DeviceVerdict.kt` +
  `device_feature_vectors.json` (readings → features → verdict). Python and Kotlin must agree to 1e-6. Same for `lure.json` with `lure_vectors.json`.
- Lure vocabulary drops n-grams containing characters outside the Basic Multilingual Plane (emoji),
  which Kotlin's UTF-16 strings would split differently.

### 6.4 What the phone does with it
Sends only its verdict with the request (`context.device_verdict`, c2-0.8): fraud class (rule order in
`model/device/verdict.py`), level, the C/L/B family scores and up to 3 reasons. The server treats the
scores as features of the same families, so the verdict can never light a family on its own. Shows
gentle local warnings by itself, including offline. Never blocks a payment.

## 7. Pattern discovery (`model/discovery.py`)
Sessions scored ≥ T1 (or reported later) that match no playbook → token sequences such as
`CALL_UNKNOWN_VOIP → LURE_KYC → SIDELOAD → ACCESSIBILITY → PAY_NEW_TYPED_6x → PAY_REPEAT` with
time-gap buckets → TF-IDF over token n-grams → HDBSCAN (`sklearn.cluster.HDBSCAN`) → candidate
playbook cards (typical sequence, size, weekly growth, top tokens, linked payees) for two-person
analyst approval, then config. **Proof:** on the `heldout` world, report days and sessions until each
held-out / emerging playbook forms a cluster, and cluster purity.

## 8. Thresholds (`model/tune.py`)
Tune on legitimate payments of the `test` world so interruptions (T2+) stay ≤ 5 per 1,000; report
catch rate at that budget. Propose `policy.yaml` values and the per-user T3 amount floor. Align T3 with
the deck (0.75+ with 3+ families, or live call + mule-like payee) — currently `policy.yaml` asks 2.

## 9. Evaluation and tests (`ml/eval`, `ml/tests`)
Deck targets (slide 7), all on the `test` world:
| Metric | Target | Note |
|---|---|---|
| Pre-loss catch: journeys at T2+ at their first scam payment | ≥ 70% | per playbook too |
| Interruptions per 1,000 legit payments | ≤ 5 | |
| T3+ precision | ≥ 1 in 4 | depends on prevalence: report the curve |
| False-positive gap by age band, language, merchant type | ≤ 1.5× | check legit 60+ users and community collections |
| p95 decision latency | < 150 ms | ML share < 10 ms |
Plus: ablation (transaction-only vs RUKO at the same budget), mules flagged days before registry
listing, PR-AUC, calibration plot, red-team journeys, giveaway audit, and a property test that a
`None` input never lowers `p_scam`. Results → `ml/eval/results.json` for Nikunj's dashboard.
The backend's demo stories (`backend/tests/test_decide.py`: Meena T3, kirana T0, split tranches)
must pass with the ML scorer swapped in.

## 10. Model card
`model/MODEL_CARD.md`: data (synthetic, assumptions file), intended use, families, metrics with
prevalence, fairness slices, known gaps, versioning.

## 11. Milestones
| # | Deliverable | Done when |
|---|---|---|
| M0 | venv, `ml/requirements.txt`, `ml/tests` running | `pytest ml/tests` green |
| M1 | `ml/synth` worlds + assumptions + lure corpus | giveaway audit passes; worlds build |
| M2 | `dataset/replay.py` training table | replay ↔ live parity test passes |
| M3 | transaction-only baseline + metrics harness | first metrics (deck H12 checkpoint) |
| M4 | `lure.json` + device models → JSON + Kotlin reference + vectors | Python ↔ JSON parity; vectors written |
| M5 | server models + `predict.score` + `serve.py` | backend demo stories pass with ML scorer |
| M6 | `tune.py` → proposed thresholds | ≤ 5 interruptions / 1,000 on `test` |
| M7 | discovery + held-out demo | emerging playbooks found, timings reported |
| M8 | full evaluation + model card | `results.json` with every metric in §9 |

## 12. Needed from other owners
Measured on the `dev` world (5,000 users × 60 days, simulator as of 372aa73, `ml/runs/experiments_dev.json`).

**Nikunj (data/sim): the world is still too easy to show RUKO's value.** Every model scores 95–100%
(transaction-only PR-AUC 0.95; today's stack with lists 0.998). `data/eval/realism/FINDINGS.md` reaches
the same verdict, and its fix list (mule account age, scam-only phone signals, amounts, too few
positives, uniform normal life) covers most of what ML needs. In addition:
1. **Mules are listed before victims pay:** 95% of scam payments go to a payee already on the I4C
   registry, because mules are reused for the whole 60 days. Make mules short-lived (used for a few
   days, then dropped) with new ones appearing all the time, so most scam payments reach accounts no
   list knows yet (the deck's Gap 2).
2. **Session leak (`data/sim/session.py`, with Jagdeep):** the "dictated amount" sign is on for 41% of
   scam payments and 0.0% of normal ones, because a legit user's digit rhythm never breaks. Add legit
   slow or dictated typing: an amount read off a bill or invoice, a family member saying it, typing
   while distracted.
3. Typed payments of ₹2,000 or more to new payees in normal life (landlord deposit, second-hand
   purchase, contractor, school fees). Today "the app asked who you are paying" is itself a scam flag.
4. The 9 red-team journeys from deck page A3.
5. **Done when** `python -m model.experiments` shows the giveaway set near the base rate and
   transaction-only clearly below RUKO.

**Jagdeep (backend):**
1. Done: baselines use only earlier payments (372aa73), and `CLAUDE.md` now says so.
2. Done in `feature/threat-analysis` (rules-0.7): an active VoIP call with unknown contact status counts as an
   unknown caller in the tracker and R1. Was: **The tracker missed WhatsApp/VoIP scam calls.** `features/tracker.py` counts contact only when
   `caller_in_contacts is False`, and `rule_scorer` R1 needs `caller_unknown`. A real phone sends `null`
   for VoIP calls and outgoing SIM calls (ContextSensor never gets those numbers). In the replay, 29% of
   scam payments stay at stage S0. Treat an active VoIP call (or a call with unknown contact status) as
   possible contact.
3. **Evaluation realism:** `data/eval/episode_eval.py` `phone_context` gives VoIP calls a caller number
   and contact status, and sends every lure regardless of the 0.6 threshold. The app can do neither,
   so evaluation numbers for call scams are optimistic. `ml/dataset/phone_view.py` follows the app
   (tested) and can be reused.
4. **The payment graph scans everything on every `/v1/decide`:** `GraphStore.expire` walks every edge
   and every payment, `paid_before` (c2-0.6) every payment into the payee, and `account_signals` every
   bait sender and every payer of the payee. Fine for a demo, but not at the deck's 3,000 decisions/s.
   `ml/dataset/graph.py` keeps heaps of expiry times and an index of completed payments, and gives
   identical results (`ml/tests/test_graph_expiry.py`); `account_signals` still needs an index.
5. Done in `feature/threat-analysis` (c2-0.8): `context.device_verdict`, `device_c/l/b` features, response
   `threat`. Please review.
6. Done in `feature/threat-analysis`: `--scorer ml|rules` (default ml, falls back to rules), `/health`
   reports the active scorer.
7. `policy.yaml`: `ml/model/artifacts/v0/policy_proposal_rules-0.7.yaml` (T1/T2/T3 = 0.20 / 0.60 / 0.70)
   brings today's engine from 6.09 to 3.93 legit interruptions per 1,000 on a held-out world (deck: at most 5),
   at about 10 points fewer scam rupees interrupted. Only the tier numbers change. The deck's T3 rule
   (3+ families, or a live call + mule-like payee) still differs from `policy.yaml` (2 families); that is a
   product call, not a tuning one.

**Manjeet (android):** items 1-5 of the old list (lure.json asset, scam-tag rule, Unicode split, lure
vectors test, on-device model) are done in `feature/threat-analysis`, plus the device features, the
verdict in the request and the threat on the decision screen. Please review, and run
`gradlew assembleDebug testDebugUnitTest`: the pure-Kotlin parts were compiled and tested here, but
`Signals.kt`, `RukoApp.kt`, `PaymentViewModel.kt` and `DecisionScreen.kt` need the Android SDK.

**Deck wording:** slide 4 says Meena is "on a WhatsApp call ... with a number not in her contacts".
The phone cannot know that for a WhatsApp call. It knows that a VoIP call is active and how long it
has lasted.
