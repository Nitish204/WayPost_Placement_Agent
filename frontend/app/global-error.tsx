"use client";
import { useEffect } from "react";

// error.tsx only catches errors within a route segment - if the root
// layout itself throws (rare, but possible), Next.js needs this separate
// global-error.tsx, which must render its own <html>/<body> since the
// root layout that would normally provide them is what failed.
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error("Unhandled error in root layout:", error);
  }, [error]);

  return (
    <html lang="en">
      <body style={{ background: "#fdfbf3", color: "#14140f", fontFamily: "system-ui, sans-serif" }}>
        <main style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: "24px", textAlign: "center" }}>
          <div>
            <h1 style={{ fontWeight: 700, fontSize: "24px", marginBottom: "8px" }}>Something went wrong</h1>
            <p style={{ color: "#4a473e", marginBottom: "24px" }}>Please refresh the page.</p>
            <button
              onClick={reset}
              style={{ background: "#ffe14d", border: "2px solid #14140f", borderRadius: "10px", padding: "12px 24px", fontWeight: 700, cursor: "pointer" }}
            >
              Try again
            </button>
          </div>
        </main>
      </body>
    </html>
  );
}
