import React, { useState, useEffect } from 'react'
import { AlertCircle, CheckCircle, Info, X } from 'lucide-react'

/**
 * Toast Component (Item)
 * Styled for "Next Level" UIUX with glassmorphism and smooth transitions.
 *
 * Extended (U5): optional `ttl` (ms; default 5000) and optional `action`
 * ({label, onClick}) so the LiveFeed escalation toast can offer a one-click
 * "Review" CTA without reimplementing the toast stack.
 */
export default function Toast({ message, type, onClose, ttl = 5000, action }) {
  const [isClosing, setIsClosing] = useState(false)

  useEffect(() => {
    const timer = setTimeout(() => {
      handleClose()
    }, ttl)

    return () => clearTimeout(timer)
  }, [ttl])

  const handleClose = () => {
    setIsClosing(true)
    setTimeout(onClose, 300)
  }

  const colors = {
    success: 'bg-emerald-900/90 text-emerald-100 border-emerald-500/50 shadow-emerald-500/20',
    error: 'bg-rose-900/90 text-rose-100 border-rose-500/50 shadow-rose-500/20',
    warning: 'bg-amber-900/90 text-amber-100 border-amber-500/50 shadow-amber-500/20',
    info: 'bg-blue-900/90 text-blue-100 border-blue-500/50 shadow-blue-500/20',
  }

  const icons = {
    success: <CheckCircle size={18} className="text-emerald-400" />,
    error: <AlertCircle size={18} className="text-rose-400" />,
    warning: <AlertCircle size={18} className="text-amber-400" />,
    info: <Info size={18} className="text-blue-400" />,
  }

  return (
    <div
      role="status"
      className={`
        flex items-center gap-3 px-4 py-3 rounded-xl border backdrop-blur-md shadow-xl
        transform transition-all duration-300 ease-out
        ${colors[type] || colors.info}
        ${isClosing ? 'translate-x-full opacity-0' : 'translate-x-0 opacity-100'}
      `}
    >
      <div className="flex-shrink-0">
        {icons[type]}
      </div>
      <div className="flex-1 text-sm font-medium pr-2">
        {message}
      </div>
      {action && action.label && typeof action.onClick === 'function' && (
        <button
          onClick={() => { action.onClick(); handleClose(); }}
          className="flex-shrink-0 text-xs font-semibold uppercase tracking-wider px-2.5 py-1 rounded-md bg-white/10 hover:bg-white/20 text-white transition-colors"
        >
          {action.label}
        </button>
      )}
      <button
        onClick={handleClose}
        className="flex-shrink-0 text-white/50 hover:text-white transition-colors"
        aria-label="Dismiss notification"
      >
        <X size={16} />
      </button>
    </div>
  )
}
