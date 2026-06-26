# Azure AI Foundry — Platform Guardrails (Portal Guide)

Platform-layer controls that sit **outside** the application, so they hold even
if the app is misconfigured (defense in depth). These complement the in-app
guardrails already shipped (per-IP rate limit, global daily token budget, call
timeouts, PII redaction, input caps).

Assumes **no APIM** in front of the model endpoint — every control here is at the
Foundry / Azure OpenAI resource or subscription level. See §7 for what APIM would
add later.

Portal: <https://ai.azure.com> (Azure AI Foundry) and <https://portal.azure.com>
(Cost Management, Diagnostics, Networking). Menu labels drift slightly between
releases — match on intent if a name differs.

| # | Guardrail | Mitigates (from the security review) |
|---|-----------|--------------------------------------|
| 1 | Content filter + Prompt Shields | Prompt injection / jailbreak, harmful content (gap #4) |
| 2 | Deployment TPM/RPM quota | Runaway cost — hard backstop (gap #3) |
| 3 | Azure Budget + cost alerts | Runaway cost — financial backstop (gap #3) |
| 4 | Diagnostic logging → Log Analytics | Replaces plaintext `log.json` (gap #6) |
| 5 | Network isolation (private endpoint) | Open/internet-reachable surface (gaps #1/#2) |
| 6 | Managed identity + Key Vault | Long-lived key in `.env` (gap, app host) |
| 7 | (Recommended) APIM in front | Durable per-token rate limiting (gaps #1/#2) |

---

## 1. Content filter + Prompt Shields

Detects and blocks jailbreak / prompt-injection attempts and harmful categories
on **both** the input and the model output. This is the platform-side answer to
untrusted submission text and chat messages flowing into the prompt.

1. In **Azure AI Foundry** → open your **Project**.
2. Left nav → **Guardrails + controls** (older UI: **Safety + security** →
   **Content filters**).
3. **+ Create content filter**. Name it e.g. `compliance-strict`.
4. **Input filter** — set thresholds for Hate / Sexual / Violence / Self-harm
   (start at **Medium**), and **enable**:
   - **Prompt shields for jailbreak attacks** (direct user jailbreaks)
   - **Prompt shields for indirect attacks** (injection hidden in the submission
     text / retrieved content — directly relevant to your RAG path)
5. **Output filter** — set the same harm thresholds; optionally enable
   **Protected material** detection for text/code.
6. **Deployment** step — **assign the filter to your chat/analysis deployment(s)**
   (the LLM_MODEL deployment, and the LLM_CLASSIFY_MODEL deployment if separate).
7. **Create**. Verify on the deployment page that the **Content filter** column
   shows `compliance-strict`, not `Default`.

> When a request is blocked the API returns HTTP 400 with a `content_filter`
> finish reason. The app already fails closed on non-retryable LLM errors
> (`LLMUnavailableError`), so a blocked analysis surfaces as an error rather
> than a fabricated clean result — which is the correct behavior for compliance.

---

## 2. Deployment TPM/RPM quota (hard cost backstop)

A per-deployment Tokens-Per-Minute ceiling that **even a compromised app cannot
exceed**. This is the platform twin of the app's daily token budget.

1. **Azure AI Foundry** → **Models + endpoints** (or **Deployments**).
2. Click the chat/analysis deployment → **Edit**.
3. Set **Tokens per Minute Rate Limit (thousands)** to a deliberate ceiling
   sized to your subscription quota and expected load (e.g. `30` = 30K TPM).
   Azure derives the **RPM** from TPM (~6 RPM per 1K TPM for Azure OpenAI).
4. **Save / Update**.
5. If you run a separate **LLM_CLASSIFY_MODEL** deployment (the cheap critic
   pass), give it its **own, lower** TPM so a loop on the cheap path can't drain
   the budget for the heavy grading path.
6. Keep the app's `LLM_MAX_TOKENS` (per-call cap, currently 4096) and
   `LLM_DAILY_TOKEN_BUDGET` aligned **below** what this TPM allows, so the app
   guard trips first and the deployment quota is the last-resort wall.

---

## 3. Azure Budget + cost alerts

Financial backstop with email/automation alerts. (Budgets **alert**; they do not
auto-stop spend by themselves — see the optional auto-disable step.)

1. **portal.azure.com** → **Cost Management + Billing** → **Cost Management** →
   **Budgets** (scope to the **subscription** or the **resource group** holding
   the Foundry / Azure OpenAI resource).
2. **+ Add**. Set **Reset period** = Monthly, an **Amount** matching your cap.
3. **Alert conditions** — add thresholds at **50%, 80%, 100%** of budget
   (type: Actual; add a Forecasted 100% alert too).
4. **Alert recipients** — add the team distribution list, or attach an
   **Action group**.
5. **Create**.
6. *(Optional hard stop)* Attach an **Action group** that triggers a **Logic App
   / Azure Function** which calls the ARM API to set the deployment's capacity to
   0 (or disable it) at 100%. This converts the alert into an actual cutoff.
   Treat as break-glass — it stops all analysis until re-enabled.

---

## 4. Diagnostic logging → Log Analytics (retire plaintext `log.json`)

Centralized, access-controlled request/response **metadata** instead of an
unrotated plaintext file on the app host.

1. **portal.azure.com** → open the **Azure OpenAI / Foundry resource**.
2. **Monitoring** → **Diagnostic settings** → **+ Add diagnostic setting**.
3. Select log categories (e.g. **RequestResponse**, **Audit**, **Trace**) and
   **AllMetrics**.
4. Destination → **Send to Log Analytics workspace** (create/select one).
5. **Save**.
6. Keep **prompt/response content logging OFF** (default) so customer PII is not
   captured centrally; rely on token/latency/status metadata. With this in place,
   set the app's `LLM_LOG_TO_FILE=false` in production.

---

## 5. Network isolation (close the open surface)

Until auth is wired in the app, restrict who can even reach the model endpoint.

1. **portal.azure.com** → Foundry / Azure OpenAI resource → **Networking**.
2. Set **Public network access** to **Disabled** (or **Selected networks** with
   your VNet / app subnet / egress IP allow-list).
3. **+ Add private endpoint** → bind to the VNet/subnet the backend runs in →
   approve the connection.
4. Confirm the backend reaches the endpoint over the private path (the app's
   `LLM_BASE_URL` host resolves to the private IP).

> This is the platform mitigation for the app's currently-unauthenticated
> endpoints: even before app auth lands, only your network can call the model.

---

## 6. Managed identity + Key Vault (remove the static key)

1. Give the backend's compute (App Service / Container App / VM) a
   **system-assigned managed identity**.
2. On the Foundry / Azure OpenAI resource → **Access control (IAM)** → assign
   that identity the **Cognitive Services OpenAI User** role.
3. Switch the app from API-key auth to **Entra ID token** auth (the OpenAI SDK
   supports `azure_ad_token_provider`), **or** store the key in **Key Vault** and
   grant the identity **get** on secrets — removing the long-lived key from
   `.env` / the host.

---

## 7. (Recommended) Add APIM in front later

You don't have APIM today. When you do, put it in front of the Foundry endpoint
to get the durable controls the in-process limiter can't provide:

- **`llm-token-limit`** policy — true token-based (not just request-based) rate
  limiting per subscription key.
- **`llm-emit-token-metric`** — per-consumer token metering for chargeback.
- **Per-subscription keys** — real per-caller identity and revocation without
  building app auth first.
- **Semantic caching** — cut cost on repeated prompts.

---

## Suggested rollout order

1. **§2 deployment quota** + **§3 budget** — fastest, hardest cost backstops.
2. **§1 content filter + Prompt Shields** — closes the injection surface.
3. **§5 network isolation** — shrinks the open attack surface until app auth lands.
4. **§4 diagnostics** then flip `LLM_LOG_TO_FILE=false`.
5. **§6 managed identity**, then **§7 APIM** when available.
