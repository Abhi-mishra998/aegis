import React, { useRef, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import Modal from './Modal'

/**
 * ConfirmDialog — Modal-backed confirm/cancel sheet with optional danger variant.
 *
 *   <ConfirmDialog
 *     isOpen={open}
 *     title="Disable agent_42?"
 *     description="The agent will stop executing tools but stay registered."
 *     confirmLabel="Disable"
 *     variant="danger"
 *     onConfirm={async () => apiCall()}
 *     onClose={() => setOpen(false)}
 *   />
 *
 * Auto-disables the confirm button + shows a spinner while `onConfirm`
 * resolves. Closes on success; surfaces errors via `onError` so the caller
 * decides how to toast/log them.
 */
export default function ConfirmDialog({
  isOpen,
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel  = 'Cancel',
  variant = 'default',
  onConfirm,
  onClose,
  onError,
  icon,
}) {
  const cancelRef = useRef(null)
  const [busy, setBusy] = useState(false)

  const handleConfirm = async () => {
    if (busy) return
    try {
      setBusy(true)
      await onConfirm?.()
      onClose?.()
    } catch (err) {
      onError?.(err)
    } finally {
      setBusy(false)
    }
  }

  const danger = variant === 'danger'
  const confirmClasses = danger
    ? 'bg-red-500 hover:bg-red-400 text-white focus-visible:ring-red-300'
    : 'bg-white hover:bg-neutral-200 text-black focus-visible:ring-white/60'

  return (
    <Modal
      isOpen={isOpen}
      onClose={busy ? () => {} : onClose}
      title={title}
      size="sm"
      initialFocusRef={cancelRef}
      footer={
        <>
          <button
            ref={cancelRef}
            type="button"
            onClick={onClose}
            disabled={busy}
            className="
              w-full sm:w-auto px-4 py-2 rounded-lg text-xs font-semibold
              text-neutral-200 bg-white/[0.04] hover:bg-white/[0.08]
              border border-[var(--border-default)]
              focus-visible:ring-2 focus-visible:ring-white/30
              disabled:opacity-50 disabled:cursor-not-allowed
              transition-colors
            "
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={busy}
            className={`
              w-full sm:w-auto px-4 py-2 rounded-lg text-xs font-semibold
              focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-surface-elevated)]
              disabled:opacity-50 disabled:cursor-not-allowed
              transition-colors
              inline-flex items-center justify-center gap-2
              ${confirmClasses}
            `}
          >
            {busy && (
              <span
                className="inline-block w-3 h-3 rounded-full border-2 border-current border-r-transparent animate-spin"
                aria-hidden="true"
              />
            )}
            {confirmLabel}
          </button>
        </>
      }
    >
      <div className="flex items-start gap-3">
        {(icon ?? (danger && <AlertTriangle className="text-red-400 shrink-0 mt-0.5" size={18} aria-hidden="true" />)) || null}
        {description && (
          <p className="text-xs text-neutral-300 leading-relaxed">
            {description}
          </p>
        )}
      </div>
    </Modal>
  )
}
