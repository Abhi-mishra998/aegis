import { chromium } from 'playwright';

const BASE = 'https://aegisagent.in';
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36';

// Spawn
const r = await fetch(`${BASE}/demo/spawn-workspace`, {
  method: 'POST', headers: { 'Content-Type': 'application/json', 'User-Agent': UA }, body: '{}'
});
const d = (await r.json()).data;
console.log('tenant_id from spawn:', d.tenant_id);

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1366, height: 768 }, userAgent: UA });
const page = await ctx.newPage();

page.on('console', m => {
  console.log(`  CONSOLE.${m.type()}: ${m.text().slice(0, 200)}`);
});
page.on('pageerror', e => console.log(`  PAGEERROR: ${e.message}`));

console.log(`\nNavigate to: ${BASE}${d.redirect_url}`);
await page.goto(BASE + d.redirect_url, { waitUntil: 'networkidle', timeout: 25000 });
await page.waitForTimeout(3000);

console.log('\nAfter load:');
const state = await page.evaluate(() => ({
  url: window.location.href,
  search: window.location.search,
  cookie: document.cookie,
  tenant_id: sessionStorage.getItem('tenant_id'),
  user_email: sessionStorage.getItem('user_email'),
  role: sessionStorage.getItem('user_role'),
  expiry: sessionStorage.getItem('acp_token_expiry'),
  expiryDate: sessionStorage.getItem('acp_token_expiry') ? new Date(parseInt(sessionStorage.getItem('acp_token_expiry'))).toISOString() : null,
}));
console.log(JSON.stringify(state, null, 2));

await browser.close();
