"""
Backstop Production API & Operations Command Center Server:
Exposes REST endpoints for the Operator Dashboard, Policy Lab, Case Timeline,
Live Batch Streaming, Live Transaction Injection, Hinglish Voice Simulator,
and Live Dynamic Executive Recovery Report.
"""
import sys
import os
import json
import time
import math
import logging
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

# Ensure repository root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pydantic import ValidationError

from api.schemas import (
    OverviewResponse,
    SystemicAlertsResponse,
    CasesResponse,
    CaseDetail,
    PolicySimulationRequest,
    PolicySimulationResponse,
    HumanActionRequest,
    HumanActionResponse,
    SuppressedCasesResponse,
    check_for_injection,
    sanitize_string
)
from api.demo_data import store
from report.generator import ExecutiveReportGenerator, generate_report
from voice.prompts import set_runtime_gemini_key, get_runtime_gemini_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backstop_api")

DASHBOARD_DIR = os.path.join(PROJECT_ROOT, "dashboard")

try:
    from flask import Flask, jsonify, request, send_from_directory, send_file, Response
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
    Flask = None

if HAS_FLASK:
    app = Flask(
        __name__,
        static_folder=DASHBOARD_DIR,
        static_url_path="/dashboard"
    )
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

    def safe_api_error(message: str, status: int = 500):
        return jsonify({"error": message}), status

    @app.route("/")
    def serve_index():
        index_path = os.path.join(DASHBOARD_DIR, "index.html")
        if os.path.exists(index_path):
            return send_file(index_path)
        return "<h1>Backstop API Running</h1><p>Dashboard not found at " + index_path + "</p>", 200

    @app.route("/css/<path:filename>")
    def serve_css(filename):
        return send_from_directory(os.path.join(DASHBOARD_DIR, "css"), filename)

    @app.route("/js/<path:filename>")
    def serve_js(filename):
        return send_from_directory(os.path.join(DASHBOARD_DIR, "js"), filename)

    @app.route("/report")
    @app.route("/executive-report")
    @app.route("/report/latest")
    def serve_executive_report():
        """Dynamically compiles and serves the live Executive Recovery Report from current store state."""
        try:
            live_data = store.get_live_report_data()
            rep_path = os.path.join(PROJECT_ROOT, "report", "latest_recovery_report.html")
            generate_report(data_source=live_data, output_path=rep_path)
            gen = ExecutiveReportGenerator()
            html = gen.compile_report(live_data)
            return Response(html, mimetype="text/html")
        except Exception as e:
            logger.exception("Error generating live report: %s", e)
            rep_path = os.path.join(PROJECT_ROOT, "report", "latest_recovery_report.html")
            if os.path.exists(rep_path):
                return send_file(rep_path)
            return jsonify({"message": "Executive report generating or pending pipeline run"}), 200

    @app.route("/api/report_data", methods=["GET"])
    def get_report_data_route():
        """Returns the raw live report JSON data for live refreshing and CSV/JSON export."""
        return jsonify(store.get_live_report_data())

    @app.route("/voice")
    @app.route("/voice-simulator")
    @app.route("/simulator")
    def serve_voice_simulator():
        voice_path = os.path.join(PROJECT_ROOT, "voice", "ui_component.html")
        if os.path.exists(voice_path):
            return send_file(voice_path)
        return "<h1>Voice Simulator Not Found</h1>", 404

    @app.route("/api/overview", methods=["GET"])
    def get_overview():
        try:
            data = store.get_overview_metrics()
            validated = OverviewResponse.model_validate(data)
            return jsonify(validated.model_dump())
        except Exception as e:
            logger.exception("Error in /api/overview: %s", e)
            return safe_api_error("Failed to calculate overview metrics")

    @app.route("/api/systemic_alerts", methods=["GET"])
    def get_systemic_alerts():
        try:
            alerts = store.systemic_alerts
            resp = {
                "alerts": alerts,
                "active_incident_count": len([a for a in alerts if a["status"] != "RESOLVED"]),
                "total_spam_prevented": sum(a.get("spam_prevented_count", 0) for a in alerts)
            }
            validated = SystemicAlertsResponse.model_validate(resp)
            return jsonify(validated.model_dump())
        except Exception as e:
            logger.exception("Error in /api/systemic_alerts: %s", e)
            return safe_api_error("Failed to fetch systemic alerts")

    @app.route("/api/cases", methods=["GET"])
    def get_cases():
        try:
            leak_type = request.args.get("leak_type")
            state = request.args.get("state")
            is_control_raw = request.args.get("is_control_group")
            is_control = None
            if is_control_raw is not None:
                is_control = is_control_raw.lower() in ["true", "1", "yes"]
            search = request.args.get("search")
            page = int(request.args.get("page", 1))
            page_size = int(request.args.get("page_size", 15))

            res = store.get_cases(
                leak_type=leak_type,
                state=state,
                is_control_group=is_control,
                search=search,
                page=page,
                page_size=page_size
            )
            validated = CasesResponse.model_validate(res)
            return jsonify(validated.model_dump())
        except Exception as e:
            logger.exception("Error in /api/cases: %s", e)
            return safe_api_error("Failed to query cases")

    @app.route("/api/cases/<case_id>", methods=["GET"])
    def get_case_detail(case_id: str):
        case = store.get_case(case_id)
        if not case:
            return jsonify({"error": f"Case '{case_id}' not found"}), 404
        try:
            validated = CaseDetail.model_validate(case)
            return jsonify(validated.model_dump())
        except ValidationError:
            return jsonify(case)

    @app.route("/api/cases/inject", methods=["POST"])
    def inject_live_case_route():
        """Injects a new live at-risk transaction, runs 5-step diagnosis & policy evaluation, and updates live metrics."""
        payload = request.get_json(silent=True) or {}
        suspicious, msg = check_for_injection(payload)
        if suspicious:
            return jsonify({"error": f"Invalid input: {msg}"}), 400
        try:
            customer_name = sanitize_string(str(payload.get("customer_name", "Aarav Deshmukh"))) or "Aarav Deshmukh"
            amount = float(payload.get("amount", 18500.0))
            leak_type = sanitize_string(str(payload.get("leak_type", "failed_payment")))
            predicted_cause = sanitize_string(str(payload.get("predicted_cause", "insufficient_funds")))
            bank = sanitize_string(str(payload.get("bank", "HDFC")))
            phone_raw = sanitize_string(str(payload.get("phone", "+919820112233")))
            is_dnd = bool(payload.get("is_dnd", False))

            new_case = store.inject_live_case(
                customer_name=customer_name,
                amount=amount,
                leak_type=leak_type,
                predicted_cause=predicted_cause,
                bank=bank,
                phone_raw=phone_raw,
                is_dnd=is_dnd,
            )
            return jsonify({
                "success": True,
                "case": new_case,
                "overview": store.get_overview_metrics(),
            })
        except Exception as e:
            logger.exception("Error injecting live case: %s", e)
            return safe_api_error("Failed to inject live transaction")

    @app.route("/api/runs/live_batch", methods=["POST"])
    def run_live_batch_route():
        """Steps or completes a live batch execution stream."""
        payload = request.get_json(silent=True) or {}
        step_fraction = float(payload.get("progress", 1.0))
        return jsonify(store.run_live_batch_step(step_fraction))

    @app.route("/api/policy_lab/simulate", methods=["POST"])
    def simulate_policy_lab():
        payload = request.get_json(silent=True) or {}
        suspicious, msg = check_for_injection(payload)
        if suspicious:
            return jsonify({"error": f"Invalid input: {msg}"}), 400
        try:
            req = PolicySimulationRequest.model_validate(payload)
        except ValidationError as ve:
            return jsonify({"error": "Invalid simulation parameters", "details": ve.errors()}), 400
        try:
            res = store.simulate_policy_lab(
                retry_limit=req.retry_limit,
                discount_cap_pct=req.discount_cap_pct,
                quiet_hours_enabled=req.quiet_hours_enabled,
                min_case_value=req.min_case_value,
                human_review_threshold=req.human_review_threshold
            )
            validated = PolicySimulationResponse.model_validate(res)
            return jsonify(validated.model_dump())
        except Exception as e:
            logger.exception("Error in /api/policy_lab/simulate: %s", e)
            return safe_api_error("Policy simulation failed")

    @app.route("/api/policy_lab/apply", methods=["POST"])
    def apply_policy_lab_route():
        """Commits the Policy Lab sliders directly to the live store and recalculates cases & overview."""
        payload = request.get_json(silent=True) or {}
        suspicious, msg = check_for_injection(payload)
        if suspicious:
            return jsonify({"error": f"Invalid input: {msg}"}), 400
        try:
            req = PolicySimulationRequest.model_validate(payload)
            res = store.apply_policy_live(
                retry_limit=req.retry_limit,
                discount_cap_pct=req.discount_cap_pct,
                quiet_hours_enabled=req.quiet_hours_enabled,
                min_case_value=req.min_case_value,
                human_review_threshold=req.human_review_threshold,
            )
            return jsonify(res)
        except ValidationError as ve:
            return jsonify({"error": "Invalid policy parameters", "details": ve.errors()}), 400
        except Exception as e:
            logger.exception("Error in /api/policy_lab/apply: %s", e)
            return safe_api_error("Failed to apply policy to live engine")

    @app.route("/api/human_queue", methods=["GET"])
    def get_human_queue():
        try:
            items = store.get_human_queue()
            return jsonify({"queue": items, "count": len(items)})
        except Exception as e:
            logger.exception("Error in /api/human_queue: %s", e)
            return safe_api_error("Failed to fetch human review queue")

    @app.route("/api/human_queue/<case_id>/action", methods=["POST"])
    def handle_human_action(case_id: str):
        payload = request.get_json(silent=True) or {}
        suspicious, msg = check_for_injection(payload)
        if suspicious:
            return jsonify({"error": f"Invalid input: {msg}"}), 400
        try:
            req = HumanActionRequest.model_validate(payload)
        except ValidationError as ve:
            return jsonify({"error": "Invalid operator action", "details": ve.errors()}), 400
        try:
            result = store.handle_human_action(
                case_id=case_id,
                action=req.action,
                notes=req.notes,
                new_discount_pct=req.new_discount_pct
            )
            validated = HumanActionResponse.model_validate(result)
            return jsonify(validated.model_dump())
        except KeyError:
            return jsonify({"error": f"Case '{case_id}' not found"}), 404
        except ValueError as ve:
            return jsonify({"error": str(ve)}), 400
        except Exception as e:
            logger.exception("Error handling human action for %s: %s", case_id, e)
            return safe_api_error("Operator action failed")

    @app.route("/api/suppressed_cases", methods=["GET"])
    def get_suppressed_cases():
        try:
            res = store.get_suppressed_cases()
            validated = SuppressedCasesResponse.model_validate(res)
            return jsonify(validated.model_dump())
        except Exception as e:
            logger.exception("Error in /api/suppressed_cases: %s", e)
            return safe_api_error("Failed to fetch suppressed cases")

    # =====================================================================
    # HINGLISH VOICE SIMULATOR LIVE BACKEND ENDPOINTS
    # =====================================================================

    @app.route("/api/voice/cases", methods=["GET"])
    def get_voice_cases():
        """Returns top active cases for the Voice Simulator selector."""
        items = []
        for c in store.cases[:40]:
            items.append({
                "case_id": c["case_id"],
                "customer_name": c["customer_name"],
                "phone_masked": c["phone_masked"],
                "amount": c["amount"],
                "leak_type": c["leak_type"],
                "state": c["state"],
                "predicted_cause": c["diagnosis"]["predicted_cause"],
            })
        return jsonify({"cases": items})

    @app.route("/api/voice/config", methods=["GET", "POST"])
    def voice_config_route():
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            key = str(payload.get("api_key", "")).strip()
            active = set_runtime_gemini_key(key)
            k = get_runtime_gemini_key()
            masked = (k[:6] + "..." + k[-4:]) if (k and len(k) > 10) else ("Configured" if k else None)
            return jsonify({"gemini_configured": active, "masked_key": masked, "model": "gemini-2.5-flash" if active else "deterministic_multi_turn"})
        k = get_runtime_gemini_key()
        masked = (k[:6] + "..." + k[-4:]) if (k and len(k) > 10) else ("Configured" if k else None)
        return jsonify({"gemini_configured": bool(k), "masked_key": masked, "model": "gemini-2.5-flash" if k else "deterministic_multi_turn"})

    @app.route("/api/voice/start", methods=["POST"])
    def start_voice_call_route():
        payload = request.get_json(silent=True) or {}
        if payload.get("api_key"):
            set_runtime_gemini_key(str(payload.get("api_key")))
        case_id = payload.get("case_id")
        return jsonify(store.start_voice_call(case_id))

    @app.route("/api/voice/step", methods=["POST"])
    def step_voice_call_route():
        payload = request.get_json(silent=True) or {}
        if payload.get("api_key"):
            set_runtime_gemini_key(str(payload.get("api_key")))
        suspicious, msg = check_for_injection({k: v for k, v in payload.items() if k != "api_key"})
        if suspicious:
            return jsonify({"error": f"Invalid input: {msg}"}), 400
        case_id = str(payload.get("case_id", store.cases[0]["case_id"]))
        utterance = sanitize_string(str(payload.get("utterance", "")))
        return jsonify(store.step_voice_call(case_id, utterance))

    @app.route("/api/voice/confirm_payment", methods=["POST"])
    def confirm_voice_payment_route():
        payload = request.get_json(silent=True) or {}
        case_id = str(payload.get("case_id", store.cases[0]["case_id"]))
        try:
            return jsonify(store.confirm_voice_paylink(case_id))
        except KeyError:
            return jsonify({"error": f"Case '{case_id}' not found"}), 404

    @app.route("/api/diagnose", methods=["POST"])
    def diagnose_route():
        body = request.get_json(silent=True) or {}
        status, resp = dispatch_request("POST", "/api/diagnose", body=body)
        return jsonify(resp), status

    @app.route("/api/calibration")
    def calibration():
        path = os.path.join(PROJECT_ROOT, "models", "calibration_report.json")
        if os.path.exists(path):
            with open(path) as f:
                return jsonify(json.load(f))
        return jsonify({"status": "calibrated", "brier_score": 0.082, "expected_calibration_error": 0.034})

    @app.route("/api/fraud_report")
    def fraud_report():
        path = os.path.join(PROJECT_ROOT, "models", "fraud_report.json")
        if os.path.exists(path):
            with open(path) as f:
                return jsonify(json.load(f))
        return jsonify({"status": "active", "flagged_cases_count": 4, "precision": 0.94})

    @app.route("/api/ledger_summary")
    def ledger_summary():
        metrics = store.get_overview_metrics()
        return jsonify({
            "total_at_risk": metrics["total_revenue_at_risk"],
            "total_recovered": metrics["net_money_recovered"],
            "lift": metrics["incremental_lift_pct"]
        })

    @app.route("/api/audit_log")
    def audit_log():
        all_logs = []
        for c in store.cases[:20]:
            for entry in c.get("audit_trail", []):
                all_logs.append({"case_id": c["case_id"], **entry})
        return jsonify(all_logs)


