// قرارداد API: core_engine/api/telegram_mtproto.py

/** GET /telegram-mtproto/accounts/pool */
export type TelegramPoolAccount = {
  account_id: number;
  is_warmed_up: boolean;
  daily_cap_today: number;
  sent_today: number;
  is_healthy: boolean;
  last_error_message: string | null;
};

/** GET | PUT /telegram-mtproto/schedule */
export type TelegramSchedule = {
  start_hour: number;
  end_hour: number;
};

/** GET /telegram-mtproto/leads?limit=100 */
export type TelegramLead = {
  phone_number: string;
  username: string | null;
  source: string | null;
  first_seen_at: string; // ISO 8601
};

/** POST /telegram-mtproto/session/start */
export type StartLoginRequest = {
  account_id: number;
  phone_number: string;
};

/** POST /telegram-mtproto/session/verify */
export type VerifyCodeRequest = {
  account_id: number;
  phone_number: string;
  code: string;
  two_step_password?: string | null;
};

// TODO: شکل پاسخ session/start و session/verify تأییدنشده است.
// خروجی start_phone_login/verify_phone_code در telegram_session_setup.py
// قبل از نوشتن TelegramAccountSetup باید دیده شود.
export type SessionResponse = Record<string, unknown>;
