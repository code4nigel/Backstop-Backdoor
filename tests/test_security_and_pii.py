"""
Security Hardening & PII Audit Test Suite:
1. PII Masking Audit on /api/cases, /api/audit_log, and /api/human_queue.
2. Safe Error Handling: Verification of structured error JSON without stack traces.
3. Input Sanitization & Injection Defense: SQL Injection and XSS detection.
"""
import os
import sys
import json
import re
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.app import dispatch_request
from engine.pii import (
    RE_RAW_INDIAN_PHONE,
    RE_RAW_EMAIL,
    RE_CREDIT_CARD_PAN,
    audit_scan_pii,
)


class TestSecurityAndPII:
    def test_pii_masking_in_api_cases(self):
        status, resp = dispatch_request("GET", "/api/cases")
        assert status == 200
        raw_text = json.dumps(resp)

        violations = audit_scan_pii(raw_text)
        assert len(violations) == 0, f"PII detected in /api/cases: {violations}"

        # Assert no raw 10-digit phone appears in cases
        cases = resp.get("cases", [])
        assert len(cases) > 0
        for c in cases:
            phone = c.get("phone_masked", "")
            assert "••" in phone, f"Phone number not masked: {phone}"
            email = c.get("email_masked", "")
            assert "•" in email, f"Email not masked: {email}"

    def test_pii_masking_in_api_audit_log(self):
        status, resp = dispatch_request("GET", "/api/audit_log")
        assert status == 200
        raw_text = json.dumps(resp)

        violations = audit_scan_pii(raw_text)
        assert len(violations) == 0, f"PII detected in /api/audit_log: {violations}"

    def test_pii_masking_in_api_human_queue(self):
        status, resp = dispatch_request("GET", "/api/human_queue")
        assert status == 200
        raw_text = json.dumps(resp)

        violations = audit_scan_pii(raw_text)
        assert len(violations) == 0, f"PII detected in /api/human_queue: {violations}"

    def test_safe_error_handling_strips_stack_traces(self, monkeypatch):
        # Force an unexpected internal exception inside an endpoint handler
        from api import app as api_module

        def broken_handler():
            raise ValueError("Database connection failure at D:\\Projects\\Suhani Project Help\\engine\\ledger.py:105")

        monkeypatch.setattr(api_module, "handle_overview", broken_handler)

        status, resp = dispatch_request("GET", "/api/overview")
        assert status == 500

        # Verify structured JSON output: {"error": "Internal server error", "code": 500, "request_id": "req_xyz"}
        assert resp.get("error") == "Internal server error"
        assert resp.get("code") == 500
        assert "request_id" in resp
        assert resp["request_id"].startswith("req_")

        # Verify stack traces, file paths, and internal exceptions are NOT leaked
        error_json_str = json.dumps(resp)
        assert "Traceback" not in error_json_str
        assert "ledger.py" not in error_json_str
        assert "line 105" not in error_json_str
        assert "D:\\" not in error_json_str
        assert "C:\\" not in error_json_str

    def test_input_sanitization_and_sql_injection_defense(self):
        sql_payloads = [
            {"decline_code": "' OR '1'='1", "amount": 1000},
            {"bank": "'; DROP TABLE actions; --", "amount": 500},
            {"bank": "HDFC' UNION SELECT * FROM users --", "amount": 2500},
        ]

        for payload in sql_payloads:
            status, resp = dispatch_request("POST", "/api/diagnose", body=payload)
            # Must either reject with 400 or sanitize safely without executing
            if status == 400:
                assert "Invalid input" in resp.get("error", "")
            else:
                assert status == 200
                diag = resp["diagnosis"]
                # Verify raw SQL injection tokens did not corrupt the diagnosis
                assert "DROP TABLE" not in diag["predicted_cause"]

        # Test Policy Lab simulation SQL injection
        pol_payload = {"retry_limit": "' OR 1=1 --", "min_case_value": 100}
        status, resp = dispatch_request("POST", "/api/policy_lab/simulate", body=pol_payload)
        assert status == 400
        assert "Invalid input" in resp.get("error", "")

    def test_xss_payload_defense(self):
        xss_payloads = [
            {"bank": "<script>alert('xss')</script>", "amount": 1000},
            {"bank": "<img src=x onerror=alert('xss')>", "amount": 1500},
            {"notes": "javascript:alert(1)"},
        ]

        for payload in xss_payloads:
            status, resp = dispatch_request("POST", "/api/diagnose", body=payload)
            if status == 400:
                assert "Invalid input" in resp.get("error", "")
            else:
                assert status == 200
                raw_resp = json.dumps(resp)
                # Verify <script> tags never reflect back raw
                assert "<script>" not in raw_resp
                assert "onerror=" not in raw_resp
