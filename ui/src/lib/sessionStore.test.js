import { beforeEach, describe, expect, it } from 'vitest';
import { getSessionItem, setSessionItem, removeSessionItem } from './sessionStore';

describe('sessionStore', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('round-trips a value', () => {
    setSessionItem('foo', 'bar');
    expect(getSessionItem('foo')).toBe('bar');
  });

  it('returns null for a missing key (not undefined)', () => {
    // Consumers rely on `?? '0'` semantics — undefined would break `expiry`
    // parsing in App.jsx / api.js.
    expect(getSessionItem('does-not-exist')).toBeNull();
  });

  it('removeSessionItem clears the key', () => {
    setSessionItem('gone', 'here');
    removeSessionItem('gone');
    expect(getSessionItem('gone')).toBeNull();
  });
});
