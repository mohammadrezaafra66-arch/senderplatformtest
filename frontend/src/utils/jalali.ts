import moment from "moment-jalaali";

let isInitialized = false;

function ensureInitialized() {
  if (isInitialized) return;
  (moment as any).loadPersian?.({
    usePersianDigits: false,
    dialect: "persian-modern",
  });
  isInitialized = true;
}

export function toJalaliDateTime(value?: string | number | Date): string {
  ensureInitialized();
  const m = value == null ? moment() : moment(value);
  return m.format("jYYYY/jMM/jDD HH:mm");
}

