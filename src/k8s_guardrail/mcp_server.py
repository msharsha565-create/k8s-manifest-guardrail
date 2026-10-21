from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from k8s_guardrail.validator import VALID_API_VERSIONS, ManifestVerificationEngine

mcp = FastMCP("k8s-guardrail")


@mcp.tool()
def validate_manifest(manifest_yaml: str, enforce_security_context: bool = True) -> dict[str, Any]:
    """Validate Kubernetes manifest YAML. Returns is_valid and a list of issues
    (severity, rule, message, resource). Only ERROR issues make a manifest invalid."""
    engine = ManifestVerificationEngine(enforce_security_context=enforce_security_context)
    return engine.verify_string(manifest_yaml).to_dict()


@mcp.tool()
def list_supported_api_versions() -> dict[str, list[str]]:
    """Kinds with a checked apiVersion, and the apiVersions the guardrail accepts for each."""
    return {kind: list(versions) for kind, versions in VALID_API_VERSIONS.items()}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
