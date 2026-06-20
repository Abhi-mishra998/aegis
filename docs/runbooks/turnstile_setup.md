# Cloudflare Turnstile — operator setup

Sprint EI-9 (2026-06-20). One-shot bring-up of the bot-defence shield in
front of `POST /demo/spawn-workspace`. After this is done, automated
spawn attempts from a corporate NAT (which WAF can't distinguish from a
real user) hit a Cloudflare challenge before they reach Aegis.

Default state: **unconfigured** — both the UI widget and the server
verifier no-op, the demo flow works the same as before. Configuring is
two env vars + one SSM secret + a few clicks in the Cloudflare console.

---

## Cloudflare side (~5 minutes)

1. Sign into the Cloudflare dashboard.
2. **Turnstile → Add site**.
   - Site name: `Aegis demo spawn` (any label — for your own audit)
   - Domain: `aegisagent.in` (and `eu.aegisagent.in` if the EU instance
     is up — Turnstile sites are domain-scoped)
   - Widget mode: **Managed** (recommended; the widget shows a challenge
     only when it suspects automation)
3. Copy:
   - The **Site key** — public; this is what the UI sends to render the widget
   - The **Secret key** — private; this is what the server sends to siteverify

---

## Aegis backend side (~3 minutes)

The server verifier is gated on the `TURNSTILE_SECRET_KEY` env var. Put
the secret in SSM, then reference it from the docker-compose env file.

```bash
# 1. Drop the secret into SSM, encrypted at rest with the default KMS key.
aws ssm put-parameter --region ap-south-1 \
  --name /aegis/prod/turnstile_secret_key \
  --value '<your-cloudflare-secret-key>' \
  --type SecureString \
  --overwrite

# 2. Reload the gateway with TURNSTILE_SECRET_KEY pointing at that param.
#    infra/restore_prod_env_from_ssm.sh already reads /aegis/prod/*; add
#    TURNSTILE_SECRET_KEY to the loop so it lands in the gateway env file.

# 3. SSM-redeploy or roll the ASG. The gateway picks up the new env on boot.
```

For the EU instance, mirror with `--region eu-west-1 --name /aegis/eu/turnstile_secret_key`
(EU customers should have their own Cloudflare site OR you can re-use
the same Cloudflare site if your DPA allows — Cloudflare data residency
is documented in `docs/security/subprocessors.md`).

---

## Aegis UI side (~2 minutes)

The UI uses the **site key** (public — safe to bake into the bundle).
Set it as a Vite env var at build time:

```bash
# In infra/.env or the Vite build environment
VITE_TURNSTILE_SITE_KEY=0x4AAAAAAA...your-public-site-key
```

Then rebuild the UI bundle and deploy:

```bash
cd ui && VITE_TURNSTILE_SITE_KEY=<site-key> npx vite build
# upload dist/ to the deploy bundle
bash scripts/ops/build_release_bundle.sh
```

The widget loads on-demand — only when a user clicks the "Try Live Demo"
button. The Landing page itself has no third-party script tax until that
click happens.

---

## Verify end-to-end

```bash
# 1. Without a token — should fail
curl -sS -X POST -H 'content-type: application/json' \
  -d '{}' \
  https://aegisagent.in/demo/spawn-workspace
# expect: 403 Forbidden + detail: "Turnstile verification failed: missing_token"

# 2. Click "Try Live Demo" on https://aegisagent.in
# - The Cloudflare widget should render (often invisibly in managed mode)
# - On success the browser lands on /dashboard?demo=1 as before
# - Server log shows: turnstile_rejected reason=... (during testing) OR
#                     spawn_demo_workspace ... (on success)

# 3. Confirm rate-limit still works (WAF + per-IP layers are unchanged)
for i in {1..7}; do
  curl -sS -X POST -H 'content-type: application/json' \
    -d '{"cf-turnstile-response":"<TEST_TOKEN>"}' \
    -o /dev/null -w '%{http_code}\n' \
    https://aegisagent.in/demo/spawn-workspace
done
# expect: 200 200 200 200 200 429 429
```

---

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Every spawn returns 403 `Turnstile verification failed: verify_unreachable` | Egress to `challenges.cloudflare.com` blocked from the gateway | Add the FQDN to the egress-allow list; in AWS this means an outbound rule on the gateway SG |
| Every spawn returns 403 `Turnstile verification failed: invalid-input-secret` | Secret key copied wrong, OR an EU-side secret pasted into the ap-south-1 SSM param | Re-paste from Cloudflare dashboard; restart gateway |
| UI never renders the widget (just hangs on "Spinning…") | Site key wrong, OR ad-blocker is killing `challenges.cloudflare.com` script | Verify `VITE_TURNSTILE_SITE_KEY` in the bundle; UI falls back to empty token after 15 s timeout regardless |
| Genuine users complaining of "Verify you are human" repeatedly | Cloudflare scoring this widget aggressively (often the case for Tor / VPN users) | Move site to "Non-interactive" mode in Cloudflare dashboard; or contact Cloudflare support to whitelist the IP range if known-good |

---

## Revocation

If a key leaks (e.g., the secret accidentally landed in the public deploy
bundle), revoke it in the Cloudflare dashboard (**Turnstile → site → Rotate
keys**), then update the SSM secret + redeploy. The old secret stops
working within ~30 seconds.
