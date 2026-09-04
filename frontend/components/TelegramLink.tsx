"use client";
import { useState } from "react";
import { Panel, Button, Input } from "@/components/ui";
import { api } from "@/lib/api";

export function TelegramLink({ token, linked }: { token: string; linked: boolean }) {
  const [isLinked, setIsLinked] = useState(linked);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function link(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    setLoading(true);
    const fd = new FormData(e.currentTarget);
    try {
      await api.linkTelegram(String(fd.get("telegram_chat_id")), token);
      setIsLinked(true);
      setStatus("Telegram linked. You'll get job alerts there too.");
    } catch (err: any) {
      setError(err.message || "Couldn't link that chat ID. Double check it and try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Panel className="p-5">
      <p className="text-sm font-medium mb-1">Telegram alerts</p>
      <p className="text-xs text-muted mb-3">
        Get pinged the moment a strong match appears. Message your bot on Telegram, then paste
        the chat ID it gives you here.
      </p>

      {isLinked ? (
        <p className="text-sm font-mono bg-signal/40 border border-ink/20 rounded-[8px] px-3 py-2 inline-block">
          ✓ Telegram linked
        </p>
      ) : (
        <form onSubmit={link} className="space-y-3">
          {error && <p className="text-sm text-red-600">{error}</p>}
          <Input label="Telegram chat ID" name="telegram_chat_id" placeholder="123456789" required />
          <Button type="submit" variant="secondary" disabled={loading} className="w-full text-sm py-2">
            {loading ? "Linking…" : "Link Telegram"}
          </Button>
        </form>
      )}
      {status && <p className="text-xs text-muted mt-2">{status}</p>}
    </Panel>
  );
}
