"use client";
import { useEffect } from "react";
import { Button } from "@/components/ui";

// Next.js renders this automatically when an unhandled error occurs
// anywhere in this route segment's component tree, instead of the
// default raw white error screen the user would otherwise see. `reset()`
// re-renders the segment without a full page reload.
export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // In a real deployment this is where you'd forward to an error
    // tracking service (Sentry, etc.) - logged here for now so it's at
    // least visible in Vercel's function logs rather than silently lost.
    console.error("Unhandled error caught by error boundary:", error);
  }, [error]);

  return (
    <main className="min-h-screen flex items-center justify-center px-6 bg-cream">
      <div className="max-w-md text-center">
        <h1 className="font-display font-bold text-2xl mb-2">Something went wrong</h1>
        <p className="text-muted mb-6">
          That's an error on our end, not something you did. Try again, or come back in a moment.
        </p>
        <Button onClick={reset}>Try again</Button>
      </div>
    </main>
  );
}
