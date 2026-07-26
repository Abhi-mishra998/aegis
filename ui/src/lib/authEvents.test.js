import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { emitAuthFailure, onAuthFailure } from './authEvents';

describe('authEvents', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });
  afterEach(() => {
    sessionStorage.clear();
  });

  it('delivers auth-failure events to a subscribed handler', () => {
    const handler = vi.fn();
    const off = onAuthFailure(handler);
    emitAuthFailure({ reason: 'unauthorized', url: '/foo', statusCode: 401 });
    expect(handler).toHaveBeenCalledTimes(1);
    const detail = handler.mock.calls[0][0].detail;
    expect(detail.reason).toBe('unauthorized');
    expect(detail.url).toBe('/foo');
    expect(detail.statusCode).toBe(401);
    expect(detail.reasonLabel).toBe('Unauthorized Access');
    off();
  });

  it('maps known reasons to friendly labels', () => {
    const handler = vi.fn();
    const off = onAuthFailure(handler);
    emitAuthFailure({ reason: 'session_expired' });
    expect(handler.mock.calls[0][0].detail.reasonLabel).toBe('Session Expired');
    off();
  });

  it('unsubscribe cleanup stops delivery', () => {
    const handler = vi.fn();
    const off = onAuthFailure(handler);
    off();
    emitAuthFailure({ reason: 'unauthorized' });
    expect(handler).not.toHaveBeenCalled();
  });

  it('suppresses events when session_kind=demo (no SOC overlay for demo users)', () => {
    sessionStorage.setItem('session_kind', 'demo');
    const handler = vi.fn();
    const off = onAuthFailure(handler);
    emitAuthFailure({ reason: 'unauthorized' });
    expect(handler).not.toHaveBeenCalled();
    off();
  });
});
