from pathlib import Path

import pytest

from k8s_guardrail import ManifestVerificationEngine, Severity
from k8s_guardrail.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def rules(result):
    return {i.rule for i in result.issues}


@pytest.fixture
def engine():
    return ManifestVerificationEngine()


def test_valid_deployment_has_no_issues(engine):
    result = engine.verify_string((FIXTURES / "valid_deployment.yaml").read_text())
    assert result.is_valid
    assert result.issues == []


def test_hallucinated_manifest_is_rejected(engine):
    result = engine.verify_string((FIXTURES / "hallucinated_deployment.yaml").read_text())
    assert not result.is_valid
    assert {"INVALID_API_VERSION", "MISSING_SELECTOR", "SECURITY_PRIVILEGED",
            "SECURITY_PRIVILEGE_ESCALATION", "UNPINNED_IMAGE",
            "MISSING_RESOURCE_LIMITS"} <= rules(result)


def test_invalid_yaml_reports_syntax_error(engine):
    result = engine.verify_string("a: [unclosed")
    assert rules(result) == {"SYNTAX_VALIDATION"}


def test_empty_input(engine):
    assert rules(engine.verify_string("")) == {"EMPTY_MANIFEST"}


def test_non_mapping_document(engine):
    assert rules(engine.verify_string("- just\n- a list")) == {"NOT_A_MAPPING"}


def test_missing_kind(engine):
    assert rules(engine.verify_string("apiVersion: v1")) == {"MISSING_TYPE_META"}


def test_warnings_alone_do_not_invalidate():
    manifest = (FIXTURES / "valid_deployment.yaml").read_text().replace(":1.0.0", ":latest")
    result = ManifestVerificationEngine().verify_string(manifest)
    assert result.is_valid
    assert [i.severity for i in result.issues] == [Severity.WARNING]


def test_security_context_check_can_be_disabled():
    manifest = (FIXTURES / "valid_deployment.yaml").read_text().replace(
        "          securityContext:\n            allowPrivilegeEscalation: false\n", "")
    assert not ManifestVerificationEngine().verify_string(manifest).is_valid
    assert ManifestVerificationEngine(enforce_security_context=False).verify_string(manifest).is_valid


def test_multi_document_and_service_api_version(engine):
    manifest = "apiVersion: apps/v1\nkind: Service\nmetadata: {name: s}\n---\nkind: ConfigMap\napiVersion: v1\nmetadata: {name: c}\n"
    result = engine.verify_string(manifest)
    assert result.total_checks == 2
    assert rules(result) == {"INVALID_API_VERSION"}


def test_cli_exit_codes_and_json(capsys):
    assert main([str(FIXTURES / "valid_deployment.yaml")]) == 0
    assert main([str(FIXTURES / "hallucinated_deployment.yaml"), "--format", "json"]) == 1
    assert '"is_valid": false' in capsys.readouterr().out
