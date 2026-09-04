export function Badge({ children, tone = "signal" }: { children: React.ReactNode; tone?: "signal" | "ink" }) {
  const toneClasses =
    tone === "signal" ? "bg-signal border-ink" : "bg-cream border-ink text-ink";
  return (
    <span
      className={`inline-flex items-center gap-2 font-mono text-[13px] border-2 ${toneClasses} px-4 py-1.5 rounded-pill shadow-offset-sm -rotate-1`}
    >
      {children}
    </span>
  );
}
