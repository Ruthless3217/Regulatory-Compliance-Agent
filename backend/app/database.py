from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from .config import settings

# Create engine. pool_size=10 sustains the per-chunk precedent analysis fan-out
# (asyncio.Semaphore(8) in analysis_node + a couple of foreground requests)
# without forcing connections to queue. max_overflow gives a small burst margin
# during periodic Cohere/Groq retries that block sessions for a few seconds.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=5,
    pool_recycle=1800,
    echo=settings.environment == "development",
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()


# Dependency for API routes
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
