/**
 * Backstop Operations Command Center & Policy Lab
 * Reactive UI Controller & Chart.js Engine
 */

// Global State
const state = {
  activeTab: 'cases',
  casesPage: 1,
  casesPageSize: 12,
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

// Utilities
const escapeHtml = (str) => {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
};

const fmtCurr = (n) => {
  if (n === undefined || n === null) return '₹0';
  return '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
};

const fmtLakh = (n) => {
  if (!n) return '₹0';
  const l = n / 100000;
  return `₹${l.toFixed(1)}L`;
};

async function runDemoBatch() {
  const btn = document.getElementById('btn-run-batch');
  const originalText = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span style="display:inline-block; width:10px; height:10px; border:2px solid #fff; border-top-color:transparent; border-radius:50%; animation:spin 0.6s linear infinite; margin-right:4px;"></span> Running...`;
  }
  try {
    const res = await fetch('/api/run_batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seed: 42, count: 320 })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const result = await res.json();
    showToast(result.message || 'Evaluated demo batch through PolicyEngine', 'success');
    await Promise.all([loadOverview(), loadCases(), loadSystemicAlerts()]);
  } catch (err) {
    console.error('Failed to run demo batch:', err);
    showToast('Failed to run batch: ' + err.message, 'danger');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = originalText;
    }
  }
}

const showToast = (message, type = 'success') => {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <span>${type === 'success' ? '✓' : '⚠️'}</span>
    <span>${message}</span>
  `;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 250);
  }, 3500);
};

// Debounce helper
let debounceTimeout = null;
const debounce = (func, delay = 200) => {
  return (...args) => {
    clearTimeout(debounceTimeout);
    debounceTimeout = setTimeout(() => func(...args), delay);
  };
};

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
    closeCaseModal();
  } catch (err) {
    showToast(err.message, 'danger');
  }
}

// =====================================================================
// 2. RENDER FUNCTIONS
// =====================================================================

