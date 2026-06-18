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
from openai import AsyncOpenAI, AsyncAzureOpenAI
try:  # openai>=1.0 ships RateLimitError; guard so import never hard-fails
    from openai import RateLimitError
except Exception:  # pragma: no cover
    RateLimitError = None  # type: ignore
import httpx

# LangSmith tracing — no-op decorator if the SDK isn't installed.
try:
    from langsmith import traceable
except Exception:  # pragma: no cover
    def traceable(*_a, **_kw):  # type: ignore
        def _decorate(fn):
            return fn
        return _decorate if not (_a and callable(_a[0])) else _a[0]

# wrap_openai instruments the raw OpenAI/Azure client so every
# chat.completions.create call becomes a traced LLM run WITH token usage
# (prompt/completion/total) attached — which a plain @traceable returning a
# string cannot capture. No-op fallback when the SDK/extra isn't present.
try:
    from langsmith.wrappers import wrap_openai
except Exception:  # pragma: no cover
    def wrap_openai(client):  # type: ignore
        return client

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


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


class LLMService:
    """Service for integrating with Cloud LLMs (Gemini/OpenAI) via OpenAI-compatible API."""

    def __init__(self):
        self.base_url = settings.llm_base_url
        self.model = settings.llm_model

        # Logging config
        self.log_file = os.path.join("logs", "log.json")
        os.makedirs("logs", exist_ok=True)

        if settings.llm_insecure_tls:
            logger.warning("LLM_INSECURE_TLS=true — disabling TLS verification for LLM calls")

        # One client per configured key. Multiple keys = TPM failover: the
        # request drivers below rotate to the next key when one is 429'd or
        # over its daily budget. key_id (last 8 chars of the key) ties each
        # key to its own per-key rate limiter without logging the secret.
        self.api_keys = settings.llm_api_keys
        if not self.api_keys:
            logger.warning("LLM_API_KEY is not set. LLM service will fail.")
            self._client_pool = [("none", self._build_client("placeholder"))]
        else:
            self._client_pool = [
                (k[-8:], self._build_client(k)) for k in self.api_keys
            ]
        if len(self._client_pool) > 1:
            logger.info(f"LLM key failover enabled across {len(self._client_pool)} keys")

        # Back-compat: callers (e.g. health_check) that reference .client / .api_key
        # get the first key in the pool.
        self.api_key = self.api_keys[0] if self.api_keys else ""
        self.client = self._client_pool[0][1]

    def _build_client(self, api_key: str):
        """Construct one client bound to ``api_key``.

        Azure OpenAI is NOT OpenAI-compatible at the resource root: it needs the
        deployment-scoped path, an ``?api-version=`` query, and an ``api-key``
        header — all handled by AsyncAzureOpenAI. Everything else (Gemini's
        OpenAI-compatible endpoint, Groq, local vLLM) uses AsyncOpenAI + base_url.
        """
        client_kwargs: Dict[str, Any] = {"api_key": api_key or "placeholder"}
        if settings.llm_insecure_tls:
            # Behind Cisco SSL inspection the verified handshake fails (httpx
            # SSLError); bypass verify so the external HTTPS call works.
            client_kwargs["http_client"] = httpx.AsyncClient(verify=False)

        if settings.llm_is_azure:
            client = AsyncAzureOpenAI(
                azure_endpoint=self.base_url,
                api_version=settings.llm_azure_api_version,
                **client_kwargs,
            )
        else:
            client_kwargs["base_url"] = self.base_url
            client = AsyncOpenAI(**client_kwargs)

        # When LangSmith tracing is on, wrap the client so token usage shows up
        # on every LLM run in the trace UI (cost tracking). wrap_openai is a
        # transparent proxy — all SDK calls keep working unchanged.
        if (settings.langchain_tracing_v2 or "").lower() == "true":
            return wrap_openai(client)
        return client

    def _chat_kwargs(self, *, temperature: float, max_tokens: int) -> Dict[str, Any]:
        """Per-call chat-completion params shaped for the active provider.

        Reasoning models (gpt-5.x on Azure) require ``max_completion_tokens``,
        reject a custom ``temperature`` (400 on anything but the default), and
        accept ``reasoning_effort``. Gemini/Groq keep the classic
        ``max_tokens`` + ``temperature``. Controlled by the LLM_* flags so the
        same code path serves every provider.
        """
        kwargs: Dict[str, Any] = {}
        if settings.llm_use_max_completion_tokens:
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
        if settings.llm_supports_temperature:
            kwargs["temperature"] = temperature
        if settings.llm_reasoning_effort:
            kwargs["reasoning_effort"] = settings.llm_reasoning_effort
        return kwargs

    async def health_check(self) -> bool:
        """Check if LLM service is available.

        Azure OpenAI does not expose ``/models`` on the resource root, so
        ``models.list()`` 404s there even when chat calls succeed. Treat that as
        available rather than failing the probe (and warning about a non-issue).
        """
        try:
            await self.client.models.list()
            logger.info(f"✅ LLM service available with model '{self.model}'")
            return True
        except Exception as e:
            if settings.llm_is_azure:
                logger.info(
                    f"Azure endpoint has no models.list; assuming deployment "
                    f"'{self.model}' is available (chat calls verified at runtime)"
                )
                return True
            logger.warning(f"LLM health check failed: {str(e)}")
            return False

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
        last_exc: Optional[Exception] = None
        for key_id, client in self._client_pool:
            try:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    **self._chat_kwargs(
                        temperature=kwargs.get("temperature", 0.7),
                        max_tokens=kwargs.get("max_tokens", settings.llm_max_tokens),
                    ),
                )
                response_text = response.choices[0].message.content.strip()
                await self._log_to_json(prompt, response_text, system_prompt, context)
                return response_text
            except Exception as e:
                if _is_rate_limit_error(e) and len(self._client_pool) > 1:
                    logger.warning(f"[failover] key …{key_id} rate-limited; trying next key")
                    last_exc = e
                    continue
                logger.error(f"LLM generation failed: {str(e)}")
                return self._get_fallback_response(prompt, context)
        logger.error(f"LLM generation failed — all keys rate-limited: {last_exc}")
        return self._get_fallback_response(prompt, context)

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

        # Failover can only happen BEFORE the first token: once we have yielded
        # text we can't restart on another key without duplicating output.
        last_exc: Optional[Exception] = None
        for key_id, client in self._client_pool:
            try:
                stream = await client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    stream=True,
                    # Ask the provider to emit a final usage chunk so we get the
                    # REAL token count (raw-SDK equivalent of stream_usage=True).
                    stream_options={"include_usage": True},
                    **self._chat_kwargs(
                        temperature=temperature,
                        max_tokens=settings.llm_max_tokens,
                    ),
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
                async for chunk in stream:
                    # The final chunk carries usage and has empty choices.
                    usage = getattr(chunk, "usage", None)
                    if usage and on_usage:
                        try:
                            on_usage(int(getattr(usage, "total_tokens", 0) or 0))
                        except Exception:
                            pass
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield delta.content
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

        last_exc: Optional[Exception] = None
        pool = self._client_pool
        for key_id, client in pool:
            # Fresh message copy per key — a failed key's JSON-correction turns
            # must not leak into the next key's conversation.
            current_messages = list(base_messages)
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
                    **self._chat_kwargs(
                        temperature=temperature,
                        max_tokens=settings.llm_max_tokens,
                    ),
                )

                response_text = response.choices[0].message.content.strip()

                # Token usage for the DB tool-invocation record (LangSmith gets
                # it from wrap_openai).
                token_usage = 0
                try:
                    if getattr(response, "usage", None):
                        token_usage = response.usage.total_tokens
                except Exception:
                    token_usage = 0

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

                if execution_id and db:
                    await self._record_tool_invocation(
                        db=db,
                        execution_id=execution_id,
                        tool_name=tool_name,
                        input_data={"prompt": prompt[:500]},
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

    @property
    def classify_model(self) -> str:
        """Deployment for cheap/fast first-pass calls (e.g. gpt-5.4-nano).

        Returns LLM_CLASSIFY_MODEL when set, else falls back to LLM_MODEL so a
        single-model deployment keeps working unchanged.
        """
        return settings.llm_classify_model or self.model

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
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "model": self.model,
                "system_prompt": system_prompt,
                "context": context,
                "prompt": prompt[:500],
                "response": response[:500]
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


# Singleton instance
llm_service = LLMService()
