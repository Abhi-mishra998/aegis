import { useEffect } from 'react'

export default function useUnsavedChanges(dirty, message = 'You have unsaved changes. Leave anyway?') {
  useEffect(() => {
    if (!dirty) return
    const handler = (e) => {
      e.preventDefault()
      e.returnValue = message
      return message
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty, message])
}
