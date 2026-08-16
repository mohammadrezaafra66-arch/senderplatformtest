# Operations Center

## Route
`/rubika` → tab `مرکز حفاظت` (`RubikaProtectionCenter`)

## Sections
1. Critical alert / circuit OPEN banner
2. Summary cards (counts from overview API)
3. System Protection circuit panel
4. In-app alerts list
5. Campaign impact
6. Account protection table
7. Account detail panel
8. Incident center + detail
9. Protection event timeline

## Refresh
- Auto: 30s interval, cleared on unmount
- Manual refresh button
- Lint-safe: `useCallback` loader + `useEffect` schedule only
