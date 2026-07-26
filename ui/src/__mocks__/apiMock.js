// Shared mock scaffolding for services/api. Each service export is a plain
// object of async functions returning an empty payload. Smoke tests import
// this via vi.mock('../services/api', () => import('../__mocks__/apiMock'));
// so a page render doesn't blow up on a missing endpoint.

const ok = () => Promise.resolve({ data: {}, items: [], total: 0 });
const list = () => Promise.resolve({ data: { items: [], total: 0 } });

const stubService = new Proxy(
  {},
  {
    get: () => (..._args) => ok(),
  },
);

// Named exports mirror the 48 service groups in services/api.js. Anything
// not enumerated here is caught by the module-level export loop below.
export const authService              = stubService;
export const api                      = stubService;
export const workspaceService         = new Proxy({}, { get: () => () => Promise.resolve({}) });
export const iagService               = stubService;
export const remediationService       = stubService;
export const auditService             = new Proxy({}, { get: () => () => list() });
export const registryService          = new Proxy({}, { get: () => () => list() });
export const forensicsService         = stubService;
export const playgroundService        = stubService;
export const dashboardService         = new Proxy({}, { get: () => () => Promise.resolve({ data: {} }) });
export const approvalService          = new Proxy({}, { get: () => () => list() });
export const replayService            = stubService;
export const policyService            = stubService;
export const socService               = stubService;
export const incidentService          = new Proxy({}, { get: () => () => list() });
export const autoResponseService      = stubService;
export const graphService             = stubService;
export const policyPlaygroundService  = stubService;
export const shadowService            = stubService;
export const evaluationService        = stubService;
export const fleetService             = stubService;
export const flightService            = stubService;
export const receiptService           = stubService;
export const transparencyService      = stubService;
export const tenantService            = stubService;
export const tenantSettingsService    = stubService;
export const lifecycleService         = stubService;
export const witnessService           = stubService;
export const autonomyService          = stubService;
export const complianceService        = stubService;
export const playbookService          = stubService;
export const webhookService           = stubService;
export const siemService              = stubService;
export const scheduledReportsService  = stubService;
export const threatIntelService       = stubService;
export const notificationService      = new Proxy({}, { get: () => () => Promise.resolve({ data: { unread: 0 } }) });
export const ssoService               = stubService;
export const decisionService          = stubService;
export const killSwitchService        = stubService;
export const auditExportService       = stubService;
export const userService              = stubService;
export const adminService             = stubService;
export const teamService              = stubService;
export const scimService              = stubService;
export const integrationsService      = stubService;

export const setSessionMetadata   = () => {};
export const clearSessionMetadata = () => {};
export const parseApiError = (e) => (e && e.message) || 'error';
