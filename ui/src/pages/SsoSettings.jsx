import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Save, Play, CheckCircle2, XCircle,
  Loader2, AlertCircle, Lock, Globe,
  AlertTriangle, RefreshCw,
} from 'lucide-react'
import { z } from 'zod'
import { ssoService } from '../services/api'
import { SecretInput } from '../components/Common/ConnectorPrimitives'
import { useRole } from '../hooks/useRole'
import useUnsavedChanges from '../hooks/useUnsavedChanges'

const PROVIDER_TYPES = [
  { value: 'saml',  label: 'SAML 2.0',   desc: 'Okta, Azure AD, ADFS, OneLogin' },
  { value: 'oidc',  label: 'OIDC',        desc: 'Auth0, Okta, Google Workspace' },
]

const INITIAL_CFG = {
  provider_type: 'saml',
  entity_id: '', sso_url: '', certificate: '',
  client_id: '', client_secret: '', issuer: '',
}

// Strings that look like API-returned masked secrets aren't fresh user input — skip validation.
const isMasked = (v) => typeof v === 'string' && v.startsWith('***')

const urlField = z.string().trim().min(1, 'Required').url('Must be a valid URL')

const samlSchema = z.object({
  provider_type: z.literal('saml'),
  entity_id: urlField,
  sso_url: urlField,
  certificate: z.string().trim().min(1, 'Certificate is required'),
})

const oidcSchema = z.object({
  provider_type: z.literal('oidc'),
  issuer: urlField,
  client_id: z.string().trim().min(1, 'Client ID is required'),
  client_secret: z.string().trim().min(1, 'Client secret is required'),
})

