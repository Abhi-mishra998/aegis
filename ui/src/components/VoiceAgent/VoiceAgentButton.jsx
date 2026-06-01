import React, { useState, lazy, Suspense } from 'react'
import { Mic } from 'lucide-react'

// Lazy-load the panel so the LiveKit JS bundle (~140 KB gzipped) only
// downloads when the user actually clicks the button.
const VoiceAgentPanel = lazy(() => import('./VoiceAgentPanel'))

/**
 * Navbar button that opens the Aegis Voice Guide. Visible to every
 * authenticated user. The panel itself handles the LiveKit handshake;
 * this just owns the open/closed state and renders the button chrome.
 */
export default function VoiceAgentButton() {
  const [open, setOpen] = useState(false)

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Open Aegis Voice Guide"
        title="Talk to the Aegis Voice Guide"
        className="hidden sm:flex items-center gap-1.5 h-8 px-2.5 rounded-md border border-white/[0.08] hover:border-white/[0.18] bg-gradient-to-r from-blue-500/[0.08] to-purple-500/[0.08] hover:from-blue-500/[0.14] hover:to-purple-500/[0.14] transition-all group"
      >
        <span className="relative flex items-center justify-center">
          <Mic size={13} className="text-blue-300 group-hover:text-blue-200" aria-hidden="true" />
          <span
            className="absolute inset-0 rounded-full bg-blue-400/40 blur-[6px] opacity-60 group-hover:opacity-100 transition-opacity"
            aria-hidden="true"
          />
        </span>
        <span className="text-xs font-semibold text-white/90 tracking-wide">
          Voice Agent
        </span>
        <span className="ml-0.5 px-1.5 py-0.5 rounded-sm bg-blue-500/20 border border-blue-500/30 text-[8.5px] font-bold uppercase tracking-widest text-blue-300">
          Live
        </span>
      </button>

      {open && (
        <Suspense fallback={null}>
          <VoiceAgentPanel open={open} onClose={() => setOpen(false)} />
        </Suspense>
      )}
    </>
  )
}
