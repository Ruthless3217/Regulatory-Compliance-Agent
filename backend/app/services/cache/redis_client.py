"""
Redis client module for LangGraph persistence.
"""
import logging
from typing import Optional
from redis.asyncio import Redis as AsyncRedis
from app.config import settings

logger = logging.getLogger(__name__)

_redis_client: Optional[AsyncRedis] = None


async def init_redis():
 """Initialize the Redis connection."""
 global _redis_client
 try:
 _redis_client = AsyncRedis.from_url(
 settings.redis_url,
 decode_responses=False
 )
 # Test connection
 await _redis_client.ping()
 logger.info(f"✅ Redis connected at {settings.redis_url}")
 except Exception as e:
 logger.warning(f"⚠️ Redis connection failed: {e}. Will use MemorySaver fallback.")
 _redis_client = None


async def get_redis() -> Optional[AsyncRedis]:
 """Get the Redis client."""
 return _redis_client


async def close_redis():
 """Close the Redis connection."""
 global _redis_client
 if _redis_client:
 await _redis_client.close()
 _redis_client = None
 logger.info("Redis connection closed")
