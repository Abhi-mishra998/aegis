import { useEffect, useState } from 'react'

export default function DataFreshness({ updatedAt, prefix = 'Updated', className = '' }) {
  const [, force] = useState(0)
  useEffect(() => {
    const id = setInterval(() => force(n => n + 1), 15_000)
    return () => clearInterval(id)
  }, [])
  if (!updatedAt) return <span className={`text-xs text-neutral-500 ${className}`}>—</span>
  const parsed = new Date(updatedAt)
  const sec = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 1000))
  const label = sec < 60 ? `${sec}s ago` : sec < 3600 ? `${Math.floor(sec/60)}m ago` : sec < 86400 ? `${Math.floor(sec/3600)}h ago` : `${Math.floor(sec/86400)}d ago`
  return <span className={`text-xs text-neutral-500 ${className}`} title={parsed.toISOString()}>{prefix} {label}</span>
}