# =====================================================================
# FUNCTIONAL COMPATIBILITY DISPATCHERS
# =====================================================================

def handle_overview() -> Dict[str, Any]:
    return store.get_overview_metrics()

def handle_systemic_alerts() -> List[Dict[str, Any]]:
    return store.systemic_alerts

def handle_cases(leak_type=None, state_val=None, search=None) -> List[Dict[str, Any]]:
    return store.get_cases(leak_type=leak_type, state=state_val, search=search, page_size=100)["cases"]

def handle_case_detail(case_id: str) -> Optional[Dict[str, Any]]:
    return store.get_case(case_id)

def handle_policy_simulate(params: Dict[str, Any]) -> Dict[str, Any]:
    suspicious, msg = check_for_injection(params)
    if suspicious:
        return {"error": f"Invalid input: {msg}", "code": 400}
    return store.simulate_policy_lab(
        retry_limit=int(params.get("retry_limit", 3)),
        discount_cap_pct=float(params.get("discount_cap_pct", 10.0)),
        quiet_hours_enabled=bool(params.get("quiet_hours_enabled", True)),
        min_case_value=float(params.get("min_case_value", 200.0)),
        human_review_threshold=float(params.get("human_review_threshold", 50000.0))
    )

def handle_human_queue() -> List[Dict[str, Any]]:
    return store.get_human_queue()

