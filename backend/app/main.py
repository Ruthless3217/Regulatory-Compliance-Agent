from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
from .config import settings
from .api.routes import submissions, compliance, dashboard, rules, chat, similar, rag_health

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
            AgentTrace, ToolInvocation, ComplianceState
        )
        logger.info("✅ Database models registered (schema managed by Alembic)")
    except Exception as e:
        logger.error(f"❌ Model registration failed: {e}")

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

# Include routers - Compliance Agent only
app.include_router(submissions.router)
app.include_router(compliance.router)
app.include_router(dashboard.router)
app.include_router(rules.router)
app.include_router(chat.router)
app.include_router(similar.router)
app.include_router(rag_health.router)


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
