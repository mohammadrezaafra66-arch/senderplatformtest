export function TelegramAccountSetup() {
  return (
    <div>
      <button
        onClick={() => {
          void fetch("/backend/telegram-mtproto/session/start");
        }}
      >
        Start session
      </button>
    </div>
  );
}
