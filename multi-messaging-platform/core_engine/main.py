"""نقطه ورود FastAPI."""

from typing import Annotated

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from core_engine.api.auth import require_roles, router as auth_router
from core_engine.api.accounts import router as accounts_router
from core_engine.api.audit import router as audit_router
from core_engine.api.bale_webhook import router as bale_webhook_router
from core_engine.api.evolution_webhook import router as evolution_webhook_router
from core_engine.api.evolution_whatsapp import router as evolution_whatsapp_router
from core_engine.api.campaigns import router as campaigns_router
from core_engine.api.controls import router as controls_router
from core_engine.api.dashboard import router as dashboard_router
from core_engine.api.dashboard_ws import router as dashboard_ws_router
from core_engine.api.debug_campaigns import router as debug_campaigns_router
from core_engine.api.debug_contacts import router as debug_contacts_router
from core_engine.api.debug_prepare import router as debug_prepare_router
from core_engine.api.debug_staging import router as debug_staging_router
from core_engine.api.dev_pricing import router as dev_pricing_router
from core_engine.api.imports import router as imports_router
from core_engine.api.metrics import router as metrics_router
from core_engine.api.send_settings import router as send_settings_router
from core_engine.api.whatsapp import router as whatsapp_router
from core_engine.middleware.metrics_middleware import MetricsMiddleware
from core_engine.api.schemas import (
    EncodingEchoRequest,
    GenerateMessageRequest,
    MessageRenderDryRunRequest,
    SaveRenderedMessageDryRunRequest,
)
from core_engine.api.utf8_json import (
    encoding_debug_fields,
    encoding_ping_payload,
    utf8_json_response,
)
from core_engine.config import get_settings
from core_engine.database import get_db
from core_engine.services.gpt_orchestrator import (
    generate_personalized_message,
    preview_personalized_message,
)
from core_engine.services.knowledge_base import build_kb_context, read_knowledge_base
from core_engine.services.message_queue_payload import (
    list_latest_rendered_messages,
    save_dry_run_rendered_message,
)
from core_engine.services.message_render import dry_run_message_render
from core_engine.services.openai_client import is_openai_api_key_configured
from core_engine.services.price_fetcher import (
    get_cached_pricing,
    probe_pricing_api,
    probe_pricing_candidates,
    refresh_pricing_cache,
)
from core_engine.services.pricing_playwright_scraper import (
    get_playwright_profile_status,
    preview_playwright_scraper_result,
)
from core_engine.services.pricing_scraper import preview_scraper_result, scrape_pricing_page
from core_engine.services.product_snapshot import (
    create_product_snapshot_from_cached_pricing,
    get_latest_product_snapshot,
    get_latest_valid_product_snapshot,
)
from core_engine.services.safety_guard import get_safety_status
from core_engine.services.phase4_utils import normalize_phone
from core_engine.tasks import add_numbers

