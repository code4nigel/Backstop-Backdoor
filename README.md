# Backstop — AI Revenue Recovery Agent

**Backstop diagnoses why revenue is lost, computes the economically optimal intervention using Expected Value math, and recovers the money safely — with hard regulatory guardrails and an immutable audit trail.**

Built for real-world payment recovery challenges (tailored for Razorpay and fintech platforms). Backstop covers all **4 critical revenue leaks**: one-off payment declines, abandoned checkouts, recurring mandate renewals, and overdue B2B receivables.

---

## The Problem: Why Naive Recovery Breaks

Most revenue recovery systems are naive scripts that blindly spam retries and messages:
1. **Blind Retries Waste Limits:** An expired card will decline 100% of the time on immediate retry, burning card network retry limits and fatiguing issuing banks.
2. **Bank Outages Spam Customers:** When a bank switch or UPI route drops (e.g., HDFC UPI core banking timeout), naive tools treat 200 failed transactions as individual customer faults, spamming users with urgent links that fail again.
3. **Vanity Recovery vs. Real Lift:** Many recovery tools claim credit for transactions that would have organically self-cured anyway, ignoring channel costs (SMS, WhatsApp, voice) and discounts.
4. **Compliance & Crash Vulnerabilities:** If an automated recovery process crashes mid-batch, it risks double-charging cards or sending duplicate nudges. Furthermore, unconstrained AI bots can make false legal threats or violate RBI quiet hours.

---

## The Backstop Architecture

```
Incoming Revenue Leak (Failed Payment / Cart Drop / Mandate Failure / B2B Invoice)
                           │
                           ▼
[1] Systemic Health Monitor (engine/systemic.py)
    - Real-time rolling window tracking (bank, gateway, upi_app)
    - Route degraded (>15% drop)? ➔ Flag systemic, reroute gateway or hold retry
    - ZERO customer outreach during bank outages (protects brand trust)
                           │
                           ▼
[2] Multi-Leak Diagnosis Suite (engine/diagnosis/)
    - Deterministic priors (e.g. card expired ➔ 100% confidence)
    - Leak-specific classifiers (cards, checkout drop-steps, mandates, B2B POs)
    - Attaches explainable evidence tags to every case
                           │
                           ▼
[3] Cost-Aware Recovery Value Model (engine/recovery_value.py)
    - Net EV = P(recovery) × Revenue − Channel Cost − Expected Discount − Risk Penalty − Human Cost
    - Ranks 11 candidate actions against a do_nothing baseline (EV = ₹0)
    - If action cost exceeds expected recovery, the agent skips
                           │
                           ▼
[4] Deterministic Policy & Compliance Gates (engine/policy.py)
    - Gate 1: TRAI DND / Opt-Out check
    - Gate 2: RBI Quiet Hours (19:00 – 08:00) suppression & morning deferral
    - Gate 3: Dispute & Hardship freeze (immediate halt on "already paid")
    - Gate 4: Frequency caps (max 2 touches / 7 days, max 3 attempts total)
    - Gate 5: Human review queue routing (≥ ₹50,000, low conf < 0.35, discounts > 10%)
                           │
                           ▼
[5] Transactional Outbox & Ledger (engine/execution/outbox.py, engine/ledger.py)
    - Stable idempotency keys: SHA-256(run_id : customer : case : attempt : channel)
    - State flow: PENDING ➔ SENT ➔ CONFIRMED / FAILED / RECONCILING
    - In-flight recovery sweeps ensure crash resilience with ZERO duplicate charges
                           │
                           ▼
[6] Omnichannel Execution & Hinglish Voice (engine/execution/, voice/)
    - WhatsApp, SMS, Email, and interactive Hinglish voice calls
    - Promise-to-Pay (PTP) tracker (kept vs broken state machine)
    - Mid-call simulated WhatsApp payment link generation
    - Google Gemini powers phrasing only; guardrails strictly govern actions
```

---

## AI Judgment — Explicit Architectural Boundaries

| Stage | Technology | Why |
| :--- | :--- | :--- |
| **Systemic Outage Detection** | **Rolling-window statistical monitor** | Identifies issuer/bank downtime in real-time, preventing customer harassment. |
| **Root-Cause Diagnosis** | **LightGBM + Deterministic rule priors** | Fast (<5ms), auditable, and calibrated against structured transaction metadata. |
| **Intervention Selection** | **Cost-Aware Expected Economic Value** | Deterministic math decides the wallet. Human escalation wins when the math says so. |
| **Regulatory Compliance** | **Hardcoded deterministic gates** | Compliance logic (RBI quiet hours, TRAI DND, dispute freeze) must never be an AI guess. |
| **Crash Safety** | **Transactional Outbox (SQLite WAL)** | Durable idempotency keys guarantee zero double charges across process restarts. |
| **Customer Messaging** | **Google Gemini (with template fallback)** | The one place language generation adds value (natural English & Hinglish code-mixing). |

