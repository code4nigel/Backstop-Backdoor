import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import pandas as pd
from flask import Flask, jsonify, request
from flask_cors import CORS
from engine.message_drafter import draft_message
from engine.diagnosis import DiagnosisEngine
from engine.recovery_value import rank_interventions
from engine.ledger import RecoveryLedger
from engine.policy import PolicyEngine
from engine.batch_runner import run_batch
from engine.message_drafter import draft_message

app = Flask(__name__)
CORS(app)

DATA_PATH = "data/transactions.csv"
CALIBRATION_PATH = "models/calibration_report.json"

diagnosis_engine = DiagnosisEngine()
ledger = RecoveryLedger()
policy = PolicyEngine(ledger=ledger, diagnosis=diagnosis_engine)

_batch_cache = {"data": None}


@app.route("/api/calibration")
def calibration():
    with open(CALIBRATION_PATH) as f:
        return jsonify(json.load(f))


@app.route("/api/diagnose", methods=["POST"])
def diagnose():
    txn = request.json
    diag = diagnosis_engine.diagnose(txn)
    ranked = rank_interventions(diag["root_cause"], revenue=txn["amount"], prior_failures=txn.get("prior_failures", 0))
    return jsonify({
        "diagnosis": diag,
        "ranked_interventions": [o.__dict__ for o in ranked],
    })


@app.route("/api/batch_summary")
def batch_summary():
    if _batch_cache["data"] is None:
        df = pd.read_csv(DATA_PATH)
        df_failed = df[df["failed"]].copy()
        df_failed["is_salary_week"] = df_failed["is_salary_week"].astype(bool)
        out, summary = run_batch(df_failed)
        sample = out.sample(min(25, len(out)), random_state=1).to_dict(orient="records")
        _batch_cache["data"] = {"summary": summary, "sample_decisions": sample}
    return jsonify(_batch_cache["data"])


@app.route("/api/ledger_summary")
def ledger_summary():
    return jsonify(ledger.summary())

@app.route("/api/fraud_report")
def fraud_report():
    with open("models/fraud_report.json") as f:
        return jsonify(json.load(f))


@app.route("/api/draft_message", methods=["POST"])
def draft_message_endpoint():
    body = request.json
    result = draft_message(
        root_cause=body["root_cause"],
        action=body["action"],
        customer_name=body.get("customer_name", "there"),
        amount_display=body["amount_display"],
        language=body.get("language", "english"),
    )
    return jsonify(result)


@app.route("/api/audit_log")
def audit_log():
    return jsonify(ledger.history(limit=100))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)