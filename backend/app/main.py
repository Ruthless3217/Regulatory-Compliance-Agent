from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
from .config import settings
from .api.routes import submissions, compliance, dashboard, model_learning, rules, similar, rag_health, knowledge_base, comparisons

# Configure logging
logging.basicConfig(
    level=settings.log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("🚀 Starting Regulatory Compliance Agent Backend")

    # Database schema is owned by Alembic migrations.
    # Run `alembic upgrade head` before/at container start.
    # Models are imported here so SQLAlchemy registers them on Base.metadata
    # (used by Alembic env.py).
    try:
        from .models import (  # noqa: F401
            User, Submission, Rule, ComplianceCheck,
            Violation, ContentChunk, AgentExecution,
            AgentTrace, ToolInvocation,
            DocumentComparison, UserSession, AnalysisRun,
            LlmUsageEvent, AuditEvent,
        )
        logger.info("✅ Database models registered (schema managed by Alembic)")
    except Exception as e:
        logger.error(f"❌ Model registration failed: {e}")

    # Seed the super-admin from env (idempotent) so a fresh VM has a way in.
    # Safe to run every startup; requires schema at head (0019 auth columns).
    try:
        if settings.super_admin_username and settings.super_admin_password:
            from .database import SessionLocal
            from .models.user import User
            from .auth.passwords import hash_password
            db = SessionLocal()
            try:
                exists = (
                    db.query(User)
                    .filter(User.username == settings.super_admin_username)
                    .first()
                )
                if not exists:
                    db.add(User(
                        username=settings.super_admin_username,
                        password_hash=hash_password(settings.super_admin_password),
                        role="super_admin",
                        registered_ip=settings.super_admin_ip or "127.0.0.1",
                        is_active=True,
                        must_change_password=True,
                        display_name="Super Admin",
                    ))
                    db.commit()
                    logger.info("✅ Seeded super-admin '%s'", settings.super_admin_username)
                else:
                    logger.info("ℹ️ Super-admin '%s' already present", settings.super_admin_username)
            finally:
                db.close()
    except Exception as e:
        logger.warning(f"⚠️ Super-admin seed skipped: {e}")

    # Initialize Redis (optional - falls back to MemorySaver)
    try:
        from .services.cache.redis_client import init_redis
        await init_redis()
    except Exception as e:
        logger.warning(f"⚠️ Redis init failed (will use memory checkpointer): {e}")

    # Check LLM connection
    try:
        from .services.llm_service import llm_service
        llm_healthy = await llm_service.health_check()
        if llm_healthy:
            logger.info("✅ LLM service is available")
        else:
            logger.warning("⚠️ LLM service is not available - check your API key")
    except Exception as e:
        logger.warning(f"⚠️ LLM health check failed: {e}")

    # Initialize Graph (warmup)
    try:
        from .services.agents.orchestrator import orchestrator
        _ = orchestrator.graph  # Trigger graph build
        logger.info("✅ Compliance Graph initialized")
    except Exception as e:
        logger.warning(f"⚠️ Failed to initialize Compliance Graph: {e}")

    yield

    # Shutdown
    logger.info("👋 Shutting down Regulatory Compliance Agent Backend")
    try:
        from .services.cache.redis_client import close_redis
        await close_redis()
    except Exception:
        pass


# Create FastAPI app
app = FastAPI(
    title="Regulatory Compliance Agent API",
    description="""
## Regulatory Compliance Agent

An AI-powered regulatory compliance checking system built with:
- **LangGraph** for multi-agent orchestration
- **Gemini 2.0 Flash** for intelligent analysis
- **FastAPI** for the REST API
- **PostgreSQL** for persistence

### Features
- 📄 Multi-format document analysis (PDF, DOCX, HTML, Markdown, Text)
- 🔍 Rule-based compliance checking with AI analysis
- 📊 Automated scoring and grading (A-F)
- 🔄 Human-in-the-Loop (HITL) review workflows
- 📈 Dashboard with violation analytics
- ⚙️ Dynamic rule generation from regulatory documents
    """,
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from .auth.middleware import AuthMiddleware
app.add_middleware(AuthMiddleware)

from .api.routes import submissions, compliance, dashboard, model_learning, rules, similar, rag_health, knowledge_base, comparisons, auth, admin_console

# Include routers - Compliance Agent only
app.include_router(auth.router)
app.include_router(admin_console.router)
app.include_router(submissions.router)
app.include_router(compliance.router)
app.include_router(dashboard.router)
app.include_router(model_learning.router)
app.include_router(rules.router)
app.include_router(similar.router)
app.include_router(rag_health.router)
app.include_router(knowledge_base.router)
app.include_router(comparisons.router)

from .api.routes import admin_corpus  # noqa: E402
app.include_router(admin_corpus.router)
from .api.routes import admin_retrieval  # noqa: E402
app.include_router(admin_retrieval.router)

# Review buckets (admin -> reviewer hand-off) and the per-document /
# per-reviewer action trail.
from .api.routes import assignments  # noqa: E402
app.include_router(assignments.router)


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint."""
    try:
        from .services.llm_service import llm_service
        llm_status = await llm_service.health_check()
    except Exception:
        llm_status = False

    return {
        "status": "healthy",
        "service": "Regulatory Compliance Agent",
        "version": "1.0.0",
        "llm_available": llm_status
    }


# Explicit allow-list ONLY. This is a hard security boundary — never return
# settings.dict()/settings.model_dump() here, never any *_api_key field, even
# indirectly. Adding a new Settings field must NOT change this endpoint's
# output unless it's deliberately added to the dict below.
@app.get("/health/models", tags=["Health"])
async def health_models():
    """Model-identity snapshot for the settings UI (no secrets)."""
    return {
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        # Same inherit-fallback as _resolve_profile() in llm_service.py: an
        # empty override means "use the main LLM_MODEL".
        "critic_llm_model": settings.critic_llm_model or settings.llm_model,
        "disclosure_check_enabled": settings.disclosure_check_enabled,
        "product_grounding_enabled": settings.product_grounding_enabled,
    }


@app.get("/", tags=["Root"])
def root():
    """Root endpoint."""
    return {
        "message": "Regulatory Compliance Agent API",
        "docs": "/docs",
        "redoc": "/redoc",
        "version": "1.0.0",
        "endpoints": {
            "submissions": "/submissions",
            "compliance": "/compliance",
            "rules": "/rules",
            "dashboard": "/dashboard/summary",
            "health": "/health"
        }
    }
