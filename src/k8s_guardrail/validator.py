from __future__ import annotations

from typing import Any

import yaml

from k8s_guardrail.models import Severity, ValidationIssue, VerificationResult

# Kinds whose apiVersion LLMs routinely get wrong (e.g. extensions/v1beta1 Deployments).
VALID_API_VERSIONS: dict[str, tuple[str, ...]] = {
    "Deployment": ("apps/v1",),
    "StatefulSet": ("apps/v1",),
    "DaemonSet": ("apps/v1",),
    "Service": ("v1",),
    "ConfigMap": ("v1",),
    "Secret": ("v1",),
    "Ingress": ("networking.k8s.io/v1",),
    "CronJob": ("batch/v1",),
    "Job": ("batch/v1",),
}

WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet"}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class ManifestVerificationEngine:
    """Checks structure, API versions and baseline security of Kubernetes manifests."""

    def __init__(self, enforce_security_context: bool = True) -> None:
        self.enforce_security_context = enforce_security_context

    def verify_string(self, manifest_yaml: str) -> VerificationResult:
        try:
            documents = [d for d in yaml.safe_load_all(manifest_yaml) if d is not None]
        except yaml.YAMLError as exc:
            issue = ValidationIssue(Severity.ERROR, "SYNTAX_VALIDATION", str(exc), "UNKNOWN")
            return VerificationResult(total_checks=1, issues=[issue])

        if not documents:
            issue = ValidationIssue(Severity.ERROR, "EMPTY_MANIFEST", "No documents found.", "UNKNOWN")
            return VerificationResult(total_checks=1, issues=[issue])

        issues: list[ValidationIssue] = []
        for doc in documents:
            issues.extend(self._verify_document(doc))
        return VerificationResult(total_checks=len(documents), issues=issues)

    def _verify_document(self, doc: Any) -> list[ValidationIssue]:
        if not isinstance(doc, dict):
            return [ValidationIssue(Severity.ERROR, "NOT_A_MAPPING",
                                    "Document is not a YAML mapping.", "UNKNOWN")]

        kind = doc.get("kind")
        name = _dict(doc.get("metadata")).get("name", "unnamed")
        res = f"{kind}/{name}"
        issues: list[ValidationIssue] = []

        def add(sev: Severity, rule: str, msg: str) -> None:
            issues.append(ValidationIssue(sev, rule, msg, res))

        if not kind or not doc.get("apiVersion"):
            add(Severity.ERROR, "MISSING_TYPE_META", "Both 'kind' and 'apiVersion' are required.")
            return issues
        if not isinstance(kind, str) or not isinstance(doc["apiVersion"], str):
            add(Severity.ERROR, "INVALID_TYPE_META", "'kind' and 'apiVersion' must be strings.")
            return issues
        if name == "unnamed":
            add(Severity.ERROR, "MISSING_NAME", "metadata.name is required.")

        allowed = VALID_API_VERSIONS.get(kind)
        if allowed and doc["apiVersion"] not in allowed:
            add(Severity.ERROR, "INVALID_API_VERSION",
                f"Kind '{kind}' cannot use apiVersion '{doc['apiVersion']}'. "
                f"Expected one of: {list(allowed)}")

        if kind in WORKLOAD_KINDS:
            self._verify_workload(_dict(doc.get("spec")), add)
        return issues

    def _verify_workload(self, spec: dict[str, Any], add) -> None:
        if not _dict(spec.get("selector")).get("matchLabels"):
            add(Severity.ERROR, "MISSING_SELECTOR", "spec.selector.matchLabels is required.")

        containers = _dict(_dict(spec.get("template")).get("spec")).get("containers")
        if not containers or not isinstance(containers, list):
            add(Severity.ERROR, "MISSING_CONTAINERS", "spec.template.spec.containers is empty.")
            return

        for c in map(_dict, containers):
            cname = c.get("name", "unnamed")
            image = str(c.get("image", ""))
            if not image:
                add(Severity.ERROR, "MISSING_IMAGE", f"Container '{cname}' has no image.")
            elif ":" not in image.rsplit("/", 1)[-1] or image.endswith(":latest"):
                add(Severity.WARNING, "UNPINNED_IMAGE",
                    f"Container '{cname}' image '{image}' is not pinned to a version.")

            res = _dict(c.get("resources"))
            if not res.get("limits") or not res.get("requests"):
                add(Severity.WARNING, "MISSING_RESOURCE_LIMITS",
                    f"Container '{cname}' must set CPU/memory requests and limits.")

            if self.enforce_security_context:
                sec = _dict(c.get("securityContext"))
                if sec.get("privileged") is True:
                    add(Severity.ERROR, "SECURITY_PRIVILEGED",
                        f"Container '{cname}' must not run privileged.")
                if sec.get("allowPrivilegeEscalation") is not False:
                    add(Severity.ERROR, "SECURITY_PRIVILEGE_ESCALATION",
                        f"Container '{cname}' must set allowPrivilegeEscalation: false.")
