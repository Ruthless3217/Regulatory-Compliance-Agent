# RHEL 9 VM Setup — Access Request Response

**To:** Ayush Zode (Infra/Security)
**From:** Diwakar Adhikari — AI/Marketing
**Application:** Regulatory Compliance Agent (Bajaj Life Insurance marketing-content compliance checker)
**Deployment model:** Single RHEL 9 VM running the full stack via Docker Engine + Compose. Container images, Python (PyPI), Node (npm) and OS (dnf) packages are pulled from the **internal Bajaj Artifactory / registry mirror**. The only external egress is to the LLM and embedding APIs (cannot be mirrored).

> **Deployment note:** the repo includes a dev-only `docker-compose.override.yml` (it remaps the frontend to port 3001 and sets `LLM_INSECURE_TLS=true` to skip TLS verification on a local machine). This file **must be excluded on the VM** (rename or delete it before `docker compose up`). The VM therefore uses the base `docker-compose.yml` only: frontend on **3000**, backend on **8000**, and **full TLS validation** against the corporate CA chain registered in §3. The numbers in this document assume base-compose-only.

> **Data that leaves the network:** no customer data or PII is involved. The application sends the **pre-publication marketing copy being reviewed** — chunked into passages — to the single approved external endpoint in §2a (the Bajaj-owned **Azure OpenAI / AI Foundry** resource, which serves both the LLM analysis and the embeddings). The content is the marketing draft itself; there is no separate anonymization step. Everything else (reviewer-decision corpus, scores, reports, DB) stays on the VM. Note the egress target is a **Bajaj-tenant Azure resource**, not a public third-party AI API.

**Server login IDs in scope:** diwakar.adhikari01, yashraj.lawate, abhishek.gurjar, tanish.jagtap, utkarsh.das

---

## 1. Business Justification

The Regulatory Compliance Agent is an internal AI tool that reviews Bajaj Life Insurance marketing copy (ads, brochures, social, web) against IRDAI / SEBI regulations and Bajaj brand guidelines **before publication**. Today this review is manual, slow, and inconsistent across reviewers.

The tool ingests a draft, retrieves the most similar **past human-reviewer decisions** from a vector knowledge base, and grades the new draft the way our reviewers historically did — producing inline-highlighted violations, a compliance score and grade, a shareable report, and a chat interface for compliant-rewrite suggestions.

**Business value:**
- Cuts pre-publication compliance review turnaround from days to minutes.
- Reduces regulatory exposure (IRDAI/SEBI penalties, forced ad withdrawals) by catching violations earlier and more consistently.
- Standardizes reviewer judgment into a reusable, auditable knowledge base.

The RHEL 9 VM is required to host this application internally. No customer data or PII is processed; only the marketing draft under review is sent (chunked) to the approved Bajaj-tenant Azure AI Foundry project (Azure OpenAI LLM + Cohere embeddings, same tenant) for analysis and embeddings — see the *Data that leaves the network* note above. All reviewer data, scores, reports and the knowledge base remain on Bajaj infrastructure.

---

## 2. Internet Access — URLs to Whitelist

Because images and language packages are served from the **internal Artifactory / registry mirror**, the external whitelist is minimal. Split into *runtime* (always required) and *conditional*.

### 2a. Required — runtime, continuous (whole stack lifetime)

**Production uses one Bajaj-tenant Azure AI Foundry project (`bl-bajaj-compliance-resource`), exposed over two host surfaces.** It is the *only* external AI provider — serving both the LLM and the embeddings.

| # | URL / Host | Port | Protocol | Use case | Duration |
|---|------------|------|----------|----------|----------|
| 1 | `bl-bajaj-compliance-resource.openai.azure.com` | 443 | HTTPS | Azure OpenAI surface — the **LLM** (`gpt-5.4` compliance analysis / chat / rule extraction, `gpt-5.4-nano` critic). Core function; the app is non-functional without it. | Permanent (life of app) |
| 2 | `bl-bajaj-compliance-resource.services.ai.azure.com` | 443 | HTTPS | Azure AI Foundry **model-inference** surface — **Cohere embed v3** embeddings that power precedent retrieval (RAG), and Cohere rerank v4. Cohere is not served on the `.openai.azure.com` host. | Permanent (life of app) |

> Both hosts belong to the same Foundry project and share one API key. The firewall rules only need the **hosts** above (port 443).
>
> Note: these hosts are intercepted by Cisco Umbrella SSL inspection. The app already trusts the Bajaj root + Cisco Umbrella CA chain (registered into the VM trust store in §3), so no TLS-bypass is required. As a Bajaj-tenant Azure resource, the marketing copy stays within Bajaj's Azure subscription rather than going to a public third-party AI API.

