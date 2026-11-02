/**
 * Backstop Operations Command Center & Policy Lab
 * High-Craft Reactive UI Controller & Chart.js Engine
 */

const state = {
  activeTab: 'cases',
  casesPage: 1,
  casesPageSize: 10,
  casesFilter: {
    leak_type: '',
    state: '',
    is_control_group: '',
    search: ''
  },
  policyLab: {
    retry_limit: 3,
    discount_cap_pct: 10.0,
    quiet_hours_enabled: true,
    min_case_value: 200.0,
    human_review_threshold: 50000.0
  },
  charts: {
    leakChart: null,
    radarChart: null
  }
};

const fmtCurr = (n) => {
  if (n === undefined || n === null) return '₹0';
  return '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
};

const fmtLakh = (n) => {
  if (!n) return '₹0';
  const l = n / 100000;
  return `₹${l.toFixed(2)}L`;
};

const showToast = (message, type = 'success') => {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <span>${type === 'success' ? '✓' : '!'}</span>
    <span>${message}</span>
  `;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 250);
  }, 3600);
};

let debounceTimeout = null;
const debounce = (func, delay = 200) => {
  return (...args) => {
    clearTimeout(debounceTimeout);
    debounceTimeout = setTimeout(() => func(...args), delay);
  };
};

function setActiveNav(el) {
  document.querySelectorAll('.sidebar-nav .nav-link').forEach(a => a.classList.remove('active'));
  if (el) el.classList.add('active');
}

// =====================================================================
// 1. DATA FETCHING & API BINDINGS
// =====================================================================

async function loadOverview() {
  try {
    const res = await fetch('/api/overview');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderOverview(data);
  } catch (err) {
    console.error('Failed to load overview:', err);
  }
}

async function loadSystemicAlerts() {
  try {
    const res = await fetch('/api/systemic_alerts');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderSystemicAlerts(data);
  } catch (err) {
    console.error('Failed to load systemic alerts:', err);
  }
}

async function loadCases() {
  try {
    const query = new URLSearchParams({
      page: state.casesPage,
      page_size: state.casesPageSize,
      ...(state.casesFilter.leak_type && { leak_type: state.casesFilter.leak_type }),
      ...(state.casesFilter.state && { state: state.casesFilter.state }),
      ...(state.casesFilter.is_control_group !== '' && { is_control_group: state.casesFilter.is_control_group }),
      ...(state.casesFilter.search && { search: state.casesFilter.search })
    });

    const res = await fetch(`/api/cases?${query.toString()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderCasesTable(data);
  } catch (err) {
    console.error('Failed to load cases:', err);
  }
}

async function loadHumanQueue() {
  try {
    const res = await fetch('/api/human_queue');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderHumanQueue(data);
  } catch (err) {
    console.error('Failed to load human queue:', err);
  }
}

async function loadSuppressedCases() {
  try {
    const res = await fetch('/api/suppressed_cases');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderSuppressedCases(data);
  } catch (err) {
    console.error('Failed to load suppressed cases:', err);
  }
}

