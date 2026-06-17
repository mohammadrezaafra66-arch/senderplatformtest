"""Endpointهای توسعه‌ای قیمت برای تست Price Fetcher و Redis Cache."""

from fastapi import APIRouter

router = APIRouter(prefix="/debug/mock-pricing", tags=["dev-pricing"])

MOCK_PRICING_PAYLOAD = {
    "source": "mock_pricing",
    "board": "amin-hozoor",
    "currency": "IRR",
    "items": [
        {
            "sku": "LG-65UA85006",
            "brand": "LG",
            "category": "TV",
            "model": "65UA85006",
            "title": "تلویزیون ال جی 65 اینچ مدل 65UA85006",
            "cash_price": 650000000,
            "availability": "available",
            "updated_at": "2026-01-01T00:00:00Z",
        },
        {
            "sku": "BOSCH-46NX01",
            "brand": "Bosch",
            "category": "Dishwasher",
            "model": "46NX01",
            "title": "ماشین ظرفشویی بوش مدل 46NX01",
            "cash_price": 580000000,
            "availability": "available",
            "updated_at": "2026-01-01T00:00:00Z",
        },
        {
            "sku": "SNOWA-SIDE-01",
            "brand": "Snowa",
            "category": "Refrigerator",
            "model": "Side-By-Side",
            "title": "یخچال ساید اسنوا",
            "cash_price": 720000000,
            "availability": "limited",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    ],
}


@router.get("/amin-hozoor-board")
def mock_pricing_amin_hozoor_board():
    return MOCK_PRICING_PAYLOAD
