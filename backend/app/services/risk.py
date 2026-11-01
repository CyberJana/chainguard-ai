from dataclasses import dataclass
from typing import Any

MODEL_VERSION = "bitcoin-rules-v0.1"


@dataclass(frozen=True)
class RiskResult:
    score: int
    category: str
    explanation: list[dict[str, Any]]
    recommendation: str


def assess_bitcoin_transaction(raw_observation: dict[str, Any]) -> RiskResult:
    """Return explicitly uncalibrated heuristic indicators, not fraud probabilities."""
    outputs = raw_observation.get("vout", [])
    inputs = raw_observation.get("vin", [])
    fee = raw_observation.get("fee", 0)
    score = 0
    explanation: list[dict[str, Any]] = []

    total_btc = sum(
        output.get("value", 0) for output in outputs if isinstance(output, dict) and type(output.get("value")) is int
    ) / 100_000_000
    if total_btc >= 100:
        score += 35
        explanation.append({"indicator": "large_observed_output_value", "observed_btc": round(total_btc, 8), "weight": 35})

    if len(outputs) >= 50:
        score += 25
        explanation.append({"indicator": "high_output_fanout", "output_count": len(outputs), "weight": 25})

    if len(inputs) >= 50:
        score += 20
        explanation.append({"indicator": "high_input_count", "input_count": len(inputs), "weight": 20})

    fee_per_input = fee / max(len(inputs), 1) if type(fee) is int else 0
    if fee_per_input >= 1_000_000:
        score += 20
        explanation.append({"indicator": "high_fee_per_input", "fee_satoshis": fee, "weight": 20})

    score = min(score, 100)
    category = "critical" if score >= 80 else "high" if score >= 50 else "medium" if score >= 20 else "low"
    recommendation = (
        "Review the contributing indicators and source observation; do not treat this heuristic as proof of fraud."
        if explanation
        else "No configured heuristic indicator was triggered; this is not evidence that the transaction is benign."
    )
    return RiskResult(score=score, category=category, explanation=explanation, recommendation=recommendation)
