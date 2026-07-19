import { TelegramAccountPool } from "../components/TelegramAccountPool";
import { TelegramAccountSetup } from "../components/TelegramAccountSetup";
import { TelegramLeadsPanel } from "../components/TelegramLeadsPanel";

export default function TelegramPage() {
  return (
    <div className="p-6 space-y-8">
      <h1 className="text-2xl font-bold">تلگرام MTProto</h1>
      <TelegramAccountSetup />
      <TelegramAccountPool />
      <TelegramLeadsPanel />
    </div>
  );
}