export default function SsoSettings() {
  const { isOwner, isAdmin } = useRole()
  const canMutate = isOwner || isAdmin
  const [cfg, setCfg] = useState(INITIAL_CFG)
  const [initialCfg, setInitialCfg] = useState(INITIAL_CFG)
  const [touched, setTouched] = useState({})
  const [saving, setSaving]       = useState(false)
  const [saved, setSaved]         = useState(false)
  const [testing, setTesting]     = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState('')
  const [loadError, setLoadError] = useState(false)
  const [providerTypes, setProviderTypes] = useState(PROVIDER_TYPES)

  const loadConfig = useCallback(() => {
    setLoading(true)
    setLoadError(false)
    ssoService.getProviders()
      .then(d => {
        const list = d?.data || d || []
        if (Array.isArray(list) && list.length > 0) setProviderTypes(list)
      })
      .catch(() => {})
    ssoService.getConfig()
      .then(d => {
        const c = d?.data || d || {}
        if (c.provider_type) {
          const merged = { ...INITIAL_CFG, ...c }
          setCfg(merged)
          setInitialCfg(merged)
          setTouched({})
        }
      })
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  const isSaml = cfg.provider_type === 'saml'

  // Masked secrets carry over from API as "***" — validate them as if filled so
  // an existing config doesn't show false errors after first load.
  const cfgForValidation = useMemo(() => ({
    ...cfg,
    certificate: isMasked(cfg.certificate) ? 'masked' : cfg.certificate,
    client_secret: isMasked(cfg.client_secret) ? 'masked' : cfg.client_secret,
  }), [cfg])

  const schema = isSaml ? samlSchema : oidcSchema
  const parsed = schema.safeParse(cfgForValidation)
  const fieldErrors = parsed.success ? {} : parsed.error.flatten().fieldErrors
  const isValid = parsed.success

  const dirty = useMemo(
    () => JSON.stringify(cfg) !== JSON.stringify(initialCfg),
    [cfg, initialCfg]
  )
  useUnsavedChanges(dirty && !saving)

  const showError = (key) => touched[key] && fieldErrors[key]?.[0]
  const markTouched = (key) => setTouched(t => t[key] ? t : { ...t, [key]: true })

  const save = async () => {
    setTouched({
      entity_id: true, sso_url: true, certificate: true,
      issuer: true, client_id: true, client_secret: true,
    })
    if (!isValid) return
    setSaving(true)
    setError('')
    try {
      const payload = Object.fromEntries(
        Object.entries(cfg).filter(([, v]) => !isMasked(v))
      )
      await ssoService.saveConfig(payload)
      setInitialCfg(cfg)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch {
      setError('Failed to save SSO configuration.')
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await ssoService.testConfig(cfg)
      setTestResult(res?.data || res)
    } catch (err) {
      setTestResult({ reachable: false, status: 'error', error: err.message })
    } finally {
      setTesting(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="animate-spin text-neutral-500" size={24} />
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white mb-1">SSO Configuration</h1>
          <p className="text-sm text-neutral-400">Configure SAML 2.0 or OIDC single sign-on for your organization.</p>
        </div>
        {canMutate && (
          <button
            onClick={save}
            disabled={saving || !isValid || !dirty}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white text-black text-sm font-medium hover:bg-neutral-200 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            {saved ? 'Saved!' : 'Save'}
          </button>
        )}
      </header>

      {error && (
        <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">
          <AlertCircle size={14} /> {error}
        </div>
      )}

      {loadError && (
        <div className="flex items-center justify-between gap-3 p-3 bg-amber-500/10 border border-amber-500/20 rounded-lg text-sm text-amber-400">
          <div className="flex items-center gap-2">
            <AlertTriangle size={14} />
            <span>Failed to load configuration. Form fields may be stale.</span>
          </div>
          <button
            type="button"
            onClick={loadConfig}
            className="flex items-center gap-1.5 px-3 py-1 rounded-md border border-amber-500/30 text-xs text-amber-300 hover:bg-amber-500/10"
          >
            <RefreshCw size={11} /> Retry
          </button>
        </div>
      )}

      {/* Protocol selector */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
        <h2 className="text-xs font-medium text-neutral-400 uppercase tracking-wider mb-3">Protocol</h2>
        <div className="grid grid-cols-2 gap-3">
          {providerTypes.map(pt => (
            <button
              key={pt.value}
              onClick={() => setCfg(c => ({ ...c, provider_type: pt.value }))}
              className={`p-4 rounded-xl border text-left transition-all ${cfg.provider_type === pt.value ? 'border-white/30 bg-white/[0.06]' : 'border-[var(--border-subtle)] hover:border-white/20'}`}
            >
              <div className="flex items-center gap-2 mb-1">
                <Lock size={13} className={cfg.provider_type === pt.value ? 'text-white' : 'text-neutral-500'} />
                <span className={`text-sm font-medium ${cfg.provider_type === pt.value ? 'text-white' : 'text-neutral-400'}`}>{pt.label}</span>
              </div>
              <div className="text-xs text-neutral-600">{pt.desc}</div>
            </button>
          ))}
        </div>
      </div>

      {/* SAML fields */}
      {isSaml && (
        <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5 space-y-4">
          <h2 className="text-xs font-medium text-neutral-400 uppercase tracking-wider">SAML Settings</h2>
          <div>
            <label htmlFor="entity_id" className="block text-xs text-neutral-400 mb-1">Entity ID / Metadata URL</label>
            <input
              id="entity_id"
              type="url"
              value={cfg.entity_id}
              onChange={e => setCfg(c => ({ ...c, entity_id: e.target.value }))}
              onBlur={() => markTouched('entity_id')}
              placeholder="https://your-idp.com/metadata"
              aria-invalid={!!showError('entity_id')}
              aria-describedby={showError('entity_id') ? 'entity_id_err' : undefined}
              className={`w-full bg-white/[0.04] border rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none ${showError('entity_id') ? 'border-red-500/50 focus:border-red-500/70' : 'border-[var(--border-subtle)] focus:border-white/20'}`}
            />
            {showError('entity_id') && (
              <p id="entity_id_err" className="mt-1 flex items-center gap-1 text-[11px] text-red-400">
                <AlertCircle size={11} /> {showError('entity_id')}
              </p>
            )}
          </div>
          <div>
            <label htmlFor="sso_url" className="block text-xs text-neutral-400 mb-1">SSO URL</label>
            <input
              id="sso_url"
              type="url"
              value={cfg.sso_url}
              onChange={e => setCfg(c => ({ ...c, sso_url: e.target.value }))}
              onBlur={() => markTouched('sso_url')}
              placeholder="https://your-idp.com/sso/saml"
              aria-invalid={!!showError('sso_url')}
              aria-describedby={showError('sso_url') ? 'sso_url_err' : undefined}
              className={`w-full bg-white/[0.04] border rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none ${showError('sso_url') ? 'border-red-500/50 focus:border-red-500/70' : 'border-[var(--border-subtle)] focus:border-white/20'}`}
            />
            {showError('sso_url') && (
              <p id="sso_url_err" className="mt-1 flex items-center gap-1 text-[11px] text-red-400">
                <AlertCircle size={11} /> {showError('sso_url')}
              </p>
            )}
          </div>
          <div>
            <SecretInput
              id="certificate"
              label="X.509 Certificate (PEM)"
              placeholder="-----BEGIN CERTIFICATE-----\n..."
              value={cfg.certificate}
              onChange={v => {
                setCfg(c => ({ ...c, certificate: v }))
                markTouched('certificate')
              }}
              rows={5}
            />
            {showError('certificate') && (
              <p className="mt-1 flex items-center gap-1 text-[11px] text-red-400">
                <AlertCircle size={11} /> {showError('certificate')}
              </p>
            )}
          </div>
        </div>
      )}

      {/* OIDC fields */}
      {!isSaml && (
        <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5 space-y-4">
          <h2 className="text-xs font-medium text-neutral-400 uppercase tracking-wider">OIDC Settings</h2>
          <div>
            <label htmlFor="issuer" className="block text-xs text-neutral-400 mb-1">Issuer URL</label>
            <input
              id="issuer"
              type="url"
              value={cfg.issuer}
              onChange={e => setCfg(c => ({ ...c, issuer: e.target.value }))}
              onBlur={() => markTouched('issuer')}
              placeholder="https://your-auth0.auth0.com/"
              aria-invalid={!!showError('issuer')}
              aria-describedby={showError('issuer') ? 'issuer_err' : undefined}
              className={`w-full bg-white/[0.04] border rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none ${showError('issuer') ? 'border-red-500/50 focus:border-red-500/70' : 'border-[var(--border-subtle)] focus:border-white/20'}`}
            />
            {showError('issuer') && (
              <p id="issuer_err" className="mt-1 flex items-center gap-1 text-[11px] text-red-400">
                <AlertCircle size={11} /> {showError('issuer')}
              </p>
            )}
          </div>
          <div>
            <label htmlFor="client_id" className="block text-xs text-neutral-400 mb-1">Client ID</label>
            <input
              id="client_id"
              value={cfg.client_id}
              onChange={e => setCfg(c => ({ ...c, client_id: e.target.value }))}
              onBlur={() => markTouched('client_id')}
              placeholder="your-client-id"
              aria-invalid={!!showError('client_id')}
              aria-describedby={showError('client_id') ? 'client_id_err' : undefined}
              className={`w-full bg-white/[0.04] border rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none ${showError('client_id') ? 'border-red-500/50 focus:border-red-500/70' : 'border-[var(--border-subtle)] focus:border-white/20'}`}
            />
            {showError('client_id') && (
              <p id="client_id_err" className="mt-1 flex items-center gap-1 text-[11px] text-red-400">
                <AlertCircle size={11} /> {showError('client_id')}
              </p>
            )}
          </div>
          <div>
            <SecretInput
              id="client_secret"
              label="Client Secret"
              placeholder="your-client-secret"
              value={cfg.client_secret}
              onChange={v => {
                setCfg(c => ({ ...c, client_secret: v }))
                markTouched('client_secret')
              }}
            />
            {showError('client_secret') && (
              <p className="mt-1 flex items-center gap-1 text-[11px] text-red-400">
                <AlertCircle size={11} /> {showError('client_secret')}
              </p>
            )}
          </div>
        </div>
      )}

      {/* Test connectivity */}
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-5">
        <h2 className="text-xs font-medium text-neutral-400 uppercase tracking-wider mb-3">Connectivity Test</h2>
        <p className="text-xs text-neutral-500 mb-3">
          {isSaml
            ? 'Fetches the identity provider metadata URL to verify it is reachable and returns valid SAML metadata.'
            : 'Fetches the OIDC discovery document from the issuer URL to verify it is reachable.'}
        </p>
        <div className="flex items-center gap-3">
          <button
            onClick={test}
            disabled={testing || (!cfg.entity_id && !cfg.issuer)}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg border border-[var(--border-subtle)] text-sm text-neutral-300 hover:border-white/20 disabled:opacity-40"
          >
            {testing ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            Test connection
          </button>
          {testResult && (
            <span className={`flex items-center gap-1.5 text-xs ${testResult.reachable ? 'text-green-400' : 'text-red-400'}`}>
              {testResult.reachable ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
              {testResult.reachable ? `Reachable — ${testResult.issuer || testResult.status}` : testResult.error || 'Unreachable'}
            </span>
          )}
        </div>
      </div>

      {/* ACS URL */}
      <div className="p-4 bg-white/[0.02] border border-[var(--border-subtle)] rounded-xl text-xs space-y-2">
        <div className="flex items-center gap-2 text-neutral-400 font-medium">
          <Globe size={12} /> Configure in your IdP
        </div>
        <div className="space-y-1 text-neutral-500">
          {isSaml ? (
            <>
              <div><span className="text-neutral-400">ACS URL:</span> <code className="text-neutral-300">{window.location.origin}/auth/sso/saml/callback</code></div>
              <div><span className="text-neutral-400">Entity ID:</span> <code className="text-neutral-300">{window.location.origin}/auth/sso/saml/metadata</code></div>
            </>
          ) : (
            <div><span className="text-neutral-400">Redirect URI:</span> <code className="text-neutral-300">{window.location.origin}/auth/sso/oidc/callback</code></div>
          )}
        </div>
      </div>
    </div>
  )
}