---

## The 4 Revenue Leak Categories

Backstop provides dedicated diagnostic and workflow pipelines for all 4 leak types:

1. **`failed_payment` (~35% of volume):**
   - Channels: UPI, Credit/Debit Cards, Netbanking.
   - Causes: `insufficient_funds`, `expired_card`, `bank_risk_block`, `3ds_friction`, `upi_timeout`, `fraud_flag`.
   - Actions: `retry_delayed` (timed to salary week), `card_update_link`, `reroute_gateway`.
2. **`abandoned_checkout` (~25% of volume):**
   - Drop Steps: Shipping info, coupon apply, payment selection, OTP screen.
   - Causes: `shipping_shock`, `coupon_invalid`, `otp_delay`, `payment_page_crash`.
   - Actions: `reminder_sms`, `whatsapp_nudge` with dynamic discount incentive.
3. **`subscription_mandate` (~20% of volume):**
   - Recurring rails: UPI Autopay, e-Mandate, Standing Instructions.
   - Causes: `mandate_limit_exceeded`, `pre_debit_notification_missed`, `issuer_auth_decline`.
   - Actions: `mandate_retry_sequence` respecting RBI 24-hr pre-debit rules.
4. **`b2b_invoice` (~20% of volume):**
   - Accounts receivable: Net-30/60 enterprise invoices.
   - Causes: `po_number_mismatch`, `invoice_dispute`, `cash_flow_timing`, `wrong_contact`.
   - Actions: Polite reminder $\to$ firm nudge $\to$ interactive call $\to$ human escalation.

---

## Scientific Measurement & True Lift

Backstop eliminates measurement contamination through **Run ID Partitioning** and a **15% Stratified Holdout Control Group**:

- **True Incremental Lift:** Evaluated strictly against the holdout group receiving zero touches (`do_nothing`), subtracting organic self-cure recovery (~5–8% base rate).
- **Net Economics:** Subtracts channel unit costs (SMS ₹3, WhatsApp ₹4, Voice ₹18, Human ₹150) and discounts given.
- **Statistical Rigor:** Computes 95% Confidence Intervals via the Wilson score interval:
  $$\hat{p} \pm 1.96 \sqrt{\frac{\hat{p}(1-\hat{p})}{n}}$$
- **Double-Blind Scoring:** Simulated actions are tested against **hidden ground-truth causes**. If the model misdiagnoses an expired card as insufficient funds and retries, it declines—penalizing ML errors.

---

## Key Results (Benchmark Run on 300 Active Cases)

- **Total Revenue at Risk:** **₹54.2 Lakhs**
- **Net Money Recovered:** **₹38.7 Lakhs**
- **Incremental Net Recovery Lift:** **+48.2%** over 15% Holdout Control
- **95% Confidence Interval:** **[43.1% – 53.3%]**
- **Total Recovery Cost:** ₹28,400 (Outreach & fees)
- **Net ROI:** **136.3x**
- **Systemic Incidents Intercepted:** 38 transactions automatically held during HDFC route degradation, preventing 38 unnecessary customer messages.
- **Policy-Protected Cases:** 64 cases deliberately suppressed due to DND, quiet hours, or negative EV, saving ₹18,450 in wasted outreach.

---

## Quick Start & Running the Demo

### Prerequisites
- Python 3.10+
- Install dependencies:
```bash
pip install lightgbm scikit-learn pandas numpy flask flask-cors google-genai python-dotenv pytest
```

### 1. Run the Automated Test Suite (116 Tests)
Run pytest to verify all models, ledger idempotency, policy gates, and outbox workflows:
```bash
python -m pytest tests
```
> **Status:** `116 passed in ~1.5 seconds`.

### 2. Launch the Application (One Command)
Run the one-click demo script from the project root:
```bash
python scripts/run_demo.py
```
*(Or run `python api/app.py` directly).*

This automatically starts the server on **`http://localhost:5050`** and opens the browser.

---

## Available Endpoints & Interfaces

| Interface | URL | Description |
| :--- | :--- | :--- |
| **Command Center Dashboard** | `http://localhost:5050` | Operations console with Revenue-at-Risk cards, Policy Lab sliders, Case Queue, and Suppressed Cases. |
| **Hinglish Voice Simulator** | `http://localhost:5050/voice` | Interactive voice simulator with preset customer objections, mid-call WhatsApp link, and live guardrail sidecar. |
| **Executive Recovery Report** | `http://localhost:5050/report` | Standalone, print-ready 1-page financial and compliance report for executives and judges. |
| **Systemic Alerts API** | `http://localhost:5050/api/systemic_alerts` | Real-time endpoint returning active gateway/bank degradation incidents. |
| **Overview Metrics API** | `http://localhost:5050/api/overview` | Aggregated recovery numbers, lift over holdout, and 95% confidence intervals. |