function renderOverview(data) {
  // Headline Metrics Bar
  const elRisk = document.getElementById('metric-total-risk');
  if (elRisk) elRisk.textContent = fmtLakh(data.total_revenue_at_risk);
  
  const elRec = document.getElementById('metric-total-rec');
  if (elRec) elRec.textContent = fmtLakh(data.net_money_recovered);
  
  const elLift = document.getElementById('metric-lift');
  if (elLift) elLift.textContent = `+${data.incremental_lift_pct}%`;
  
  const elCi = document.getElementById('metric-ci');
  if (elCi) elCi.textContent = `[95% CI: ${data.confidence_interval_95.lower_pct}% – ${data.confidence_interval_95.upper_pct}%]`;
  
  const elRoi = document.getElementById('metric-cost-roi');
  if (elRoi) elRoi.textContent = `${fmtCurr(data.total_outreach_cost)} spent → ${data.net_roi_multiplier}x ROI`;

  const elActive = document.getElementById('metric-active-count');
  if (elActive) elActive.textContent = data.active_cases_count;

  const elSupp = document.getElementById('metric-suppressed-count');
  if (elSupp) elSupp.textContent = data.suppressed_count;
  
  // Update sidebar counter
  const hqCount = document.getElementById('sidebar-hq-counter');
  if (hqCount) hqCount.textContent = data.human_queue_count;

  // 4 Leak Category Cards
  const leakContainer = document.getElementById('leak-cards-container');
  if (leakContainer && data.leak_breakdown) {
    leakContainer.innerHTML = data.leak_breakdown.map(card => `
      <div class="leak-card ${state.casesFilter.leak_type === card.leak_type ? 'active-filter' : ''}" 
           onclick="filterByLeakCard('${escapeHtml(card.leak_type)}')">
        <div class="leak-card-title-row">
          <span class="leak-card-title">${escapeHtml(card.title)}</span>
          <span class="leak-badge">${escapeHtml(card.at_risk_count)} cases</span>
        </div>
        <div class="leak-nums-row">
          <span class="leak-at-risk">At Risk: <strong class="num-font">${fmtLakh(card.total_at_risk)}</strong></span>
          <span class="leak-rate-highlight num-font">${escapeHtml(card.recovery_rate_pct)}%</span>
        </div>
        <div class="progress-track">
          <div class="progress-fill" style="width: ${Number(card.recovery_rate_pct)}%"></div>
        </div>
        <div class="leak-footer">
          <span>Top Cause:</span>
          <span class="leak-cause-tag">${escapeHtml(card.top_root_cause)}</span>
        </div>
      </div>
    `).join('');
  }

  // Chart: Leak Breakdown
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
    <div class="incident-strip">
      <div class="incident-strip-left">
        <span class="incident-badge-pill">${escapeHtml(alert.status || 'ACTIVE_MITIGATION')}</span>
        <span class="incident-title-text">${escapeHtml(alert.title)}</span>
        <span class="incident-meta-text">(${escapeHtml(alert.impacted_entity)} · ${escapeHtml(alert.drop_pct)}% drop · ${escapeHtml(alert.spam_prevented_count)} cases held from spam)</span>
      </div>
      <a href="#section-active-cases" onclick="filterByLeakCard('failed_payment')" class="incident-action-link">View impacted cases →</a>
    </div>
  `;
}

function renderCasesTable(data) {
  const tbody = document.getElementById('cases-table-body');
  if (!tbody) return;

  if (!data.cases || data.cases.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:32px; color:var(--text-muted);">No cases match the selected filters.</td></tr>`;
    return;
  }

  tbody.innerHTML = data.cases.map(c => {
    let stateClass = 'badge-state-ready';
    if (c.state === 'RECOVERED') stateClass = 'badge-state-recovered';
    else if (c.state === 'EXECUTING') stateClass = 'badge-state-executing';
    else if (c.state === 'PROMISED') stateClass = 'badge-state-promised';
    else if (c.state === 'DISPUTED') stateClass = 'badge-state-disputed';
    else if (c.state === 'POLICY_BLOCKED') stateClass = 'badge-state-blocked';

    return `
      <tr onclick="openCaseDetail('${escapeHtml(c.case_id)}')">
        <td class="num-font" style="font-weight:600; color:var(--primary-accent);">${escapeHtml(c.case_id)}</td>
        <td>
          <div style="font-weight:600;">${escapeHtml(c.customer_name)}</div>
          <div class="num-font" style="font-size:11px; color:var(--text-muted);">${escapeHtml(c.phone_masked)}</div>
        </td>
        <td>
          <span class="leak-badge">${escapeHtml(c.leak_type.replace('_', ' '))}</span>
          ${c.is_control_group ? '<span class="badge-control-tag">HOLD-OUT</span>' : ''}
          ${c.is_systemic ? '<span class="badge-pill" style="background:#FEE2E2; color:#B91C1C;">SYSTEMIC</span>' : ''}
        </td>
        <td class="num-font" style="font-weight:700;">${fmtCurr(c.amount)}</td>
        <td>
          <div>${escapeHtml(c.predicted_cause)}</div>
          <div style="font-size:10.5px; color:var(--text-muted);">${Math.round(c.confidence * 100)}% confidence</div>
        </td>
        <td class="num-font" style="color:#059669; font-weight:600;">+${fmtCurr(c.net_ev)}</td>
        <td><span class="badge-pill ${stateClass}">${escapeHtml(c.state)}</span></td>
      </tr>
    `;
  }).join('');

  // Pagination info
  document.getElementById('pagination-info').textContent = 
    `Showing page ${data.page} of ${data.total_pages} (${data.total} total cases)`;
  document.getElementById('btn-prev-page').disabled = (data.page <= 1);
  document.getElementById('btn-next-page').disabled = (data.page >= data.total_pages);
}