app = FastAPI(
    title="Multi Messaging Platform",
    description="پلتفرم پیام‌رسان چندکاناله",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3010",
        "http://127.0.0.1:3010",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(MetricsMiddleware)

app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(audit_router)
app.include_router(bale_webhook_router)
app.include_router(campaigns_router)
app.include_router(controls_router)
app.include_router(dashboard_router)
app.include_router(dashboard_ws_router)
app.include_router(debug_campaigns_router)
app.include_router(debug_contacts_router)
app.include_router(debug_prepare_router)
app.include_router(debug_staging_router)
app.include_router(dev_pricing_router)
app.include_router(imports_router)
app.include_router(metrics_router)
app.include_router(send_settings_router)
app.include_router(whatsapp_router)
app.include_router(evolution_whatsapp_router)
app.include_router(evolution_webhook_router)


@app.on_event("startup")
def validate_security_settings() -> None:
    """Fail fast when session encryption is not configured."""
    get_settings()


@app.get("/health")
def health():
    return {"status": "ok"}


def _is_configured_value(value: str, placeholders: frozenset[str] | None = None) -> bool:
    blocked = placeholders or frozenset()
    stripped = value.strip()
    return bool(stripped and stripped not in blocked)


@app.get("/debug/config")
def debug_config():
    settings = get_settings()
    return {
        "openai_model": settings.OPENAI_MODEL,
        "openai_api_key_configured": is_openai_api_key_configured(settings),
        "pricing_api_url": settings.PRICING_API_URL,
        "pricing_cache_ttl_seconds": settings.PRICING_CACHE_TTL_SECONDS,
        "redis_url_configured": _is_configured_value(settings.REDIS_URL),
        "database_url_configured": _is_configured_value(settings.DATABASE_URL),
    }


@app.get("/debug/pricing-cache")
async def debug_pricing_cache():
    return utf8_json_response(await get_cached_pricing())


@app.post("/debug/pricing-cache/refresh")
async def debug_pricing_cache_refresh():
    return utf8_json_response(await refresh_pricing_cache())


@app.get("/debug/pricing-api/probe")
async def debug_pricing_api_probe():
    return utf8_json_response(await probe_pricing_api())


@app.get("/debug/pricing-api/candidates")
async def debug_pricing_api_candidates():
    return utf8_json_response(await probe_pricing_candidates())


@app.get("/debug/pricing-scraper/preview")
async def debug_pricing_scraper_preview():
    result = await scrape_pricing_page()
    return utf8_json_response(preview_scraper_result(result))


@app.get("/debug/pricing-playwright-scraper/preview")
async def debug_pricing_playwright_scraper_preview(max_pages: int | None = None):
    from core_engine.services.pricing_playwright_scraper import (
        scrape_all_pricing_pages_with_playwright,
    )

    result = await scrape_all_pricing_pages_with_playwright(max_pages=max_pages)
    return utf8_json_response(preview_playwright_scraper_result(result))


@app.get("/debug/pricing-playwright-profile/status")
def debug_pricing_playwright_profile_status():
    return utf8_json_response(get_playwright_profile_status())


@app.post("/debug/pricing-snapshots/from-cache")
async def debug_pricing_snapshots_from_cache(
    db: Annotated[Session, Depends(get_db)],
):
    return utf8_json_response(await create_product_snapshot_from_cached_pricing(db))


@app.get("/debug/pricing-snapshots/latest")
def debug_pricing_snapshots_latest(
    db: Annotated[Session, Depends(get_db)],
):
    return utf8_json_response(get_latest_product_snapshot(db))


@app.get("/debug/pricing-snapshots/latest-valid")
def debug_pricing_snapshots_latest_valid(
    db: Annotated[Session, Depends(get_db)],
):
    return utf8_json_response(get_latest_valid_product_snapshot(db))


@app.get("/debug/encoding/ping")
def debug_encoding_ping():
    return utf8_json_response(encoding_ping_payload())


@app.get("/debug/safety/status")
def debug_safety_status():
    return utf8_json_response(get_safety_status())


@app.get("/debug/phase4/utils/phone-normalize")
def debug_phase4_phone_normalize(
    phone: str = Query(..., min_length=1),
):
    return utf8_json_response(
        {
            "input": phone,
            "normalized": normalize_phone(phone),
        }
    )


@app.post("/debug/encoding/echo")
def debug_encoding_echo(request: EncodingEchoRequest):
    payload = request.model_dump()
    return utf8_json_response(
        {
            "success": True,
            "echo": payload,
            "encoding": "utf-8",
            "encoding_debug": {
                "customer_name": encoding_debug_fields(request.customer_name),
                "message_goal": encoding_debug_fields(request.message_goal),
            },
        }
    )


@app.get("/debug/knowledge-base")
def debug_knowledge_base():
    return utf8_json_response(read_knowledge_base())


@app.get("/debug/knowledge-base/context")
def debug_knowledge_base_context(
    max_chars: int = Query(default=4000, ge=1),
):
    return utf8_json_response(build_kb_context(max_chars=max_chars))


@app.post("/debug/gpt/preview")
def debug_gpt_preview(
    request: GenerateMessageRequest,
    db: Annotated[Session, Depends(get_db)],
):
    return utf8_json_response(preview_personalized_message(db, request))


@app.post("/debug/gpt/generate")
def debug_gpt_generate(
    request: GenerateMessageRequest,
    db: Annotated[Session, Depends(get_db)],
):
    return utf8_json_response(generate_personalized_message(db, request))


@app.post("/debug/message-render/dry-run")
def debug_message_render_dry_run(
    request: MessageRenderDryRunRequest,
    db: Annotated[Session, Depends(get_db)],
):
    return utf8_json_response(dry_run_message_render(db, request))


@app.post("/debug/message-render/save-dry-run")
def debug_message_render_save_dry_run(
    request: SaveRenderedMessageDryRunRequest,
    db: Annotated[Session, Depends(get_db)],
):
    return utf8_json_response(save_dry_run_rendered_message(db, request))


@app.get("/debug/rendered-messages/latest")
def debug_rendered_messages_latest(
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=10, ge=1, le=100),
):
    return utf8_json_response(
        {
            "success": True,
            "count": limit,
            "items": list_latest_rendered_messages(db, limit=limit),
        }
    )


@app.get("/admin/ping")
async def admin_ping(
    current_user: Annotated[dict[str, str], Depends(require_roles("admin"))],
):
    return {"message": "admin pong", "role": current_user["role"]}


@app.get("/operator/ping")
async def operator_ping(
    current_user: Annotated[dict[str, str], Depends(require_roles("admin", "operator"))],
):
    return {"message": "operator pong", "role": current_user["role"]}


@app.get("/debug/celery/add")
async def debug_celery_add(
    a: int,
    b: int,
    current_user: Annotated[dict[str, str], Depends(require_roles("admin"))],
):
    task = add_numbers.delay(a, b)
    result = task.get(timeout=10)
    return {"a": a, "b": b, "result": result, "task_id": task.id}
