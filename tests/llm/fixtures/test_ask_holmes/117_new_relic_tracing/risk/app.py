import os
import logging
from flask import Flask, request, jsonify

PORT = int(os.environ.get("PORT", "8000"))

# logging (off by default unless LOG_LEVEL set)
lvl_name = os.environ.get("LOG_LEVEL")  # e.g., DEBUG, INFO, WARNING, ERROR
if lvl_name:
    logging.basicConfig(
        level=getattr(logging, lvl_name.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
else:
    logging.disable(logging.CRITICAL)  # effectively off

log = logging.getLogger("risk")
app = Flask(__name__)


@app.get("/healthz")
def healthz():
    return jsonify(ok=True, service="risk")


@app.post("/risk/score")
def risk_score():
    p = request.get_json(silent=True) or {}
    user_id = p.get("userId", "anon")
    amount = float(p.get("amount", 0))
    qty = int(p.get("qty", 1))
    score = min(100, int(amount // 10) + qty * 5)
    is_fraud = score >= 70
    log.info(
        "score user_id=%s amount=%s qty=%s score=%s is_fraud=%s",
        user_id,
        amount,
        qty,
        score,
        is_fraud,
    )
    return jsonify(isFraud=is_fraud, score=score, policy="retail_default_v2")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