### 2b. Build / setup — served from internal mirror (confirm internal hostnames with Infra)

These are normally **internal Artifactory** proxies, not public internet. Listed so the proxy mapping can be confirmed. If any must reach public source, the public origin is shown in parentheses.

| Purpose | Internal mirror (to confirm) | Public origin (only if no mirror) | Port |
|---------|------------------------------|-----------------------------------|------|
| Container images (pgvector/pg15, redis:7, postgres:15, python:3.11-slim, node:20-slim) | Internal Docker registry mirror | `registry-1.docker.io`, `auth.docker.io`, `production.cloudflare.docker.com` | 443 |
| Docker Engine RPMs | Internal dnf/Docker-CE repo mirror | `download.docker.com` | 443 |
| RHEL OS packages (git, dnf-plugins-core, etc.) | Internal RHEL/Satellite repo | Red Hat CDN | 443 |
| Python packages (pip) | Internal PyPI index | `pypi.org`, `files.pythonhosted.org` | 443 |
| Node packages (npm) | Internal npm registry | `registry.npmjs.org` | 443 |
| Debian base-image apt (build-essential, libpq-dev, poppler-utils) | Internal Debian mirror | `deb.debian.org`, `security.debian.org` | 80/443 |

### 2c. Conditional — OFF by default, request only if the feature is enabled

| URL / Host | Port | When needed |
|------------|------|-------------|
| `openaipublic.blob.core.windows.net` | 443 | tiktoken tokenizer download. **Pre-cached in the image — not needed at runtime.** Keep blocked. |
| `api.smith.langchain.com` | 443 | LangSmith tracing. Disabled by default (`LANGCHAIN_TRACING_V2=false`). Request only if observability tracing is turned on. |
| `api.cohere.com` | 443 | Only if embeddings switched to Cohere. Not used in production. |
| `generativelanguage.googleapis.com`, `api.openai.com`, `api.cohere.com` | 443 | **Dev/local only** LLM/embedding providers. **Not used in production** — production uses the Azure Foundry hosts in §2a. Keep blocked on the VM. |
| `<search-service>.search.windows.net` | 443 | Only if the vector store is switched away from on-VM pgvector to Azure AI Search. Not used by default (pgvector runs on the VM, no egress). |

---

## 3. SUDO Access

### 3a. Activities to be performed (with the commands)

All commands are one-time **setup/maintenance** of a Docker host; day-to-day app operation runs as the non-root `docker` group user and needs no sudo.

**(i) Install Docker Engine + Compose plugin** (from internal mirror repo)
```bash
sudo dnf install -y dnf-plugins-core
sudo dnf config-manager --add-repo <INTERNAL_ARTIFACTORY>/docker-ce/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io \
                    docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

**(ii) Allow the project team to run containers without per-command root**
```bash
sudo usermod -aG docker diwakar.adhikari01
sudo usermod -aG docker yashraj.lawate
sudo usermod -aG docker abhishek.gurjar
sudo usermod -aG docker tanish.jagtap
sudo usermod -aG docker utkarsh.das
```

**(iii) Point Docker at the internal registry mirror + corporate proxy**
```bash
# /etc/docker/daemon.json  -> registry-mirrors / insecure-registries (internal)
sudo install -d /etc/docker
sudo tee /etc/docker/daemon.json   # write registry-mirror config
# Proxy for the daemon to reach internal Artifactory / Azure OpenAI:
sudo install -d /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/http-proxy.conf   # HTTP(S)_PROXY
sudo systemctl daemon-reload
sudo systemctl restart docker
```

**(iv) Register Bajaj + Cisco Umbrella root CAs into the VM trust store** (so SSL-inspected HTTPS to the Azure OpenAI host validates)
```bash
sudo cp certs/bajaj-root.pem certs/cisco-umbrella-root.pem \
        certs/cisco-umbrella-primary.pem certs/cisco-umbrella-secondary.pem \
        /etc/pki/ca-trust/source/anchors/
