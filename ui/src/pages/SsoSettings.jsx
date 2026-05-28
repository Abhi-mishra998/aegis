import React, { useCallback, useEffect, useState } from 'react'
import {
  Shield, Save, Play, CheckCircle2, XCircle,
  Loader2, AlertCircle, Eye, EyeOff, ChevronDown,
  Lock, Globe, Key, AlertTriangle, RefreshCw,
} from 'lucide-react'
import { ssoService } from '../services/api'

const PROVIDER_TYPES = [
  { value: 'saml',  label: 'SAML 2.0',   desc: 'Okta, Azure AD, ADFS, OneLogin' },
  { value: 'oidc',  label: 'OIDC',        desc: 'Auth0, Okta, Google Workspace' },
]

function SecretInput({ id, label, placeholder, value, onChange, rows }) {
  const [show, setShow] = useState(false)
  if (rows) {
    return (
      <div>
        <div className="flex items-center justify-between mb-1">
          <label htmlFor={id} className="text-xs text-neutral-400">{label}</label>
          <button type="button" onClick={() => setShow(v => !v)} className="text-[10px] text-neutral-500 hover:text-white flex items-center gap-1">
            {show ? <EyeOff size={11} /> : <Eye size={11} />} {show ? 'Hide' : 'Show'}
          </button>
        </div>
        <textarea
          id={id}
          rows={rows}
          value={show ? value : value ? '•'.repeat(Math.min(value.length, 40)) : ''}
          onChange={e => onChange(e.target.value)}
          onFocus={() => setShow(true)}
          placeholder={placeholder}
          className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-xs text-white placeholder-neutral-600 focus:outline-none focus:border-white/20 font-mono resize-none"
        />
      </div>
    )
  }
  return (
    <div>
      <label htmlFor={id} className="block text-xs text-neutral-400 mb-1">{label}</label>
      <div className="relative">
        <input
          id={id}
          type={show ? 'text' : 'password'}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 pr-9 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-white/20"
        />
        <button type="button" onClick={() => setShow(v => !v)} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-neutral-500 hover:text-white">
          {show ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
      </div>
    </div>
  )
}

export default function SsoSettings() {
  const [cfg, setCfg] = useState({
    provider_type: 'saml',
    entity_id: '', sso_url: '', certificate: '',
    client_id: '', client_secret: '', issuer: '',
  })
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
        if (c.provider_type) setCfg(prev => ({ ...prev, ...c }))
      })
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { loadConfig() }, [loadConfig])

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      // Strip out masked values (***...) that came from the API — sending them back
      // would overwrite the stored secret with the placeholder string.
      const payload = Object.fromEntries(
        Object.entries(cfg).filter(([, v]) => !String(v).startsWith('***'))
      )
      await ssoService.saveConfig(payload)
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

  const isSaml = cfg.provider_type === 'saml'

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
        <button
          onClick={save}
          disabled={saving}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white text-black text-sm font-medium hover:bg-neutral-200 disabled:opacity-50"
        >
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
          {saved ? 'Saved!' : 'Save'}
        </button>
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
              placeholder="https://your-idp.com/metadata"
              className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-white/20"
            />
          </div>
          <div>
            <label htmlFor="sso_url" className="block text-xs text-neutral-400 mb-1">SSO URL</label>
            <input
              id="sso_url"
              type="url"
              value={cfg.sso_url}
              onChange={e => setCfg(c => ({ ...c, sso_url: e.target.value }))}
              placeholder="https://your-idp.com/sso/saml"
              className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-white/20"
            />
          </div>
          <SecretInput
            id="certificate"
            label="X.509 Certificate (PEM)"
            placeholder="-----BEGIN CERTIFICATE-----\n..."
            value={cfg.certificate}
            onChange={v => setCfg(c => ({ ...c, certificate: v }))}
            rows={5}
          />
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
              placeholder="https://your-auth0.auth0.com/"
              className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-white/20"
            />
          </div>
          <div>
            <label htmlFor="client_id" className="block text-xs text-neutral-400 mb-1">Client ID</label>
            <input
              id="client_id"
              value={cfg.client_id}
              onChange={e => setCfg(c => ({ ...c, client_id: e.target.value }))}
              placeholder="your-client-id"
              className="w-full bg-white/[0.04] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-sm text-white placeholder-neutral-600 focus:outline-none focus:border-white/20"
            />
          </div>
          <SecretInput
            id="client_secret"
            label="Client Secret"
            placeholder="your-client-secret"
            value={cfg.client_secret}
            onChange={v => setCfg(c => ({ ...c, client_secret: v }))}
          />
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
