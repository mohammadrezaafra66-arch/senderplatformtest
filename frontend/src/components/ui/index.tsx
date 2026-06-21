import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from "react";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "ghost";
  size?: "md" | "sm";
};

export function Button({
  variant = "default",
  size = "md",
  className,
  type = "button",
  ...props
}: ButtonProps) {
  const classes = [
    "mmp-btn",
    variant === "primary" ? "mmp-btn--primary" : "",
    variant === "ghost" ? "mmp-btn--ghost" : "",
    size === "sm" ? "mmp-btn--sm" : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");

  return <button type={type} className={classes} {...props} />;
}

export function PageContent({
  children,
  className,
  style,
}: {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <div className={`mmp-page ${className ?? ""}`.trim()} style={style}>
      {children}
    </div>
  );
}

export function Panel({
  title,
  headerExtra,
  children,
  flushTable,
  className,
}: {
  title?: ReactNode;
  headerExtra?: ReactNode;
  children: ReactNode;
  flushTable?: boolean;
  className?: string;
}) {
  const classes = [
    "mmp-panel",
    flushTable ? "mmp-panel--flush-table" : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <section className={classes}>
      {title != null || headerExtra != null ? (
        <div className="mmp-panel__header">
          {title != null ? <div>{title}</div> : <span />}
          {headerExtra}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export function PanelContent({ children }: { children: ReactNode }) {
  return <div className="mmp-panel__content">{children}</div>;
}

export function Alert({
  variant = "error",
  children,
}: {
  variant?: "error" | "success";
  children: ReactNode;
}) {
  return (
    <div
      role={variant === "error" ? "alert" : "status"}
      className={`mmp-alert mmp-alert--${variant}`}
    >
      {children}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="mmp-empty">{children}</div>;
}

export function TableWrap({ children }: { children: ReactNode }) {
  return <div className="mmp-table-wrap">{children}</div>;
}

export function FormField({
  label,
  children,
}: {
  label: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="mmp-field">
      <span className="mmp-field__label">{label}</span>
      {children}
    </label>
  );
}

export function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="mmp-card mmp-stat-card">
      <div className="mmp-stat-card__label">{label}</div>
      <div className="mmp-stat-card__value">{value}</div>
    </div>
  );
}

export const inputClassName = "mmp-input";
export const selectClassName = "mmp-select";
export const textareaClassName = "mmp-textarea";
export const tableClassName = "mmp-table";
