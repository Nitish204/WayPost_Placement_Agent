"use client";
import { useState } from "react";
import { Panel, Button } from "@/components/ui";
import { api } from "@/lib/api";

// Field names match app/core/ats_scorer.py's compute_ats_score() return
// dict exactly: ats_score_estimate, keyword_similarity, missing_keywords,
// formatting_issues, formatting_warnings, disclaimer, qualitative_feedback.

type AtsResult = {
  ats_score_estimate: number;
  keyword_similarity: number;
  missing_keywords: string[];
  formatting_issues: string[];
  formatting_warnings: string[];
  disclaimer: string;
  qualitative_feedback?: string;
};

export function AtsScorePanel({ token, hasResume }: { token: string; hasResume: boolean }) {
  const [jd, setJd] = useState("");
  const [result, setResult] = useState<AtsResult | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function run() {
    setError("");
    if (!jd.trim()) {
      setError("Paste a job description first.");
      return;
    }
    if (!hasResume) {
      setError("Upload a resume first — the score needs something to compare against.");
      return;
    }
    setLoading(true);
    try {
      const res = await api.atsScore(jd, token);
      setResult(res);
    } catch (err: any) {
      setError(err.message || "Couldn't score that. Try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Panel className="p-6">
      <h2 className="font-bold text-xl mb-1">ATS score</h2>
      <p className="text-sm text-muted mb-4">
        Paste a job description to see how your resume scores against it, the way an
        applicant-tracking system would.
      </p>

      <textarea
        value={jd}
        onChange={(e) => setJd(e.target.value)}
        placeholder="Paste the job description here…"
        rows={6}
        className="w-full border-2 border-ink rounded-[10px] px-4 py-3 bg-cream focus:bg-white transition-colors outline-none text-sm resize-y"
      />

      {error && <p className="text-sm text-red-600 mt-2">{error}</p>}

      <Button onClick={run} disabled={loading} className="mt-4">
        {loading ? "Scoring…" : "Score my resume"}
      </Button>

      {result && (
        <div className="mt-6 border-t-2 border-ink/10 pt-5 space-y-5">
          <div className="flex items-baseline gap-6">
            <div className="flex items-baseline gap-2">
              <span className="font-bold text-4xl">{Math.round(result.ats_score_estimate)}</span>
              <span className="text-sm text-muted">/ 100 overall</span>
            </div>
            <div className="flex items-baseline gap-2">
              <span className="font-bold text-2xl">{Math.round(result.keyword_similarity)}</span>
              <span className="text-sm text-muted">/ 100 keyword match</span>
            </div>
          </div>

          {result.qualitative_feedback && (
            <div>
              <p className="text-xs font-bold uppercase tracking-wide text-muted mb-1.5">Feedback</p>
              <p className="text-sm leading-relaxed whitespace-pre-line">{result.qualitative_feedback}</p>
            </div>
          )}

          {result.missing_keywords?.length > 0 && (
            <div>
              <p className="text-xs font-bold uppercase tracking-wide text-muted mb-2">Missing keywords</p>
              <div className="flex flex-wrap gap-1.5">
                {result.missing_keywords.map((k) => (
                  <span key={k} className="text-xs font-mono bg-red-100 border border-red-300 rounded-pill px-2 py-0.5">{k}</span>
                ))}
              </div>
            </div>
          )}

          {(result.formatting_issues?.length > 0 || result.formatting_warnings?.length > 0) && (
            <div>
              <p className="text-xs font-bold uppercase tracking-wide text-muted mb-2">Formatting</p>
              <ul className="text-sm space-y-1">
                {result.formatting_issues?.map((i) => (
                  <li key={i} className="text-red-700">⚠ {i}</li>
                ))}
                {result.formatting_warnings?.map((w) => (
                  <li key={w} className="text-muted">· {w}</li>
                ))}
              </ul>
            </div>
          )}

          <p className="text-xs text-muted italic">{result.disclaimer}</p>
        </div>
      )}
    </Panel>
  );
}
