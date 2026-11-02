# Backstop redesign and integration brief

## Non-negotiable outcome

Build Backstop as a calm, credible revenue-recovery operator console. The UI must present a **real seeded simulation run** from the existing engine. It must not invent, hard-code, or “calibrate for presentation” any headline metric, case, policy result, confidence interval, or incident.

Visual target:

- `design/backstop-command-center-reference.png` is the visual mood reference.
- `design/backstop-ui-reference.html` is a runnable layout/token reference.
- Preserve the Backstop name and current core-engine modules.

## First: remove the split-brain dashboard

The current frontend API imports `api.demo_data.store`, which constructs a separate in-memory dashboard dataset and includes forced display figures. Replace it with a run-backed adapter.

### Required flow

```text
POST /api/runs/demo
  → generate_demo_batch(seed=42, count=300)
  → assign_stratified_holdout(...)
  → evaluate_batch(...) or run_batch(...) with a unique run_id
  → persist run summary, case decisions, audit events and provider outcomes
  → return run_id

GET /api/runs/{run_id}/overview
GET /api/runs/{run_id}/cases?page=&page_size=&leak_type=&state=&q=
GET /api/runs/{run_id}/cases/{case_id}
GET /api/runs/{run_id}/incidents
POST /api/runs/{run_id}/policy-simulation
GET /api/runs/{run_id}/review-queue
POST /api/runs/{run_id}/review-queue/{case_id}/decision
GET /api/runs/{run_id}/report
```

Use the existing canonical modules as the source of truth:

- `data/generate_demo_batch.py`
- `engine/models.py`
- `engine/measurement.py`
- `engine/evaluator.py`
- `engine/policy.py`
- `engine/systemic.py`
- `engine/ledger.py`
- `engine/execution/outbox.py`

Do not use `api/demo_data.py` for any live endpoint. It may remain only as a clearly marked test fixture until it is deleted.

## Truthful metric rules

1. Every number comes from a persisted `run_id` result.
2. Every demo view displays `Synthetic demo · Seed 42 · Run <run_id>`.
3. Present `simulated` beside recovery, lift, ROI, and confidence metrics.
4. Do not display a p-value unless the evaluation code calculates that exact p-value and its assumptions are visible.
5. “Production ready” must not appear anywhere.
6. Mock provider confirmations must read `Simulated provider confirmation`.
7. The control group receives only `do_nothing`; the UI should expose that fact in its case timeline.

## Visual system

Use the following design rules; do not copy a generic SaaS dashboard template.

```css
--canvas: #FAFAF7;
--surface: #FFFFFF;
--ink: #172033;
--muted: #657087;
--line: #E5E8EE;
--indigo: #2947B8;
--indigo-soft: #EDF1FF;
--green: #087A56;
--green-soft: #EAF7F0;
--amber: #9A5B00;
--amber-soft: #FFF5DD;
--red: #B42318;
--red-soft: #FFF0EF;
```

- One accent colour: indigo. Green/amber/red are semantic states only.
- No emoji, gradients, glassmorphism, neon, huge shadows, fake badges, or decorative scorecards.
- Use a normal sans-serif for all product text. Use tabular numbers only for money, percentages, IDs, and dates.
- Use a 208px off-white navigation rail with Overview, Cases, Policy Lab, Reviews.
- Use 8px/12px radius maximum and hairline borders instead of floating-card shadows.
- Meet WCAG 2.2 AA contrast and visible keyboard focus requirements.

## Required screens

### 1. Overview

Above the fold:

- Heading: `Revenue recovery, safely prioritized`
- Short explanation: recovery without customer/compliance risk.
- One compact active-incident strip only when an incident exists.
- Exactly three top metrics:
  1. Revenue at risk
  2. Incremental net recovery vs control
  3. Policy-protected cases
- Priority work queue; rank by safe net expected value, not arbitrary order.
- Right-side operational signal with route health and a small human-review queue.

### 2. Case detail

This is the signature Backstop screen. Do not use a modal stuffed with dozens of badges. Use a full page or wide side sheet with a chronological timeline:

```text
Detected → Evidence → Diagnosis → Policy decision → Scheduled/executed action → Outcome
```

Show:

- Masked identity only
- Exact diagnosis evidence and confidence
- Ranked intervention table with probability, cost, discount, risk penalty, net EV
- One plain-English “why this action” sentence
- Policy gates with pass/blocked/deferred state
- Idempotency key and audit entries under a collapsible technical section
- A clear simulated-provider label wherever relevant

### 3. Policy Lab

Controls: retry cap, discount cap, quiet hours, minimum case amount, human-review threshold.

Changing a control must submit the configuration to the backend, which reruns the existing policy/evaluation logic against the selected run. Do not use a frontend formula or fixed baseline. Return and show:

- projected net recovery
- control lift
- outreach/discount/human-review cost
- number of policy-blocked cases
- plain-language trade-off notes generated from actual changed decisions

### 4. Reviews and suppressed cases

- Human-review table: approve, reject/suppress, modify discount within permitted cap, resolve dispute.
- “Protected by policy” list: show cases not chased and the reason. This is a trust feature, not an error state.

### 5. Hinglish voice demo

Preserve the simulator, but put it behind a `Voice demo` action on a case. It must have a permanent label: `Simulation — no message or payment is sent.`

Show structured intent buttons and a transcript. Dispute, hardship, hostile sentiment, or opt-out must visibly freeze outreach and create a review task.

## Security and product constraints

- Local development only: bind to `127.0.0.1`, not `0.0.0.0`.
- No wildcard CORS. Restrict the development origin explicitly.
- Add a simple development auth middleware and role checks now: `operator`, `reviewer`, `admin`. Make human decisions require `reviewer` or `admin`.
- No dynamic API values rendered through `innerHTML`. Use DOM nodes/textContent or an HTML-escaping template helper.
- Do not return raw exception messages to the browser.
- All demo data must already be PII masked. Do not put raw PII into outbox payloads, the frontend, logs, or reports.

## Acceptance checks

Before declaring the redesign complete, add and pass these tests:

1. Start a seeded demo run, then verify the displayed overview equals that run’s persisted evaluator result.
2. Change Policy Lab sliders and verify the response equals a backend re-evaluation, not a client calculation.
3. Refresh/restart the app and verify a case timeline/audit trail remains available for its run ID.
4. Verify all dashboard values are escaped before rendering.
5. Verify an unauthorised role cannot approve/reject review items.
6. Verify the UI labels all simulated values/providers honestly.
7. Verify keyboard navigation, visible focus, and mobile layout.

## Build instruction for Gemini

> Refactor the Backstop dashboard according to `GEMINI_UI_AND_INTEGRATION_BRIEF.md`. Treat existing engine modules as source of truth; do not rewrite them or fabricate dashboard values. First replace `api.demo_data.store` endpoints with a run-backed orchestration/service layer connecting `generate_demo_batch`, holdout assignment, policy evaluation, evaluator, ledger and outbox. Persist each seeded demo run and make every dashboard endpoint query a selected `run_id`. Then implement the UI using `design/backstop-ui-reference.html` and `design/backstop-command-center-reference.png` as visual references. Use plain HTML/CSS/JS unless a framework already exists. Remove emoji, fake “Production Ready” claims, hard-coded financial metrics, and dynamic `innerHTML` for API data. Add the acceptance tests in this brief and run the full test suite. In the final response, list every changed file, exact commands to run the project, test results, and any remaining simulated dependencies.