---

## Interactive Demo Features to Highlight

1. **The Systemic Alert Banner:** Showcases real-time detection of an HDFC route drop (Incident #INC-904), automatically holding 38 cases.
2. **The Policy Lab Sliders:** Drag Max Retries (1–5) or Margin Discount Cap (0–20%), or toggle Quiet Hours to see dynamic recalculation between recovered cash and customer friction.
3. **Case Detail Reasoning Chain:** Click any case row to inspect the 5-step modal: Ingestion Evidence $\to$ Diagnosis $\to$ Net EV Ranking $\to$ Policy Gates $\to$ Outbox Transition Log.
4. **"Cases We Chose NOT to Chase":** Shows the list of cases deliberately blocked by DND, quiet hours, or negative EV, saving ₹18,450 in wasted outreach.
5. **Hinglish Voice Simulator:**
   - Select *"Salary aane do 5th ko"* ➔ Registers a Promise-to-Pay (PTP) and freezes outreach.
   - Select *"Maine already pay kar diya"* ➔ Triggers an instant dispute freeze and opens a human reconciliation ticket.
   - Select *"WhatsApp pe link bhej do"* ➔ Dispatches a mid-call payment link with delivery confirmation.

---

## Project Structure

```
BACKSTOP--RAZORPAY/
├── api/
│   ├── app.py                     # Flask REST API & SPA server
│   ├── demo_data.py               # In-memory store & live evaluation connector
│   └── schemas.py                 # Pydantic validation & injection defenses
├── dashboard/
│   ├── index.html                 # Operations Command Center SPA
│   ├── css/style.css              # Calm financial-operations styling
│   └── js/app.js                  # Reactive dashboard controller & Policy Lab
├── data/
│   ├── generate_demo_batch.py     # Active batch of 300 multi-leak cases
│   ├── generate_historical.py     # 10,000+ historical training cases
│   ├── active_cases_300.json      # Ingested active demo dataset
│   └── historical_cases.csv       # Training corpus
├── design/                        # Visual mood references & UI layout tokens
├── docs/
│   ├── ARCHITECTURE.md            # Deep-dive system design & guarantees
│   ├── DEMO_SCRIPT.md             # 90-second rapid pitch & 5-minute deep-dive script
│   └── LIMITATIONS.md             # Honest technical trade-offs & production roadmap
├── engine/
│   ├── models.py                  # Canonical domain models (RecoveryCase, CaseState)
│   ├── config.py                  # Policy thresholds, quiet hours, margin caps
│   ├── pii.py                     # High-speed PII masking (phone, email, card, GSTIN)
│   ├── ledger.py                  # SQLite WAL state machine with run_id partitioning
│   ├── systemic.py                # Rolling window gateway/bank outage detector
│   ├── diagnosis/                 # Multi-leak diagnosis suite (payment, cart, mandate, invoice)
│   ├── recovery_value.py          # Cost-aware Net EV formula and intervention ranking
│   ├── policy.py                  # Compliance gates (DND, quiet hours, touch caps, human queue)
│   ├── measurement.py             # Stratified holdout, lift, ROI, Wilson 95% CI
│   ├── evaluator.py               # Pure in-memory benchmark runner (zero DB contamination)
│   └── execution/
│       ├── outbox.py              # Transactional outbox state machine
│       ├── scheduler.py           # Delayed retry & salary-credit timing planner
│       ├── ptp.py                 # Promise-to-Pay tracker (kept/broken states)
│       ├── dispute.py             # Immediate dispute freeze & recon task creator
│       └── providers.py           # Mocked Razorpay, WhatsApp, SMS, Email adapters
├── report/
│   ├── generator.py               # Standalone executive recovery report compiler
│   └── template.html              # Printable C-suite report template
├── scripts/
│   └── run_demo.py                # One-click seeded demo runner
├── tests/                         # 116 passing unit and integration tests
└── voice/
    ├── simulator.py               # Hinglish dialogue state machine & intent parser
    ├── prompts.py                 # Gemini prompts for natural dialogue phrasing
    ├── mid_call_link.py           # Mid-call WhatsApp payment link generator
    └── ui_component.html          # Interactive voice simulator web interface
```

---

## License & Credits

Built for the AI Revenue Recovery Challenge. All models, simulation engines, and mock adapters are designed for clean pluggability into production payment aggregators (e.g. Razorpay APIs, Twilio/Gupshup SMS, and WhatsApp Business API).
