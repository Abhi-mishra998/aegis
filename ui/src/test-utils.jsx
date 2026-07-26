import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthContext } from './context/AuthContext';
import { AgentContext } from './context/AgentContext';

/**
 * Test harness: wraps the component in the providers a real Aegis page needs.
 * Every page reads useAuth() at least once (for role gating) — without this
 * wrapper, `useAuth` returns undefined and every `.role` / `.tenant_id`
 * dereference throws.
 *
 * Usage:
 *   renderWithProviders(<Dashboard />)
 *   renderWithProviders(<Team />, { route: '/team', auth: { role: 'ADMIN' } })
 */
const defaultAuth = {
  isAuthenticated: true,
  user: 'test@aegis.local',
  tenant_id: '00000000-0000-0000-0000-000000000001',
  token: null,
  role: 'ADMIN',
  toasts: [],
  updateAuth: () => {},
  addToast: () => {},
  removeToast: () => {},
};

const defaultAgentCtx = {
  agents: [],
  selectedAgentId: null,
  selectedAgent: null,
  agentsLoading: false,
  sseConnected: false,
  fetchAgents: async () => {},
  setSelectedAgentId: () => {},
  refreshAgents: async () => {},
};

export function renderWithProviders(ui, options = {}) {
  const {
    route  = '/',
    auth   = {},
    agent  = {},
    ...rest
  } = options;
  const authValue  = { ...defaultAuth, ...auth };
  const agentValue = { ...defaultAgentCtx, ...agent };
  return render(
    <MemoryRouter initialEntries={[route]}>
      <AuthContext.Provider value={authValue}>
        <AgentContext.Provider value={agentValue}>
          {ui}
        </AgentContext.Provider>
      </AuthContext.Provider>
    </MemoryRouter>,
    rest,
  );
}