sudo update-ca-trust
```

**(v) Open the application ports in firewalld**
```bash
sudo firewall-cmd --permanent --add-port=3000/tcp   # frontend (Next.js)
sudo firewall-cmd --permanent --add-port=8000/tcp   # backend API (FastAPI)
sudo firewall-cmd --reload
```

**(vi) SELinux labelling for the bind-mounted data directories** (uploads, logs, dataset, docs, db backups)
```bash
sudo chcon -Rt container_file_t /opt/regulatory-compliance/{uploads,logs,backups,dataset,docs}
# (persistent equivalent: semanage fcontext -a -t container_file_t '<path>(/.*)?' && restorecon -Rv <path>)
```

**(vii) Create the application directory and hand ownership to the team**
```bash
sudo mkdir -p /opt/regulatory-compliance
sudo chown -R diwakar.adhikari01:appteam /opt/regulatory-compliance
sudo dnf install -y git    # if pulling code from internal GitLab/Bitbucket
```

**(viii) Service lifecycle during maintenance / patching**
```bash
sudo systemctl restart docker
sudo systemctl status docker
sudo dnf update -y docker-ce docker-ce-cli containerd.io   # security patching
```

> We are **not** requesting unrestricted `ALL=(ALL) ALL`. The above can be granted as a scoped sudoers entry limited to: `dnf`, `systemctl` (docker/containerd), `usermod -aG docker`, `firewall-cmd`, `update-ca-trust`, `cp`/`tee` into the CA-anchor and docker config paths, `chcon`/`semanage`/`restorecon`, and `mkdir`/`chown` under `/opt/regulatory-compliance`.
>
> **Acknowledged:** `docker` group membership is effectively root-equivalent (the daemon runs as root). We accept this because the VM is **single-tenant** (this app only) and the five IDs are the app owners. If Security prefers to avoid it, the alternative is **rootless Docker** / Podman; we chose Docker Engine for fidelity to the compose file but can switch to rootless on request.

### 3b. Scope of responsibility (AI/Marketing project team)

- Install, configure, patch and operate **only** the Docker host and this single application stack on the assigned VM.
- Manage application containers, configuration, logs, and the Postgres/Redis data volumes for the Regulatory Compliance Agent.
- Keep Docker Engine and base images on patched versions per Bajaj security policy.
- **Out of scope:** any OS-level change unrelated to this app, other tenants/services on the VM, network/firewall changes beyond ports 3000/8000, or user/account administration beyond adding the five named IDs to the `docker` group.

### 3c. Access duration

- **Privileged (sudo) setup window:** time-boxed, e.g. **5 working days** to cover install + configuration + validation. Convert to JIT/just-in-time via ARCON PAM if preferred.
- **Ongoing privileged access:** on-demand only, raised per change ticket for patching/upgrades. No standing root.
- **Application (non-root) access:** for the life of the project.

---

## 4. File Upload / Download via ARCON PAM — Justification

PAM file transfer is needed to move a small, defined set of **non-interactive deployment artifacts** onto the VM and to retrieve diagnostics off it. The app itself does not browse the internet for these.

**Upload (into the VM) — required:**
- Application source / deployment bundle (Docker Compose files + backend/frontend code) from internal repo, if not pulled via internal git.
- Corporate root-CA certificate bundle (`bajaj-root.pem`, `cisco-umbrella-*.pem`) for the trust store.
- Environment config (`.env`) containing the Azure OpenAI API key and DB credentials — sensitive, must go through PAM, not email.
- The reviewer-decision dataset and guideline docs used to seed the knowledge base (read-only reference corpus).

**Download (off the VM) — required:**
- Application/diagnostic logs and Postgres backup dumps (the stack writes daily 14-day-retention dumps) for troubleshooting and DR validation.

All transfers are auditable through ARCON PAM session recording. No bulk or ad-hoc data export — only the artifacts above.

---

## 5. HOD Approval

The above activities (internet whitelist §2, sudo scope §3, PAM transfer §4) are submitted for HOD sign-off. **[HOD name] — approval to follow on this thread / in the access request ticket.]**

---

## 6. Source IP VA Report & EDR Installation Screenshot

- **Internal VA report** for the VM's source IP: to be attached once the VM is provisioned and scanned (coordinate scan with Infra/Security). *[Attach VA report PDF.]*
- **EDR installation screenshot** confirming the endpoint agent is installed and reporting on the VM: *[Attach screenshot.]*

These two are environment-dependent and will be attached after the VM is handed over and the agent/scan complete.

---

### Quick reference — what each named ID needs
| ID | docker group | sudo (setup window) | PAM file transfer |
|----|:---:|:---:|:---:|
| diwakar.adhikari01 | ✅ | ✅ (lead) | ✅ |
| yashraj.lawate | ✅ | ✅ | ✅ |
| abhishek.gurjar | ✅ | ✅ | ✅ |
| tanish.jagtap | ✅ | ✅ | ✅ |
| utkarsh.das | ✅ | ✅ | ✅ |
