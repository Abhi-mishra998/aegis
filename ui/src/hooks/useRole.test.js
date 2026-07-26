import { describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';

vi.mock('./useAuth', () => ({
  useAuth: vi.fn(),
}));

import { useRole } from './useRole';
import { useAuth } from './useAuth';

describe('useRole role-ladder projection', () => {
  it('OWNER → isOwner + isAdmin + canExitShadowMode all true', () => {
    useAuth.mockReturnValue({ role: 'OWNER' });
    const { result } = renderHook(() => useRole());
    expect(result.current.isOwner).toBe(true);
    expect(result.current.isAdmin).toBe(true);
    expect(result.current.canExitShadowMode).toBe(true);
    expect(result.current.canViewKillSwitch).toBe(true);
  });

  it('ADMIN → isAdmin true, isOwner false, canExitShadowMode false', () => {
    useAuth.mockReturnValue({ role: 'ADMIN' });
    const { result } = renderHook(() => useRole());
    expect(result.current.isAdmin).toBe(true);
    expect(result.current.isOwner).toBe(false);
    expect(result.current.canExitShadowMode).toBe(false);
  });

  it('legacy VIEWER/AUDITOR/SECURITY project onto canonical vocab', () => {
    useAuth.mockReturnValue({ role: 'VIEWER' });
    let { result } = renderHook(() => useRole());
    expect(result.current.role).toBe('READ_ONLY');
    expect(result.current.isReadOnly).toBe(true);

    useAuth.mockReturnValue({ role: 'AUDITOR' });
    result = renderHook(() => useRole()).result;
    expect(result.current.role).toBe('READ_ONLY');
    expect(result.current.isAuditor).toBe(true);

    useAuth.mockReturnValue({ role: 'SECURITY' });
    result = renderHook(() => useRole()).result;
    expect(result.current.role).toBe('SECURITY_ANALYST');
    expect(result.current.isSecurityAnalyst).toBe(true);
  });

  it('missing role falls back to READ_ONLY (least privilege)', () => {
    useAuth.mockReturnValue({ role: null });
    const { result } = renderHook(() => useRole());
    expect(result.current.role).toBe('READ_ONLY');
    expect(result.current.isAdmin).toBe(false);
    expect(result.current.canMutate).toBe(false);
  });
});
