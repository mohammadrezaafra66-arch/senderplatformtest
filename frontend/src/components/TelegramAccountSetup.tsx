"use client";

import { useState } from "react";

export function TelegramAccountSetup({ accountId }: { accountId: number }) {
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [twoStepPassword, setTwoStepPassword] = useState("");
  const [step, setStep] = useState<"phone" | "code" | "2fa" | "done">("phone");
  const [message, setMessage] = useState("");

  async function handleStart() {
    const res = await fetch("/api/telegram-mtproto/session/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: accountId, phone_number: phone }),
    });
    const data = await res.json();
    setMessage(data.message);
    setStep("code");
  }

  async function handleVerify() {
    const res = await fetch("/api/telegram-mtproto/session/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        account_id: accountId,
        phone_number: phone,
        code,
        two_step_password: twoStepPassword || null,
      }),
    });
    const data = await res.json();
    if (data.status === "needs_2fa") {
      setStep("2fa");
      setMessage(data.message);
    } else if (data.status === "session_saved") {
      setStep("done");
      setMessage("«ò«‰  »« „Ê›ﬁÌ  „ ’· ‘œ.");
    } else {
      setMessage(data.message || "Œÿ« œ—  «ÌÌœ òœ.");
    }
  }

  return (
    <div className="p-4 border rounded-lg space-y-3">
      <h3 className="font-medium">—«Âù«‰œ«“Ì «ò«‰   ·ê—«„ (MTProto)</h3>
      {step === "phone" && (
        <>
          <input
            className="border p-2 w-full rounded"
            placeholder="‘„«—Â „Ê»«Ì· »« òœ ò‘Ê— ó „À·« 989121234567"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
          />
          <button onClick={handleStart} className="bg-blue-600 text-white px-4 py-2 rounded">
            «—”«· òœ  «ÌÌœ
          </button>
        </>
      )}
      {(step === "code" || step === "2fa") && (
        <>
          <p className="text-sm text-gray-600">{message}</p>
          <input
            className="border p-2 w-full rounded"
            placeholder="òœ  «ÌÌœ œ—Ì«› Ì «“  ·ê—«„"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
          {step === "2fa" && (
            <input
              className="border p-2 w-full rounded"
              type="password"
              placeholder="—„“ œÊ „—Õ·Âù«Ì"
              value={twoStepPassword}
              onChange={(e) => setTwoStepPassword(e.target.value)}
            />
          )}
          <button onClick={handleVerify} className="bg-green-600 text-white px-4 py-2 rounded">
             «ÌÌœ
          </button>
        </>
      )}
      {step === "done" && <p className="text-green-700">{message}</p>}
    </div>
  );
}
