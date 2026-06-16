import React, { useEffect, useState } from 'react';
import {
  AlertOctagon,
  CheckCircle2,
  CreditCard,
  ExternalLink,
  Sparkles,
} from 'lucide-react';
import { billingService } from '../../services/api';
import Button from '../Common/Button';
import Card from '../Common/Card';

const TIER_LABELS = {
  basic:      { label: 'Free',       hint: 'Up to 1k requests/day · 1 workspace' },
  starter:    { label: 'Starter',    hint: 'Default fallback when billing lapses' },
  pro:        { label: 'Pro',        hint: 'Up to 1M requests/day · per-agent quotas' },
  enterprise: { label: 'Enterprise', hint: 'Custom limits · SOC2 audit channel · SSO' },
};

/**
 * Sprint 9 — Plan & Upgrade card.
 *
 * Reads /billing/plan to surface the current tier; offers Upgrade or
 * Manage Billing buttons that POST to /billing/checkout-session and
 * /billing/portal-session. Redirects the browser to the Stripe-hosted
 * URL — Stripe handles the rest.
 *
 * When STRIPE_SECRET_KEY isn't set on prod (e.g. during initial
 * deploy), /billing/plan responds with stripe_configured=false and we
 * show a "Stripe not configured" hint instead of the buttons.
 */
export default function PlanCard() {
  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    billingService
      .getPlan()
      .then((resp) => {
        if (cancelled) return;
        setPlan(resp?.data || resp || null);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err?.message || 'Failed to load plan');
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleUpgrade = async (tier) => {
    setBusy(true);
    setError('');
    try {
      const resp = await billingService.createCheckoutSession(tier);
      const data = resp?.data || resp || {};
      if (data.url) {
        window.location.href = data.url;
      } else {
        setError('Stripe returned no checkout URL');
      }
    } catch (err) {
      setError(err?.message || 'Could not start checkout');
    } finally {
      setBusy(false);
    }
  };

  const handlePortal = async () => {
    setBusy(true);
    setError('');
    try {
      // Customer ID lookup is a Phase-6 enhancement — the backend
      // expects one. For now we show a 409-style hint if missing.
      const customerId = window.prompt(
        'Enter your Stripe customer_id (starts with cus_…) to open the billing portal.',
      );
      if (!customerId) {
        setBusy(false);
        return;
      }
      const resp = await billingService.createPortalSession(customerId.trim());
      const data = resp?.data || resp || {};
      if (data.url) window.location.href = data.url;
    } catch (err) {
      setError(err?.message || 'Could not open billing portal');
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <Card title="Plan" icon={CreditCard}>
        <p className="text-[11px] text-neutral-500">Loading…</p>
      </Card>
    );
  }

  const tier = plan?.tier || 'basic';
  const tierMeta = TIER_LABELS[tier] || { label: tier, hint: '' };
  const stripeConfigured = !!plan?.stripe_configured;
  const upgrades = plan?.available_upgrades || [];

  return (
    <Card title="Plan" icon={CreditCard}>
      <div className="space-y-4">
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <div className="space-y-0.5">
            <div className="text-[10px] uppercase tracking-widest text-neutral-500">
              Current tier
            </div>
            <div className="text-2xl font-bold text-white flex items-center gap-2">
              {tierMeta.label}
              {tier === 'pro' && <Sparkles size={18} className="text-amber-400" aria-hidden="true" />}
            </div>
            <p className="text-[11px] text-neutral-500">{tierMeta.hint}</p>
          </div>
          <div className="text-[10px] text-neutral-600 uppercase tracking-widest">
            {stripeConfigured ? (
              <span className="flex items-center gap-1 text-green-400">
                <CheckCircle2 size={11} aria-hidden="true" /> Stripe live
              </span>
            ) : (
              <span className="flex items-center gap-1 text-amber-400">
                <AlertOctagon size={11} aria-hidden="true" /> Stripe not configured
              </span>
            )}
          </div>
        </div>

        {error && (
          <div className="flex items-start gap-2 text-[11px] text-red-400">
            <AlertOctagon size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}

        {stripeConfigured && (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
              {upgrades.map(({ tier: t }) => (
                <Button
                  key={t}
                  size="sm"
                  onClick={() => handleUpgrade(t)}
                  disabled={busy || tier === t}
                >
                  {tier === t ? 'On this plan' : `Upgrade to ${TIER_LABELS[t]?.label || t}`}
                  {tier !== t && <ExternalLink size={11} aria-hidden="true" />}
                </Button>
              ))}
              {upgrades.length === 0 && (
                <span className="text-[11px] text-neutral-500 italic">
                  No upgrade prices configured.
                </span>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={handlePortal}
                disabled={busy}
              >
                Manage billing
                <ExternalLink size={11} aria-hidden="true" />
              </Button>
            </div>
            <p className="text-[10px] text-neutral-600 leading-snug">
              Upgrade opens a Stripe-hosted checkout — your card never touches
              Aegis. After successful payment Stripe fires the webhook
              (services/gateway/routers/stripe_webhook.py) and your workspace
              tier flips on the next request.
            </p>
          </div>
        )}
      </div>
    </Card>
  );
}
