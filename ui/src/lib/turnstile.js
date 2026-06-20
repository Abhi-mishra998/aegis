// Sprint EI-9 (2026-06-20) — Cloudflare Turnstile loader + invisible-mode helper.
//
// The page imports `loadTurnstile()` lazily — the cf-turnstile script is
// only injected when the user clicks the demo CTA. Keeps the Landing page
// cold-start free of third-party tax.
//
// If VITE_TURNSTILE_SITE_KEY is unset, every function is a no-op that
// resolves with an empty token. The server side mirrors this: it bypasses
// verification when TURNSTILE_SECRET_KEY is unset. Both sides agree on
// "no site key configured = development mode = no challenge".

const SITE_KEY = import.meta.env.VITE_TURNSTILE_SITE_KEY || ''
const SCRIPT_SRC =
  'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'

let _scriptPromise = null

function _loadScript () {
  if (!SITE_KEY) return Promise.resolve(false)
  if (_scriptPromise) return _scriptPromise
  if (typeof window === 'undefined') return Promise.resolve(false)
  if (window.turnstile) return Promise.resolve(true)
  _scriptPromise = new Promise((resolve) => {
    const s = document.createElement('script')
    s.src = SCRIPT_SRC
    s.async = true
    s.defer = true
    s.onload = () => resolve(true)
    s.onerror = () => {
      _scriptPromise = null
      resolve(false)
    }
    document.head.appendChild(s)
  })
  return _scriptPromise
}

/**
 * Render an invisible Turnstile widget and resolve with the token.
 *
 * The widget mounts inside the provided container element; if none is
 * passed, a hidden div is added to <body>. The promise resolves with the
 * token string on success, '' on cancel, or '' on script failure (the
 * server still rate-limits + the local-dev fallback covers both ends).
 *
 * @param {HTMLElement|null} container
 * @returns {Promise<string>}  the cf-turnstile-response token, or ''
 */
export async function getTurnstileToken (container = null) {
  if (!SITE_KEY) return ''           // local dev — verifier is bypassed
  const ok = await _loadScript()
  if (!ok || !window.turnstile) return ''

  return new Promise((resolve) => {
    const host = container || (() => {
      const d = document.createElement('div')
      d.style.position = 'fixed'
      d.style.bottom = '12px'
      d.style.right  = '12px'
      d.style.zIndex = '9999'
      document.body.appendChild(d)
      return d
    })()

    let settled = false
    const finish = (tok) => {
      if (settled) return
      settled = true
      try { window.turnstile.remove(id) } catch (_) {}
      if (!container && host.parentNode) host.parentNode.removeChild(host)
      resolve(tok || '')
    }

    let id
    try {
      id = window.turnstile.render(host, {
        sitekey: SITE_KEY,
        size: 'flexible',
        appearance: 'interaction-only',  // hidden until challenge needed
        callback: (tok) => finish(tok),
        'error-callback': () => finish(''),
        'expired-callback': () => finish(''),
        'timeout-callback': () => finish(''),
      })
    } catch (_) {
      finish('')
    }

    // Safety timeout — never block the UI for more than 15 s. If we
    // didn't get a token, send '' and let the server enforce.
    setTimeout(() => finish(''), 15000)
  })
}

/** True if a site key is configured (UI hint only — server is the truth). */
export const turnstileEnabled = !!SITE_KEY
