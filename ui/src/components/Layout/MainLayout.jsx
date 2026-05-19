import React, { useState, useEffect, useCallback } from 'react'
import Sidebar from './Sidebar'
import Topbar from './Topbar'
import CommandPalette from '../Common/CommandPalette'

/**
 * MainLayout — the application shell.
 *
 * Structure:
 *   [Sidebar]  [Topbar]
 *              [Main content]
 *
 * Stacking contract (matches the Modal + Sidebar primitives):
 *   navbar         z-40
 *   sidebar mobile z-50 (aside) with z-40 backdrop
 *   sidebar desktop z-30 (under navbar so the brand never overlaps)
 *   modal overlay  z-50, content z-[60]
 *   toast          z-[80]
 *
 * The content wrapper deliberately uses OPACITY-only fade (no transform), so
 * `position: fixed` descendants such as Modal portals are not anchored to a
 * transformed ancestor. The page-content wrapper also enforces `min-w-0` on
 * the flex column so long table rows cannot push the layout horizontally.
 */
export default function MainLayout({ children }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)

  const openPalette  = useCallback(() => setPaletteOpen(true),  [])
  const closePalette = useCallback(() => setPaletteOpen(false), [])

  // Cmd+K / Ctrl+K global shortcut
  useEffect(() => {
    const handle = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setPaletteOpen((v) => !v)
      }
    }
    document.addEventListener('keydown', handle)
    return () => document.removeEventListener('keydown', handle)
  }, [])

  // Auto-close the mobile drawer when crossing the lg breakpoint up — avoids
  // a stuck open drawer after a window resize.
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 1024px)')
    const onChange = (e) => { if (e.matches) setSidebarOpen(false) }
    mq.addEventListener?.('change', onChange)
    return () => mq.removeEventListener?.('change', onChange)
  }, [])

  return (
    <div className="h-[100dvh] bg-[var(--bg-base)] flex overflow-hidden">
      {/* Skip link for keyboard users */}
      <a
        href="#main-content"
        className="
          sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[90]
          focus:px-3 focus:py-2 focus:rounded-md focus:bg-white focus:text-black
          focus:text-xs focus:font-semibold focus:shadow-lg
        "
      >
        Skip to main content
      </a>

      <Sidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <Topbar
          onMenuClick={() => setSidebarOpen((v) => !v)}
          onCommandPalette={openPalette}
        />

        <main
          id="main-content"
          tabIndex={-1}
          className="
            flex-1 min-w-0
            overflow-y-auto overflow-x-hidden
            bg-[var(--bg-base)]
            relative outline-none
          "
        >
          {/* Decorative baseline grid — pinned behind content. Uses fixed
              positioning intentionally; the parent main element is not
              transformed so fixed positioning behaves as expected. */}
          <div
            className="pointer-events-none fixed inset-0 grid-baseline opacity-[0.12]"
            aria-hidden="true"
          />

          {/* Page-content container.
              IMPORTANT: no transforms here — earlier `animate-fade-in` applied
              a `translateY` keyframe which created a stacking context that
              trapped fixed-position modals beneath the navbar. We use the
              opacity-only `animate-fade-in-opacity` instead. */}
          <div className="relative z-10 w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-6 lg:py-8 animate-fade-in-opacity">
            {children}
          </div>
        </main>
      </div>

      <CommandPalette isOpen={paletteOpen} onClose={closePalette} />
    </div>
  )
}
