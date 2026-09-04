"use client";
import { useState } from "react";
import { Button, Input } from "@/components/ui";
import { api } from "@/lib/api";

// Two-step flow matching the backend exactly:
// 1) POST /auth/security-question -> returns the question to display
// 2) POST /auth/reset-with-security-answer -> verifies answer + sets new password

export function ForgotPassword({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState<"email" | "answer">("email");
  const [email, setEmail] = useState("");
  const [question, setQuestion] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  async function submitEmail(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    setLoading(true);
    const fd = new FormData(e.currentTarget);
    const emailValue = String(fd.get("email"));
    try {
      const res = await api.getSecurityQuestion(emailValue);
      setEmail(emailValue);
      setQuestion(res.security_question);
      setStep("answer");
    } catch (err: any) {
      setError(err.message || "No account found with that email.");
    } finally {
      setLoading(false);
    }
  }

  async function submitAnswer(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    setLoading(true);
    const fd = new FormData(e.currentTarget);
    try {
      await api.resetWithSecurityAnswer({
        email,
        security_answer: String(fd.get("security_answer")),
        new_password: String(fd.get("new_password")),
      });
      setSuccess("Password updated. You can log in with your new password now.");
    } catch (err: any) {
      setError(err.message || "That answer doesn't match our records.");
    } finally {
      setLoading(false);
    }
  }

  if (success) {
    return (
      <div className="space-y-4">
        <p className="text-sm font-medium">{success}</p>
        <Button onClick={onDone} className="w-full">Back to log in</Button>
      </div>
    );
  }

  if (step === "email") {
    return (
      <form onSubmit={submitEmail} className="space-y-4">
        <p className="text-sm text-muted">Enter your account email to see your security question.</p>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <Input label="Email" name="email" type="email" required />
        <Button type="submit" disabled={loading} className="w-full">
          {loading ? "Checking…" : "Continue"}
        </Button>
        <button type="button" onClick={onDone} className="text-sm underline font-medium block mx-auto">
          Back to log in
        </button>
      </form>
    );
  }

  return (
    <form onSubmit={submitAnswer} className="space-y-4">
      <p className="text-sm text-muted">{question}</p>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <Input label="Your answer" name="security_answer" required />
      <Input label="New password" name="new_password" type="password" minLength={8} required />
      <Button type="submit" disabled={loading} className="w-full">
        {loading ? "Updating…" : "Update password"}
      </Button>
      <button type="button" onClick={onDone} className="text-sm underline font-medium block mx-auto">
        Back to log in
      </button>
    </form>
  );
}
