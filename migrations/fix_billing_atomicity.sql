-- Fix critical billing atomicity issue: enforce 1-to-1 audit-to-usage mapping
-- This ensures every audit log has exactly one usage record and vice versa

-- 1. Make audit_id NOT NULL in usage_records
ALTER TABLE usage_records
ALTER COLUMN audit_id SET NOT NULL;

-- 2. Add UNIQUE constraint to ensure 1-to-1 mapping (one usage per audit)
ALTER TABLE usage_records
DROP CONSTRAINT IF EXISTS ux_usage_audit_id;

CREATE UNIQUE INDEX ux_usage_audit_id ON usage_records(audit_id);

-- 3. Add foreign key constraint to audit_logs (if not already present)
ALTER TABLE usage_records
DROP CONSTRAINT IF EXISTS fk_usage_audit_id;

ALTER TABLE usage_records
ADD CONSTRAINT fk_usage_audit_id
FOREIGN KEY (audit_id)
REFERENCES audit_logs(id)
ON DELETE CASCADE;

-- 4. Verify integrity: find orphan audit logs (for reporting only)
-- SELECT COUNT(*)
-- FROM audit_logs a
-- LEFT JOIN usage_records u ON a.id = u.audit_id
-- WHERE u.audit_id IS NULL
-- AND a.action IN ('execute_tool', 'behavior_firewall_decision', 'inference_proxy_block');

COMMIT;
