"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { Panel, Button, Input } from "@/components/ui";
import { Badge } from "@/components/Badge";
import { AtsScorePanel } from "@/components/AtsScorePanel";
import { AgentChat } from "@/components/AgentChat";
import { TelegramLink } from "@/components/TelegramLink";
import { api } from "@/lib/api";

type Job = { title: string; company: string; location: string; apply_url: string; score?: number };
type Tab = "search" | "ats" | "agent";

export default function Dashboard() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<any>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("search");

  useEffect(() => {
    const t = localStorage.getItem("waypost_token");
    if (!t) { router.push("/"); return; }
    setToken(t);
    api.me(t).then(setUser).catch(() => { localStorage.removeItem("waypost_token"); router.push("/"); });
  }, [router]);

  if (!user || !token) {
    return <div className="min-h-screen flex items-center justify-center font-mono text-sm text-muted">loading…</div>;
  }

  async function search(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy("search");
    const fd = new FormData(e.currentTarget);
    try {
      const res = await api.searchJobs(String(fd.get("job_titles")), String(fd.get("locations")), token!);
      setJobs(res.jobs);
      setStatus(`${res.count} matches found.`);
    } catch (err: any) { setStatus(err.message); }
    setBusy(null);
  }

  async function seedSample() {
    setBusy("seed");
    try {
      await api.seedSample(token!);
      setStatus("Sample jobs loaded. Search above to see them.");
    } catch (err: any) { setStatus(err.message); }
    setBusy(null);
  }

  async function uploadResume(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy("resume");
    try {
      const res = await api.uploadResume(file, token!);
      setStatus(`Resume parsed. Skills found: ${res.skills_found.join(", ") || "none detected"}.`);
      const fresh = await api.me(token!);
      setUser(fresh);
    } catch (err: any) { setStatus(err.message); }
    setBusy(null);
  }

  function logout() {
    localStorage.removeItem("waypost_token");
    router.push("/");
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: "search", label: "Search matches" },
    { id: "ats", label: "ATS score" },
    { id: "agent", label: "Ask the agent" },
  ];

  return (
    <main className="min-h-screen pb-20">
      <header className="border-b-2 border-ink flex items-center justify-between px-6 md:px-10 py-4">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-signal border-2 border-ink rounded-[8px]" />
          <span className="font-bold text-lg">Waypost</span>
        </div>
        <div className="flex items-center gap-4">
          <span className="text-sm text-muted hidden sm:inline">{user.email}</span>
          <button onClick={logout} className="text-sm font-bold underline">Log out</button>
        </div>
      </header>

      <div className="max-w-5xl mx-auto px-6 pt-10 grid md:grid-cols-[300px_1fr] gap-8">
        {/* Sidebar: profile, resume, sample jobs, telegram */}
        <div className="space-y-6">
          <Panel className="p-5">
            <Badge tone="ink">your profile</Badge>
            <h3 className="font-bold text-lg mt-3">{user.name}</h3>
            <p className="text-sm text-muted">{user.job_titles} · {user.locations}</p>

            <div className="mt-5 pt-4 border-t border-ink/10">
              <p className="text-sm font-medium mb-2">Resume</p>
              {user.has_resume ? (
                <div className="flex flex-wrap gap-1.5">
                  {user.resume_skills.slice(0, 6).map((s: string) => (
                    <span key={s} className="text-xs font-mono bg-signal/40 border border-ink/20 rounded-pill px-2 py-0.5">{s}</span>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted">No resume uploaded yet.</p>
              )}
              <label className="mt-3 block">
                <span className="text-xs font-bold underline cursor-pointer">
                  {busy === "resume" ? "Uploading…" : "Upload resume (PDF/DOCX)"}
                </span>
                <input type="file" accept=".pdf,.docx" onChange={uploadResume} className="hidden" disabled={busy === "resume"} />
              </label>
            </div>
          </Panel>

          <TelegramLink token={token} linked={user.telegram_linked} />

          <Panel className="p-5">
            <p className="text-sm font-medium mb-3">No jobs yet?</p>
            <Button variant="secondary" onClick={seedSample} disabled={busy === "seed"} className="w-full text-sm py-2.5">
              {busy === "seed" ? "Loading…" : "Load sample jobs"}
            </Button>
          </Panel>
        </div>

        {/* Main panel: tabbed search / ats / agent */}
        <div>
          <div className="flex gap-2 mb-6 border-b-2 border-ink/10">
            {tabs.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`px-4 py-2.5 text-sm font-bold border-b-2 -mb-0.5 transition-colors ${
                  tab === t.id ? "border-ink" : "border-transparent text-muted"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {tab === "search" && (
            <>
              <Panel className="p-6">
                <h2 className="font-bold text-xl mb-4">Search matches</h2>
                <form onSubmit={search} className="grid sm:grid-cols-[1fr_1fr_auto] gap-3">
                  <Input label="Roles" name="job_titles" defaultValue={user.job_titles} required />
                  <Input label="Locations" name="locations" defaultValue={user.locations} required />
                  <div className="flex items-end">
                    <Button type="submit" disabled={busy === "search"} className="whitespace-nowrap">
                      {busy === "search" ? "Searching…" : "Search"}
                    </Button>
                  </div>
                </form>
              </Panel>

              {status && <p className="text-sm text-muted mt-3 font-mono">{status}</p>}

              <div className="mt-6 space-y-3">
                <AnimatePresence>
                  {jobs.map((job, i) => (
                    <motion.a
                      key={job.title + job.company + i}
                      href={job.apply_url}
                      target="_blank"
                      rel="noreferrer"
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0, transition: { delay: i * 0.04 } }}
                      className="block"
                    >
                      <Panel className="p-4 flex items-center justify-between hover:-translate-y-[1px] transition-transform">
                        <div>
                          <p className="font-bold">{job.title}</p>
                          <p className="text-sm text-muted">{job.company} · {job.location}</p>
                        </div>
                        {job.score !== undefined && (
                          <span className="font-mono text-sm bg-signal border-2 border-ink rounded-pill px-3 py-1">
                            {Math.round(job.score * 100)}%
                          </span>
                        )}
                      </Panel>
                    </motion.a>
                  ))}
                </AnimatePresence>
              </div>
            </>
          )}

          {tab === "ats" && <AtsScorePanel token={token} hasResume={user.has_resume} />}

          {tab === "agent" && <AgentChat token={token} />}
        </div>
      </div>
    </main>
  );
}
