# Frontend Wiring

## Files
- `frontend/src/pages/rubika.tsx` — protection tab
- `frontend/src/components/RubikaProtectionCenter.tsx`
- `frontend/src/lib/rubika-api.ts` — overview/detail/restore/alerts/incidents
- `frontend/src/types/rubika.ts` — Phase 5 types
- `frontend/locales/fa/common.json` (+ root `locales/fa/common.json`)

## Data flow
Single `fetchRubikaProtectionOverview()` → local state. Detail fetch on account click. Restore uses `POST /rubika/accounts/{id}/restore` (not pool restore).

## Unknown fields
Display `نامشخص` via `display()` helper — no invented values.
