FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (including ca-certificates so we can register
# the Bajaj corporate root)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    poppler-utils \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Register corporate root CAs — Bajaj's Cisco Umbrella SIG intercepts every
# outbound HTTPS (api.smith.langchain.com, api.groq.com, api.cohere.com,
# api.openai.com). Without trusting these intermediates, Python clients fail
# with CERTIFICATE_VERIFY_FAILED. Three certs:
#   1. BajajLife-Root-CA       — internal Bajaj endpoints
#   2. Cisco Umbrella Primary  — top of the proxy chain
#   3. Cisco Umbrella Secondary (mum2-SG region) — regional intermediate
COPY certs/bajaj-root.pem               /usr/local/share/ca-certificates/bajaj-root.crt
COPY certs/cisco-umbrella-root.pem      /usr/local/share/ca-certificates/cisco-umbrella-root.crt
COPY certs/cisco-umbrella-primary.pem   /usr/local/share/ca-certificates/cisco-umbrella-primary.crt
COPY certs/cisco-umbrella-secondary.pem /usr/local/share/ca-certificates/cisco-umbrella-secondary.crt
RUN update-ca-certificates

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Python clients (requests, openai, cohere, langsmith) use certifi's bundle,
# NOT /etc/ssl. Append all corp CAs to certifi's cacert.pem.
RUN CERTIFI_BUNDLE=$(python -c "import certifi; print(certifi.where())") \
 && for c in bajaj-root cisco-umbrella-root cisco-umbrella-primary cisco-umbrella-secondary; do \
        echo "" >> "$CERTIFI_BUNDLE"; \
        cat /usr/local/share/ca-certificates/$c.crt >> "$CERTIFI_BUNDLE"; \
        echo "Appended $c to certifi"; \
    done

# Pre-cache the tiktoken cl100k_base BPE so runtime never needs to call out
# to openaipublic.blob.core.windows.net (Bajaj firewall blocks DNS to it).
# Set TIKTOKEN_CACHE_DIR to a deterministic path inside the image.
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache
RUN mkdir -p $TIKTOKEN_CACHE_DIR \
 && python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" \
 || echo "tiktoken pre-cache failed (build host has no internet); runtime will fall back to paragraph chunking"

# Copy app code
COPY . .

# Create necessary directories
RUN mkdir -p uploads logs

# Drop root (audit H7): run as an unprivileged user, owning the writable dirs.
RUN useradd --system --create-home --uid 10001 appuser \
 && chown -R appuser:appuser /app /opt/tiktoken-cache
USER appuser

# Expose port
EXPOSE 8000

# Run migrations then start server
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
