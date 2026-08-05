"""Regression checks for the Telegram Helm integration."""

from pathlib import Path

import yaml

HELM_DIR = Path(__file__).resolve().parents[1] / "helm" / "holmes"
TELEGRAM_TEMPLATE = HELM_DIR / "templates" / "telegram-deployment.yaml"


def _telegram_values() -> dict:
    with open(HELM_DIR / "values.yaml") as values_file:
        return yaml.safe_load(values_file)["telegram"]


def test_telegram_values_are_secure_and_opt_in():
    values = _telegram_values()

    assert values["enabled"] is False
    assert values["existingSecret"] == {"name": "", "key": "bot-token"}
    assert values["allowedChatIds"] == []
    assert values["pollTimeoutSeconds"] == 30
    assert values["requestTimeoutSeconds"] == 120
    assert values["historyMessages"] == 30


def test_telegram_deployment_has_one_poller_and_internal_holmes_url():
    template = TELEGRAM_TEMPLATE.read_text()

    assert "replicas: 1" in template
    assert 'printf "http://%s-holmes:80" .Release.Name' in template
    assert "holmes.plugins.telegram.bot" in template
    assert "TELEGRAM_ALLOWED_CHAT_IDS" in template


def test_telegram_token_comes_from_existing_secret():
    template = TELEGRAM_TEMPLATE.read_text()

    assert "telegram.existingSecret.name is required" in template
    assert "secretKeyRef:" in template
    assert "TELEGRAM_BOT_TOKEN" in template


def test_telegram_pod_uses_restricted_security_defaults():
    template = TELEGRAM_TEMPLATE.read_text()

    assert "automountServiceAccountToken: false" in template
    assert "readOnlyRootFilesystem: true" in template
    assert "runAsUser: 10001" in template
    assert "allowPrivilegeEscalation: false" in template
    assert "drop:" in template
    assert "- ALL" in template
