// Render the 1200x630 OG image to ui/public/og-image.png.
// One-shot script. Run when the brand mark / tagline changes:
//   node ui/scripts/gen-og-image.mjs
import { chromium } from 'playwright'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const OUT = path.resolve(__dirname, '../public/og-image.png')

const HTML = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@600;800&display=swap');
    html, body { margin: 0; padding: 0; width: 1200px; height: 630px; font-family: Inter, system-ui, sans-serif; }
    body {
      background: radial-gradient(ellipse at top, #1a1a1a 0%, #0a0a0a 60%, #050505 100%);
      color: #fff;
      display: flex;
      flex-direction: column;
      padding: 80px;
      box-sizing: border-box;
      position: relative;
      overflow: hidden;
    }
    /* subtle grid backdrop */
    body::before {
      content: '';
      position: absolute; inset: 0;
      background-image:
        linear-gradient(rgba(255,255,255,0.04) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.04) 1px, transparent 1px);
      background-size: 60px 60px;
      mask-image: radial-gradient(ellipse at center, #000 30%, transparent 80%);
      -webkit-mask-image: radial-gradient(ellipse at center, #000 30%, transparent 80%);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 16px;
      position: relative;
      z-index: 1;
    }
    .mark {
      width: 64px;
      height: 64px;
      border-radius: 14px;
      background: #fff;
      color: #000;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .name {
      font-size: 28px;
      font-weight: 800;
      letter-spacing: -0.02em;
    }
    h1 {
      font-size: 76px;
      font-weight: 800;
      line-height: 1.05;
      letter-spacing: -0.03em;
      margin: 64px 0 0 0;
      max-width: 950px;
      position: relative;
      z-index: 1;
    }
    h1 span { color: #a3a3a3; }
    .sub {
      font-size: 24px;
      font-weight: 600;
      color: #a3a3a3;
      margin-top: 28px;
      max-width: 900px;
      line-height: 1.35;
      position: relative;
      z-index: 1;
    }
    .foot {
      position: absolute;
      bottom: 60px;
      left: 80px;
      right: 80px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      z-index: 1;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 12px 20px;
      border-radius: 999px;
      background: rgba(255,255,255,0.06);
      border: 1px solid rgba(255,255,255,0.1);
      font-size: 18px;
      font-weight: 600;
      color: #e5e5e5;
    }
    .dot { width: 10px; height: 10px; border-radius: 50%; background: #22c55e; box-shadow: 0 0 0 4px rgba(34,197,94,0.15); }
    .url { font-size: 22px; font-weight: 700; color: #fff; letter-spacing: -0.01em; }
  </style>
</head>
<body>
  <div class="brand">
    <div class="mark">
      <svg width="36" height="36" viewBox="0 0 32 32" fill="none">
        <path d="M16 4 L26 8 V16 C26 22 21 26.5 16 28 C11 26.5 6 22 6 16 V8 Z"
              fill="none" stroke="#000" stroke-width="2.4" stroke-linejoin="round"/>
        <path d="M12 21 L16 11 L20 21 M13.6 17.4 L18.4 17.4"
              stroke="#000" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
      </svg>
    </div>
    <div class="name">Aegis</div>
  </div>
  <h1>AI governance &amp;<br/><span>runtime security platform</span></h1>
  <p class="sub">
    Sits between AI agents and the systems they control. Policy, approvals,
    usage caps &mdash; and a cryptographically verifiable audit trail.
  </p>
  <div class="foot">
    <div class="pill"><span class="dot"></span>Live · 14-day shadow mode</div>
    <div class="url">aegisagent.in</div>
  </div>
</body>
</html>`

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1200, height: 630 } })
await page.setContent(HTML, { waitUntil: 'networkidle' })
await page.screenshot({ path: OUT, fullPage: false, omitBackground: false, type: 'png' })
await browser.close()
console.log(`wrote ${OUT}`)
