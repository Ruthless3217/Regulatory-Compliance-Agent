import json
import asyncio
import time
import os
from typing import Dict, Any, Optional, Type, TypeVar, AsyncIterator, List, Callable
from sqlalchemy.orm import Session
import logging
from datetime import datetime
from pydantic import BaseModel, ValidationError
from ..config import settings
from openai import AsyncOpenAI
try:  # AsyncAzureOpenAI ships with openai>=1.0; guard so import never hard-fails
    from openai import AsyncAzureOpenAI
except Exception:  # pragma: no cover
    AsyncAzureOpenAI = None  # type: ignore
try:  # openai>=1.0 ships RateLimitError; guard so import never hard-fails
    from openai import RateLimitError
except Exception:  # pragma: no cover
    RateLimitError = None  # type: ignore
import httpx
from app.services.observability import usage_recorder

# LangSmith tracing — no-op decorator if the SDK isn't installed.
try:
    try:
        from langsmith import traceable, get_current_run_tree
    except ImportError:
        from langsmith import traceable
        from langsmith.run_helpers import get_current_run_tree
except Exception:  # pragma: no cover
    def traceable(*_a, **_kw):  # type: ignore
        def _decorate(fn):
            return fn
        return _decorate if not (_a and callable(_a[0])) else _a[0]

    def get_current_run_tree():
        return None

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)

# health_check() cache: model -> (monotonic_timestamp, ok). Keyed by model so
# the analysis / chat / critic profiles never share a verdict. Monotonic clock
# so a system clock change can't pin a stale entry as fresh forever.
_HEALTH_TTL_SECONDS = 60.0
_HEALTH_CACHE: Dict[str, tuple] = {}
_monotonic = time.monotonic


async def _record_budget_tokens(tokens: int) -> None:
    """Add real token usage to the global daily budget counter. Best-effort:
    a budget/Redis hiccup must never break an LLM call (imported lazily to
    avoid a circular import at module load)."""
    try:
        from .llm_budget import record_tokens

        await record_tokens(tokens)
    except Exception:  # pragma: no cover - defensive
        pass


class LLMUnavailableError(RuntimeError):
    """Raised when the LLM cannot produce a usable result (transport failure,
    or schema-invalid output after all retries).

    For a compliance system this MUST propagate rather than be swallowed: a
    fabricated empty result would be scored as a clean (100/A) document, i.e.
    the pipeline would fail OPEN. Callers must fail closed on this error.
    See docs/architect-audit-2026-05-30.md (C1, H4).
    """


class _RateLimitFailover(RuntimeError):
    """Internal signal: the current API key was rate-limited (HTTP 429).

    Raised out of a single-key attempt so the multi-key driver can rotate to
    the next key instead of failing closed. Never surfaced to callers — it is
    either swallowed (more keys to try) or converted to LLMUnavailableError
    (all keys exhausted). ``__cause__`` holds the original provider error.
    """


def _is_rate_limit_error(exc: Exception) -> bool:
    """True if ``exc`` is a provider rate-limit / 429 (Groq TPM or TPD).

    Matches the typed openai.RateLimitError, an HTTP 429 on any wrapped
    response, or telltale text — robust across SDK versions and the way Groq
    phrases its quota errors."""
    if RateLimitError is not None and isinstance(exc, RateLimitError):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 429:
        return True
    msg = str(exc).lower()
    return "rate_limit" in msg or "rate limit" in msg or "429" in msg


