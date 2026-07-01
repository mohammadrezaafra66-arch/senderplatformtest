"use client";

import { useEffect, useState } from "react";

type PoolAccount = {
  account_id: number;
  is_warmed_up: boolean;
  daily_cap_today: number;
  sent_today: number;
  is_healthy: boolean;
  last_error_message: string | null;
};

export function TelegramAccountPool() {
  const [accounts, setAccounts] = useState<PoolAccount[]>([]);

  useEffect(() => {
    fetch("/api/telegram-mtproto/accounts/pool")
      .then((r) => r.json())
      .then(setAccounts);
  }, []);

  return (
    <div className="space-y-2">
      <h3 className="font-medium">Ê÷⁄Ì  «ò«‰ ùÂ«Ì MTProto</h3>
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b">
            <th className="text-right p-2">«ò«‰ </th>
            <th className="text-right p-2">Ê÷⁄Ì  ê—„ ‘œ‰</th>
            <th className="text-right p-2">”ﬁ› «„—Ê“</th>
            <th className="text-right p-2">«—”«· ‘œÂ «„—Ê“</th>
            <th className="text-right p-2">”·«„ </th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((acc) => (
            <tr key={acc.account_id} className="border-b">
              <td className="p-2">{acc.account_id}</td>
              <td className="p-2">{acc.is_warmed_up ? "ò«„·" : "œ— Õ«· ê—„ ‘œ‰"}</td>
              <td className="p-2">{acc.daily_cap_today}</td>
              <td className="p-2">{acc.sent_today}</td>
              <td className="p-2">
                {acc.is_healthy ? (
                  <span className="text-green-600">”«·„</span>
                ) : (
                  <span className="text-red-600" title={acc.last_error_message ?? ""}>
                    „‘ò· œ«—œ
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