async function simulatePolicyLab() {
  try {
    const res = await fetch('/api/policy_lab/simulate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(state.policyLab)
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderPolicySimulation(data);
  } catch (err) {
    console.error('Policy lab simulation failed:', err);
  }
}

async function commitPolicyToLiveEngine() {
  try {
    const res = await fetch('/api/policy_lab/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(state.policyLab)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Failed to apply policy');
    renderOverview(data.overview);
    renderPolicySimulation(data.simulation);
    loadCases();
    loadHumanQueue();
    loadSuppressedCases();
    showToast(`Applied Policy to Live Engine (${data.changed_cases_count} cases updated, Live Report synced).`, 'success');
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

async function runLiveBatchStream() {
  const banner = document.getElementById('live-batch-banner');
  const bar = document.getElementById('live-batch-bar');
  const pctEl = document.getElementById('live-batch-pct');
  const detailEl = document.getElementById('live-batch-detail');
  const btn = document.getElementById('btn-run-live-batch');
  if (banner) banner.style.display = 'block';
  if (btn) btn.disabled = true;

  const steps = [
    { p: 0.28, msg: 'Step 1/4: Ingesting at-risk events & isolating HDFC UPI degradation (#INC-904)...' },
    { p: 0.55, msg: 'Step 2/4: Running Multi-Leak ML Diagnosis & 15% Stratified Holdout split...' },
    { p: 0.82, msg: 'Step 3/4: Ranking Cost-Aware Net EV interventions & enforcing TRAI DND / RBI Quiet Hours...' },
    { p: 1.00, msg: 'Step 4/4: Dispatching via Idempotent Outbox & syncing Executive Recovery Report...' }
  ];

  for (const s of steps) {
    if (bar) bar.style.width = `${s.p * 100}%`;
    if (pctEl) pctEl.textContent = `${Math.round(s.p * 100)}%`;
    if (detailEl) detailEl.textContent = s.msg;
    try {
      const res = await fetch('/api/runs/live_batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ progress: s.p })
      });
      if (res.ok) {
        const data = await res.json();
        renderOverview(data.overview);
      }
    } catch (e) {}
    await new Promise(r => setTimeout(r, 450));
  }

  loadCases();
  loadHumanQueue();
  loadSuppressedCases();
  showToast('Live Batch Run Completed! Dashboard & Executive Report updated.', 'success');
  if (btn) btn.disabled = false;
  setTimeout(() => {
    if (banner) banner.style.display = 'none';
  }, 2000);
}

function openInjectModal() {
  const m = document.getElementById('inject-modal');
  if (m) m.classList.add('open');
}

function closeInjectModal() {
  const m = document.getElementById('inject-modal');
  if (m) m.classList.remove('open');
}

async function submitInjectTransaction() {
  const payload = {
    customer_name: document.getElementById('inj-name').value.trim() || 'Suhani Sharma',
    amount: parseFloat(document.getElementById('inj-amount').value) || 24500,
    leak_type: document.getElementById('inj-leak').value,
    predicted_cause: document.getElementById('inj-cause').value,
    bank: document.getElementById('inj-bank').value,
    is_dnd: document.getElementById('inj-dnd').checked
  };

  try {
    const res = await fetch('/api/cases/inject', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Injection failed');

    closeInjectModal();
    renderOverview(data.overview);
    state.casesPage = 1;
    loadCases();
    loadHumanQueue();
    loadSuppressedCases();
    showToast(`Injected ${data.case.case_id} (${data.case.customer_name}, ₹${data.case.amount.toLocaleString('en-IN')}) → ${data.case.state}`, 'success');
    renderCaseModal(data.case);
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

async function openCaseDetail(caseId) {
  try {
    const res = await fetch(`/api/cases/${caseId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const caseData = await res.json();
    renderCaseModal(caseData);
  } catch (err) {
    showToast(`Failed to load case ${caseId}`, 'danger');
  }
}

async function submitHumanAction(caseId, action, notes = '', newDiscount = null) {
  try {
    const res = await fetch(`/api/human_queue/${caseId}/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: action,
        notes: notes,
        new_discount_pct: newDiscount
      })
    });
    const result = await res.json();
    if (!res.ok) throw new Error(result.error || 'Action failed');

    showToast(`Case ${caseId}: ${result.message}`, 'success');
    loadHumanQueue();
    loadOverview();
    loadCases();
    loadSuppressedCases();
    closeCaseModal();
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

// =====================================================================
// 2. RENDER FUNCTIONS
// =====================================================================

function renderOverview(data) {
  document.getElementById('metric-total-risk').textContent = fmtLakh(data.total_revenue_at_risk);
  document.getElementById('metric-total-rec').textContent = fmtLakh(data.net_money_recovered);
  document.getElementById('metric-lift').textContent = `↑ +${data.incremental_lift_pct}%`;
  document.getElementById('metric-ci').textContent = `[95% CI: ${data.confidence_interval_95.lower_pct}%–${data.confidence_interval_95.upper_pct}%]`;
  document.getElementById('metric-cost-roi').textContent = `${fmtCurr(data.total_outreach_cost)} cost`;

  const roiBadge = document.getElementById('metric-roi-badge');
  if (roiBadge) roiBadge.textContent = `${data.net_roi_multiplier}x ROI`;

  const casesBadge = document.getElementById('metric-cases-badge');
  if (casesBadge) casesBadge.textContent = `${data.active_cases_count} cases`;

  const protCount = document.getElementById('metric-protected-count');
  if (protCount) protCount.textContent = data.suppressed_count;

  const hqCount = document.getElementById('sidebar-hq-counter');
  if (hqCount) hqCount.textContent = data.human_queue_count;
  const actCount = document.getElementById('sidebar-active-counter');
  if (actCount) actCount.textContent = data.active_cases_count;
  const casesTabCount = document.getElementById('cases-tab-counter');
  if (casesTabCount) casesTabCount.textContent = data.active_cases_count;
  const suppSideCount = document.getElementById('sidebar-supp-counter');
  if (suppSideCount) suppSideCount.textContent = data.suppressed_count;
  const asideHqTotal = document.getElementById('aside-hq-total');
  if (asideHqTotal) asideHqTotal.textContent = data.human_queue_count;

  const shortTitles = {
    failed_payment: 'Failed Payments',
    abandoned_checkout: 'Abandoned Carts',
    subscription_mandate: 'Mandate Renewals',
    b2b_invoice: 'B2B Invoices'
  };

  const leakContainer = document.getElementById('leak-cards-container');
  if (leakContainer && data.leak_breakdown) {
    leakContainer.innerHTML = data.leak_breakdown.map(card => `
      <div class="leak-card ${state.casesFilter.leak_type === card.leak_type ? 'active-filter' : ''}" 
           onclick="filterByLeakCard('${card.leak_type}')">
        <div class="leak-card-title-row">
          <span class="leak-card-title">${shortTitles[card.leak_type] || card.title}</span>
          <span class="leak-badge">${card.at_risk_count}</span>
        </div>
        <div class="leak-nums-row">
          <span class="leak-at-risk">At risk: <strong class="num-font" style="color:var(--ink);">${fmtLakh(card.total_at_risk)}</strong></span>
          <span class="leak-rate-highlight num-font">${card.recovery_rate_pct}%</span>
        </div>
        <div class="progress-track">
          <div class="progress-fill" style="width: ${card.recovery_rate_pct}%"></div>
        </div>
        <div class="leak-footer">
          <span>Top cause</span>
          <span class="leak-cause-tag">${card.top_root_cause}</span>
        </div>
      </div>
    `).join('');
  }

  renderLeakChart(data.leak_breakdown);
}

function renderSystemicAlerts(data) {
  const container = document.getElementById('systemic-alert-wrapper');
  if (!container) return;

  if (!data.alerts || data.alerts.length === 0) {
    container.innerHTML = '';
    return;
  }

  const alert = data.alerts[0];
  container.innerHTML = `
    <div class="incident-strip" role="status">
      <div class="incident-strip-left">
        <span class="incident-dot"></span>
        <span>HDFC UPI route degradation (-${alert.drop_pct}%) · ${alert.spam_prevented_count} cases safely held or rerouted without customer spam</span>
      </div>
      <button class="link-btn" onclick="filterByLeakCard('failed_payment')">View incident cases →</button>
    </div>
  `;
}

function renderCasesTable(data) {
  const tbody = document.getElementById('cases-table-body');
  if (!tbody) return;

  if (!data.cases || data.cases.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:32px; color:var(--muted);">No cases match the selected filters.</td></tr>`;
    return;
  }

  const stateLabels = {
    RECOVERED: { cls: 'badge-state-recovered', text: 'Recovered' },
    EXECUTING: { cls: 'badge-state-executing', text: 'Allowed · Executing' },
    PROMISED: { cls: 'badge-state-promised', text: 'Promised (PTP)' },
    DISPUTED: { cls: 'badge-state-disputed', text: 'Needs review' },
    POLICY_BLOCKED: { cls: 'badge-state-blocked', text: 'Blocked' },
    READY: { cls: 'badge-state-ready', text: 'Needs review' },
    CLOSED: { cls: 'badge-state-ready', text: 'Closed' }
  };

  tbody.innerHTML = data.cases.map(c => {
    const st = stateLabels[c.state] || { cls: 'badge-state-ready', text: c.state };
    const cleanAction = (c.chosen_action || '').replace(/_/g, ' ');

    return `
      <tr onclick="openCaseDetail('${c.case_id}')">
        <td class="num-font" style="font-weight:600; color:var(--ink);">#${c.case_id.replace('CASE-2026', 'CS-')}</td>
        <td>
          <div style="font-weight:600; color:var(--ink);">${c.customer_name}</div>
          <div class="num-font" style="font-size:11px; color:var(--muted);">${c.phone_masked}</div>
        </td>
        <td>
          <span style="color:var(--ink-secondary); font-weight:500;">${c.leak_type.replace(/_/g, ' ')}</span>
          ${c.is_control_group ? '<span class="badge-control-tag">Holdout</span>' : ''}
        </td>
        <td>
          <div style="font-weight:500; color:var(--ink); text-transform:capitalize;">${cleanAction}</div>
          <div style="font-size:11px; color:var(--muted);">Cause: ${c.predicted_cause.replace(/_/g, ' ')} (${Math.round(c.confidence * 100)}%)</div>
        </td>
        <td class="num-font" style="font-weight:600; color:var(--ink);">${fmtCurr(c.amount)}</td>
        <td class="num-font" style="color:var(--green); font-weight:600;">+${fmtCurr(c.net_ev)}</td>
        <td><span class="badge-pill ${st.cls}">${st.text}</span></td>
      </tr>
    `;
  }).join('');

  document.getElementById('pagination-info').textContent = 
    `Showing page ${data.page} of ${data.total_pages} (${data.total} total cases)`;
  document.getElementById('btn-prev-page').disabled = (data.page <= 1);
  document.getElementById('btn-next-page').disabled = (data.page >= data.total_pages);
}

function renderHumanQueue(data) {
  const tbody = document.getElementById('human-queue-body');
  const countBadge = document.getElementById('hq-tab-counter');
  if (countBadge) countBadge.textContent = data.count || 0;
  const asideTotal = document.getElementById('aside-hq-total');
  if (asideTotal) asideTotal.textContent = data.count || 0;

  if (data.queue) {
    const dispCnt = data.queue.filter(i => i.state === 'DISPUTED' || (i.escalation_reason || '').toLowerCase().includes('dispute')).length;
    const highCnt = data.queue.filter(i => i.amount >= 50000 && i.state !== 'DISPUTED').length;
    const lowCnt = Math.max(0, data.queue.length - dispCnt - highCnt);
    const elD = document.getElementById('aside-hq-dispute');
    const elH = document.getElementById('aside-hq-highval');
    const elL = document.getElementById('aside-hq-lowconf');
    if (elD) elD.textContent = dispCnt;
    if (elH) elH.textContent = highCnt;
    if (elL) elL.textContent = lowCnt;
  }

  if (!tbody) return;

  if (!data.queue || data.queue.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:32px; color:var(--muted);">All flagged cases reviewed. Zero items awaiting human approval.</td></tr>`;
    return;
  }

  tbody.innerHTML = data.queue.map(item => `
    <tr>
      <td class="num-font" style="font-weight:600; color:var(--indigo); cursor:pointer;" onclick="openCaseDetail('${item.case_id}')">${item.case_id}</td>
      <td>
        <div style="font-weight:600; color:var(--ink);">${item.customer_name}</div>
        <div class="num-font" style="font-size:11.5px; font-weight:600; color:var(--muted);">${fmtCurr(item.amount)}</div>
      </td>
      <td>
        <span class="badge-pill ${item.urgency === 'HIGH' ? 'badge-state-blocked' : 'badge-state-disputed'}">${item.urgency}</span>
      </td>
      <td>
        <div style="font-weight:500; color:var(--ink);">${item.escalation_reason}</div>
        <div style="font-size:11px; color:var(--muted);">${item.diagnosis_summary}</div>
      </td>
      <td>
        <span class="leak-badge">${item.recommended_action.replace(/_/g, ' ')}</span>
      </td>
      <td>
        <div style="display:flex; gap:6px;">
          <button class="btn btn-sm btn-success" onclick="event.stopPropagation(); submitHumanAction('${item.case_id}', 'approve')">Approve</button>
          <button class="btn btn-sm btn-outline" onclick="event.stopPropagation(); submitHumanAction('${item.case_id}', 'reject')">Suppress</button>
          <button class="btn btn-sm btn-danger" onclick="event.stopPropagation(); submitHumanAction('${item.case_id}', 'resolve_dispute')">Freeze</button>
        </div>
      </td>
    </tr>
  `).join('');
}

function renderSuppressedCases(data) {
  const container = document.getElementById('suppressed-summary-bar');
  const tbody = document.getElementById('suppressed-table-body');
  const countBadge = document.getElementById('suppressed-tab-counter');
  if (countBadge) countBadge.textContent = data.total_suppressed;

  if (container) {
    container.innerHTML = `
      <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px;">
        <div>
          <div style="font-size:14px; font-weight:700; color:var(--green);">✓ ${data.trust_statement}</div>
          <div style="font-size:12px; color:var(--muted); margin-top:2px;">
            Enforcing TRAI DND checks, negative EV margin filters, and dispute freezes protects brand trust.
          </div>
        </div>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          ${Object.entries(data.breakdown_by_rule || {}).map(([r, cnt]) => `
            <span class="leak-badge" style="background:var(--surface); border:1px solid var(--green-border); color:var(--ink);">
              ${r}: <strong>${cnt}</strong>
            </span>
          `).join('')}
        </div>
      </div>
    `;
  }

  if (tbody && data.suppressed_cases) {
    tbody.innerHTML = data.suppressed_cases.map(item => `
      <tr onclick="openCaseDetail('${item.case_id}')">
        <td class="num-font" style="font-weight:600; color:var(--indigo);">${item.case_id}</td>
        <td>
          <div style="font-weight:600; color:var(--ink);">${item.customer_name}</div>
          <div class="num-font" style="font-size:11px; color:var(--muted);">${item.phone_masked}</div>
        </td>
        <td><span class="leak-badge">${item.leak_type.replace(/_/g, ' ')}</span></td>
        <td class="num-font" style="font-weight:600; color:var(--ink);">${fmtCurr(item.amount)}</td>
        <td>
          <span class="badge-pill badge-state-blocked">${item.block_reason}</span>
          <div style="font-size:10.5px; color:var(--muted); margin-top:2px;">Rule: ${item.stopping_rule_fired}</div>
        </td>
        <td>
          <div style="color:var(--green); font-weight:600;">₹${item.wasted_spend_saved} outreach saved</div>
          <div style="font-size:11px; color:var(--muted);">${item.customer_goodwill_preserved}</div>
        </td>
      </tr>
    `).join('');
  }
}

function renderPolicySimulation(data) {
  document.getElementById('sim-projected-rec').textContent = fmtLakh(data.projected_net_recovered);
  document.getElementById('sim-delta-badge').textContent = `${data.delta_vs_baseline_pct >= 0 ? '+' : ''}${data.delta_vs_baseline_pct}% vs baseline`;
  document.getElementById('sim-roi').textContent = `${data.projected_net_roi_multiplier}x`;
  document.getElementById('sim-blocked-cases').textContent = `${data.blocked_cases_count} cases`;
  const costEl = document.getElementById('sim-cost');
  if (costEl) costEl.textContent = fmtCurr(data.projected_outreach_cost);
  
  const notesContainer = document.getElementById('sim-notes-container');
  if (notesContainer && data.simulation_notes) {
    notesContainer.innerHTML = data.simulation_notes.map(n => `<div>• ${n}</div>`).join('');
  }

  renderPolicyRadarChart(data.tradeoff_radar);
}

// =====================================================================
// 3. CASE DRILL-DOWN MODAL
// =====================================================================

function renderCaseModal(c) {
  const modal = document.getElementById('case-modal');
  if (!modal) return;

  document.getElementById('modal-case-id').textContent = c.case_id;
  document.getElementById('modal-cust-name').textContent = c.customer_name;
  document.getElementById('modal-amount').textContent = fmtCurr(c.amount);

  document.getElementById('step1-content').innerHTML = `
    <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:12px; font-size:12px;">
      <div><span style="color:var(--muted);">Leak Type:</span> <strong class="leak-badge">${c.leak_type}</strong></div>
      <div><span style="color:var(--muted);">Timestamp:</span> <strong class="num-font">${c.created_at_str}</strong></div>
      <div><span style="color:var(--muted);">Days at Risk:</span> <strong>${c.days_at_risk} days</strong></div>
      <div><span style="color:var(--muted);">Masked Phone:</span> <strong class="num-font">${c.phone_masked}</strong></div>
    </div>
    <div style="margin-top:8px; font-size:11.5px; background:var(--canvas); padding:7px 10px; border-radius:4px; color:var(--ink-secondary);">
      Metadata: ${Object.entries(c.metadata || {}).map(([k, v]) => `<code>${k}: ${v}</code>`).join(' · ')}
    </div>
  `;

  const diag = c.diagnosis;
  document.getElementById('step2-content').innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
      <div>
        <span style="font-size:10.5px; text-transform:uppercase; color:var(--muted);">Predicted Root Cause</span>
        <div style="font-size:15px; font-weight:700; color:var(--indigo);">${diag.predicted_cause}</div>
      </div>
      <div style="text-align:right;">
        <span style="font-size:10.5px; text-transform:uppercase; color:var(--muted);">Model Confidence</span>
        <div class="num-font" style="font-size:16px; font-weight:700; color:var(--green);">${Math.round(diag.confidence * 100)}%</div>
      </div>
    </div>
    <ul style="padding-left:18px; font-size:12px; color:var(--ink-secondary); line-height:1.5;">
      ${(diag.evidence || []).map(e => `<li>${e}</li>`).join('')}
    </ul>
  `;

  const rankings = c.ranked_interventions || [];
  document.getElementById('step3-content').innerHTML = `
    <table class="ev-table">
      <thead>
        <tr>
          <th>Candidate Action</th>
          <th>P(Rec)</th>
          <th>Exp. Value</th>
          <th>Channel Cost</th>
          <th>Discount</th>
          <th>Net EV</th>
          <th>Rationale</th>
        </tr>
      </thead>
      <tbody>
        ${rankings.map(r => `
          <tr class="${r.is_chosen ? 'row-chosen' : ''}">
            <td><strong>${r.action}</strong> ${r.is_chosen ? '★ Chosen' : ''}</td>
            <td>${Math.round(r.p_recovery * 100)}%</td>
            <td>${fmtCurr(r.expected_value)}</td>
            <td>₹${r.channel_cost}</td>
            <td>₹${r.expected_discount}</td>
            <td style="color:${r.net_ev > 0 ? 'var(--green)' : 'var(--red)'}; font-weight:700;">+${fmtCurr(r.net_ev)}</td>
            <td>${r.rationale}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;

  const pol = c.policy_check;
  document.getElementById('step4-content').innerHTML = `
    <div class="policy-gate-grid">
      <div class="gate-box">
        <div class="gate-name">TRAI National DND</div>
        <div class="gate-status ${pol.dnd_status.includes('PASS') ? 'status-pass' : 'status-fail'}">${pol.dnd_status}</div>
      </div>
      <div class="gate-box">
        <div class="gate-name">RBI Quiet Hours</div>
        <div class="gate-status ${pol.quiet_hours.includes('PASS') ? 'status-pass' : 'status-fail'}">${pol.quiet_hours}</div>
      </div>
      <div class="gate-box">
        <div class="gate-name">Margin Guardrail</div>
        <div class="gate-status ${pol.margin_guardrail.includes('PASS') ? 'status-pass' : 'status-fail'}">${pol.margin_guardrail}</div>
      </div>
      <div class="gate-box">
        <div class="gate-name">Frequency Cap</div>
        <div class="gate-status status-pass">${pol.frequency_cap}</div>
      </div>
      <div class="gate-box">
        <div class="gate-name">Dispute Freeze</div>
        <div class="gate-status ${pol.dispute_hold.includes('PASS') ? 'status-pass' : 'status-held'}">${pol.dispute_hold}</div>
      </div>
      <div class="gate-box">
        <div class="gate-name">Human Escalation</div>
        <div class="gate-status ${pol.requires_human ? 'status-held' : 'status-pass'}">${pol.requires_human ? 'Flagged for Review' : 'Auto-Cleared'}</div>
      </div>
    </div>
  `;

  const ex = c.execution;
  document.getElementById('step5-content').innerHTML = `
    <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:12px; font-size:12px;">
      <div><span style="color:var(--muted);">Channel:</span> <strong>${ex.channel}</strong></div>
      <div><span style="color:var(--muted);">Outbox Status:</span> <strong class="badge-pill badge-state-executing">${ex.outbox_status}</strong></div>
      <div><span style="color:var(--muted);">Receipt ID:</span> <strong class="num-font">${ex.delivery_receipt_id || 'N/A'}</strong></div>
      <div><span style="color:var(--muted);">Dispatch Time:</span> <strong class="num-font">${ex.timestamp}</strong></div>
    </div>
    <div style="margin-top:6px; font-size:11px; color:var(--muted);">
      Idempotency Key: <code class="num-font">${ex.idempotency_key}</code> · ${ex.dispatch_note || 'Simulated provider confirmation'}
    </div>
  `;

  const audit = c.audit_trail || [];
  document.getElementById('modal-audit-log').innerHTML = audit.map(a => `
    <div style="display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px solid var(--line); font-size:11.5px;">
      <div>
        <strong>${a.action_or_rule}</strong> (${a.from_state} → ${a.to_state})
        <div style="color:var(--muted); font-size:11px;">${a.notes} [Actor: ${a.actor}]</div>
      </div>
      <div class="num-font" style="color:var(--muted); font-size:11px;">${a.timestamp}</div>
    </div>
  `).join('');

  const actionContainer = document.getElementById('modal-actions-container');
  if (actionContainer) {
    const voiceLinkBtn = `<a href="/voice?case_id=${c.case_id}" target="_blank" class="btn btn-secondary">Launch Live Voice Call (${c.customer_name}) ↗</a>`;
    if (c.requires_human_approval) {
      actionContainer.innerHTML = `
        ${voiceLinkBtn}
        <button class="btn btn-outline" onclick="closeCaseModal()">Close</button>
        <button class="btn btn-danger" onclick="submitHumanAction('${c.case_id}', 'reject')">Suppress</button>
        <button class="btn btn-success" onclick="submitHumanAction('${c.case_id}', 'approve')">Approve Action</button>
      `;
    } else {
      actionContainer.innerHTML = `
        ${voiceLinkBtn}
        <button class="btn btn-outline" onclick="closeCaseModal()">Close</button>
      `;
    }
  }

  modal.classList.add('open');
}

function closeCaseModal() {
  const modal = document.getElementById('case-modal');
  if (modal) modal.classList.remove('open');
}

// =====================================================================
// 4. CHART.JS VISUALIZATIONS
// =====================================================================

function renderLeakChart(breakdown) {
  const ctx = document.getElementById('leakChart');
  if (!ctx || !breakdown) return;

  if (state.charts.leakChart) {
    state.charts.leakChart.destroy();
  }

  const shortMap = {
    failed_payment: 'Failed Pay',
    abandoned_checkout: 'Carts',
    subscription_mandate: 'Mandates',
    b2b_invoice: 'Invoices'
  };
  const labels = breakdown.map(b => shortMap[b.leak_type] || b.title);
  const atRiskData = breakdown.map(b => b.total_at_risk / 100000);
  const recoveredData = breakdown.map(b => b.net_recovered / 100000);

  state.charts.leakChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'At Risk',
          data: atRiskData,
          backgroundColor: '#CBD5E1',
          borderRadius: 4
        },
        {
          label: 'Recovered',
          data: recoveredData,
          backgroundColor: '#2947B8',
          borderRadius: 4
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'top', labels: { boxWidth: 10, font: { family: 'Inter', size: 11 } } }
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { font: { size: 10 } },
          grid: { color: 'rgba(0,0,0,0.04)' }
        },
        x: {
          ticks: { font: { size: 10 } },
          grid: { display: false }
        }
      }
    }
  });
}

function renderPolicyRadarChart(radarData) {
  const ctx = document.getElementById('policyRadarChart');
  if (!ctx || !radarData) return;

  if (state.charts.radarChart) {
    state.charts.radarChart.destroy();
  }

  state.charts.radarChart = new Chart(ctx, {
    type: 'radar',
    data: {
      labels: [
        'Recovery Rate',
        'Compliance Safety',
        'Customer Goodwill',
        'Cost Efficiency',
        'Op. Speed'
      ],
      datasets: [{
        label: 'Policy Profile',
        data: [
          radarData.recovery_strength || 70,
          radarData.compliance_safety || 85,
          radarData.customer_satisfaction || 80,
          radarData.cost_efficiency || 75,
          radarData.operational_speed || 90
        ],
        backgroundColor: 'rgba(41, 71, 184, 0.15)',
        borderColor: '#2947B8',
        pointBackgroundColor: '#2947B8',
        pointBorderColor: '#fff'
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        r: {
          min: 0,
          max: 100,
          ticks: { display: false, stepSize: 25 },
          pointLabels: { font: { size: 10.5, family: 'Inter', weight: '600' } }
        }
      },
      plugins: {
        legend: { display: false }
      }
    }
  });
}

// =====================================================================
// 5. EVENT LISTENERS & INITIALIZATION
// =====================================================================

function switchTab(tabId) {
  state.activeTab = tabId;
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabId);
  });
  document.querySelectorAll('.tab-content-panel').forEach(panel => {
    panel.classList.toggle('active', panel.id === `tab-panel-${tabId}`);
  });

  if (tabId === 'cases') loadCases();
  else if (tabId === 'human_queue') loadHumanQueue();
  else if (tabId === 'suppressed') loadSuppressedCases();
}

function filterByLeakCard(leakType) {
  const select = document.getElementById('filter-leak-type');
  if (state.casesFilter.leak_type === leakType) {
    state.casesFilter.leak_type = '';
    if (select) select.value = '';
  } else {
    state.casesFilter.leak_type = leakType;
    if (select) select.value = leakType;
  }
  state.casesPage = 1;
  switchTab('cases');
  loadCases();
  loadOverview();
}

function applyPolicyPreset(preset) {
  document.querySelectorAll('.preset-btn').forEach(b => b.classList.remove('active'));
  if (window.event && window.event.target) {
    window.event.target.classList.add('active');
  }

  if (preset === 'balanced') {
    state.policyLab = {
      retry_limit: 3,
      discount_cap_pct: 10.0,
      quiet_hours_enabled: true,
      min_case_value: 200.0,
      human_review_threshold: 50000.0
    };
  } else if (preset === 'aggressive') {
    state.policyLab = {
      retry_limit: 5,
      discount_cap_pct: 18.0,
      quiet_hours_enabled: false,
      min_case_value: 0.0,
      human_review_threshold: 80000.0
    };
  } else if (preset === 'compliance') {
    state.policyLab = {
      retry_limit: 2,
      discount_cap_pct: 5.0,
      quiet_hours_enabled: true,
      min_case_value: 500.0,
      human_review_threshold: 25000.0
    };
  }

  document.getElementById('slider-retry').value = state.policyLab.retry_limit;
  document.getElementById('val-retry').textContent = `${state.policyLab.retry_limit} attempts`;

  document.getElementById('slider-discount').value = state.policyLab.discount_cap_pct;
  document.getElementById('val-discount').textContent = `${state.policyLab.discount_cap_pct}%`;

  document.getElementById('switch-quiet-hours').checked = state.policyLab.quiet_hours_enabled;
  document.getElementById('val-quiet-hours').textContent = state.policyLab.quiet_hours_enabled ? 'Enforced' : 'Off';

  document.getElementById('slider-min-val').value = state.policyLab.min_case_value;
  document.getElementById('val-min-val').textContent = fmtCurr(state.policyLab.min_case_value);

  document.getElementById('slider-human-thr').value = state.policyLab.human_review_threshold;
  document.getElementById('val-human-thr').textContent = fmtCurr(state.policyLab.human_review_threshold);

  simulatePolicyLab();
}

const debouncedSimulate = debounce(simulatePolicyLab, 150);

document.addEventListener('DOMContentLoaded', () => {
  loadOverview();
  loadSystemicAlerts();
  loadCases();
  loadHumanQueue();
  loadSuppressedCases();
  simulatePolicyLab();

  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  const filterLeak = document.getElementById('filter-leak-type');
  if (filterLeak) {
    filterLeak.addEventListener('change', (e) => {
      state.casesFilter.leak_type = e.target.value;
      state.casesPage = 1;
      loadCases();
    });
  }

  const filterState = document.getElementById('filter-state');
  if (filterState) {
    filterState.addEventListener('change', (e) => {
      state.casesFilter.state = e.target.value;
      state.casesPage = 1;
      loadCases();
    });
  }

  const filterControl = document.getElementById('filter-control');
  if (filterControl) {
    filterControl.addEventListener('change', (e) => {
      state.casesFilter.is_control_group = e.target.value;
      state.casesPage = 1;
      loadCases();
    });
  }

  const searchInput = document.getElementById('search-cases-input');
  if (searchInput) {
    searchInput.addEventListener('input', debounce((e) => {
      state.casesFilter.search = e.target.value.trim();
      state.casesPage = 1;
      loadCases();
    }, 220));
  }

  const globalSearch = document.getElementById('global-topbar-search');
  if (globalSearch) {
    globalSearch.addEventListener('input', debounce((e) => {
      const val = e.target.value.trim();
      state.casesFilter.search = val;
      if (searchInput) searchInput.value = val;
      state.casesPage = 1;
      switchTab('cases');
      loadCases();
    }, 220));
  }

  const btnPrev = document.getElementById('btn-prev-page');
  const btnNext = document.getElementById('btn-next-page');
  if (btnPrev) {
    btnPrev.addEventListener('click', () => {
      if (state.casesPage > 1) {
        state.casesPage--;
        loadCases();
      }
    });
  }
  if (btnNext) {
    btnNext.addEventListener('click', () => {
      state.casesPage++;
      loadCases();
    });
  }

  const sRetry = document.getElementById('slider-retry');
  if (sRetry) {
    sRetry.addEventListener('input', (e) => {
      state.policyLab.retry_limit = parseInt(e.target.value, 10);
      document.getElementById('val-retry').textContent = `${state.policyLab.retry_limit} attempts`;
      debouncedSimulate();
    });
  }

  const sDiscount = document.getElementById('slider-discount');
  if (sDiscount) {
    sDiscount.addEventListener('input', (e) => {
      state.policyLab.discount_cap_pct = parseFloat(e.target.value);
      document.getElementById('val-discount').textContent = `${state.policyLab.discount_cap_pct}%`;
      debouncedSimulate();
    });
  }

  const sQuiet = document.getElementById('switch-quiet-hours');
  if (sQuiet) {
    sQuiet.addEventListener('change', (e) => {
      state.policyLab.quiet_hours_enabled = e.target.checked;
      document.getElementById('val-quiet-hours').textContent = e.target.checked ? 'Enforced' : 'Off';
      debouncedSimulate();
    });
  }

  const sMinVal = document.getElementById('slider-min-val');
  if (sMinVal) {
    sMinVal.addEventListener('input', (e) => {
      state.policyLab.min_case_value = parseFloat(e.target.value);
      document.getElementById('val-min-val').textContent = fmtCurr(state.policyLab.min_case_value);
      debouncedSimulate();
    });
  }

  const sHuman = document.getElementById('slider-human-thr');
  if (sHuman) {
    sHuman.addEventListener('input', (e) => {
      state.policyLab.human_review_threshold = parseFloat(e.target.value);
      document.getElementById('val-human-thr').textContent = fmtCurr(state.policyLab.human_review_threshold);
      debouncedSimulate();
    });
  }

  const themeToggle = document.getElementById('theme-toggle-btn');
  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      document.body.classList.toggle('dark');
    });
  }
});
