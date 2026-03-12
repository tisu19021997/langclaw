"use client";

interface SettingsToggleProps {
  on: boolean;
  onChange?: (val: boolean) => void;
  disabled?: boolean;
}

export function SettingsToggle({ on, onChange, disabled = false }: SettingsToggleProps) {
  return (
    <button
      onClick={() => !disabled && onChange?.(!on)}
      className="flex-shrink-0 relative"
      style={{
        width: 44,
        height: 26,
        borderRadius: "var(--r-full)",
        background: on ? "var(--terra)" : "var(--ink-15)",
        transition: "background 0.2s",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.6 : 1,
      }}
    >
      <div
        className="absolute top-[3px] rounded-full bg-white transition-transform"
        style={{
          width: 20,
          height: 20,
          left: 3,
          transform: on ? "translateX(18px)" : "translateX(0)",
          boxShadow: "0 1px 4px rgba(0,0,0,.2)",
          transition: "transform 0.2s",
        }}
      />
    </button>
  );
}

interface SettingsRowProps {
  label: string;
  sub: string;
  toggle?: React.ReactNode;
  rightContent?: React.ReactNode;
  onClick?: () => void;
}

export function SettingsRow({ label, sub, toggle, rightContent, onClick }: SettingsRowProps) {
  const content = (
    <>
      <div className="flex-1 min-w-0">
        <div className="text-[14px] font-semibold" style={{ color: "var(--ink)" }}>
          {label}
        </div>
        <div className="text-[12px]" style={{ color: "var(--ink-50)" }}>
          {sub}
        </div>
      </div>
      {toggle}
      {rightContent}
    </>
  );

  if (onClick) {
    return (
      <button
        onClick={onClick}
        className="w-full flex items-center gap-3 py-3 px-4 text-left"
      >
        {content}
      </button>
    );
  }

  return (
    <div className="flex items-center gap-3 py-3 px-4">
      {content}
    </div>
  );
}

export function SettingsGroup({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        background: "var(--ds-white)",
        borderRadius: "var(--r-lg)",
        border: "1px solid var(--ink-08)",
        overflow: "hidden",
      }}
    >
      {children}
    </div>
  );
}

export function SettingsDivider() {
  return <div style={{ borderTop: "1px solid var(--ink-04)" }} />;
}

export function SettingsSectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p
      className="text-[11px] font-semibold uppercase mb-2 px-1"
      style={{ color: "var(--ink-30)", letterSpacing: "0.8px" }}
    >
      {children}
    </p>
  );
}
