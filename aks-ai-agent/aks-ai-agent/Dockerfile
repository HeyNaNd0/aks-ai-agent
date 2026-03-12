# ============================================================
# AKS AI Agent — Dockerfile
# ============================================================
# Multi-stage build for a lean production image.
# Stage 1: Install dependencies
# Stage 2: Copy only what's needed into the final image
# ============================================================

# ── Stage 1: Builder ──────────────────────────────────────────
FROM python:3.12-slim AS builder

# Install build tools (some Azure SDK deps need C compilation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --prefix=/install --no-cache-dir -r requirements.txt


# ── Stage 2: Runtime ─────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Security: run as non-root user
RUN groupadd -r aksagent && useradd -r -g aksagent aksagent

# Copy installed packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy application code
COPY agent/    ./agent/
COPY config/   ./config/

# Create writable directories for logs and data
# In K8s these should be backed by PersistentVolumeClaims
RUN mkdir -p logs data \
    && chown -R aksagent:aksagent /app

USER aksagent

# Health check: verify Python can import the agent
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import agent; print('healthy')" || exit 1

ENTRYPOINT ["python", "-m", "agent.main"]
