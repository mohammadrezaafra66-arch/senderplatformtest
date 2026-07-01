"use client";

import { useEffect, useState } from "react";

type Lead = {
  phone_number: string;
  username: string | null;
  source: string;
  first_seen_at: string;
};

export function TelegramLeadsPanel() {
  const [leads, setLeads] = useState<Lead[]>([]);

  useEffect(() => {
    fetch("/api/telegram-mtproto/leads")
      .then((r) => r.json())
      .then(setLeads);
  }, []);

  return (
    <div className="space-y-2">
      <h3 className="font-medium">áíÏåÇí ÌÏíÏ ÊáÑÇã</h3>
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b">
            <th className="text-right p-2">ÔãÇÑå</th>
            <th className="text-right p-2">íæÒÑäíã</th>
            <th className="text-right p-2">ãäÈÚ</th>
            <th className="text-right p-2">ÊÇÑíÎ</th>
          </tr>
        </thead>
        <tbody>
          {leads.map((lead, i) => (
            <tr key={i} className="border-b">
              <td className="p-2">{lead.phone_number}</td>
              <td className="p-2">{lead.username ?? "—"}</td>
              <td className="p-2">{lead.source === "replied" ? "ÇÓÎ ÏÇÏ" : "ÔäÇÓÇíí ÔÏ"}</td>
              <td className="p-2">{new Date(lead.first_seen_at).toLocaleDateString("fa-IR")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
