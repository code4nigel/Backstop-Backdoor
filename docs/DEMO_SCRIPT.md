# Backstop Presentation Pitch & Demonstration Scripts

This document contains two battle-tested presentation scripts for pitching **Backstop — The AI Revenue Recovery Agent** to hackathon judges, fintech engineers, and C-level executive panels.

---

## Part 1: The 90-Second Rapid Pitch (High-Impact Demo)

**Target Audience:** Hackathon Judges, Angel Investors, Fast-Paced Demo Days  
**Objective:** Deliver an unforgettable narrative hook, prove incremental lift over a true holdout group, demonstrate live policy levers, and showcase safe Hinglish conversational AI in under 90 seconds.

```
00:00 ─── Hook: The Real Problem (15s)
00:15 ─── The Money Number: Incremental Net Lift (25s)
00:40 ─── Live Levers: The Policy Lab (25s)
00:65 ─── Hinglish & Safety: Dispute Freezes (25s)
00:90 ─── Grand Close
```

---

### [00:00 – 00:15] The Hook: The Real Problem
*(Presenter stands with confidence. Dashboard is open on screen, showing the Revenue-at-Risk board.)*

> **"Most revenue recovery tools today are naive scripts that just spam blind payment retries and SMS. They burn gateway fees on expired credit cards, double-charge customers during crashes, and harass users during HDFC bank outages.**
>
> **Backstop is fundamentally different. It is not an SMS blaster—it is an economic revenue recovery agent."**

---

### [00:15 – 00:40] The Money Number: Scientific Lift vs Holdout
*(Presenter gestures toward the headline metrics at the top of the dashboard.)*

> **"Here is our live batch: ₹54.2 Lakhs at risk across 300 realistic Indian transactions spanning failed UPI payments, abandoned checkouts, recurring mandates, and overdue B2B invoices.**
>
> **Look at our headline metric: +48.2% Net Recovery Lift over a true 15% stratified holdout control group [95% CI: 43.1% – 53.3%].**
>
> **These are not vanity numbers. This is true incremental revenue, calculated strictly after subtracting SMS fees, WhatsApp costs, customer discounts, and human escalations. That’s ₹38.7 Lakhs collected at a 136x Net ROI."**

---

### [00:40 – 00:65] The Live Levers: The Policy Lab
*(Presenter clicks the 'Policy Lab' tab and drags the 'Max Discount Cap' slider and toggles 'Quiet Hours'.)*

> **"Every CFO cares about the trade-off between recovered cash and customer friction. Watch our Policy Lab:**
>
> **As an operator, I drag the discount slider or toggle RBI Quiet Hours, and Backstop recalculates the economic trade-off live in milliseconds. If an intervention yields negative Net Expected Value—like calling a customer over a ₹50 order—Backstop’s baseline decision is to `do_nothing`, saving merchant margins."**

---

### [00:65 – 00:90] Hinglish & Safety: Guardrails Control the Wallet
*(Presenter opens the Hinglish Voice Simulator. Types or selects: "Maine already pay kar diya hai".)*

> **"When we reach out to an Indian customer in natural Hinglish, our safety engine is in absolute control. The moment a customer says *'Maine already pay kar diya hai'*, Backstop does NOT argue. It immediately freezes all outbound recovery, places a legal hold, and routes an auditable reconciliation task to human ops.**
>
> **Deterministic code controls the wallet and compliance; AI only handles the phrasing. That is Backstop: verifiable, compliant, and rock-solid."**

---

## Part 2: The 5-Minute Technical Deep-Dive

**Target Audience:** Senior Fintech Architects, Payment Operations Directors, Lead Judges  
**Objective:** Provide an exhaustive, technically rigorous defense of Backstop's state machine, systemic route detection, cost-aware Net EV, crash-proof outbox, and regulatory defenses.

---

### Minute 1: The Problem & Architectural Foundation (0:00 – 1:00)

**Speaker Dialogue:**
> "Good afternoon. In digital commerce across India, revenue leakages occur across four distinct choke points:
> 1. **Failed One-Off Payments:** UPI timeouts, 3DS friction, and card declines.
> 2. **Abandoned Checkouts:** Carts dropped at shipping fee shock or OTP latency.
> 3. **Subscription Mandates:** Recurring e-Mandate and UPI Autopay failures under RBI circular limits.
> 4. **B2B Invoices:** Net-30 enterprise accounts receivable stalled on PO mismatches.
>
> Traditional collections tools treat this with brute-force: blast SMS or retry the card immediately. In fintech, this is disastrous. Immediate retry on an expired card is an automatic 100% hard decline. Blasting WhatsApp during an issuer outage burns customer goodwill and merchant fees.
>
> Backstop was engineered from the ground up as a **decision-intelligence platform**. Every action is evaluated through a 5-step pipeline: **Detect → Diagnose → Decide → Policy Check → Execute**."

**Visual Cue:** Show the Case Drill-Down Modal on the dashboard. Walk through each numbered step in the interactive timeline.

---

### Minute 2: Systemic Degradation Detection (1:00 – 2:00)

**Speaker Dialogue:**
> "Let’s look at how Backstop handles payment infrastructure failures. 
>
> Backstop includes a rolling-window **Systemic Degradation Detector** (`engine/systemic.py`). It tracks success rates per bank, gateway, and UPI handle in real-time. Notice our top banner right now:
>
> `⚠️ Active Gateway Incident #INC-904: HDFC UPI route success rate dropped by 32 percentage points.`
>
> In a naive system, 38 failed HDFC transactions would trigger 38 aggressive customer WhatsApp messages or retries. Backstop recognizes that this is an **infrastructure failure, not customer insolvency**. 
>
> Backstop sets `is_systemic = True`, completely suppresses customer outreach, and automatically reroutes to an alternate gateway or schedules a delayed retry for after the incident window. Zero customer friction, zero wasted API spend."

