import { describe, expect, it } from 'vitest';
import { titleForPath, BASE_TITLE } from './pageTitles';

describe('titleForPath', () => {
  it('returns base for null / empty / unmatched', () => {
    expect(titleForPath(null)).toBe(BASE_TITLE);
    expect(titleForPath('')).toBe(BASE_TITLE);
    expect(titleForPath('/no-such-route')).toBe(BASE_TITLE);
  });

  it('returns the labelled title for common routes', () => {
    expect(titleForPath('/dashboard')).toBe('Dashboard · Aegis');
    expect(titleForPath('/incidents')).toBe('Incidents · Aegis');
    expect(titleForPath('/settings')).toBe('Settings · Aegis');
    expect(titleForPath('/compliance')).toBe('Compliance · Aegis');
  });

  it('strips trailing slashes before lookup', () => {
    expect(titleForPath('/dashboard/')).toBe('Dashboard · Aegis');
    expect(titleForPath('/settings///')).toBe('Settings · Aegis');
  });

  it('falls back to the longest-prefix label for dynamic segments', () => {
    expect(titleForPath('/agents/abc-123')).toBe('Agent · Aegis');
    expect(titleForPath('/team/alice@acme.com')).toBe('Team member · Aegis');
    expect(titleForPath('/replay/req-42')).toBe('Replay · Aegis');
  });

  it('picks static match over prefix when both would apply', () => {
    // /agents (exact) exists AND /agents/ is a prefix. Exact must win so the
    // Agents list page reads "Agents · Aegis", not "Agent · Aegis".
    expect(titleForPath('/agents')).toBe('Agents · Aegis');
  });

  it('handles login sub-routes via prefix', () => {
    expect(titleForPath('/login/verify-email-address')).toBe('Sign in · Aegis');
    expect(titleForPath('/signup/factor-two')).toBe('Create workspace · Aegis');
  });
});