def _resolve_profile(profile: str) -> Dict[str, Any]:
    """Resolve a provider config profile from ``settings``.

    ``"main"`` drives analysis/grading off the LLM_* vars. ``"chat"`` drives the
    streaming chat assistant off the CHAT_LLM_* vars, each field falling back to
    the main profile when left empty — so chat can run on a different provider
    (e.g. Groq) than analysis (e.g. Azure) without duplicating config.
    ``"critic"`` drives the independent generator/critic model (e.g. gpt-5.4-nano)
    off the CRITIC_LLM_* vars with the same fallback semantics.
    """
    if profile == "chat":
        provider = (settings.chat_llm_provider or settings.llm_provider or "openai").lower()
        base_url = settings.chat_llm_base_url or settings.llm_base_url
        model = settings.chat_llm_model or settings.llm_model
        api_keys = settings.chat_llm_api_keys
        max_tokens = settings.chat_llm_max_tokens or settings.llm_max_tokens
        insecure_tls = settings.chat_llm_insecure_tls
        use_max_completion = settings.chat_llm_use_max_completion_tokens
        supports_temperature = settings.chat_llm_supports_temperature
        azure_api_version = settings.chat_llm_azure_api_version or settings.llm_azure_api_version
        reasoning_effort = settings.chat_llm_reasoning_effort
    elif profile == "critic":
        provider = (settings.critic_llm_provider or settings.llm_provider or "openai").lower()
        base_url = settings.critic_llm_base_url or settings.llm_base_url
        model = settings.critic_llm_model or settings.llm_model
        api_keys = settings.critic_llm_api_keys
        max_tokens = settings.critic_llm_max_tokens or settings.llm_max_tokens
        insecure_tls = settings.critic_llm_insecure_tls
        # None (unset/blank) inherits main; an explicit True/False is honored.
        # The critic shares the main Azure resource, so these MUST track main —
        # e.g. gpt-5.4-nano rejects `temperature` exactly like gpt-5.4.
        use_max_completion = (
            settings.critic_llm_use_max_completion_tokens
            if settings.critic_llm_use_max_completion_tokens is not None
            else settings.llm_use_max_completion_tokens
        )
        supports_temperature = (
            settings.critic_llm_supports_temperature
            if settings.critic_llm_supports_temperature is not None
            else settings.llm_supports_temperature
        )
        azure_api_version = settings.critic_llm_azure_api_version or settings.llm_azure_api_version
        reasoning_effort = settings.critic_llm_reasoning_effort
    else:  # "main"
        provider = (settings.llm_provider or "openai").lower()
        base_url = settings.llm_base_url
        model = settings.llm_model
        api_keys = settings.llm_api_keys
        max_tokens = settings.llm_max_tokens
        insecure_tls = settings.llm_insecure_tls
        use_max_completion = settings.llm_use_max_completion_tokens
        supports_temperature = settings.llm_supports_temperature
        azure_api_version = settings.llm_azure_api_version
        reasoning_effort = settings.llm_reasoning_effort

    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_keys": api_keys,
        "max_tokens": max_tokens,
        "insecure_tls": insecure_tls,
        "token_limit_param": "max_completion_tokens" if use_max_completion else "max_tokens",
        "supports_temperature": supports_temperature,
        "azure_api_version": azure_api_version,
        "reasoning_effort": (reasoning_effort or "").strip(),
    }


