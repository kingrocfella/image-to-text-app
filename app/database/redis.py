"""Redis connection and configuration for Dramatiq queue broker."""

from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import CurrentMessage
from dramatiq.results import Results
from dramatiq.results.backends.redis import RedisBackend

from app.config import get_settings
from app.utils.logger import logger

REDIS_URL = get_settings().redis_url

# Singleton instances
_redis_broker: RedisBroker | None = None
_result_backend: RedisBackend | None = None


def get_redis_url() -> str:
    """Get the Redis URL."""
    return REDIS_URL


def get_result_backend() -> RedisBackend:
    """Get or create the Redis result backend for Dramatiq."""
    global _result_backend
    if _result_backend is None:
        logger.debug("Initializing Redis result backend")
        _result_backend = RedisBackend(url=REDIS_URL)
    return _result_backend


def get_redis_broker() -> RedisBroker:
    """Get or create the Redis broker for Dramatiq with Results middleware."""
    global _redis_broker
    if _redis_broker is None:
        logger.debug("Initializing Redis broker")
        _redis_broker = RedisBroker(url=REDIS_URL)

        # Add Results middleware for storing job results
        result_backend = get_result_backend()
        _redis_broker.add_middleware(Results(backend=result_backend))
        # Lets an actor learn its own message ID, to record the job's outcome.
        _redis_broker.add_middleware(CurrentMessage())

        logger.info("Redis broker initialized with Results middleware")
    return _redis_broker
