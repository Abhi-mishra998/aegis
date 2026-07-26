import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  parseApiError,
  setSessionMetadata,
  clearSessionMetadata,
} from './api';

// api.js also exports request() / blobRequest() but they touch fetch +
// Clerk + auth events and are exercised implicitly by the smoke tests.
// Here we cover the pure helpers, which are the ones every consumer imports.

describe('parseApiError', () => {
  it('returns fallback for undefined error', () => {
    expect(parseApiError(undefined, 'default')).toBe('default');
  });

  it('surfaces error.message when present', () => {
    expect(parseApiError(new Error('boom'))).toBe('boom');
  });

  it('collapses a 429 error into the rate-limit copy', () => {
    const e = new Error('API Error');
    e._status = 429;
    expect(parseApiError(e)).toMatch(/rate limit/i);
  });

  it('collapses a 5xx into the server-error copy', () => {
    const e = new Error('API Error');
    e._status = 503;
    expect(parseApiError(e)).toMatch(/server error/i);
  });

  it('extracts pydantic 422 validation msg list into a semicolon string', () => {
    const e = Object.assign(new Error('API Error'), {
      _status: 422,
      _body: { detail: [{ msg: 'field a required' }, { msg: 'field b invalid' }] },
    });
    expect(parseApiError(e)).toContain('field a required');
    expect(parseApiError(e)).toContain('field b invalid');
  });

  it('maps 401 realm slugs to friendly copy', () => {
    const e401 = Object.assign(new Error(''), {
      _status: 401,
      _wwwAuth: 'Bearer realm="session_expired"',
    });
    expect(parseApiError(e401)).toMatch(/session expired/i);
  });

  it('403 → "access denied" copy', () => {
    const e = Object.assign(new Error(''), { _status: 403 });
    expect(parseApiError(e)).toMatch(/access denied/i);
  });
});

describe('setSessionMetadata / clearSessionMetadata', () => {
  beforeEach(() => sessionStorage.clear());
  afterEach(() => sessionStorage.clear());

  it('setSessionMetadata writes tenant_id / role / expiry / email', () => {
    setSessionMetadata({
      tenant_id: 't1',
      user_email: 'x@y.z',
      role: 'admin',
      agent_id: 'a1',
      expires_in: 60,
    });
    expect(sessionStorage.getItem('tenant_id')).toBe('t1');
    expect(sessionStorage.getItem('user_email')).toBe('x@y.z');
    expect(sessionStorage.getItem('user_role')).toBe('ADMIN'); // uppercased
    expect(sessionStorage.getItem('agent_id')).toBe('a1');
    // expiry is now + expires_in * 1000
    const exp = parseInt(sessionStorage.getItem('acp_token_expiry'), 10);
    expect(exp).toBeGreaterThan(Date.now());
  });

  it('clearSessionMetadata removes every session key', () => {
    setSessionMetadata({ tenant_id: 't', user_email: 'e', role: 'r', expires_in: 60 });
    clearSessionMetadata();
    expect(sessionStorage.getItem('tenant_id')).toBeNull();
    expect(sessionStorage.getItem('user_email')).toBeNull();
    expect(sessionStorage.getItem('user_role')).toBeNull();
    expect(sessionStorage.getItem('acp_token_expiry')).toBeNull();
  });
});
