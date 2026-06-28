from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/webhooks/whatsapp", tags=["evolution-webhook"])


@router.post("/evolution")
async def evolution_webhook_receiver(request: Request):
    """دریافت رویدادهای webhook از Evolution API."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return JSONResponse(content={"received": True})
