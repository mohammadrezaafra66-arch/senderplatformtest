export function TelegramAccountPool() {
  return (
    <div>
      <button
        onClick={() => {
          void fetch("/backend/telegram-mtproto/accounts/pool");
        }}
      >
        Load pool
      </button>
    </div>
  );
}
