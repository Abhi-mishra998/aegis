import React, { useEffect, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

/**
 * Modal — enterprise dialog primitive.
 *
 * Portaled to document.body so it cannot be trapped by an ancestor `transform`
 * or `overflow: hidden` (the MainLayout content wrapper used to anchor it
 * under the navbar). z-index follows the platform hierarchy: overlay z-50,
 * content z-[60], above sidebar (z-30/40) and navbar (z-40).
 *
 * Features:
 *   - React Portal render (escapes stacking contexts)
 *   - Backdrop click + Escape key close
 *   - Focus trap (Tab / Shift+Tab cycle inside dialog)
 *   - Restore focus to invoker on close
 *   - Body scroll lock while open (no background scroll-through)
 *   - Responsive: max-h-[90dvh], scrollable body, mobile-safe padding
 *   - Reduced-motion aware
 */
const SIZES = {
  sm:   'max-w-sm',
  md:   'max-w-md',
  lg:   'max-w-lg',
  xl:   'max-w-2xl',
  '2xl':'max-w-3xl',
  full: 'max-w-5xl',
}

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export default function Modal({
  isOpen,
  title,
  description,
  onClose,
  children,
  size = 'md',
  footer,
  initialFocusRef,
  closeOnBackdrop = true,
  className = '',
}) {
  const dialogRef    = useRef(null)
  const previousActive = useRef(null)

  /* Capture invoker for focus restoration */
  useEffect(() => {
    if (isOpen) {
      previousActive.current =
        typeof document !== 'undefined' ? document.activeElement : null
    }
  }, [isOpen])

  /* Escape to close */
  useEffect(() => {
    if (!isOpen) return
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose?.()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isOpen, onClose])

  /* Lock body scroll while open — preserve gutter width to prevent layout shift */
  useEffect(() => {
    if (!isOpen) return
    const { body, documentElement } = document
    const previousOverflow   = body.style.overflow
    const previousPaddingRight = body.style.paddingRight
    const scrollbarWidth = window.innerWidth - documentElement.clientWidth
    body.style.overflow = 'hidden'
    if (scrollbarWidth > 0) body.style.paddingRight = `${scrollbarWidth}px`
    return () => {
      body.style.overflow = previousOverflow
      body.style.paddingRight = previousPaddingRight
    }
  }, [isOpen])

  /* Initial focus + focus trap */
  useEffect(() => {
    if (!isOpen) return
    const dialog = dialogRef.current
    if (!dialog) return

    // Initial focus: prefer explicit ref, then first focusable, then dialog itself
    const focusInitial = () => {
      const target =
        initialFocusRef?.current ||
        dialog.querySelector(FOCUSABLE_SELECTOR) ||
        dialog
      target?.focus?.({ preventScroll: true })
    }
    // Defer one frame so animate-in transform doesn't fight the scroll target
    const raf = requestAnimationFrame(focusInitial)

    const onTrap = (e) => {
      if (e.key !== 'Tab') return
      const focusables = Array.from(
        dialog.querySelectorAll(FOCUSABLE_SELECTOR),
      ).filter((el) => !el.hasAttribute('data-modal-skip'))
      if (focusables.length === 0) {
        e.preventDefault()
        dialog.focus()
        return
      }
      const first = focusables[0]
      const last  = focusables[focusables.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    dialog.addEventListener('keydown', onTrap)
    return () => {
      cancelAnimationFrame(raf)
      dialog.removeEventListener('keydown', onTrap)
    }
  }, [isOpen, initialFocusRef])

  /* Restore focus to invoker when closing */
  useEffect(() => {
    if (isOpen) return
    const node = previousActive.current
    if (node && typeof node.focus === 'function') {
      // Defer so React unmount completes before focus restoration
      const id = requestAnimationFrame(() => node.focus({ preventScroll: true }))
      return () => cancelAnimationFrame(id)
    }
  }, [isOpen])

  const handleBackdrop = useCallback(
    (e) => {
      if (!closeOnBackdrop) return
      if (e.target === e.currentTarget) onClose?.()
    },
    [closeOnBackdrop, onClose],
  )

  if (!isOpen) return null
  if (typeof document === 'undefined') return null

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6"
      role="presentation"
      onMouseDown={handleBackdrop}
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/70 backdrop-blur-sm animate-fade-in"
        aria-hidden="true"
      />

      {/* Dialog */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? 'acp-modal-title' : undefined}
        aria-describedby={description ? 'acp-modal-desc' : undefined}
        tabIndex={-1}
        onMouseDown={(e) => e.stopPropagation()}
        className={`
          relative z-[60] w-full ${SIZES[size] ?? SIZES.md}
          bg-[var(--bg-surface-elevated)]
          border border-[var(--border-default)]
          rounded-2xl shadow-2xl
          flex flex-col
          max-h-[90dvh]
          outline-none
          animate-scale-in
          motion-reduce:animate-none
          ${className}
        `}
      >
        {(title || description) && (
          <div className="px-6 py-4 border-b border-[var(--border-subtle)] flex items-start justify-between gap-4 shrink-0">
            <div className="min-w-0">
              {title && (
                <h2
                  id="acp-modal-title"
                  className="text-sm font-bold text-white tracking-tight truncate"
                >
                  {title}
                </h2>
              )}
              {description && (
                <p
                  id="acp-modal-desc"
                  className="text-xs text-neutral-400 mt-1 leading-relaxed"
                >
                  {description}
                </p>
              )}
            </div>
            <button
              onClick={onClose}
              aria-label="Close dialog"
              className="
                p-1.5 -m-1.5 rounded-lg shrink-0
                text-neutral-500 hover:text-white hover:bg-white/[0.06]
                focus-visible:ring-2 focus-visible:ring-white/30
                transition-colors
              "
            >
              <X size={18} aria-hidden="true" />
            </button>
          </div>
        )}

        <div className="px-6 py-5 overflow-y-auto flex-1 min-h-0">
          {children}
        </div>

        {footer && (
          <div className="px-6 py-4 border-t border-[var(--border-subtle)] flex flex-col-reverse sm:flex-row items-stretch sm:items-center justify-end gap-2 sm:gap-3 shrink-0">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
