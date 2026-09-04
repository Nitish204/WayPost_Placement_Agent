// Thin client over the existing FastAPI backend. Every function here
// maps 1:1 to a real route in app/main.py - nothing invented.

const BASE = "/api";

function authHeaders(token?: string): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function postForm(path: string, data: Record<string, any>, token?: string) {
  const body = new URLSearchParams();
  Object.entries(data).forEach(([k, v]) => {
    if (v !== undefined && v !== null) body.append(k, String(v));
  });
  const res = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", ...authHeaders(token) },
    body,
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Request failed");
  return res.json();
}

export const api = {
  register: (data: {
    name: string; email: string; password: string; security_question: string;
    security_answer: string; job_titles: string; locations: string; experience_level?: string;
  }) => postForm("/auth/register", data),

  login: (email: string, password: string) => postForm("/auth/login", { email, password }),

  me: async (token: string) => {
    const res = await fetch(BASE + "/auth/me", { headers: authHeaders(token) });
    if (!res.ok) throw new Error("Not authenticated");
    return res.json();
  },

  getSecurityQuestion: (email: string) => postForm("/auth/security-question", { email }),
  resetWithSecurityAnswer: (data: { email: string; security_answer: string; new_password: string }) =>
    postForm("/auth/reset-with-security-answer", data),

  updateProfile: (data: Record<string, any>, token: string) => postForm("/profile/update", data, token),
  linkTelegram: (telegram_chat_id: string, token: string) =>
    postForm("/notifications/telegram/link", { telegram_chat_id }, token),

  uploadResume: async (file: File, token: string) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(BASE + "/resume/upload", { method: "POST", headers: authHeaders(token), body: fd });
    if (!res.ok) throw new Error("Upload failed");
    return res.json();
  },

  atsScore: (job_description: string, token: string, resume_text?: string) =>
    postForm("/resume/ats-score", { job_description, resume_text }, token),

  searchJobs: (job_titles: string, locations: string, token: string, top_k = 20) =>
    postForm("/jobs/search", { job_titles, locations, top_k }, token),

  seedSample: (token: string) => postForm("/jobs/seed-sample", {}, token),

  agentChat: (message: string, token: string) => postForm("/agent/chat", { message }, token),
};
