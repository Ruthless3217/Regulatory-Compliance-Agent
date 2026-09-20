"""Suite-wide guards.

Langfuse: ``backend/.env`` carries real LANGFUSE_* keys for local runs, and the
SDK is initialised when ``app.services.llm_service`` is imported. Force it off
here (process env beats dotenv in pydantic-settings, and the SDK honours the
same variable) so unit tests never ship spans to Langfuse Cloud. Tests that
exercise the tracing shim itself construct their own disabled client.
"""
import os

os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
