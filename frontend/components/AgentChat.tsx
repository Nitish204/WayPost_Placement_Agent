"use client";
import { useState, useRef, useEffect } from "react";
import { motion } from "framer-motion";
import { Panel, Button } from "@/components/ui";
import { api } from "@/lib/api";

type Message = { role: "user" | "agent"; text: string };

export function AgentChat({ token }: { token: string }) {
  const [messages, setMessages] = useState<Message[]>([
    { role: "agent", text: "Ask me things like \"find me remote data analyst roles in India\" and I'll search and rank them against your resume." },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  async function send(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const text = input.trim();
    if (!text || loading) return;
    setMessages((m) => [...m, { role: "user", text }]);
    setInput("");
    setLoading(true);
    try {
      const res = await api.agentChat(text, token);
      setMessages((m) => [...m, { role: "agent", text: res.reply }]);
    } catch (err: any) {
      setMessages((m) => [...m, { role: "agent", text: `Something went wrong: ${err.message || "try again."}` }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Panel className="p-6 flex flex-col h-[480px]">
      <h2 className="font-bold text-xl mb-4">Ask the agent</h2>

      <div className="flex-1 overflow-y-auto space-y-3 pr-1">
        {messages.map((m, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[80%] rounded-[10px] px-4 py-2.5 text-sm leading-relaxed border-2 border-ink ${
                m.role === "user" ? "bg-signal" : "bg-cream"
              }`}
            >
              {m.text}
            </div>
          </motion.div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="rounded-[10px] px-4 py-2.5 text-sm border-2 border-ink bg-cream text-muted">thinking…</div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <form onSubmit={send} className="flex gap-2 mt-4 pt-4 border-t-2 border-ink/10">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Find me remote data analyst internships in India"
          className="flex-1 border-2 border-ink rounded-[10px] px-4 py-2.5 bg-cream focus:bg-white transition-colors outline-none text-sm"
        />
        <Button type="submit" disabled={loading || !input.trim()}>Send</Button>
      </form>
    </Panel>
  );
}