function renderHumanQueue(data) {
  const tbody = document.getElementById('human-queue-body');
  const countBadge = document.getElementById('hq-tab-counter');
  if (countBadge) countBadge.textContent = data.count || 0;
  if (!tbody) return;

  if (!data.queue || data.queue.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:32px; color:var(--text-muted);">🎉 All flagged cases reviewed! Zero items in human approval escrow.</td></tr>`;
    return;
  }

  tbody.innerHTML = data.queue.map(item => `
    <tr>
      <td class="num-font" style="font-weight:600; color:var(--primary-accent);">${escapeHtml(item.case_id)}</td>
      <td>
        <div style="font-weight:600;">${escapeHtml(item.customer_name)}</div>
        <div class="num-font" style="font-size:11.5px; font-weight:700;">${fmtCurr(item.amount)}</div>
      </td>
      <td>
        <span class="badge-pill ${item.urgency === 'HIGH' ? 'badge-state-blocked' : 'badge-state-disputed'}">${escapeHtml(item.urgency)}</span>
      </td>
      <td>
        <div style="font-weight:500;">${escapeHtml(item.escalation_reason)}</div>
        <div style="font-size:11px; color:var(--text-muted);">${escapeHtml(item.diagnosis_summary)}</div>
      </td>
      <td>
        <span class="leak-badge">${escapeHtml(item.recommended_action)}</span>
      </td>
      <td>
        <div style="display:flex; gap:6px;">
          <button class="btn btn-sm btn-success" onclick="event.stopPropagation(); submitHumanAction('${escapeHtml(item.case_id)}', 'approve')">Approve</button>
          <button class="btn btn-sm btn-outline" onclick="event.stopPropagation(); submitHumanAction('${escapeHtml(item.case_id)}', 'reject')">Reject</button>
          <button class="btn btn-sm btn-danger" onclick="event.stopPropagation(); submitHumanAction('${escapeHtml(item.case_id)}', 'resolve_dispute')">Freeze</button>
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
      <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:16px;">
        <div>
          <div style="font-size:16px; font-weight:700; color:#065F46;">🛡️ ${escapeHtml(data.trust_statement)}</div>
          <div style="font-size:12px; color:var(--text-muted); margin-top:3px;">
            By enforcing strict TRAI DND checks, negative EV margin filters, and dispute freezes, Backstop guarantees no brand damage.
          </div>
        </div>
        <div style="display:flex; gap:10px;">
          ${Object.entries(data.breakdown_by_rule || {}).map(([r, cnt]) => `
            <span class="alert-chip" style="background:#F1F5F9; border-color:#CBD5E1; color:#334155;">
              ${escapeHtml(r)}: <strong>${escapeHtml(cnt)}</strong>
            </span>
          `).join('')}
        </div>
      </div>
    `;
  }

  if (tbody && data.suppressed_cases) {
    tbody.innerHTML = data.suppressed_cases.map(item => `
      <tr onclick="openCaseDetail('${escapeHtml(item.case_id)}')">
        <td class="num-font" style="font-weight:600; color:var(--primary-accent);">${escapeHtml(item.case_id)}</td>
        <td>
          <div style="font-weight:600;">${escapeHtml(item.customer_name)}</div>
          <div class="num-font" style="font-size:11px; color:var(--text-muted);">${escapeHtml(item.phone_masked)}</div>
        </td>
        <td><span class="leak-badge">${escapeHtml(item.leak_type.replace('_', ' '))}</span></td>
        <td class="num-font" style="font-weight:700;">${fmtCurr(item.amount)}</td>
        <td>
          <span class="badge-pill badge-state-blocked">${escapeHtml(item.block_reason)}</span>
          <div style="font-size:10.5px; color:var(--text-muted); margin-top:2px;">Fired: ${escapeHtml(item.stopping_rule_fired)}</div>
        </td>
        <td>
          <div style="color:#059669; font-weight:600;">₹${escapeHtml(item.wasted_spend_saved)} saved</div>
          <div style="font-size:11px; color:var(--text-muted);">${escapeHtml(item.customer_goodwill_preserved)}</div>
        </td>
      </tr>
    `).join('');
  }
}

