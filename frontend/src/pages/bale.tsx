import { BaleAccountPanel } from "@/components/BaleAccountPanel";
import { BaleWebhookStatus } from "@/components/BaleWebhookStatus";

export default function BalePage() {
  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-semibold">مدیریت بله</h1>
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 text-sm text-amber-800">
        <strong>نکته:</strong> ربات بله فقط به کسانی پیام می‌دهد که به ربات /start زده‌اند.
        برای ارسال به شماره موبایل مستقیم، از User Account (سطح ۲) استفاده کنید.
      </div>
      <BaleAccountPanel />
      <BaleWebhookStatus />
    </div>
  );
}
