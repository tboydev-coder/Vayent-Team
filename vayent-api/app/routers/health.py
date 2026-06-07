"""Health check and status API routes."""
from fastapi import APIRouter

from app.config import get_settings
from app.database import check_db_health
from app.rag.rag_service import rag_service
from app.services.openai_config_service import get_openai_reachability_error

router = APIRouter(tags=["Health"])
settings = get_settings()


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    database_ok = await check_db_health()
    rag_ok = True
    openai_configured = settings.openai_configured
    openai_reachable = openai_configured
    openai_reachability_error = None
    try:
        rag_service.get_collection_stats()
    except Exception:
        rag_ok = False

    if openai_configured:
        openai_reachability_error = get_openai_reachability_error(settings)
        openai_reachable = openai_reachability_error is None

    status_value = "healthy" if database_ok and rag_ok and openai_reachable else "degraded"
    return {
        "status": status_value,
        "service": "Vayent API",
        "version": "1.0.0",
        "checks": {
            "database": database_ok,
            "rag": rag_ok,
            "openai_configured": openai_configured,
            "openai_reachable": openai_reachable,
            "openai_reachability_error": openai_reachability_error,
        },
        "features": {
            "metric_monitoring_enabled": settings.metric_monitoring_enabled,
        },
    }


@router.get("/status")
async def get_status():
    """Get system status including RAG collection info."""
    rag_stats = rag_service.get_collection_stats()

    return {
        "status": "running",
        "service": "Vayent API",
        "rag": rag_stats,
        "features": {
            "metric_monitoring_enabled": settings.metric_monitoring_enabled,
        },
    }