function renderPolicySimulation(data) {
  // Update projections
  document.getElementById('sim-projected-rec').textContent = fmtLakh(data.projected_net_recovered);
  document.getElementById('sim-delta-badge').textContent = `${data.delta_vs_baseline_pct >= 0 ? '+' : ''}${data.delta_vs_baseline_pct}% vs baseline`;
  document.getElementById('sim-roi').textContent = `${data.projected_net_roi_multiplier}x`;
  document.getElementById('sim-blocked-cases').textContent = `${data.blocked_cases_count} cases`;
  document.getElementById('sim-cost').textContent = fmtCurr(data.projected_outreach_cost);
  
  // Update notes
  const notesContainer = document.getElementById('sim-notes-container');
  if (notesContainer && data.simulation_notes) {
    notesContainer.innerHTML = data.simulation_notes.map(n => `<div>• ${n}</div>`).join('');
  }

  // Update Radar Chart
  renderPolicyRadarChart(data.tradeoff_radar);
}

// =====================================================================
// 3. CASE DRILL-DOWN MODAL (5-STEP EXPLAINABILITY CHAIN)
// =====================================================================

function renderCaseModal(c) {
  const modal = document.getElementById('case-modal');
  if (!modal) return;

  document.getElementById('modal-case-id').textContent = c.case_id;
  document.getElementById('modal-cust-name').textContent = c.customer_name;
  document.getElementById('modal-amount').textContent = fmtCurr(c.amount);

  // Step 1: Detect
  document.getElementById('step1-content').innerHTML = `
    <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:12px; font-size:12px;">
      <div><span style="color:var(--text-muted);">Leak Type:</span> <strong class="leak-badge">${escapeHtml(c.leak_type)}</strong></div>
      <div><span style="color:var(--text-muted);">Timestamp:</span> <strong class="num-font">${escapeHtml(c.created_at_str)}</strong></div>
      <div><span style="color:var(--text-muted);">Days at Risk:</span> <strong>${escapeHtml(c.days_at_risk)} days</strong></div>
      <div><span style="color:var(--text-muted);">Masked Phone:</span> <strong class="num-font">${escapeHtml(c.phone_masked)}</strong></div>
    </div>
    <div style="margin-top:10px; font-size:11.5px; background:var(--bg-surface-elevated); padding:8px 12px; border-radius:var(--radius-sm);">
      Metadata: ${Object.entries(c.metadata || {}).map(([k, v]) => `<code>${escapeHtml(k)}: ${escapeHtml(v)}</code>`).join(' | ')}
    </div>
  `;

  // Step 2: Diagnose
  const diag = c.diagnosis || {};
  document.getElementById('step2-content').innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
      <div>
        <span style="font-size:11px; text-transform:uppercase; color:var(--text-muted);">Predicted Root Cause</span>
        <div style="font-size:17px; font-weight:700; color:var(--primary-accent);">${escapeHtml(diag.predicted_cause)}</div>
      </div>
      <div style="text-align:right;">
        <span style="font-size:11px; text-transform:uppercase; color:var(--text-muted);">Model Confidence</span>
        <div class="num-font" style="font-size:18px; font-weight:700; color:#059669;">${Math.round((diag.confidence || 0) * 100)}%</div>
      </div>
    </div>
    <div style="font-size:12px; margin-bottom:6px; font-weight:600; color:var(--text-muted);">Extracted Diagnostic Evidence:</div>
    <ul style="padding-left:18px; font-size:12px; color:var(--text-main); line-height:1.6;">
      ${(diag.evidence || []).map(e => `<li>${escapeHtml(e)}</li>`).join('')}
    </ul>
  `;

  // Step 3: Decide (EV Ranking Table)
  const rankings = c.ranked_interventions || [];
  document.getElementById('step3-content').innerHTML = `
    <div style="font-size:12px; color:var(--text-muted); margin-bottom:6px;">
      Cost-Aware Net EV Calculation: <code>Net EV = (P_rec × Amount) - Cost_channel - Discount - Risk_penalty</code>
    </div>
    <table class="ev-table">
      <thead>
        <tr>
          <th>Candidate Action</th>
          <th>P(Rec)</th>
          <th>Exp. Value</th>
          <th>Channel Cost</th>
          <th>Discount</th>
          <th>Net EV</th>
          <th>Selection Rationale</th>
        </tr>
      </thead>
      <tbody>
        ${rankings.map(r => `
          <tr class="${r.is_chosen ? 'row-chosen' : ''}">
            <td><strong>${escapeHtml(r.action)}</strong> ${r.is_chosen ? '★ WINNER' : ''}</td>
            <td>${Math.round((r.p_recovery || 0) * 100)}%</td>
            <td>${fmtCurr(r.expected_value)}</td>
            <td>₹${escapeHtml(r.channel_cost)}</td>
            <td>₹${escapeHtml(r.expected_discount)}</td>
            <td style="color:${r.net_ev > 0 ? '#059669' : '#DC2626'}; font-weight:700;">+${fmtCurr(r.net_ev)}</td>
            <td>${escapeHtml(r.rationale)}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;

  // Step 4: Policy Check
  const pol = c.policy_check || {};
  document.getElementById('step4-content').innerHTML = `
    <div class="policy-gate-grid">
      <div class="gate-box">
        <div class="gate-name">TRAI National DND Check</div>
        <div class="gate-status ${pol.dnd_status && pol.dnd_status.includes('PASS') ? 'status-pass' : 'status-fail'}">${escapeHtml(pol.dnd_status || 'PASS')}</div>
      </div>
      <div class="gate-box">
        <div class="gate-name">RBI Quiet Hours (8PM-8AM)</div>
        <div class="gate-status ${pol.quiet_hours && pol.quiet_hours.includes('PASS') ? 'status-pass' : 'status-fail'}">${escapeHtml(pol.quiet_hours || 'PASS')}</div>
      </div>
      <div class="gate-box">
        <div class="gate-name">Margin Discount Guardrail</div>
        <div class="gate-status ${pol.margin_guardrail && pol.margin_guardrail.includes('PASS') ? 'status-pass' : 'status-fail'}">${escapeHtml(pol.margin_guardrail || 'PASS')}</div>
      </div>
      <div class="gate-box">
        <div class="gate-name">Anti-Spam Frequency Cap</div>
        <div class="gate-status status-pass">${escapeHtml(pol.frequency_cap || 'PASS')}</div>
      </div>
      <div class="gate-box">
        <div class="gate-name">Dispute Freeze Protection</div>
        <div class="gate-status ${pol.dispute_hold && pol.dispute_hold.includes('PASS') ? 'status-pass' : 'status-held'}">${escapeHtml(pol.dispute_hold || 'PASS')}</div>
      </div>
      <div class="gate-box">
        <div class="gate-name">Human Escalation Floor</div>
        <div class="gate-status ${pol.requires_human ? 'status-held' : 'status-pass'}">${pol.requires_human ? 'Flagged for Human' : 'Auto-Cleared'}</div>
      </div>
    </div>
  `;

  // Step 5: Execute
  const ex = c.execution || {};
  document.getElementById('step5-content').innerHTML = `
    <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:12px; font-size:12px;">
      <div><span style="color:var(--text-muted);">Channel:</span> <strong>${escapeHtml(ex.channel)}</strong></div>
      <div><span style="color:var(--text-muted);">Outbox Status:</span> <strong class="badge-pill badge-state-executing">${escapeHtml(ex.outbox_status)}</strong></div>
      <div><span style="color:var(--text-muted);">Receipt ID:</span> <strong class="num-font">${escapeHtml(ex.delivery_receipt_id || 'N/A')}</strong></div>
      <div><span style="color:var(--text-muted);">Dispatch Time:</span> <strong class="num-font">${escapeHtml(ex.timestamp)}</strong></div>
    </div>
    <div style="margin-top:8px; font-size:11.5px; color:var(--text-muted);">
      Idempotency Key: <code class="num-font">${escapeHtml(ex.idempotency_key)}</code>
    </div>
  `;

  // Audit Log Timeline
  const audit = c.audit_trail || [];
  document.getElementById('modal-audit-log').innerHTML = audit.map(a => `
    <div style="display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--border-subtle); font-size:12px;">
      <div>
        <strong>${escapeHtml(a.action_or_rule)}</strong> (${escapeHtml(a.from_state)} ➔ ${escapeHtml(a.to_state)})
        <div style="color:var(--text-muted); font-size:11px;">${escapeHtml(a.notes)} [Actor: ${escapeHtml(a.actor)}]</div>
      </div>
      <div class="num-font" style="color:var(--text-muted); font-size:11px;">${escapeHtml(a.timestamp)}</div>
    </div>
  `).join('');

  // Operator Action Buttons in Modal
  const actionContainer = document.getElementById('modal-actions-container');
  if (actionContainer) {
    if (c.requires_human_approval) {
      actionContainer.innerHTML = `
        <button class="btn btn-outline" onclick="closeCaseModal()">Close</button>
        <button class="btn btn-danger" onclick="submitHumanAction('${escapeHtml(c.case_id)}', 'reject')">Reject & Suppress</button>
        <button class="btn btn-success" onclick="submitHumanAction('${escapeHtml(c.case_id)}', 'approve')">Approve Action</button>
      `;
    } else {
      actionContainer.innerHTML = `
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

  const labels = breakdown.map(b => b.title);
  const atRiskData = breakdown.map(b => b.total_at_risk / 100000);
  const recoveredData = breakdown.map(b => b.net_recovered / 100000);

  state.charts.leakChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'Total at Risk (₹ Lakhs)',
          data: atRiskData,
          backgroundColor: '#94A3B8',
          borderRadius: 6
        },
        {
          label: 'Net Recovered (₹ Lakhs)',
          data: recoveredData,
          backgroundColor: '#10B981',
          borderRadius: 6
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'top', labels: { boxWidth: 12, font: { family: 'Inter' } } },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ₹${ctx.parsed.y.toFixed(1)} Lakhs`
          }
        }
      },
      scales: {
        y: {
          beginAtZero: true,
          title: { display: true, text: '₹ Lakhs' },
          grid: { color: 'rgba(0,0,0,0.05)' }
        },
        x: {
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
        'Operational Speed'
      ],
      datasets: [{
        label: 'Policy Balance Profile',
        data: [
          radarData.recovery_strength || 70,
          radarData.compliance_safety || 85,
          radarData.customer_satisfaction || 80,
          radarData.cost_efficiency || 75,
          radarData.operational_speed || 90
        ],
        backgroundColor: 'rgba(37, 99, 235, 0.2)',
        borderColor: '#2563EB',
        pointBackgroundColor: '#2563EB',
        pointBorderColor: '#fff',
        pointHoverBackgroundColor: '#fff',
        pointHoverBorderColor: '#2563EB'
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
  loadCases();
  loadOverview();
}

function applyPolicyPreset(preset) {
  document.querySelectorAll('.preset-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');

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

  // Update DOM sliders
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
  // Initial loads
  loadOverview();
  loadSystemicAlerts();
  loadCases();
  loadHumanQueue();
  loadSuppressedCases();
  simulatePolicyLab();

  // Tab switching
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  // Table filters
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
    }, 250));
  }

  // Pagination buttons
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

  // Policy Lab Sliders Listeners
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

  // Dark Mode Toggle
  const themeToggle = document.getElementById('theme-toggle-btn');
  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      document.body.classList.toggle('dark');
      const isDark = document.body.classList.contains('dark');
      themeToggle.textContent = isDark ? '☀️' : '🌙';
    });
  }
});
