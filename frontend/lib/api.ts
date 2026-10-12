// Thin client over the existing FastAPI backend. Every function here
// maps 1:1 to a real route in app/main.py - nothing invented.

const BASE = "/api";

// Auth is an httpOnly session cookie set by the server on login/register.
// The browser attaches it automatically to these same-origin /api calls, so
// no token is ever read, stored or sent by JavaScript.
async function postForm(path: string, data: Record<string, any>) {
  const body = new URLSearchParams();
  Object.entries(data).forEach(([k, v]) => {
    if (v !== undefined && v !== null) body.append(k, String(v));
  });
  const res = await fetch(BASE + path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
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

  logout: () => postForm("/auth/logout", {}),

  me: async () => {
    const res = await fetch(BASE + "/auth/me", { credentials: "same-origin" });
    if (!res.ok) throw new Error("Not authenticated");
    return res.json();
  },

  getSecurityQuestion: (email: string) => postForm("/auth/security-question", { email }),
  resetWithSecurityAnswer: (data: { email: string; security_answer: string; new_password: string }) =>
    postForm("/auth/reset-with-security-answer", data),

  updateProfile: (data: Record<string, any>) => postForm("/profile/update", data),
  linkTelegram: (telegram_chat_id: string) =>
    postForm("/notifications/telegram/link", { telegram_chat_id }),

  uploadResume: async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(BASE + "/resume/upload", { method: "POST", credentials: "same-origin", body: fd });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Upload failed");
    return res.json();
  },

  atsScore: (job_description: string, resume_text?: string) =>
    postForm("/resume/ats-score", { job_description, resume_text }),

  searchJobs: (job_titles: string, locations: string, experience_level?: string, top_k = 20) =>
    postForm("/jobs/search", { job_titles, locations, experience_level, top_k }),

  seedSample: () => postForm("/jobs/seed-sample", {}),

  agentChat: (message: string) => postForm("/agent/chat", { message }),

  // job_id here must be the real numeric Job row id - /jobs/search now
  // includes it directly in each result (see backend/app/main.py), so
  // callers pass job.id straight through.
  applyPrepare: (job_id: number, phone?: string, cover_note?: string) =>
    postForm("/apply/prepare", { job_id, phone, cover_note }),

  applyConfirm: (applicationId: number) =>
    postForm(`/apply/${applicationId}/confirm`, {}),

  applyReject: (applicationId: number) =>
    postForm(`/apply/${applicationId}/reject`, {}),

  listApplications: async () => {
    const res = await fetch(BASE + "/applications", { credentials: "same-origin" });
    if (!res.ok) throw new Error("Could not load applications");
    return res.json();
  },

  boardHealth: async () => {
    const res = await fetch(BASE + "/admin/board-health", { credentials: "same-origin" });
    if (!res.ok) throw new Error("Could not check board health");
    return res.json();
  },

  // No auth header - powers the public landing page's scan.log panel.
  scanLog: async (): Promise<{ rows: { src: string; open: number }[] }> => {
    const res = await fetch(BASE + "/public/scan-log");
    if (!res.ok) throw new Error("Could not load scan log");
    return res.json();
  },
};