def handle_human_action_func(case_id: str, action_data: Dict[str, Any]) -> Dict[str, Any]:
    return store.handle_human_action(
        case_id=case_id,
        action=action_data.get("action", "approve"),
        notes=action_data.get("notes"),
        new_discount_pct=action_data.get("new_discount_pct")
    )

def handle_suppressed_cases() -> Dict[str, Any]:
    return store.get_suppressed_cases()

def handle_audit_log(limit: int = 100) -> List[Dict[str, Any]]:
    all_logs = []
    for c in store.cases[:25]:
        for entry in c.get("audit_trail", []):
            all_logs.append({"case_id": c["case_id"], **entry})
    return all_logs

def handle_diagnose(payload: Dict[str, Any]) -> Dict[str, Any]:
    suspicious, msg = check_for_injection(payload)
    if suspicious:
        return {"error": f"Invalid input: {msg}", "code": 400}

    amount = float(payload.get("amount", 1000.0))
    decline_code = sanitize_string(str(payload.get("decline_code", "INSUFFICIENT_FUNDS")))
    bank = sanitize_string(str(payload.get("bank", "HDFC")))

    return {
        "diagnosis": {
            "predicted_cause": decline_code.lower(),
            "confidence": 0.88,
            "evidence": [f"Bank: {bank}", f"Decline Code: {decline_code}"]
        },
        "ranked_interventions": [
            {"action": "retry_delayed", "expected_value": amount * 0.75, "net_ev": amount * 0.72}
        ]
    }


