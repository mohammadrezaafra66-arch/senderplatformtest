import React, { useState } from "react";

interface TooltipHintProps {
  text: string;
}

export default function TooltipHint({ text }: TooltipHintProps) {
  const [visible, setVisible] = useState(false);

  return (
    <span
      style={{ position: "relative", display: "inline-flex", alignItems: "center" }}
    >
      <span
        onMouseEnter={() => setVisible(true)}
        onMouseLeave={() => setVisible(false)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: "16px",
          height: "16px",
          borderRadius: "50%",
          backgroundColor: "#e5e7eb",
          color: "#6b7280",
          fontSize: "11px",
          fontWeight: "bold",
          cursor: "help",
          userSelect: "none",
          flexShrink: 0,
        }}
      >
        ؟
      </span>
      {visible && (
        <span
          style={{
            position: "absolute",
            bottom: "calc(100% + 6px)",
            right: 0,
            backgroundColor: "#111827",
            color: "#ffffff",
            fontSize: "12px",
            borderRadius: "8px",
            padding: "8px 12px",
            zIndex: 9999,
            width: "260px",
            textAlign: "right",
            lineHeight: "1.6",
            boxShadow: "0 4px 16px rgba(0,0,0,0.25)",
            pointerEvents: "none",
            direction: "rtl",
            whiteSpace: "normal",
          }}
        >
          {text}
        </span>
      )}
    </span>
  );
}
