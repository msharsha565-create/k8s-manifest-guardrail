# k8s-manifest-guardrail

A static verification gate for LLM-generated Kubernetes manifests. It catches common
hallucinations (wrong `apiVersion`, missing selectors/containers, unpinned images) and
baseline security problems (privileged containers, privilege escalation) before a manifest
reaches a cluster.

```
LLM output ──► k8s-guardrail ──► exit 0 ──► kubectl apply
                    │
                    └── exit 1 + issue list ──► feed back to the LLM for repair
```

## Rules

| Rule | Severity |
| --- | --- |
| `SYNTAX_VALIDATION`, `EMPTY_MANIFEST`, `NOT_A_MAPPING`, `MISSING_TYPE_META`, `MISSING_NAME` | ERROR |
| `INVALID_API_VERSION` (e.g. `extensions/v1beta1` Deployment) | ERROR |
| `MISSING_SELECTOR`, `MISSING_CONTAINERS`, `MISSING_IMAGE` | ERROR |
| `SECURITY_PRIVILEGED`, `SECURITY_PRIVILEGE_ESCALATION` | ERROR |
| `UNPINNED_IMAGE`, `MISSING_RESOURCE_LIMITS` | WARNING |

Only ERRORs make a manifest invalid (exit code 1).

## Usage

```bash
pip install -e ".[dev]"
k8s-guardrail deploy.yaml                 # human-readable
k8s-guardrail deploy.yaml --format json   # machine-readable
cat deploy.yaml | k8s-guardrail -         # stdin
```

```python
from k8s_guardrail import ManifestVerificationEngine

result = ManifestVerificationEngine().verify_string(llm_output)
if not result.is_valid:
    retry_with_feedback(result.to_dict())
```

## Web demo

```bash
pip install -e ".[web]"
k8s-guardrail-web     # open http://127.0.0.1:8000
```

Paste a manifest, or load the valid / AI-hallucinated example, and click **Check manifest**.

## AI agent: write manifests and repair them (optional)

An [MCP](https://modelcontextprotocol.io) server exposes the checker as tools, and an LLM agent
uses it: it drafts a manifest, validates it through MCP, fixes the reported errors, and retries
until the manifest passes. It runs on Claude by default, or on any OpenAI-compatible
tool-calling API (OpenAI, Gemini, Ollama, vLLM).

```bash
pip install -e ".[agent]"

# Claude (default); makes real, billable API calls
export ANTHROPIC_API_KEY=...
k8s-guardrail-agent "A 3-replica nginx Deployment with a ClusterIP Service"

# Gemini, via Google's OpenAI-compatible endpoint (use a current model name)
GEMINI_API_KEY=... k8s-guardrail-agent "A 3-replica nginx Deployment with a ClusterIP Service" \
    --provider openai --api-key-env GEMINI_API_KEY --model <gemini-model> \
    --base-url https://generativelanguage.googleapis.com/v1beta/openai
```

Use the checker from any MCP client with `{"command": "k8s-guardrail-mcp"}`.

**Verified vs not verified:** the MCP server and both agent loops are tested against scripted
fake models, and the OpenAI-compatible client against a mocked transport (request format, retries
on 429/5xx, error bodies, provider metadata echoed back unchanged). A live run against a real
model has not been recorded here yet.

## Docker

```bash
docker build -t k8s-guardrail .
docker run --rm -v "$PWD:/m:ro" k8s-guardrail /m/deploy.yaml
```

## Development

```bash
pytest --cov=k8s_guardrail
ruff check src tests
```

## Scope and limitations

This is a rule-based linter, not a full schema validator. For complete schema checking, pair
it with `kubeconform`. Rules cover workloads (Deployment/StatefulSet/DaemonSet) plus
`apiVersion` checks for common kinds.
