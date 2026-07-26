import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/react';
import { useHotkeys, formatHotkey } from './useHotkeys';

function Probe({ bindings }) {
  useHotkeys(bindings);
  return <div>probe</div>;
}

describe('useHotkeys', () => {
  afterEach(() => cleanup());

  it('fires a single-key binding when nothing is focused', () => {
    const handler = vi.fn();
    render(<Probe bindings={[{ key: '?', handler }]} />);
    fireEvent.keyDown(document, { key: '?' });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('fires a mod+key chord', () => {
    const handler = vi.fn();
    render(<Probe bindings={[{ key: 'mod+k', handler }]} />);
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('fires a two-key sequence (g then p)', () => {
    const handler = vi.fn();
    render(<Probe bindings={[{ key: 'g p', handler }]} />);
    fireEvent.keyDown(document, { key: 'g' });
    fireEvent.keyDown(document, { key: 'p' });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('does NOT fire single-key bindings while typing in an input', () => {
    const handler = vi.fn();
    render(
      <>
        <Probe bindings={[{ key: '?', handler }]} />
        <input data-testid="text" />
      </>,
    );
    const input = document.querySelector('input');
    input.focus();
    fireEvent.keyDown(input, { key: '?' });
    expect(handler).not.toHaveBeenCalled();
  });
});

describe('formatHotkey', () => {
  it('returns empty for null', () => {
    expect(formatHotkey('')).toBe('');
    expect(formatHotkey(null)).toBe('');
  });

  it('uppercases single-character keys', () => {
    // Cross-platform: mac shows glyphs, others show Ctrl/Alt — but a plain
    // single-key `k` is always uppercased.
    expect(formatHotkey('k')).toBe('K');
  });

  it('joins two-key sequences with a double-space separator', () => {
    expect(formatHotkey('g p')).toContain('  ');
    expect(formatHotkey('g p')).toContain('G');
    expect(formatHotkey('g p')).toContain('P');
  });
});
