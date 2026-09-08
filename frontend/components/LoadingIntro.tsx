"use client";
import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

// A brief, unmistakable entrance sequence shown once per page load, before
// the real content underneath. The previous approach (small fade+rise on
// individual elements) was too subtle to reliably notice - finishing before
// a user's eyes even reached that part of the screen, especially with page
// load/hydration competing for attention at the same moment. This is
// intentionally bigger and impossible to miss: full-screen, high contrast,
// fixed ~900ms duration regardless of how fast the rest of the page loads.
export function LoadingIntro() {
  const [show, setShow] = useState(true);

  useEffect(() => {
    const t = setTimeout(() => setShow(false), 900);
    return () => clearTimeout(t);
  }, []);

  return (
    <AnimatePresence>
      {show && (
        <motion.div
          initial={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.4, ease: "easeInOut" }}
          className="fixed inset-0 z-50 bg-ink flex items-center justify-center"
        >
          <motion.div
            initial={{ scale: 0.7, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            className="flex items-center gap-3"
          >
            <motion.div
              animate={{ rotate: 360 }}
              transition={{ duration: 1.2, repeat: Infinity, ease: "linear" }}
              className="w-10 h-10 rounded-xl bg-signal border-2 border-cream"
            />
            <span className="font-display font-bold text-2xl text-cream">Waypost</span>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
