import type { ReactNode } from "react";
import type { SessionState } from "../api/types";
import { stateMeta, type Tone } from "../lib/model";

export function Badge({
  tone,
  children,
  pulse = false,
  dot = true,
}: {
  tone: Tone;
  children: ReactNode;
  pulse?: boolean;
  dot?: boolean;
}) {
  return (
    <span className={`badge tone-${tone}`}>
      {dot && <span className={`dot${pulse ? " pulse" : ""}`} />}
      {children}
    </span>
  );
}

export function StateBadge({ state, large = false }: { state: SessionState; large?: boolean }) {
  const meta = stateMeta(state);
  return (
    <span
      className={`badge tone-${meta.tone}${large ? " badge-lg" : ""}`}
      data-testid="state-badge"
    >
      <span className={`dot${meta.active ? " pulse" : ""}`} />
      {meta.label}
    </span>
  );
}

export function Swatch({ tone, size = 8 }: { tone: Tone | "fg2" | "fg3"; size?: number }) {
  return (
    <span className="swatch" style={{ width: size, height: size, background: `var(--${tone})` }} />
  );
}

export function Label({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`label ${className}`}>{children}</span>;
}

export function Card({
  children,
  className = "",
  testId,
}: {
  children: ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <section className={`card ${className}`} data-testid={testId}>
      {children}
    </section>
  );
}

export function CardHead({
  title,
  note,
  children,
}: {
  title: ReactNode;
  note?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="card-head">
      <span className="card-title">{title}</span>
      {note && <span className="muted">{note}</span>}
      <span className="grow" />
      {children}
    </div>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  small = false,
}: {
  value: T;
  options: { value: T; label: ReactNode; disabled?: boolean; title?: string }[];
  onChange: (value: T) => void;
  small?: boolean;
}) {
  return (
    <div className={`seg${small ? " seg-sm" : ""}`} role="tablist">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={option.value === value}
          className={option.value === value ? "on" : ""}
          disabled={option.disabled}
          title={option.title}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Locked({ title, owner }: { title: string; owner: string }) {
  return (
    <div className="locked">
      <div className="locked-blur" aria-hidden>
        <div className="locked-title">{title}</div>
        <div className="bar" style={{ width: "86%" }} />
        <div className="bar" style={{ width: "64%" }} />
        <div className="bar" style={{ width: "75%" }} />
      </div>
      <div className="locked-cover">
        <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
          <rect x="2.5" y="6" width="9" height="6.5" rx="1.5" fill="currentColor" />
          <path
            d="M4.5 6V4.5a2.5 2.5 0 0 1 5 0V6"
            stroke="currentColor"
            strokeWidth="1.4"
            fill="none"
          />
        </svg>
        <div>
          <div className="locked-name">{title}</div>
          <div className="muted">Private to {owner} · not shared</div>
        </div>
      </div>
    </div>
  );
}

export function Empty({
  title,
  children,
  action,
  icon = "circle",
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  icon?: "circle" | "doc";
}) {
  return (
    <div className="empty">
      <div className={icon === "doc" ? "empty-doc" : "empty-circle"} />
      <div className="empty-title">{title}</div>
      {children && <div className="empty-text">{children}</div>}
      {action}
    </div>
  );
}

export function Spinner() {
  return <span className="spinner" aria-label="loading" />;
}

export function Skeleton({ widths = ["72%", "90%", "56%", "80%"] }: { widths?: string[] }) {
  return (
    <div className="skeleton">
      {widths.map((width, index) => (
        <div key={index} style={{ width }} />
      ))}
    </div>
  );
}

export function Kpi({
  label,
  value,
  tone,
  sub,
}: {
  label: string;
  value: ReactNode;
  tone?: Tone;
  sub?: ReactNode;
}) {
  return (
    <div className="kpi">
      <Label>{label}</Label>
      <div className="kpi-value" style={tone ? { color: `var(--${tone})` } : undefined}>
        {value}
      </div>
      {sub && <div className="muted">{sub}</div>}
    </div>
  );
}

export function Logo({ size = 16 }: { size?: number }) {
  return (
    <span className="logo" style={{ width: size * 1.45, height: size }} aria-hidden>
      <span style={{ width: size, height: size, borderColor: "var(--a)" }} />
      <span style={{ width: size, height: size, left: size * 0.45, borderColor: "var(--b)" }} />
    </span>
  );
}
