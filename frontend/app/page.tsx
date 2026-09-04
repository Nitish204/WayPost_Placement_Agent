"use client";
import { useState } from "react";
import { motion } from "framer-motion";
import { useRouter } from "next/navigation";
import { Badge } from "@/components/Badge";
import { Panel, Button, Input } from "@/components/ui";
import { ForgotPassword } from "@/components/ForgotPassword";
import { api } from "@/lib/api";

const scanLog = [
  { src: "greenhouse · figma", open: 4 },
  { src: "lever · razorpay", open: 7 },
  { src: "ashby · linear", open: 2 },
  { src: "adzuna · full-stack, IN", open: 31 },
  { src: "greenhouse · stripe", open: 12 },
];

// One orchestrated page-load sequence (staggered rise), not scattered
// hover effects on every element - per the "one memorable moment" rule.
const rise = {
  hidden: { opacity: 0, y: 16 },
  show: (i: number) => ({ opacity: 1, y: 0, transition: { delay: i * 0.12, duration: 0.6, ease: [0.16, 1, 0.3, 1] } }),
};

type Mode = "login" | "register" | "forgot";

export default function LandingPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("login");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleLogin(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    setLoading(true);
    const fd = new FormData(e.currentTarget);
    try {
      const res = await api.login(String(fd.get("email")), String(fd.get("password")));
      localStorage.setItem("waypost_token", res.access_token);
      router.push("/dashboard");
    } catch (err: any) {
      setError(err.message || "Couldn't log in. Check your details and try again.");
    } finally {
      setLoading(false);
    }
  }

  async function handleRegister(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    setLoading(true);
    const fd = new FormData(e.currentTarget);
    try {
      const res = await api.register({
        name: String(fd.get("name")),
        email: String(fd.get("email")),
        password: String(fd.get("password")),
        security_question: String(fd.get("security_question")),
        security_answer: String(fd.get("security_answer")),
        job_titles: String(fd.get("job_titles")),
        locations: String(fd.get("locations")),
      });
      localStorage.setItem("waypost_token", res.access_token);
      router.push("/dashboard");
    } catch (err: any) {
      setError(err.message || "That didn't work. Check your details and try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen">
      <header className="border-b-2 border-ink flex items-center justify-between px-6 md:px-10 py-4">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-signal border-2 border-ink rounded-[8px]" />
          <span className="font-bold text-lg">Waypost</span>
          <span className="hidden sm:inline text-xs font-mono border border-ink/40 rounded-pill px-3 py-1 text-muted">
            placement agent
          </span>
        </div>
      </header>

      <section className="max-w-3xl mx-auto px-6 pt-16 pb-10">
        <motion.div variants={rise} initial="hidden" animate="show" custom={0}>
          <Badge>● waypost · placement intelligence</Badge>
        </motion.div>

        <motion.h1
          variants={rise} initial="hidden" animate="show" custom={1}
          className="font-bold text-[40px] md:text-[54px] leading-[1.05] tracking-tight mt-6"
        >
          The job board that reads job boards{" "}
          <span className="bg-signal border-2 border-ink rounded-[6px] px-3 shadow-offset inline-block rotate-1">
            for you.
          </span>
        </motion.h1>

        <motion.p
          variants={rise} initial="hidden" animate="show" custom={2}
          className="text-[17px] leading-relaxed text-muted max-w-lg mt-6"
        >
          New roles appear across the web every hour. Waypost scans them quietly, checks each one
          against your resume, and only tells you what&apos;s actually worth applying to.
        </motion.p>

        <motion.div variants={rise} initial="hidden" animate="show" custom={3} className="mt-10">
          <Panel dark className="p-5 font-mono text-[13px]">
            <div className="flex justify-between text-muted pb-2 border-b border-white/10">
              <span>scan.log</span>
              <span>21:37</span>
            </div>
            {scanLog.map((row) => (
              <div key={row.src} className="flex justify-between py-2 border-b border-white/5 last:border-0">
                <span className="text-cream/80">{row.src}</span>
                <span className="text-signal font-bold">{row.open} open</span>
              </div>
            ))}
          </Panel>
        </motion.div>

        <motion.div
          variants={rise} initial="hidden" animate="show" custom={4}
          className="grid grid-cols-3 gap-6 mt-10 max-w-md"
        >
          {[["3", "sources tracked"], ["24/7", "hourly scanning"], ["0", "manual refreshing"]].map(([n, l]) => (
            <div key={l}>
              <div className="font-bold text-3xl">{n}</div>
              <div className="text-xs text-muted mt-1">{l}</div>
            </div>
          ))}
        </motion.div>
      </section>

      <motion.section
        variants={rise} initial="hidden" animate="show" custom={5}
        className="max-w-md mx-auto px-6 pb-20"
      >
        <Panel className="p-7">
          <Badge tone="ink">private workspace</Badge>

          {mode === "forgot" ? (
            <>
              <h2 className="font-bold text-2xl mt-4 mb-6">Reset your password.</h2>
              <ForgotPassword onDone={() => setMode("login")} />
            </>
          ) : (
            <>
              <h2 className="font-bold text-2xl mt-4">Find your next role.</h2>
              <p className="text-sm text-muted mt-1 mb-6">
                Sign in to see matches, or create an account to start tracking.
              </p>

              <div className="flex gap-2 mb-6">
                <button
                  onClick={() => setMode("login")}
                  className={`flex-1 py-2.5 rounded-[10px] border-2 border-ink font-bold text-sm transition-colors ${
                    mode === "login" ? "bg-signal" : "bg-cream"
                  }`}
                >
                  Log in
                </button>
                <button
                  onClick={() => setMode("register")}
                  className={`flex-1 py-2.5 rounded-[10px] border-2 border-ink font-bold text-sm transition-colors ${
                    mode === "register" ? "bg-signal" : "bg-cream"
                  }`}
                >
                  Create account
                </button>
              </div>

              {error && <p className="text-sm text-red-600 mb-4">{error}</p>}

              {mode === "login" ? (
                <form onSubmit={handleLogin} className="space-y-4">
                  <Input label="Email" name="email" type="email" placeholder="you@email.com" required />
                  <Input label="Password" name="password" type="password" required />
                  <Button type="submit" disabled={loading} className="w-full">
                    {loading ? "Logging in…" : "Log in"}
                  </Button>
                  <button
                    type="button"
                    onClick={() => setMode("forgot")}
                    className="text-sm underline font-medium block mx-auto"
                  >
                    Forgot password?
                  </button>
                </form>
              ) : (
                <form onSubmit={handleRegister} className="space-y-4">
                  <Input label="Full name" name="name" required />
                  <Input label="Email" name="email" type="email" required />
                  <Input label="Password" name="password" type="password" minLength={8} required />
                  <Input label="Security question" name="security_question" placeholder="Your first pet's name?" required />
                  <Input label="Security answer" name="security_answer" required />
                  <Input label="Roles you want" name="job_titles" placeholder="Software Engineer, Data Analyst" required />
                  <Input label="Preferred locations" name="locations" placeholder="Bangalore, Remote" required />
                  <Button type="submit" disabled={loading} className="w-full">
                    {loading ? "Creating account…" : "Create account"}
                  </Button>
                </form>
              )}
            </>
          )}
        </Panel>
      </motion.section>
    </main>
  );
}
