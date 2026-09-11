"use client";
import { useEffect, useState } from "react";

export function LiveClock() {
  const [time, setTime] = useState<string | null>(null);
  const [colonOn, setColonOn] = useState(true);

  useEffect(() => {
    function tick() {
      const now = new Date();
      const hh = String(now.getHours()).padStart(2, "0");
      const mm = String(now.getMinutes()).padStart(2, "0");
      setTime(`${hh}|${mm}`);
      setColonOn((v) => !v);
    }
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  if (!time) return <span className="opacity-0">00:00</span>;

  const [hh, mm] = time.split("|");
  return (
    <span>
      {hh}
      <span className={colonOn ? "opacity-100" : "opacity-20"}>:</span>
      {mm}
    </span>
  );
}
