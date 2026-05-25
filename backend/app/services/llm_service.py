import json
import asyncio
import time
import os
from typing import Dict, Any, Optional, Type, TypeVar, AsyncIterator, List
from sqlalchemy.orm import Session
import logging
from datetime import datetime
from pydantic import BaseModel, ValidationError
from ..config import settings
from openai import AsyncOpenAI
import httpx

# LangSmith tracing — no-op decorator if the SDK isn't installed.
try:
    from langsmith import traceable
except Exception:  # pragma: no cover
    def traceable(*_a, **_kw):  # type: ignore
        def _decorate(fn):
            return fn
        return _decorate if not (_a and callable(_a[0])) else _a[0]

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


class LLMService:
    """Service for integrating with Cloud LLMs (Gemini/OpenAI) via OpenAI-compatible API."""

    def __init__(self):
        self.api_key = settings.llm_api_key
        self.base_url = settings.llm_base_url
        self.model = settings.llm_model

        # Logging config
        self.log_file = os.path.join("logs", "log.json")
        os.makedirs("logs", exist_ok=True)

        if not self.api_key:
            logger.warning("LLM_API_KEY is not set. LLM service will fail.")

        client_kwargs: Dict[str, Any] = {
            "api_key": self.api_key or "placeholder",
            "base_url": self.base_url,
        }
        if settings.llm_insecure_tls:
            logger.warning("LLM_INSECURE_TLS=true — disabling TLS verification for LLM calls")
            client_kwargs["http_client"] = httpx.AsyncClient(verify=False)
        self.client = AsyncOpenAI(**client_kwargs)

    async def health_check(self) -> bool:
        """Check if LLM service is available."""
        try:
            await self.client.models.list()
            logger.info(f"✅ LLM service available with model '{self.model}'")
            return True
        except Exception as e:
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
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=kwargs.get("temperature", 0.7),
            )
            response_text = response.choices[0].message.content.strip()
            await self._log_to_json(prompt, response_text, system_prompt, context)
            return response_text
        except Exception as e:
            logger.error(f"LLM generation failed: {str(e)}")
            return self._get_fallback_response(prompt, context)

    @traceable(run_type="llm", name="LLM.stream_response")
    async def stream_response(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream response tokens from LLM. Yields text deltas."""
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                stream=True,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
        except Exception as e:
            logger.error(f"LLM streaming failed: {e}")
            yield f"\n\n[Error: streaming failed — {str(e)}]"

    @traceable(run_type="llm", name="LLM.generate_structured_response")
    async def generate_structured_response(
        self,
        prompt: str,
        output_model: Type[T],
        system_prompt: str = None,
        context: Dict[str, Any] = None,
        execution_id: str = None,
        db: Session = None,
        tool_name: str = "llm_structured"
    ) -> T:
        """Generate a structured response validated against a Pydantic model."""
        schema_instruction = (
            f"\nYou must output JSON that adheres to this schema:\n"
            f"{output_model.model_json_schema()}\n"
            f"Return ONLY the JSON object, no other text."
        )
        full_system_prompt = (system_prompt or "") + schema_instruction

        messages = self._build_chat_messages(prompt, full_system_prompt, context)
        start_time = time.time()
        max_retries = 3
        current_messages = messages
        response_text = ""

        for attempt in range(max_retries):
            try:
                response_wrapper = await self.client.chat.completions.with_raw_response.create(
                    model=self.model,
                    messages=current_messages,
                    temperature=0.2,
                    response_format={"type": "json_object"}
                )

                response = response_wrapper.parse()
                response_text = response.choices[0].message.content.strip()

                # Extract token usage
                token_usage = 0
                try:
                    raw_data = json.loads(response_wrapper.http_response.text)
                    if "usageMetadata" in raw_data:
                        token_usage = raw_data["usageMetadata"].get("totalTokenCount", 0)
                    elif hasattr(response, 'usage') and response.usage:
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

            except (ValidationError, json.JSONDecodeError, Exception) as e:
                logger.warning(f"Structured generation attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    # Return empty/fallback result
                    logger.error("All retries exhausted, returning fallback")
                    return self._get_fallback_structured_response(output_model)

                error_feedback = f"\n\nPrevious response was invalid. Error: {str(e)}. Please CORRECT the JSON output."
                current_messages.append({"role": "assistant", "content": response_text})
                current_messages.append({"role": "user", "content": error_feedback})
                await asyncio.sleep(1)

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

    def _get_fallback_structured_response(self, output_model: Type[T]) -> T:
        """Return a minimal valid structured response."""
        try:
            # Try to construct with empty violations
            return output_model(
                violations=[],
                overall_assessment="Analysis failed - LLM service unavailable",
                key_issues=["LLM service is unavailable"]
            )
        except Exception:
            return output_model.model_construct()

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