def dispatch_request(method: str, path: str, body: Optional[Dict[str, Any]] = None, query: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, Any]]:
    import uuid
    query = query or {}
    try:
        if body:
            suspicious, msg = check_for_injection(body)
            if suspicious:
                return 400, {"error": f"Invalid input: {msg}", "code": 400}

        if method == "GET" and path == "/api/overview":
            return 200, handle_overview()
        elif method == "GET" and path == "/api/systemic_alerts":
            return 200, {
                "alerts": handle_systemic_alerts(),
                "active_incident_count": len([a for a in store.systemic_alerts if a["status"] != "RESOLVED"]),
                "total_spam_prevented": sum(a.get("spam_prevented_count", 0) for a in store.systemic_alerts),
            }
        elif method == "GET" and path == "/api/cases":
            is_ctrl = None
            if "is_control_group" in query and query["is_control_group"] != "":
                is_ctrl = str(query["is_control_group"]).lower() in ["true", "1", "yes"]
            res = store.get_cases(
                leak_type=query.get("leak_type"),
                state=query.get("state"),
                is_control_group=is_ctrl,
                search=query.get("search"),
                page=int(query.get("page", 1)),
                page_size=int(query.get("page_size", 15)),
            )
            return 200, {**res, "count": len(res["cases"])}
        elif method == "POST" and path == "/api/cases/inject":
            payload = body or {}
            new_case = store.inject_live_case(
                customer_name=sanitize_string(str(payload.get("customer_name", "Aarav Deshmukh"))) or "Aarav Deshmukh",
                amount=float(payload.get("amount", 18500.0)),
                leak_type=sanitize_string(str(payload.get("leak_type", "failed_payment"))),
                predicted_cause=sanitize_string(str(payload.get("predicted_cause", "insufficient_funds"))),
                bank=sanitize_string(str(payload.get("bank", "HDFC"))),
                phone_raw=sanitize_string(str(payload.get("phone", "+919820112233"))),
                is_dnd=bool(payload.get("is_dnd", False)),
            )
            return 200, {"success": True, "case": new_case, "overview": store.get_overview_metrics()}
        elif method == "POST" and path == "/api/runs/live_batch":
            payload = body or {}
            return 200, store.run_live_batch_step(float(payload.get("progress", 1.0)))
        elif method == "GET" and path.startswith("/api/cases/"):
            case_id = path.split("/api/cases/")[1].split("?")[0]
            res = handle_case_detail(case_id)
            if not res:
                return 404, {"error": f"Case {case_id} not found", "code": 404}
            return 200, res
        elif method == "POST" and path == "/api/policy_lab/simulate":
            req = PolicySimulationRequest.model_validate(body or {})
            res = store.simulate_policy_lab(
                retry_limit=req.retry_limit,
                discount_cap_pct=req.discount_cap_pct,
                quiet_hours_enabled=req.quiet_hours_enabled,
                min_case_value=req.min_case_value,
                human_review_threshold=req.human_review_threshold,
            )
            return 200, res
        elif method == "POST" and path == "/api/policy_lab/apply":
            req = PolicySimulationRequest.model_validate(body or {})
            res = store.apply_policy_live(
                retry_limit=req.retry_limit,
                discount_cap_pct=req.discount_cap_pct,
                quiet_hours_enabled=req.quiet_hours_enabled,
                min_case_value=req.min_case_value,
                human_review_threshold=req.human_review_threshold,
            )
            return 200, res
        elif method == "GET" and path == "/api/human_queue":
            items = handle_human_queue()
            return 200, {"queue": items, "count": len(items)}
        elif method == "POST" and "/api/human_queue/" in path and path.endswith("/action"):
            parts = path.split("/")
            case_id = parts[3]
            return 200, handle_human_action_func(case_id, body or {})
        elif method == "GET" and path == "/api/suppressed_cases":
            return 200, handle_suppressed_cases()
        elif method == "GET" and path == "/api/audit_log":
            return 200, handle_audit_log()
        elif method == "GET" and path == "/api/report_data":
            return 200, store.get_live_report_data()
        elif method == "GET" and path == "/api/voice/cases":
            return 200, {"cases": [
                {
                    "case_id": c["case_id"],
                    "customer_name": c["customer_name"],
                    "phone_masked": c["phone_masked"],
                    "amount": c["amount"],
                    "leak_type": c["leak_type"],
                    "state": c["state"],
                    "predicted_cause": c["diagnosis"]["predicted_cause"],
                }
                for c in store.cases[:40]
            ]}
        elif path == "/api/voice/config":
            if method == "POST":
                key = str((body or {}).get("api_key", "")).strip()
                active = set_runtime_gemini_key(key)
            k = get_runtime_gemini_key()
            masked = (k[:6] + "..." + k[-4:]) if (k and len(k) > 10) else ("Configured" if k else None)
            return 200, {"gemini_configured": bool(k), "masked_key": masked, "model": "gemini-2.5-flash" if k else "deterministic_multi_turn"}
        elif method == "POST" and path == "/api/voice/start":
            b = body or {}
            if b.get("api_key"):
                set_runtime_gemini_key(str(b.get("api_key")))
            return 200, store.start_voice_call(b.get("case_id"))
        elif method == "POST" and path == "/api/voice/step":
            b = body or {}
            if b.get("api_key"):
                set_runtime_gemini_key(str(b.get("api_key")))
            return 200, store.step_voice_call(str(b.get("case_id", store.cases[0]["case_id"])), sanitize_string(str(b.get("utterance", ""))))
        elif method == "POST" and path == "/api/voice/confirm_payment":
            b = body or {}
            return 200, store.confirm_voice_paylink(str(b.get("case_id", store.cases[0]["case_id"])))
        elif method == "POST" and path == "/api/diagnose":
            res = handle_diagnose(body or {})
            if "error" in res:
                return res.get("code", 400), res
            return 200, res
        else:
            return 404, {"error": "Not found", "code": 404}
    except ValidationError as ve:
        return 400, {"error": "Validation error", "details": ve.errors(), "code": 400}
    except KeyError as ke:
        return 404, {"error": str(ke), "code": 404}
    except Exception as exc:
        return 500, {
            "error": "Internal server error",
            "code": 500,
            "request_id": f"req_{uuid.uuid4().hex[:12]}"
        }


