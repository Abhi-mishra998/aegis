import { useEffect } from 'react'

/**
 * useHotkeys — Linear/Datadog-style global keyboard shortcuts.
 *
 * Supports:
 *   - Single keys:          { key: '?',  handler }
 *   - Modifier combos:      { key: 'mod+k',  handler }   ("mod" = ⌘ on Mac, Ctrl elsewhere)
 *   - Two-key sequences:    { key: 'g p',    handler }   (vim-style "go to policies")
 *   - Capture-in-inputs:    { allowInInput: true }       (default false — skips when typing)
 *
 * Bindings are deduplicated by `key` and live as long as the component is
 * mounted. Tear-down on unmount automatically.
 */
const SEQUENCE_TIMEOUT_MS = 1200

let pendingPrefix = null
let pendingTimer = null

const isInputLike = (el) => {
  if (!el) return false
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (el.isContentEditable) return true
  return false
}

const normalizeChord = (e) => {
  // Build a canonical "mod+shift+key" string for matching modifier combos.
  const parts = []
  if (e.metaKey || e.ctrlKey) parts.push('mod')
  if (e.altKey) parts.push('alt')
  if (e.shiftKey && e.key.length === 1 && e.key !== '?') parts.push('shift')
  parts.push((e.key || '').toLowerCase())
  return parts.join('+')
}

export function useHotkeys(bindings) {
  useEffect(() => {
    if (!Array.isArray(bindings) || bindings.length === 0) return

    const handler = (e) => {
      // Clearing the pending prefix happens on EVERY keydown so an unrelated
      // key resets the sequence — exactly how Linear/Gmail behave.
      const clearPrefix = () => {
        pendingPrefix = null
        clearTimeout(pendingTimer)
      }

      const target = e.target
      const inInput = isInputLike(target)
      const chord = normalizeChord(e)
      const rawKey = (e.key || '').toLowerCase()

      // Sequence resolution: if a prefix is pending and the next key is plain
      // (no modifiers), look for "<prefix> <key>" matches first.
      if (pendingPrefix && !e.metaKey && !e.ctrlKey && !e.altKey) {
        const candidate = `${pendingPrefix} ${rawKey}`
        for (const b of bindings) {
          if (b.key === candidate && (b.allowInInput || !inInput)) {
            e.preventDefault()
            b.handler(e)
            clearPrefix()
            return
          }
        }
      }

      // Modifier combos take priority over single-key bindings.
      for (const b of bindings) {
        if (b.key === chord && (b.allowInInput || !inInput)) {
          e.preventDefault()
          b.handler(e)
          clearPrefix()
          return
        }
      }

      // Single-key bindings — only fire when not in an input.
      if (!e.metaKey && !e.ctrlKey && !e.altKey && !inInput) {
        for (const b of bindings) {
          if (b.key === rawKey) {
            e.preventDefault()
            b.handler(e)
            clearPrefix()
            return
          }
        }
        // If this single key is a registered prefix for any sequence, arm it.
        const hasSeq = bindings.some(
          (b) => typeof b.key === 'string' && b.key.startsWith(`${rawKey} `),
        )
        if (hasSeq) {
          pendingPrefix = rawKey
          clearTimeout(pendingTimer)
          pendingTimer = setTimeout(clearPrefix, SEQUENCE_TIMEOUT_MS)
          return
        }
      }

      // Any other key resets pending state so prefixes don't linger forever.
      clearPrefix()
    }

    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [bindings])
}

/**
 * formatHotkey — render a binding key in human-readable form for cheatsheets.
 *   "mod+k"   → "⌘K" on Mac, "Ctrl+K" elsewhere
 *   "g p"     → "G  P"
 *   "?"       → "?"
 */
export function formatHotkey(key) {
  if (!key) return ''
  const isMac =
    typeof navigator !== 'undefined' && /mac/i.test(navigator.platform || '')
  return key
    .split(' ')
    .map((chord) =>
      chord
        .split('+')
        .map((p) => {
          if (p === 'mod')   return isMac ? '⌘' : 'Ctrl'
          if (p === 'alt')   return isMac ? '⌥' : 'Alt'
          if (p === 'shift') return '⇧'
          return p.length === 1 ? p.toUpperCase() : p
        })
        .join(isMac ? '' : '+'),
    )
    .join('  ')
}
