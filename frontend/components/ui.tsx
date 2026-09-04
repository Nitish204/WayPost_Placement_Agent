import { ButtonHTMLAttributes, InputHTMLAttributes } from "react";

export function Panel({
  children,
  className = "",
  dark = false,
}: {
  children: React.ReactNode;
  className?: string;
  dark?: boolean;
}) {
  return (
    <div
      className={`border-2 border-ink rounded-card shadow-offset-signal ${
        dark ? "bg-ink text-cream" : "bg-white"
      } ${className}`}
    >
      {children}
    </div>
  );
}

export function Button({
  children,
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" }) {
  const base =
    "font-display font-bold text-[15px] px-6 py-3 rounded-[10px] border-2 border-ink transition-transform active:translate-x-[2px] active:translate-y-[2px] active:shadow-none disabled:opacity-50 disabled:pointer-events-none";
  const styles =
    variant === "primary"
      ? "bg-signal text-ink shadow-offset hover:-translate-y-[1px] hover:shadow-offset-lg"
      : "bg-cream text-ink shadow-offset-sm hover:-translate-y-[1px]";
  return (
    <button className={`${base} ${styles} ${className}`} {...props}>
      {children}
    </button>
  );
}

export function Input({ label, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return (
    <label className="block">
      <span className="block text-sm font-medium mb-1.5">{label}</span>
      <input
        className="w-full border-2 border-ink rounded-[10px] px-4 py-2.5 bg-cream focus:bg-white transition-colors outline-none font-display text-[15px]"
        {...props}
      />
    </label>
  );
}
