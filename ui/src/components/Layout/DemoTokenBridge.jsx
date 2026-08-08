/**
 * DemoTokenBridge — DISABLED per audit C7 (2026-07-31).
 *
 * Previously read `?demo_token=<JWT>` from the URL, base64-decoded the
 * payload with NO signature verification, and installed it as an
 * authenticated session (cookie + sessionStorage). A phishing link like
 * `aegisagent.in/dashboard?demo_token=<forged>` rendered the victim's tab
 * as an OWNER of any tenant the attacker named.
 *
 * ponytail: demo flow now requires POST /auth/demo-token/exchange server round-trip — client cannot forge sessions
 * The server endpoint does not exist yet — the identity team owns adding it
 * and returning an httpOnly Set-Cookie on the response. Until then this
 * component is a no-op and the demo redirect will bounce to /login.
 *
 * Kept as a no-op export so App.jsx doesn't have to change and so this
 * file's git history stays discoverable when the server endpoint lands.
 */
export default function DemoTokenBridge() {
  return null;
}