class LLMService:
    """Service for integrating with Cloud LLMs via the OpenAI Chat Completions
    surface — works against any OpenAI-compatible endpoint (Groq/Ollama/OpenAI)
    or Azure OpenAI (via AsyncAzureOpenAI). One instance per provider profile."""

    def __init__(self, profile: str = "main"):
        cfg = _resolve_profile(profile)
        self.profile = profile
        self.provider = cfg["provider"]
        self.base_url = cfg["base_url"]
        self.model = cfg["model"]
        self.max_tokens = cfg["max_tokens"]
        self.insecure_tls = cfg["insecure_tls"]
        self.token_limit_param = cfg["token_limit_param"]
        self.supports_temperature = cfg["supports_temperature"]
        self.azure_api_version = cfg["azure_api_version"]
        self.reasoning_effort = cfg["reasoning_effort"]

        # Logging config
        self.log_file = os.path.join("logs", "log.json")
        os.makedirs("logs", exist_ok=True)

        if self.insecure_tls:
            logger.warning(
                "[%s] LLM_INSECURE_TLS=true — disabling TLS verification for LLM calls",
                profile,
            )

        # One client per configured key. Multiple keys = TPM failover: the
        # request drivers below rotate to the next key when one is 429'd or
        # over its daily budget. key_id (last 8 chars of the key) ties each
        # key to its own per-key rate limiter without logging the secret.
        self.api_keys = cfg["api_keys"]
        if not self.api_keys:
            logger.warning("[%s] LLM API key is not set. LLM service will fail.", profile)
            self._client_pool = [("none", self._build_client("placeholder"))]
        else:
            self._client_pool = [
                (k[-8:], self._build_client(k)) for k in self.api_keys
            ]
        logger.info(
            "[%s] LLM provider=%s model=%s keys=%d token_param=%s",
            profile, self.provider, self.model, len(self._client_pool),
            self.token_limit_param,
        )

        # Back-compat: callers (e.g. health_check) that reference .client / .api_key
        # get the first key in the pool.
        self.api_key = self.api_keys[0] if self.api_keys else ""
        self.client = self._client_pool[0][1]

    def _build_client(self, api_key: str):
        """Construct one provider client bound to ``api_key``.

        Azure OpenAI is NOT wire-compatible with the plain OpenAI client (api-key
        header, api-version query, deployment-based routing), so it gets the
        dedicated AsyncAzureOpenAI client. For Azure, ``self.base_url`` is the
        resource root and ``self.model`` is the deployment name.
        """
        http_client = httpx.AsyncClient(verify=False) if self.insecure_tls else None
        if self.provider == "azure":
            if AsyncAzureOpenAI is None:  # pragma: no cover
                raise RuntimeError(
                    "LLM_PROVIDER=azure but AsyncAzureOpenAI is unavailable; "
                    "upgrade the openai package."
                )
            azure_kwargs: Dict[str, Any] = {
                "api_key": api_key or "placeholder",
                "azure_endpoint": self.base_url,
                "api_version": self.azure_api_version,
                "azure_deployment": self.model,
            }
            if http_client is not None:
                azure_kwargs["http_client"] = http_client
            return AsyncAzureOpenAI(**azure_kwargs)

        client_kwargs: Dict[str, Any] = {
            "api_key": api_key or "placeholder",
            # Bound every call so a hung provider can't pin an async worker
            # forever; SDK-level retries cover transient transport blips.
            "timeout": settings.llm_request_timeout,
            "max_retries": settings.llm_max_retries,
        }
        if http_client is not None:
            client_kwargs["http_client"] = http_client
        return AsyncOpenAI(**client_kwargs)

    def _gen_params(self, temperature, max_tokens=None) -> Dict[str, Any]:
        """Build the provider-variable generation params: the token-limit kwarg
        under the right name (``max_tokens`` vs ``max_completion_tokens`` for
        reasoning models) and ``temperature`` only when the model accepts it."""
        params: Dict[str, Any] = {
            self.token_limit_param: max_tokens if max_tokens is not None else self.max_tokens
        }
        if self.supports_temperature and temperature is not None:
            params["temperature"] = temperature
        # Reasoning effort is the dominant cost lever on reasoning models; only
        # send it when configured (non-reasoning models like Groq llama reject it).
        if self.reasoning_effort:
            params["reasoning_effort"] = self.reasoning_effort
        return params

    async def health_check(self) -> bool:
        """Check if LLM service is available. Result is cached per model for
        ``_HEALTH_TTL_SECONDS``.

        Azure OpenAI does not expose ``/models`` on the resource root, so
        ``models.list()`` 404s there even when chat calls succeed. Treat that as
        available rather than failing the probe (and warning about a non-issue).

        The cache is not an optimisation, it is a quota fix. GET /health calls
        this on every request and the frontend polls it continuously; in
        production that was ~2 live Azure round-trips every 10s (~17k/day),
        drawn from the SAME per-minute token quota the analysis pipeline needs,
        so health polling was actively slowing down grading. Note also that on
        Azure both branches below return True — there, the network call cannot
        change the answer at all.
        """
        now = _monotonic()
        cached = _HEALTH_CACHE.get(self.model)
        if cached is not None and now - cached[0] < _HEALTH_TTL_SECONDS:
            return cached[1]

        try:
            await self.client.models.list()
            logger.info(f"✅ LLM service available with model '{self.model}'")
            ok = True
        except Exception as e:
            # NB: this used to read `settings.llm_is_azure`, which does not
            # exist on Settings — accessing it raises AttributeError, so this
            # entire fallback could never run. It went unnoticed because
            # models.list() succeeds against the current Azure resource, so the
            # except branch is normally dead. The moment Azure did 404 (the
            # exact case the fallback documents) the probe would have raised
            # instead of reporting available.
            if str(settings.llm_provider).strip().lower() == "azure":
                logger.info(
                    f"Azure endpoint has no models.list; assuming deployment "
                    f"'{self.model}' is available (chat calls verified at runtime)"
                )
                ok = True
            else:
                logger.warning(f"LLM health check failed: {str(e)}")
                ok = False

        _HEALTH_CACHE[self.model] = (now, ok)
        return ok

    @traceable(run_type="llm", name="LLM.generate_response")
    async def generate_response(
        self,
        prompt: str,
        system_prompt: str = None,
        context: Dict[str, Any] = None,
        **kwargs
    ) -> str:
        """Generate response from LLM."""
        messages = self._build_chat_messages(prompt, system_prompt, context)
        self._enforce_context_budget(messages)
        from app.services.llm_budget import get_global_budget
        global_budget = get_global_budget()
        estimate = self._estimate_tokens(messages)
        last_exc: Optional[Exception] = None
        for key_id, client in self._client_pool:
            limiter = self._groq_limiter_for(key_id)
            try:
                if limiter is not None:
                    await limiter.acquire(estimate)
                await global_budget.reserve(estimate)
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    **self._gen_params(
                        kwargs.get("temperature", 0.7),
                        kwargs.get("max_tokens"),
                    ),
                )
                response_text = response.choices[0].message.content.strip()

                # Extract token usage and update LangSmith run tree
                usage = getattr(response, "usage", None)
                input_tokens = 0
                output_tokens = 0
                total_tokens = 0
                if usage:
                    input_tokens = getattr(usage, "prompt_tokens", 0)
                    output_tokens = getattr(usage, "completion_tokens", 0)
                    total_tokens = getattr(usage, "total_tokens", 0)

                self._update_langsmith_usage(input_tokens, output_tokens, total_tokens)

                await usage_recorder.record(
                    model=self.model,
                    provider=self.provider,
                    profile=self.profile,
                    prompt_tokens=input_tokens,
                    completion_tokens=output_tokens
                )

                actual = total_tokens or estimate
                if limiter is not None:
                    limiter.reconcile(estimate, actual)
                await global_budget.reconcile(estimate, actual)
                await self._log_to_json(prompt, response_text, system_prompt, context)
                return response_text
            except Exception as e:
                if _is_rate_limit_error(e) and len(self._client_pool) > 1:
                    logger.warning(f"[failover] key …{key_id} rate-limited; trying next key")
                    last_exc = e
                    continue
                # FAIL CLOSED: do not return a canned string that a caller would
                # treat as a valid (benign) answer. Mirrors the structured path.
                logger.error(f"LLM generation failed: {str(e)}")
                raise LLMUnavailableError(f"LLM call failed: {e}") from e
        logger.error(f"LLM generation failed — all keys rate-limited: {last_exc}")
        raise LLMUnavailableError(
            f"All {len(self._client_pool)} LLM key(s) rate-limited; last error: {last_exc}"
        ) from last_exc

    @traceable(run_type="llm", name="LLM.stream_response")
    async def stream_response(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
        on_usage: Optional[Callable[[int], None]] = None,
    ) -> AsyncIterator[str]:
        """Stream response tokens from LLM. Yields text deltas.

        ``on_usage`` (optional) is invoked once with the real total token count
        from the provider's final usage chunk (requires stream_options below).
        """
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        self._enforce_context_budget(messages)
        from app.services.llm_budget import get_global_budget
        global_budget = get_global_budget()
        estimate = self._estimate_tokens(messages)

        # Failover can only happen BEFORE the first token: once we have yielded
        # text we can't restart on another key without duplicating output.
        last_exc: Optional[Exception] = None
        for key_id, client in self._client_pool:
            limiter = self._groq_limiter_for(key_id)
            try:
                if limiter is not None:
                    await limiter.acquire(estimate)
                await global_budget.reserve(estimate)
                stream = await client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    stream=True,
                    stream_options={"include_usage": True},
                    **self._gen_params(temperature),
                )
            except Exception as e:
                if _is_rate_limit_error(e) and len(self._client_pool) > 1:
                    logger.warning(f"[failover] stream key …{key_id} rate-limited; trying next key")
                    last_exc = e
                    continue
                logger.error(f"LLM streaming failed: {e}")
                yield f"\n\n[Error: streaming failed — {str(e)}]"
                return
            try:
                input_tokens = 0
                output_tokens = 0
                total_tokens = 0
                async for chunk in stream:
                    usage = getattr(chunk, "usage", None)
                    if usage:
                        input_tokens = getattr(usage, "prompt_tokens", 0)
                        output_tokens = getattr(usage, "completion_tokens", 0)
                        total_tokens = getattr(usage, "total_tokens", 0)

                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield delta.content

                if total_tokens > 0:
                    self._update_langsmith_usage(input_tokens, output_tokens, total_tokens)
                    await usage_recorder.record(
                        model=self.model,
                        provider=self.provider,
                        profile=self.profile,
                        prompt_tokens=input_tokens,
                        completion_tokens=output_tokens
                    )
                return
            except Exception as e:
                logger.error(f"LLM streaming failed mid-stream: {e}")
                yield f"\n\n[Error: streaming failed — {str(e)}]"
                return
        logger.error(f"LLM streaming failed — all keys rate-limited: {last_exc}")
        yield "\n\n[Error: streaming failed — all API keys are rate-limited]"

    @traceable(run_type="llm", name="LLM.generate_structured_response")
    async def generate_structured_response(
        self,
        prompt: str,
        output_model: Type[T],
        system_prompt: str = None,
        context: Dict[str, Any] = None,
        execution_id: str = None,
        db: Session = None,
        tool_name: str = "llm_structured",
        temperature: float = 0.2,
        model: Optional[str] = None,
    ) -> T:
        """Generate a structured response validated against a Pydantic model.

        ``model`` overrides the deployment for this call (e.g. the cheap
        LLM_CLASSIFY_MODEL for the critic pass); it defaults to LLM_MODEL.

        Drives the request across all configured API keys: when a key returns
        HTTP 429 we rotate to the next key. Only when every key is exhausted do
        we fail closed.
        """
        use_model = model or self.model
        schema_instruction = (
            f"\nYou must output JSON that adheres to this schema:\n"
            f"{output_model.model_json_schema()}\n"
            f"Return ONLY the JSON object, no other text."
        )
        full_system_prompt = (system_prompt or "") + schema_instruction
        base_messages = self._build_chat_messages(prompt, full_system_prompt, context)
        self._enforce_context_budget(base_messages)
        from app.services.llm_budget import get_global_budget
        global_budget = get_global_budget()

        # Groq enforces TPM/TPD ceilings PER KEY. Reserve budget before each call
        # so we rotate (or queue) rather than burn a 429-doomed run. Only applies
        # to Groq — other providers (Gemini) must not inherit Groq's limits.
        # See architect-audit C7.
        is_groq = "groq" in (self.base_url or "").lower()
        get_rate_limiter = None
        DailyLimitApproaching = ()  # so `except DailyLimitApproaching` is a no-op when not Groq
        if is_groq:
            from app.services.groq_rate_limiter import (
                get_rate_limiter,
                DailyLimitApproaching,
            )

        last_exc: Optional[Exception] = None
        daily_blocked = 0
        pool = self._client_pool
        for key_id, client in pool:
            # Fresh message copy per key — a failed key's JSON-correction turns
            # must not leak into the next key's conversation.
            current_messages = list(base_messages)

            limiter = None
            token_estimate = 0
            if get_rate_limiter is not None:
                limiter = get_rate_limiter(self.model, key_id)
                token_estimate = self._estimate_tokens(current_messages)
                try:
                    await limiter.acquire(token_estimate)
                except DailyLimitApproaching as e:
                    logger.warning(
                        f"[failover] key …{key_id} at daily cap; trying next key ({e})"
                    )
                    last_exc = e
                    daily_blocked += 1
                    continue
            else:
                token_estimate = self._estimate_tokens(current_messages)

            # Global wallet ceiling — independent of per-key Groq quota. Applies
            # to every key, so exhaustion fails closed rather than rotating.
            from app.services.llm_budget import LLMBudgetExceeded
            try:
                await global_budget.reserve(token_estimate)
            except LLMBudgetExceeded as e:
                raise LLMUnavailableError(str(e)) from e

            try:
                return await self._structured_attempts(
                    client=client,
                    output_model=output_model,
                    current_messages=current_messages,
                    model=use_model,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    context=context,
                    execution_id=execution_id,
                    db=db,
                    tool_name=tool_name,
                    temperature=temperature,
                    limiter=limiter,
                    token_estimate=token_estimate,
                )
            except _RateLimitFailover as e:
                logger.warning(f"[failover] key …{key_id} hit 429; trying next key")
                last_exc = e.__cause__ or e
                continue

        # Every key is exhausted — fail closed (callers must not treat this as a
        # clean document; see LLMUnavailableError).
        raise LLMUnavailableError(
            f"All {len(pool)} LLM key(s) exhausted; last error: {last_exc}"
        ) from last_exc

    async def _structured_attempts(
        self,
        *,
        client,
        output_model: Type[T],
        current_messages: list,
        model: str,
        prompt: str,
        system_prompt: Optional[str],
        context: Optional[Dict[str, Any]],
        execution_id: Optional[str],
        db: Optional[Session],
        tool_name: str,
        temperature: float,
        limiter=None,
        token_estimate: int = 0,
    ) -> T:
        """Run the schema-validation retry loop against a single key's client.

        Raises :class:`_RateLimitFailover` on an HTTP 429 so the driver can
        rotate to the next key; raises :class:`LLMUnavailableError` (fail
        closed) on schema-invalid JSON after retries or any other
        non-retryable transport error.
        """
        start_time = time.time()
        max_retries = 3
        response_text = ""

        for attempt in range(max_retries):
            try:
                # Plain create (not with_raw_response): standard for Azure/OpenAI
                # and the path wrap_openai instruments, so token usage is
                # captured on the LangSmith run automatically. (The old raw path
                # existed only to parse Gemini's non-standard usageMetadata.)
                response = await client.chat.completions.create(
                    model=model,
                    messages=current_messages,
                    response_format={"type": "json_object"},
                    **self._gen_params(temperature),
                )

                response_text = response.choices[0].message.content.strip()

                # FAIL CLOSED on output truncation. finish_reason='length' means
                # the model stopped because it hit max_tokens, so the JSON finding
                # list is likely cut short — accepting it would silently
                # under-report violations. Retrying won't help (same ceiling), so
                # raise immediately → the chunk degrades to needs_review rather
                # than recording an incomplete grade. Raise LLM_MAX_TOKENS for the
                # deployed model to fix. (Audit: finish_reason never checked.)
                finish_reason = getattr(response.choices[0], "finish_reason", None)
                if finish_reason == "length":
                    logger.error(
                        "LLM output truncated (finish_reason='length') at "
                        "max_tokens=%s; failing closed instead of accepting a "
                        "possibly-incomplete result.",
                        self.max_tokens,
                    )
                    raise LLMUnavailableError(
                        f"LLM output truncated at max_tokens="
                        f"{self.max_tokens} (finish_reason='length'); "
                        f"refusing a possibly-incomplete grading result. "
                        f"Increase LLM_MAX_TOKENS for the deployed model."
                    )

                # Extract token usage from the standard OpenAI response object.
                # (The old raw_response / usageMetadata path was removed when we
                # switched from with_raw_response to plain create().)
                token_usage = 0
                input_tokens = 0
                output_tokens = 0
                if hasattr(response, 'usage') and response.usage:
                    input_tokens = getattr(response.usage, 'prompt_tokens', 0) or 0
                    output_tokens = getattr(response.usage, 'completion_tokens', 0) or 0
                    token_usage = getattr(response.usage, 'total_tokens', 0) or 0


                self._update_langsmith_usage(
                    input_tokens,
                    output_tokens,
                    token_usage or (input_tokens + output_tokens)
                )

                await usage_recorder.record(
                    model=model,
                    provider=self.provider,
                    profile=self.profile,
                    prompt_tokens=input_tokens,
                    completion_tokens=output_tokens,
                    is_retry=(attempt > 0)
                )

                await self._log_to_json(prompt, response_text, system_prompt, context)

                # Clean JSON
                if "```json" in response_text:
                    start = response_text.find("```json") + 7
                    end = response_text.find("```", start)
                    response_text = response_text[start:end].strip()
                elif "```" in response_text:
                    start = response_text.find("```") + 3
                    end = response_text.find("```", start)
                    response_text = response_text[start:end].strip()

                result = output_model.model_validate_json(response_text)
                end_time = time.time()

                # Correct the reserved estimate against Groq's reported usage.
                actual = token_usage or token_estimate
                if limiter is not None:
                    limiter.reconcile(token_estimate, actual)
                from app.services.llm_budget import get_global_budget
                await get_global_budget().reconcile(token_estimate, actual)

                if execution_id and db:
                    await self._record_tool_invocation(
                        db=db,
                        execution_id=execution_id,
                        tool_name=tool_name,
                        input_data={
                            "prompt": prompt[:500],
                            "model": model,
                            "provider": self.provider,
                        },
                        output_data=result.model_dump(mode='json'),
                        start_time=start_time,
                        end_time=end_time,
                        tokens=token_usage
                    )

                return result

            except (ValidationError, json.JSONDecodeError) as e:
                # Schema-invalid output is the only retryable case: re-prompt the
                # model with the error and try again.
                logger.warning(
                    f"Structured generation attempt {attempt + 1} produced "
                    f"invalid JSON: {e}"
                )
                if attempt == max_retries - 1:
                    # FAIL CLOSED: do not fabricate an empty ('clean') result.
                    logger.error(
                        "All retries exhausted; raising LLMUnavailableError "
                        "(failing closed) instead of returning a fake result."
                    )
                    raise LLMUnavailableError(
                        f"LLM did not return schema-valid JSON for "
                        f"{output_model.__name__} after {max_retries} attempts"
                    ) from e

                error_feedback = f"\n\nPrevious response was invalid. Error: {str(e)}. Please CORRECT the JSON output."
                current_messages.append({"role": "assistant", "content": response_text})
                current_messages.append({"role": "user", "content": error_feedback})
                await asyncio.sleep(1)
            except LLMUnavailableError:
                raise
            except Exception as e:
                # A 429 means THIS key is rate-limited — signal the driver to
                # rotate to the next key rather than failing closed.
                if _is_rate_limit_error(e):
                    raise _RateLimitFailover(str(e)) from e
                # Other transport / auth / quota errors are NOT retryable as
                # "bad JSON". Fail closed immediately.
                logger.error(f"Structured generation failed (non-retryable): {e}")
                raise LLMUnavailableError(f"LLM call failed: {e}") from e

    def _estimate_tokens(self, messages: list) -> int:
        """Rough token estimate for rate-limiting: prompt tokens (tiktoken when
        available, else ~4 chars/token) plus this profile's output ceiling.
        Reconciled against actual usage after the call."""
        return self._count_prompt_tokens(messages) + self.max_tokens

    @staticmethod
    def _count_prompt_tokens(messages: list) -> int:
        """Count prompt tokens accurately with tiktoken (cl100k_base) when it's
        installed; fall back to a 4-chars/token heuristic otherwise."""
        text = "\n".join(str(m.get("content", "")) for m in messages)
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            return len(text) // 4

    @staticmethod
    def _enforce_context_budget(messages: list) -> None:
        """Fail CLOSED if the assembled prompt + reserved output would overflow
        the model's context window. Without this the provider silently truncates
        the TAIL of the prompt — which is exactly where the document section and
        the authoritative anti-injection REMINDER sit. A too-large prompt becomes
        an explicit LLMUnavailableError (→ needs_review) instead of a silently
        degraded grade. See architect-audit (no token budgeting)."""
        prompt_tokens = LLMService._count_prompt_tokens(messages)
        safety = 512
        ceiling = settings.llm_context_window - settings.llm_max_tokens - safety
        if prompt_tokens > ceiling:
            raise LLMUnavailableError(
                f"Prompt ({prompt_tokens} tokens) + reserved output "
                f"({settings.llm_max_tokens}) exceeds the model context window "
                f"({settings.llm_context_window}). Refusing to send a prompt that "
                f"would be silently truncated."
            )

    def _groq_limiter_for(self, key_id: str):
        """Return the per-key Groq limiter for the active model, or None when the
        provider isn't Groq (other providers must not inherit Groq's ceilings)."""
        if "groq" not in (self.base_url or "").lower():
            return None
        from app.services.groq_rate_limiter import get_rate_limiter
        return get_rate_limiter(self.model, key_id)

    def _build_chat_messages(
        self,
        prompt: str,
        system_prompt: str = None,
        context: Dict[str, Any] = None
    ) -> list:
        """Build chat messages list."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if context:
            context_str = "\n".join(f"{k}: {v}" for k, v in context.items())
            messages.append({"role": "user", "content": f"Context:\n{context_str}\n\n{prompt}"})
        else:
            messages.append({"role": "user", "content": prompt})
        return messages

    def _get_fallback_response(self, prompt: str, context: Dict = None) -> str:
        """Return a fallback response when LLM fails."""
        return "Unable to generate response at this time. Please check LLM configuration."

    async def _record_tool_invocation(
        self,
        db: Session,
        execution_id: str,
        tool_name: str,
        input_data: Dict,
        output_data: Dict,
        start_time: float,
        end_time: float,
        tokens: int
    ):
        """Record a tool invocation to the database."""
        try:
            from ..models.tool_invocation import ToolInvocation
            import uuid

            invocation = ToolInvocation(
                execution_id=uuid.UUID(execution_id),
                tool_name=tool_name,
                input_data=input_data,
                output_data=output_data,
                tokens_used=tokens,
                latency_ms=int((end_time - start_time) * 1000)
            )
            db.add(invocation)
            db.commit()
        except Exception as e:
            logger.error(f"Failed to record tool invocation: {e}")

    async def _log_to_json(self, prompt: str, response: str, system_prompt: str = None, context: Dict = None):
        """Log LLM interaction to log.json."""
        try:
            from app.services.pii import mask_pii, mask_obj
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "model": self.model,
                # Mask PII before it lands on disk — the log must not become a
                # plaintext store of emails/phones/PAN/Aadhaar from submissions.
                "system_prompt": mask_pii(system_prompt),
                "context": mask_obj(context),
                "prompt": mask_pii(prompt[:500]),
                "response": mask_pii(response[:500]),
            }
            logs = []
            if os.path.exists(self.log_file):
                try:
                    with open(self.log_file, "r") as f:
                        logs = json.load(f)
                except Exception:
                    pass
            logs.append(log_entry)
            # Keep last 100 entries
            logs = logs[-100:]
            with open(self.log_file, "w") as f:
                json.dump(logs, f, indent=2)
        except Exception as e:
            logger.debug(f"Failed to log to JSON: {e}")

    def _update_langsmith_usage(self, input_tokens: int, output_tokens: int, total_tokens: int):
        """Update token usage metadata for the active LangSmith run."""
        try:
            run = get_current_run_tree()
            if run:
                provider = "openai"
                base_url_lower = (self.base_url or "").lower()
                model_lower = (self.model or "").lower()
                if "groq" in base_url_lower:
                    provider = "groq"
                elif "google" in base_url_lower or "gemini" in model_lower:
                    provider = "google_genai"
                elif "cohere" in base_url_lower or "cohere" in model_lower:
                    provider = "cohere"
                
                run.add_metadata({
                    "ls_provider": provider,
                    "ls_model_name": self.model
                })
                run.set(usage_metadata={
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens
                })
        except Exception as e:
            logger.warning(f"Failed to set usage_metadata on LangSmith run: {e}")


# Singleton instances.
#   llm_service        → analysis / grading pipeline (main profile, e.g. Azure)
#   chat_llm_service   → streaming chat assistant (chat profile, e.g. Groq); falls
#                        back to the main profile when no CHAT_LLM_* vars are set.
#   critic_llm_service → independent generator/critic model (critic profile, e.g.
#                        gpt-5.4-nano); falls back to main when CRITIC_LLM_* unset.
llm_service = LLMService("main")
chat_llm_service = LLMService("chat")
critic_llm_service = LLMService("critic")
