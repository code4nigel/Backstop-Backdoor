# Backstop — AI Revenue Recovery Agent

**Backstop diagnoses why a payment failed, calculates the economically right response, and recovers the money — safely, even if the system crashes mid-batch.**

Built for the AI Revenue Recovery challenge. Every number in this README is reproducible by running the scripts below on the same data.

---

## The problem

Revenue loss from failed payments rarely happens in one clean step — a card expires, a bank flags a transaction, a subscription retry fails silently. Most recovery systems just blindly retry everything, which is often the *wrong* fix: an expired card will never succeed on retry no matter how many times you try it.

Backstop diagnoses the actual root cause, picks the intervention with the highest expected economic value, executes it through a crash-safe ledger, and drafts the customer-facing message — with hardcoded compliance rules and a full audit trail throughout.

---

## Architecture

```
Failed transaction
      │
      ▼
Diagnosis Engine          engine/diagnosis.py
   - deterministic rules first (e.g. card already expired)
   - LightGBM classifier otherwise, calibration-checked (not just accuracy)
   - low confidence → forced human escalation, never guessed
      │
      ▼
Recovery Value Model       engine/recovery_value.py
   - EV = P(recovery | root_cause, intervention) × revenue
          − intervention cost − expected discount − risk penalty
   - ranks every intervention, agent picks the max-EV option
      │
      ▼
Policy Engine              engine/policy.py
   - hardcoded stopping rules: max attempts, do-not-contact, confidence floor
   - NOT model-decided — compliance logic must be deterministic
      │
      ▼
Recovery Ledger            engine/ledger.py
   - every action = idempotency_key(customer, invoice, attempt, action)
   - PENDING → EXECUTED, durable in SQLite (WAL mode)
   - re-running an already-EXECUTED action is a proven no-op, not a re-attempt
   - the transition log IS the audit trail
      │
      ▼
Message Drafter            engine/message_drafter.py
   - Google Gemini drafts the customer-facing message (English or Hinglish)
   - the ONLY place an LLM is used — it never decides WHAT to do, only
     HOW to phrase what's already been decided
   - graceful template fallback if the API is unavailable
```

**A separate, real-data fraud model** (`engine/fraud_model.py`) is trained and validated independently on the ULB Credit Card Fraud dataset — see "On the data" below.

---

## AI judgment — the design decisions, explicit

| Stage | Technology | Why |
|---|---|---|
| Root-cause diagnosis | **LightGBM** (gradient-boosted trees) + deterministic rules | Supervised learning problem with ground truth — needs to be fast, cheap, and auditable. An LLM would be slower and non-auditable for no benefit. |
| Intervention selection | **Explicit Expected Value calculation** | The agent maximizes a calculated economic value, not an LLM's stylistic preference. Human escalation wins when the math says so — not because a model "felt like it." |
| Customer message drafting | **Google Gemini** | The one place language generation and tone genuinely add value — including natural Hinglish. |
| Stopping rules (max retries, do-not-contact) | **Hardcoded, deterministic** | Compliance logic must never be a probabilistic guess an LLM could argue itself out of. |
| Crash safety | **Idempotent state machine (SQLite, WAL mode)** | Not AI at all — a durable engineering guarantee that a naive recovery agent typically lacks entirely. |

---

## Setup

```bash
python -m venv venv
venv\Scripts\activate          # (Windows) — source venv/bin/activate on Mac/Linux

python -m pip install lightgbm scikit-learn pandas numpy flask flask-cors ^
    google-genai python-dotenv imbalanced-learn
```

Create a `.env` file in the project root:
```
GEMINI_API_KEY=your_key_here
```
Get a free key at https://aistudio.google.com/apikey. Backstop works without a key too — it falls back to template messages.

For the real fraud model, download `creditcard.csv` from the [ULB Credit Card Fraud dataset on Kaggle](https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud) and place it at `data/creditcard.csv`.

## Run order