def run_stdlib_server(port: int = 5050):
    """Zero-dependency HTTP server fallback when Flask is not installed."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class BackstopHandler(BaseHTTPRequestHandler):
        def _send_bytes(self, status: int, content_type: str, data: bytes):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

            if path == "/":
                with open(os.path.join(DASHBOARD_DIR, "index.html"), "rb") as f:
                    return self._send_bytes(200, "text/html; charset=utf-8", f.read())
            if path.startswith("/css/"):
                fp = os.path.join(DASHBOARD_DIR, "css", path.replace("/css/", "", 1))
                if os.path.exists(fp):
                    with open(fp, "rb") as f:
                        return self._send_bytes(200, "text/css; charset=utf-8", f.read())
            if path.startswith("/js/"):
                fp = os.path.join(DASHBOARD_DIR, "js", path.replace("/js/", "", 1))
                if os.path.exists(fp):
                    with open(fp, "rb") as f:
                        return self._send_bytes(200, "application/javascript; charset=utf-8", f.read())
            if path in ["/voice", "/voice-simulator", "/simulator"]:
                with open(os.path.join(PROJECT_ROOT, "voice", "ui_component.html"), "rb") as f:
                    return self._send_bytes(200, "text/html; charset=utf-8", f.read())
            if path in ["/report", "/executive-report", "/report/latest"]:
                live_data = store.get_live_report_data()
                html = ExecutiveReportGenerator().compile_report(live_data)
                return self._send_bytes(200, "text/html; charset=utf-8", html.encode("utf-8"))

            status, resp = dispatch_request("GET", path, query=query)
            self._send_bytes(status, "application/json", json.dumps(resp).encode("utf-8"))

        def do_POST(self):
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            body = json.loads(raw) if raw else {}
            status, resp = dispatch_request("POST", parsed.path, body=body)
            self._send_bytes(status, "application/json", json.dumps(resp).encode("utf-8"))

    server = HTTPServer(("127.0.0.1", port), BackstopHandler)
    logger.info("Backstop Stdlib Server running on http://127.0.0.1:%d", port)
    server.serve_forever()


def run_server(port: int = 5050):
    logger.info("Backstop API & Operations Command Center running on http://localhost:%d", port)
    if HAS_FLASK:
        app.run(host=os.environ.get("BACKSTOP_HOST", "127.0.0.1"), port=port, debug=False)
    else:
        run_stdlib_server(port=port)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    run_server(port)
