// Sprint S3 (2026-06-19) — Vendor-card grid SIEM settings.
//
// Replaces the prior single paste-form (Splunk + Datadog only) with a
// grid of <SiemVendorCard /> components, one per vendor returned by
// GET /siem/vendors. The router supports 5 vendors today (Splunk,
// Datadog, Elastic, Sentinel, Chronicle); adding a 6th is a backend
// change only — the UI follows.

import React, { useCallback, useEffect, useState } from 'react'
import { Database, Loader2, AlertTriangle, RefreshCw } from 'lucide-react'
import { siemService } from '../services/api'
import { useRole } from '../hooks/useRole'
import { SiemVendorCard } from '../components/SiemVendorCard'

export default function SiemSettings() {
  const { isOwner, isAdmin } = useRole()
  const canMutate = isOwner || isAdmin

  const [vendors, setVendors] = useState([])
  const [savedConfigs, setSavedConfigs] = useState({})
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [error, setError] = useState('')

  const loadAll = useCallback(async () => {
    setLoading(true)
    setLoadError(false)
    try {
      const [vendorResp, configResp] = await Promise.all([
        siemService.vendors(),
        siemService.getConfig().catch(() => ({ data: {} })),
      ])
      const vList = (vendorResp?.data || vendorResp || {}).vendors || []
      const cfg = configResp?.data || configResp || {}
      setVendors(vList)
      setSavedConfigs(cfg.vendors || {})
    } catch {
      setLoadError(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadAll() }, [loadAll])

  const handleSave = useCallback(async (vendorId, creds) => {
    setError('')
    try {
      await siemService.saveConfig({ vendor: vendorId, credentials: creds })
      setSavedConfigs((m) => ({ ...m, [vendorId]: creds }))
    } catch (err) {
      setError(err.message || 'Save failed')
    }
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="animate-spin text-neutral-500" size={24} />
      </div>
    )
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white mb-1 flex items-center gap-2">
            <Database size={22} /> SIEM Forwarders
          </h1>
          <p className="text-sm text-neutral-500">
            Forward every audit row to your SIEM. Aegis writes the cryptographic
            evidence; your SIEM keeps its place as the on-call system of record.
          </p>
        </div>
      </header>

      {loadError && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-xs text-amber-300 flex items-center gap-2">
          <AlertTriangle size={14} />
          <span>Couldn't load vendor metadata.</span>
          <button
            onClick={loadAll}
            className="ml-auto flex items-center gap-1.5 px-3 py-1 rounded-md border border-amber-500/30 text-xs text-amber-300 hover:bg-amber-500/10"
          >
            <RefreshCw size={11} /> Retry
          </button>
        </div>
      )}

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-xs text-red-300">
          {error}
        </div>
      )}

      {!canMutate && (
        <div className="bg-neutral-900 border border-[var(--border-subtle)] rounded-lg p-3 text-xs text-neutral-400">
          SIEM forwarding can only be configured by Owners or Admins.
        </div>
      )}

      <div className="grid grid-cols-1 gap-4">
        {vendors.map((vendor) => (
          <SiemVendorCard
            key={vendor.id}
            vendor={vendor}
            savedConfig={savedConfigs[vendor.id]}
            onSave={canMutate ? handleSave : (() => {})}
          />
        ))}
      </div>

      <p className="text-xs text-neutral-600 leading-relaxed pt-2">
        Test Connection fires one synthetic event marked
        <code className="text-neutral-400 mx-1">action=aegis_siem_connection_test</code>
        + <code className="text-neutral-400 mx-1">decision=monitor</code>.
        Filter on those fields in your SIEM to suppress them from production
        dashboards.
      </p>
    </div>
  )
}