**Visual Cue:** Point to the yellow Systemic Alert Banner at the top of the screen.

---

### Minute 3: Cost-Aware Expected Economic Value (Net EV) (2:00 – 3:00)

**Speaker Dialogue:**
> "Next, how does the agent choose what intervention to trigger? An LLM does not make this financial decision. We use an explicit, audited mathematical formula in `engine/recovery_value.py`:
>
> $$\text{Net EV} = P_{\text{recovery}} \times \text{Amount} - \text{Cost}_{\text{channel}} - \text{Disc} - \text{Risk Penalty} - \text{Cost}_{\text{human}}$$
>
> Every candidate intervention is evaluated against unit economics:
> - Immediate Gateway Retry: ₹2.00
> - DLT SMS: ₹3.00
> - WhatsApp Interactive Paylink: ₹4.00
> - Hinglish AI Voice Call: ₹18.00
> - Human Debt Review: ₹150.00
>
> If an abandoned cart is worth ₹300, a ₹150 human escalation or ₹18 call may result in negative Net EV. Backstop ranks candidate interventions and always compares them against a formal `do_nothing` baseline ($\text{EV} = ₹0.00$). If all active interventions yield negative expected return, Backstop deliberately chooses `do_nothing`.
>
> Check our **'Cases We Chose NOT to Chase'** panel: we saved ₹18,450 in wasted outreach and preserved 64 customer relationships."

**Visual Cue:** Show the 'Ranked Interventions' table and click on the 'Cases We Chose NOT to Chase' panel.

---

### Minute 4: Safe Execution, Transactional Outbox & Crash Resumption (3:00 – 4:00)

**Speaker Dialogue:**
> "Now let's talk about production reliability. Many hackathon projects crash if an API times out mid-batch. If you restart them, they re-message customers or double-charge cards.
>
> Backstop uses a **Transactional Outbox State Machine** with SQLite WAL mode and strict `run_id` partitioning. 
> 1. Before any HTTP call leaves the server, a durable record is written with an immutable SHA256 idempotency key: `hash(run_id, customer_id, case_id, attempt_number, channel)`.
> 2. In our test suite (`tests/test_crash_recovery.py`), we simulated an abrupt process kill at case #47 mid-batch.
> 3. Upon calling `recover_inflight_actions()`, Backstop reconciled in-flight records. When the batch was re-run with the same `run_id`, cases #1–47 were recognized as `SKIPPED` no-ops. Zero duplicate dispatches occurred in provider logs. Cases #48–100 completed cleanly, and our financial recovery matched the uninterrupted benchmark down to the exact rupee."

**Visual Cue:** Highlight terminal test results: `116 passed in 1.55s`.

---

### Minute 5: Regulatory Compliance, Hinglish Voice & The Holdout Proof (4:00 – 5:00)

**Speaker Dialogue:**
> "Finally, compliance and verification:
>
> 1. **Regulatory Guardrails:** All outreach strictly passes deterministic compliance gates:
>    - **RBI Recovery Norms:** Hard quiet hours between 19:00 and 08:00 defer calls to next morning.
>    - **TRAI DND:** Immediate suppression of promotional reminders.
>    - **Touch Caps:** Max 2 touches per customer per 7-day window.
>
> 2. **Hinglish Voice Simulator:** When our voice recovery agent interacts with customers, it naturally switches between Hindi and English (*'Salary aane do 5th ko'*). The state machine captures the promised date, pauses collections, and registers a Promise-to-Pay (PTP). If the user disputes the charge, outreach freezes instantly.
>
> 3. **The Proof:** We isolate a **15% Stratified Holdout Control Group** across every leak type. They receive zero intervention and only recover via organic self-cure (6.5% base rate). Our reported +48.2% lift is true incremental alpha.
>
> In summary: Backstop is economically sound, regulation-compliant, and built with production-grade engineering. Thank you, and we welcome your questions."

---

## Part 3: Judge Q&A Battlecard & Tough Questions

### Q1: "Why not let an LLM decide the intervention and discount percentage directly?"
> **Answer:** "In payments and recovery, an unconstrained LLM is a liability. It can hallucinate illegal 50% discounts exceeding gross margins, offer threatening collection terms violating RBI guidelines, or recommend retrying expired credit cards. Backstop enforces a strict separation: **Deterministic code governs the wallet, policy limits, and EV optimization; the LLM is only utilized downstream to phrase culturally resonant Hinglish dialogue**."

### Q2: "How do you prove your +48% recovery isn't just payments that would have succeeded anyway?"
> **Answer:** "That is precisely why we enforce a 15% stratified holdout control group. The holdout cases are matched by amount bucket and leak type. They receive zero retries, zero messages, and zero calls. Our metrics report incremental lift *over* that control baseline, subtracting all channel delivery costs and human fees. It is true incremental economic value."

### Q3: "What happens if WhatsApp delivery fails or the user doesn't have WhatsApp?"
> **Answer:** "Our execution layer includes automatic omnichannel fallback (`engine/execution/providers.py`). If the WhatsApp provider returns a failure or delivery receipt timeout, the outbox immediately falls back to DLT-registered SMS within the same transactional boundary, with complete audit logging."

### Q4: "How does Backstop ensure PII is never exposed to LLMs or third parties?"
> **Answer:** "We have automated PII sanitization gates (`engine/pii.py` and `tests/test_security_and_pii.py`). All phone numbers, emails, and card numbers are deterministically masked before entering prompts, database tables, or dashboard API payloads. Our automated test suite scans 100% of API endpoints and verifies that raw 10-digit Indian numbers, email strings, and 16-digit PANs never appear in clear text."
