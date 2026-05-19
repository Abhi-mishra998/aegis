import React from 'react'
import Modal from './Modal'
import { formatHotkey } from '../../hooks/useHotkeys'

/**
 * KeyboardCheatsheet — modal listing the active shortcuts. Triggered by
 * pressing `?` anywhere outside an input.
 *
 *   <KeyboardCheatsheet
 *     isOpen={open}
 *     onClose={() => setOpen(false)}
 *     groups={[
 *       { label: 'Navigate', items: [{ key: 'g p', desc: 'Policies' }, ...] },
 *     ]}
 *   />
 */
export default function KeyboardCheatsheet({ isOpen, onClose, groups = [] }) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Keyboard shortcuts"
      description="Linear-style global shortcuts. Press ? anywhere to open this sheet."
      size="lg"
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-6">
        {groups.map((group) => (
          <section key={group.label} className="flex flex-col gap-2">
            <h3 className="text-[10px] font-bold uppercase tracking-[0.15em] text-neutral-500">
              {group.label}
            </h3>
            <ul className="flex flex-col divide-y divide-[var(--border-subtle)]">
              {group.items.map((item) => (
                <li
                  key={`${group.label}:${item.key}`}
                  className="flex items-center justify-between gap-4 py-2"
                >
                  <span className="text-xs text-neutral-300">{item.desc}</span>
                  <kbd
                    className="
                      inline-flex items-center gap-1
                      px-2 py-1 rounded-md
                      bg-white/[0.05] border border-white/[0.08]
                      text-[11px] font-mono font-semibold text-neutral-200
                      whitespace-nowrap
                    "
                  >
                    {formatHotkey(item.key)}
                  </kbd>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Modal>
  )
}
