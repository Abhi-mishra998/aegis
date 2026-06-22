// Drives https://aegisagent.in as the prod user via a fresh Clerk sign-in ticket.
// Writes per-screen { console, net, navOk } JSON + PNG into /tmp/walkthrough/.
import { chromium } from 'playwright';
import fs from 'node:fs';

const TICKET = fs.readFileSync('/tmp/clerk_ticket', 'utf8').trim();
const BASE   = 'https://aegisagent.in';
const OUT    = '/tmp/walkthrough';
fs.mkdirSync(OUT, { recursive: true });

// Sidebar set + the protected routes the brief flagged as suspect.
const SCREENS = [
  '/dashboard',
  '/team',
  '/live-feed',
  '/agents',
  '/incidents',
  '/policies',
  '/approval-inbox',
  '/compliance',
  '/settings',
  '/audit-logs',
  '/forensics',
  '/playground',
  '/threat-intel',
  '/evaluation',
  '/playbooks',
  '/auto-response',
  '/identity-graph',
  '/threat-graph',
  '/shadow-mode',
  '/shadow-review',
  '/flight-recorder',
  '/decision-explorer',
  '/session-explorer',
  '/fleet',
  '/system-health',
  '/billing',
  '/onboarding',
  '/kill-switch',
  '/rbac',
  '/developer',
  '/webhook-settings',
  // /admin is the Aegis platform-staff super-admin surface (ROOT only).
  // Not in the customer sidebar; excluded so the walkthrough only covers
  // surfaces reachable from the signed-in user UX.
  '/siem',
  '/scheduled-reports',
  '/quota',
  '/sso',
  '/notifications',
  '/users',
  '/settings/teams',
];

function safe(p){ return p.replace(/^\//,'').replace(/[\/]/g,'_') || 'root'; }

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
    viewport: { width: 1440, height: 900 },
  });
  const page = await ctx.newPage();

  // ---- sign in via Clerk sign-in-token ticket
  console.log('[auth] navigating to /login with ticket');
  await page.goto(`${BASE}/login?__clerk_ticket=${TICKET}`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
  try {
    await page.waitForURL(u => u.pathname === '/dashboard', { timeout: 45_000 });
    console.log('[auth] landed on /dashboard');
  } catch (e) {
    const url = page.url();
    const html = (await page.content()).slice(0, 800);
    fs.writeFileSync(`${OUT}/_auth_fail.html`, await page.content());
    await page.screenshot({ path: `${OUT}/_auth_fail.png`, fullPage: true });
    console.log('[auth] FAILED to reach /dashboard. url=', url);
    console.log('[auth] body head:', html);
    await browser.close();
    process.exit(2);
  }

  const summary = {};
  for (const route of SCREENS) {
    const consoleMsgs = [];
    const netFails    = [];
    const onCons = m => {
      const t = m.type();
      if (t === 'error' || t === 'warning') consoleMsgs.push({ type: t, text: m.text() });
    };
    const onResp = async r => {
      const s = r.status();
      const u = r.url();
      if (s >= 400 && u.startsWith('http')) {
        let body = '';
        try { body = (await r.text()).slice(0, 400); } catch {}
        netFails.push({ status: s, method: r.request().method(), url: u, body });
      }
    };
    const onErr = err => consoleMsgs.push({ type: 'pageerror', text: String(err) });

    page.on('console', onCons);
    page.on('response', onResp);
    page.on('pageerror', onErr);

    let navOk = true, errMsg = null;
    try {
      await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 30_000 });
      await page.waitForLoadState('networkidle', { timeout: 8_000 }).catch(() => {});
      await page.waitForTimeout(1200);
    } catch (e) {
      navOk = false; errMsg = String(e).slice(0, 400);
    }

    const finalUrl = page.url();
    const title = await page.title().catch(() => '');
    const pngPath = `${OUT}/${safe(route)}.png`;
    try { await page.screenshot({ path: pngPath, fullPage: true }); } catch {}

    page.off('console', onCons);
    page.off('response', onResp);
    page.off('pageerror', onErr);

    summary[route] = {
      navOk, errMsg, finalUrl, title,
      consoleCount: consoleMsgs.length,
      netFailCount: netFails.length,
      console: consoleMsgs.slice(0, 30),
      netFails: netFails.slice(0, 30),
    };
    const tag = navOk
      ? (netFails.length ? `NET×${netFails.length}` : (consoleMsgs.length ? `CON×${consoleMsgs.length}` : 'ok'))
      : 'NAV-FAIL';
    console.log(`[walk] ${route.padEnd(22)} ${tag}  finalUrl=${finalUrl.replace(BASE,'')}`);
  }

  fs.writeFileSync(`${OUT}/_summary.json`, JSON.stringify(summary, null, 2));
  await browser.close();
  console.log('[done] summary →', `${OUT}/_summary.json`);
})().catch(e => { console.error(e); process.exit(1); });
