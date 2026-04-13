import type { PropsWithChildren, ReactNode } from "react";

type ButtonTone = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  tone?: ButtonTone;
  busy?: boolean;
}

export function Button({ tone = "ghost", busy = false, children, className = "", ...props }: ButtonProps) {
  const classes = ["btn", `btn-${tone}`, className].filter(Boolean).join(" ");
  return (
    <button {...props} className={classes} disabled={props.disabled || busy}>
      {busy ? <span className="btn-spinner" aria-hidden="true" /> : null}
      <span>{children}</span>
    </button>
  );
}

export function Badge({
  tone = "neutral",
  children,
}: PropsWithChildren<{ tone?: "neutral" | "good" | "warn" | "bad" }>) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Panel({
  title,
  kicker,
  actions,
  children,
}: PropsWithChildren<{ title?: ReactNode; kicker?: ReactNode; actions?: ReactNode }>) {
  return (
    <section className="panel">
      {(title || kicker || actions) && (
        <header className="panel-head">
          <div>
            {kicker ? <p className="panel-kicker">{kicker}</p> : null}
            {title ? <h2 className="panel-title">{title}</h2> : null}
          </div>
          {actions ? <div className="panel-actions">{actions}</div> : null}
        </header>
      )}
      {children}
    </section>
  );
}

export function Tabs<T extends string>({
  value,
  items,
  onChange,
}: {
  value: T;
  items: Array<{ value: T; label: string }>;
  onChange: (value: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          className={`tab ${item.value === value ? "is-active" : ""}`}
          onClick={() => onChange(item.value)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      {detail ? <p>{detail}</p> : null}
    </div>
  );
}

export function Modal({
  title,
  subtitle,
  children,
  footer,
  onClose,
}: PropsWithChildren<{
  title: string;
  subtitle?: string;
  footer?: ReactNode;
  onClose: () => void;
}>) {
  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal-card" role="dialog" aria-modal="true">
        <header className="modal-head">
          <div>
            <h3>{title}</h3>
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
          <Button tone="ghost" onClick={onClose}>
            关闭
          </Button>
        </header>
        <div className="modal-body">{children}</div>
        {footer ? <footer className="modal-footer">{footer}</footer> : null}
      </div>
    </div>
  );
}

export interface ToastItem {
  id: number;
  tone: "success" | "error";
  message: string;
}

export function ToastStack({ toasts, onDismiss }: { toasts: ToastItem[]; onDismiss: (id: number) => void }) {
  if (!toasts.length) return null;
  return (
    <div className="toast-stack" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast toast-${toast.tone}`}>
          <span>{toast.message}</span>
          <button type="button" className="toast-close" onClick={() => onDismiss(toast.id)} aria-label="关闭提示">
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
