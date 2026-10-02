import type { ReactNode } from 'react';
export function Icon({
  name,
  size = 18,
}: {
  name: 'play' | 'pause' | 'expand' | 'volume' | 'mute' | 'file' | 'arrow' | 'refresh';
  size?: number;
}) {
  const paths: Record<string, ReactNode> = {
    play: <path d="m8 5 11 7-11 7Z" />,
    pause: (
      <>
        <path d="M8 5v14M16 5v14" />
      </>
    ),
    expand: <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" />,
    volume: (
      <>
        <path d="M11 5 6 9H3v6h3l5 4ZM15 8c3 2 3 6 0 8m3-11c5 4 5 10 0 14" />
      </>
    ),
    mute: (
      <>
        <path d="M11 5 6 9H3v6h3l5 4Z M16 9l6 6m0-6-6 6" />
      </>
    ),
    file: <path d="M14 3H5v18h14V8Zm0 0v6h5M8 13h8M8 17h6" />,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
    refresh: <path d="M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 14 6M18 18a8 8 0 0 1-14-6" />,
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={name === 'play' ? 'currentColor' : 'none'}
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}
export function Empty({ children, compact = false }: { children: ReactNode; compact?: boolean }) {
  return (
    <div className={`empty ${compact ? 'compact' : ''}`}>
      <Icon name="file" size={34} />
      <span>{children}</span>
    </div>
  );
}
export function Alert({ message, onRetry }: { message?: string | null; onRetry?: () => void }) {
  return message ? (
    <div className="alert" role="alert">
      <span>{message}</span>
      {onRetry && (
        <button className="text-button" onClick={onRetry}>
          重试
        </button>
      )}
    </div>
  ) : null;
}
export function Modes<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className="modes" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          aria-pressed={value === o.value}
          className={value === o.value ? 'active' : ''}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
export function PageHeader({
  title,
  description,
  children,
  leading,
}: {
  title: string;
  description?: string;
  children?: ReactNode;
  leading?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div className="heading-title-group">
        {leading}
        <div>
          <h1>{title}</h1>
          {description && <p>{description}</p>}
        </div>
      </div>
      <div className="heading-actions">{children}</div>
    </div>
  );
}
export function Footer({ children }: { children: ReactNode }) {
  return (
    <footer className="workspace-footer">
      <span>{children}</span>
      <span className="footer-brand">RefereeLink</span>
    </footer>
  );
}
