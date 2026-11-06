"""Regression checks for the Holmes operator Deployment Helm wiring.

Assert the operator Deployment supports imagePullSecrets: the value is present
(and empty by default) and the template renders the block guarded by an if.
"""

from pathlib import Path

import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"


def _operator_values() -> dict:
    with open(HELM_DIR / "values.yaml") as f:
        return yaml.safe_load(f)["operator"]


def test_operator_values_have_empty_image_pull_secrets_default():
    v = _operator_values()
    assert v["imagePullSecrets"] == []


def test_operator_deployment_renders_image_pull_secrets():
    text = (HELM_DIR / "templates" / "operator-deployment.yaml").read_text()
    assert "if .Values.operator.imagePullSecrets" in text
    assert "imagePullSecrets:" in text
    assert "toYaml .Values.operator.imagePullSecrets | nindent 6" in text
