"use client";
import { useEffect, useState } from "react";
import { Panel, Button } from "@/components/ui";
import { api } from "@/lib/api";

type Job = { id: number; title: string; company: string; location: string; apply_url: string };

type ApplicationState = {
  id: number;
  status: string;
  filled_fields: { field: string; label: string }[];
  submit_warning?: string | null;
  preview_screenshot_b64?: string;
  confirmation_screenshot_b64?: string;
  error_message?: string;
};

/**
 * Two-stage auto-apply flow, matching the backend's human-approval gate:
 * on open, calls /apply/prepare (fills the REAL apply form via the
 * browser agent, does not submit) and shows the screenshot. The user
 * must explicitly click Approve for /apply/{id}/confirm to actually
 * click submit on the real page - or Reject, which never touches the
 * submission path at all.
 */
export function ApplyModal({ job, token, onClose }: { job: Job; token: string; onClose: () => void }) {
  const [application, setApplication] = useState<ApplicationState | null>(null);
  const [phase, setPhase] = useState<"preparing" | "reviewing" | "submitting" | "done">("preparing");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setPhase("preparing");
    api
      .applyPrepare(job.id, token)
      .then((res) => {
        if (cancelled) return;
        setApplication(res);
        setPhase(res.status === "pending_approval" ? "reviewing" : "done");
        if (res.status !== "pending_approval") setError(res.error_message || "Could not prepare this application.");
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err.message || "Something went wrong preparing this application.");
        setPhase("done");
      });
    return () => {
      cancelled = true;
    };
  }, [job.id, token]);

  async function approve() {
    if (!application) return;
    setPhase("submitting");
    try {
      const res = await api.applyConfirm(application.id, token);
      setApplication(res);
      setPhase("done");
      if (res.status !== "submitted") setError(res.error_message || "Submission failed.");
    } catch (err: any) {
      setError(err.message || "Submission failed.");
      setPhase("done");
    }
  }

  async function reject() {
    if (!application) return;
    try {
      await api.applyReject(application.id, token);
    } finally {
      onClose();
    }
  }

  return (
    <div className="fixed inset-0 bg-ink/60 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="w-full max-w-2xl max-h-[85vh] overflow-y-auto">
        <Panel className="p-6">
          <div className="flex items-start justify-between mb-4">
            <div>
              <h2 className="font-bold text-lg">{job.title}</h2>
              <p className="text-sm text-muted">
                {job.company} · {job.location}
              </p>
            </div>
            <button onClick={onClose} className="text-sm font-bold underline">
              Close
            </button>
          </div>

          {phase === "preparing" && (
            <p className="text-sm text-muted font-mono">
              Opening the real application form and filling in your details… this can take a few seconds.
            </p>
          )}

          {phase === "reviewing" && application && (
            <>
              <p className="text-sm mb-3">
                This is the actual apply page for this job, filled in with your info. Nothing has been
                submitted yet.
              </p>
              {application.submit_warning && (
                <p className="text-xs bg-yellow-100 border border-yellow-400 text-yellow-900 rounded-md px-3 py-2 mb-3">
                  ⚠️ {application.submit_warning}
                </p>
              )}
              {application.filled_fields.length > 0 && (
                <div className="mb-3 flex flex-wrap gap-1.5">
                  {application.filled_fields.map((f, i) => (
                    <span
                      key={i}
                      className="text-xs font-mono bg-signal/40 border border-ink/20 rounded-pill px-2 py-0.5"
                    >
                      filled: {f.field}
                    </span>
                  ))}
                </div>
              )}
              {application.preview_screenshot_b64 && (
                <div className="border-2 border-ink rounded-[10px] mb-4 overflow-y-auto max-h-[45vh] bg-cream">
                  <img
                    src={`data:image/png;base64,${application.preview_screenshot_b64}`}
                    alt="Application preview"
                    className="w-full block"
                  />
                </div>
              )}
              <div className="flex gap-3">
                <Button onClick={approve} className="flex-1">
                  Approve &amp; submit
                </Button>
                <Button variant="secondary" onClick={reject} className="flex-1">
                  Reject
                </Button>
              </div>
            </>
          )}

          {phase === "submitting" && (
            <p className="text-sm text-muted font-mono">Submitting the real application…</p>
          )}

          {phase === "done" && application?.status === "submitted" && (
            <>
              <p className="text-sm font-bold mb-3">Submitted.</p>
              {application.confirmation_screenshot_b64 && (
                <div className="border-2 border-ink rounded-[10px] mb-4 overflow-y-auto max-h-[45vh] bg-cream">
                  <img
                    src={`data:image/png;base64,${application.confirmation_screenshot_b64}`}
                    alt="Submission confirmation"
                    className="w-full block"
                  />
                </div>
              )}
              <Button onClick={onClose}>Done</Button>
            </>
          )}

          {phase === "done" && application?.status !== "submitted" && (
            <>
              <p className="text-sm text-red-600 mb-3">
                {error || "This application could not be completed automatically."}
              </p>
              <p className="text-xs text-muted mb-3">
                You can still apply directly on the company's site.
              </p>
              <div className="flex gap-3">
                <a href={job.apply_url} target="_blank" rel="noreferrer" className="flex-1">
                  <Button className="w-full">Open apply page</Button>
                </a>
                <Button variant="secondary" onClick={onClose} className="flex-1">
                  Close
                </Button>
              </div>
            </>
          )}
        </Panel>
      </div>
    </div>
  );
}
