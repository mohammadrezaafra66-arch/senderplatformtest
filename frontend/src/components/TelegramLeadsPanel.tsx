export function TelegramLeadsPanel() {
  return (
    <div>
      <button
        onClick={() => {
          void fetch("/backend/telegram-mtproto/leads");
        }}
      >
        Load leads
      </button>
    </div>
  );
}
