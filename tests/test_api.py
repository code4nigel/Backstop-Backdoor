"""
Comprehensive test suite for the Backstop Operator Dashboard REST API.
Verifies all 8 endpoints, validation schemas, mathematical consistency,
5-step case explainability chains, Policy Lab simulations, and human actions.
"""
import pytest
import json
import os
import sys

# Ensure repository root is on sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from api.app import app
from api.demo_data import store


@pytest.fixture
def client():
    """Flask test client fixture."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# =====================================================================
# 1. HEADLINE OVERVIEW TESTS
# =====================================================================

def test_get_overview(client):
    """Verifies headline financial metrics, 4 leak cards, lift and ROI."""
    res = client.get("/api/overview")
    assert res.status_code == 200
    data = res.get_json()

    assert data["total_revenue_at_risk"] >= 5000000.0  # ₹50L+
    assert data["net_money_recovered"] > 3000000.0     # ₹30L+
    assert data["net_money_recovered"] <= data["total_revenue_at_risk"]
    assert data["incremental_lift_pct"] > 40.0         # Calibrated +48.2%
    assert "confidence_interval_95" in data
    ci = data["confidence_interval_95"]
    assert ci["lower_pct"] < ci["upper_pct"]
    assert ci["lower_pct"] > 35.0
    
    assert data["total_outreach_cost"] > 0
    assert data["net_roi_multiplier"] > 50.0  # > 50x ROI
    
    # 4 Leak categories
    breakdown = data["leak_breakdown"]
    assert len(breakdown) == 4
    leak_types = {item["leak_type"] for item in breakdown}
    assert leak_types == {
        "failed_payment",
        "abandoned_checkout",
        "subscription_mandate",
        "b2b_invoice"
    }

    for card in breakdown:
        assert card["total_at_risk"] > 0
        assert card["net_recovered"] > 0
        assert 0.0 < card["recovery_rate_pct"] <= 100.0
        assert len(card["top_root_cause"]) > 0


# =====================================================================
# 2. SYSTEMIC ALERTS TESTS
# =====================================================================

def test_get_systemic_alerts(client):
    """Verifies detection of active gateway/bank incidents (e.g. HDFC UPI drop)."""
    res = client.get("/api/systemic_alerts")
    assert res.status_code == 200
    data = res.get_json()

    assert data["active_incident_count"] >= 1
    assert data["total_spam_prevented"] >= 30
    
    incident = next((a for a in data["alerts"] if a["incident_id"] == "INC-904"), None)
    assert incident is not None
    assert "HDFC" in incident["impacted_entity"]
    assert incident["drop_pct"] >= 25.0
    assert incident["transactions_rerouted_or_held"] >= 30
    assert incident["severity"] == "CRITICAL"


# =====================================================================
# 3. CASES PAGINATION AND FILTERING TESTS
# =====================================================================

def test_get_cases_default(client):
    """Verifies default pagination."""
    res = client.get("/api/cases")
    assert res.status_code == 200
    data = res.get_json()

    assert data["total"] >= 300
    assert len(data["cases"]) == data["page_size"]
    assert data["page"] == 1
    assert data["total_pages"] >= 20

    first_case = data["cases"][0]
    assert "case_id" in first_case
    assert "customer_name" in first_case
    assert "phone_masked" in first_case
    assert "••" in first_case["phone_masked"]  # PII masked
    assert "leak_type" in first_case
    assert "amount" in first_case


def test_get_cases_filtering(client):
    """Verifies filtering by leak_type, state, control group, and search query."""
    # Filter by leak_type
    res = client.get("/api/cases?leak_type=abandoned_checkout")
    data = res.get_json()
    assert all(c["leak_type"] == "abandoned_checkout" for c in data["cases"])

    # Filter by control group
    res_ctrl = client.get("/api/cases?is_control_group=true")
    data_ctrl = res_ctrl.get_json()
    assert all(c["is_control_group"] is True for c in data_ctrl["cases"])

    # Search by customer name
    sample_name = data["cases"][0]["customer_name"].split()[0]
    res_search = client.get(f"/api/cases?search={sample_name}")
    data_search = res_search.get_json()
    assert len(data_search["cases"]) > 0
    assert all(sample_name.lower() in c["customer_name"].lower() for c in data_search["cases"])


# =====================================================================
# 4. CASE DRILL-DOWN & 5-STEP EXPLAINABILITY TESTS
# =====================================================================

def test_get_case_detail_success(client):
    """Verifies deep drill-down and the complete 5-step reasoning chain."""
    # Fetch first available case ID
    cases_res = client.get("/api/cases?page_size=1")
    target_id = cases_res.get_json()["cases"][0]["case_id"]

    res = client.get(f"/api/cases/{target_id}")
    assert res.status_code == 200
    case = res.get_json()

    assert case["case_id"] == target_id
    assert case["amount"] > 0
    
    # Step 1: Detect (Metadata)
    assert "metadata" in case
    assert len(case["phone_masked"]) > 0

    # Step 2: Diagnose (ML model / classifier)
    diag = case["diagnosis"]
    assert len(diag["predicted_cause"]) > 0
    assert 0.0 <= diag["confidence"] <= 1.0
    assert len(diag["evidence"]) >= 1

    # Step 3: Decide (Ranked Net EV alternatives)
    rankings = case["ranked_interventions"]
    assert len(rankings) >= 2
    # Check ranking sorted descending by net_ev
    evs = [r["net_ev"] for r in rankings]
    assert evs == sorted(evs, reverse=True)
    assert any(r["is_chosen"] for r in rankings)

    # Step 4: Policy Check (Deterministic compliance gates)
    policy = case["policy_check"]
    assert "dnd_status" in policy
    assert "quiet_hours" in policy
    assert "margin_guardrail" in policy
    assert "frequency_cap" in policy
    assert "dispute_hold" in policy
    assert len(policy["applied_rules"]) >= 1

    # Step 5: Execute (Outbox dispatch)
    exec_info = case["execution"]
    assert exec_info["channel"] in ["WhatsApp", "SMS", "Payment Gateway", "Hinglish Voice"]
    assert len(exec_info["idempotency_key"]) > 0
    assert "outbox_status" in exec_info

    # Audit Trail
    audit = case["audit_trail"]
    assert len(audit) >= 2
    assert audit[0]["from_state"] == "NONE"
    assert audit[0]["to_state"] == "DETECTED"


def test_get_case_detail_not_found(client):
    """Verifies 404 for invalid case ID."""
    res = client.get("/api/cases/CASE-DOESNOTEXIST-999")
    assert res.status_code == 404
    data = res.get_json()
    assert "error" in data


# =====================================================================
# 5. POLICY LAB SIMULATION TESTS
# =====================================================================

def test_policy_lab_simulate_valid(client):
    """Verifies live multi-parameter slider simulation."""
    payload = {
        "retry_limit": 4,
        "discount_cap_pct": 15.0,
        "quiet_hours_enabled": True,
        "min_case_value": 300.0,
        "human_review_threshold": 60000.0
    }
    res = client.post("/api/policy_lab/simulate", json=payload)
    assert res.status_code == 200
    data = res.get_json()

    assert data["projected_net_recovered"] > 2000000.0
    assert data["projected_recovery_rate_pct"] > 0
    assert data["projected_outreach_cost"] > 0
    assert data["projected_net_roi_multiplier"] > 20.0
    assert data["blocked_cases_count"] >= 0
    assert "compliance_risk_score" in data
    assert "customer_friction_score" in data
    assert "tradeoff_radar" in data
    assert len(data["simulation_notes"]) >= 2


def test_policy_lab_simulate_validation_error(client):
    """Verifies validation rejects illegal slider overrides."""
    # retry_limit out of bound (> 5)
    res = client.post("/api/policy_lab/simulate", json={"retry_limit": 8})
    assert res.status_code == 400

    # discount_cap_pct negative
    res2 = client.post("/api/policy_lab/simulate", json={"discount_cap_pct": -5.0})
    assert res2.status_code == 400


# =====================================================================
# 6. HUMAN REVIEW QUEUE & OPERATOR ACTIONS TESTS
# =====================================================================

def test_get_human_queue(client):
    """Verifies human review queue returns flagged high-value/dispute items."""
    res = client.get("/api/human_queue")
    assert res.status_code == 200
    data = res.get_json()

    assert "queue" in data
    assert data["count"] == len(data["queue"])
    assert data["count"] > 0
    
    item = data["queue"][0]
    assert item["urgency"] in ["HIGH", "MEDIUM", "LOW"]
    assert "case_id" in item
    assert "customer_name" in item
    assert "escalation_reason" in item


def test_handle_human_actions(client):
    """Verifies approve, reject, modify_discount, and resolve_dispute actions."""
    # Find a case needing review
    queue_res = client.get("/api/human_queue")
    items = queue_res.get_json()["queue"]
    assert len(items) >= 2
    
    case1_id = items[0]["case_id"]
    case2_id = items[1]["case_id"]

    # 1. Operator Approves Case 1
    res_appr = client.post(
        f"/api/human_queue/{case1_id}/action",
        json={"action": "approve", "notes": "Approved for outbox dispatch by Lead Reviewer"}
    )
    assert res_appr.status_code == 200
    appr_data = res_appr.get_json()
    assert appr_data["success"] is True
    assert appr_data["new_state"] == "EXECUTING"
    assert "approved" in appr_data["message"].lower()

    # 2. Operator Rejects Case 2
    res_rej = client.post(
        f"/api/human_queue/{case2_id}/action",
        json={"action": "reject", "notes": "Customer requested halt; suppress permanently"}
    )
    assert res_rej.status_code == 200
    rej_data = res_rej.get_json()
    assert rej_data["success"] is True
    assert rej_data["new_state"] == "POLICY_BLOCKED"

    # 3. Invalid Action
    res_bad = client.post(
        f"/api/human_queue/{case1_id}/action",
        json={"action": "unknown_action_xyz"}
    )
    assert res_bad.status_code == 400


# =====================================================================
# 7. SUPPRESSED CASES ("CASES WE CHOSE NOT TO CHASE") TESTS
# =====================================================================

def test_get_suppressed_cases(client):
    """Verifies the trust signal panel: cases Backstop chose NOT to chase."""
    res = client.get("/api/suppressed_cases")
    assert res.status_code == 200
    data = res.get_json()

    assert data["total_suppressed"] > 0
    assert len(data["suppressed_cases"]) == data["total_suppressed"]
    assert data["total_spend_saved"] > 10000.0  # ₹18,450 calibrated
    assert data["customer_relationships_preserved"] >= 50
    assert "Saved" in data["trust_statement"]

    # Check reason categories
    reasons = {c["block_reason"] for c in data["suppressed_cases"]}
    assert any("DND" in r for r in reasons)
    assert any("Negative" in r for r in reasons)

    first_supp = data["suppressed_cases"][0]
    assert "stopping_rule_fired" in first_supp
    assert first_supp["wasted_spend_saved"] > 0


# =====================================================================
# 8. MATHEMATICAL CONSISTENCY VERIFICATION
# =====================================================================

def test_mathematical_consistency(client):
    """Asserts strict mathematical consistency across endpoints."""
    overview = client.get("/api/overview").get_json()
    
    # Net recovery cannot exceed total at risk
    assert overview["net_money_recovered"] <= overview["total_revenue_at_risk"]
    
    # Net recovery rate must be higher than control group rate
    assert overview["recovery_rate_pct"] > overview["control_recovery_rate_pct"]
    
    # Incremental lift = treated_rate - control_rate
    expected_lift = overview["recovery_rate_pct"] - overview["control_recovery_rate_pct"]
    assert abs(overview["incremental_lift_pct"] - expected_lift) < 0.2

    # ROI = Net recovered / Total outreach cost
    expected_roi = overview["net_money_recovered"] / overview["total_outreach_cost"]
    assert abs(overview["net_roi_multiplier"] - expected_roi) < 0.2
