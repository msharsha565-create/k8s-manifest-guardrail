FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
COPY --from=builder /install /usr/local
USER 10001
ENTRYPOINT ["k8s-guardrail"]