```bash
# 1. Generate synthetic transactions (documented ground-truth mechanisms, see file header)
python data\generate_data.py

# 2. Train and calibrate the diagnosis model
python engine\diagnosis.py

# 3. Train and validate the real-data fraud model
python -m engine.fraud_model

# 4. Run the full batch — agent vs. naive-retry vs. do-nothing
python -m engine.batch_runner

# 5. THE CRASH DEMO — run it, kill it (Ctrl+C) partway through, run the exact
#    same command again. Watch already-done actions get SKIPPED, not redone.
python -m engine.crash_demo

# 6. Start the API (dashboard reads from this)
python api\app.py

# 7. In a second terminal, serve the dashboard
cd dashboard
python -m http.server 5500
```

Open `http://localhost:5500` in a browser.

---

## Results (reproducible — re-run `batch_runner.py`)

- Total at-risk revenue in test batch: **₹50.8L**
- Recovered by naive "always retry immediately": **₹5.1L**
- Recovered by Backstop: **₹33.2L**
- **Lift over naive retry: 548%**
- Diagnosis model: 48.3% accuracy on 5 classes (random baseline 20%), from 348 synthetic training examples — small by design, calibration checked rather than assumed
- Fraud model (real data): **PR-AUC 0.778** on 284,807 real transactions, catching **83.7%** of real fraud at a tuned decision threshold

---

## On the data — read this before anyone asks

Backstop uses **two intentionally separate data sources**, and we're upfront about the boundary between them:

1. **Five root causes** (insufficient funds, expired card, bank risk block, 3DS friction, general fraud signal used in diagnosis) use a **controlled, documented synthetic dataset** (`data/generate_data.py`). Outcome-labeled payment-failure data of this kind isn't publicly available — we're not claiming to have discovered real-world patterns from a few thousand synthetic rows. What we're demonstrating is the *architecture*: diagnosis, economic decision-making, and crash-safe execution, built so real historical data could be dropped in without changing the design.

2. **Fraud detection specifically** has a well-known real public dataset, so we trained and validated a separate model (`engine/fraud_model.py`) on the **ULB Credit Card Fraud dataset** — 284,807 real, anonymized European card transactions from 2013. We report precision, recall, and PR-AUC — not accuracy, which is meaningless on a 0.17%-positive-rate dataset — and we explicitly tuned the decision threshold rather than defaulting to 0.5, because a false positive (escalating a legitimate transaction) and a false negative (missing real fraud) have very different real costs.

**Where real data exists, we used it. Where it doesn't, we said so.**

---

## Known limitations (said out loud, not hidden)

- The `P(recovery | root_cause, intervention)` table in `recovery_value.py` is a documented assumption, not learned from real outcome data — because that data doesn't exist publicly. The architecture is built so it can be swapped for a learned table the moment real recovery-outcome data exists.
- SQLite (WAL mode) is used for the ledger for zero-setup durability in this build. The idempotency-key pattern would swap directly onto Postgres or a distributed store in a real deployment — only the storage adapter changes, not the design.
- The diagnosis model's 48.3% accuracy (vs. 20% random baseline) reflects a genuinely small, purpose-built synthetic training set — a real-data equivalent would be expected to improve with more examples.

---

## Project structure

```
revenue-recovery/
├── data/
│   ├── generate_data.py       # synthetic transaction generator
│   ├── transactions.csv       # generated output
│   └── creditcard.csv         # real ULB fraud dataset (download separately)
├── engine/
│   ├── diagnosis.py           # LightGBM + deterministic rules
│   ├── recovery_value.py      # Expected Value model
│   ├── policy.py              # stopping rules + orchestration
│   ├── ledger.py              # idempotent, crash-safe state machine
│   ├── batch_runner.py        # agent vs. baselines comparison
│   ├── crash_demo.py          # live crash-and-resume demonstration
│   ├── message_drafter.py     # Gemini-powered customer messaging
│   └── fraud_model.py         # real-data fraud detection model
├── api/
│   └── app.py                 # Flask API
├── dashboard/
│   └── index.html             # live operations dashboard
└── models/                    # trained models, reports, ledger DB (generated)
```

---

