import { describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import useUnsavedChanges from './useUnsavedChanges';

describe('useUnsavedChanges', () => {
  it('does nothing when dirty is false', () => {
    const addSpy = vi.spyOn(window, 'addEventListener');
    renderHook(() => useUnsavedChanges(false));
    expect(addSpy).not.toHaveBeenCalledWith('beforeunload', expect.any(Function));
    addSpy.mockRestore();
  });

  it('attaches a beforeunload listener when dirty is true', () => {
    const addSpy = vi.spyOn(window, 'addEventListener');
    const removeSpy = vi.spyOn(window, 'removeEventListener');
    const { unmount } = renderHook(() => useUnsavedChanges(true));
    expect(addSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function));
    unmount();
    expect(removeSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function));
    addSpy.mockRestore();
    removeSpy.mockRestore();
  });

  it('handler sets returnValue on the event (browser confirm-leave)', () => {
    let capturedHandler;
    const addSpy = vi.spyOn(window, 'addEventListener').mockImplementation((evt, h) => {
      if (evt === 'beforeunload') capturedHandler = h;
    });
    renderHook(() => useUnsavedChanges(true, 'Custom leave?'));
    expect(capturedHandler).toBeDefined();
    const e = { preventDefault: vi.fn() };
    const result = capturedHandler(e);
    expect(e.preventDefault).toHaveBeenCalled();
    expect(e.returnValue).toBe('Custom leave?');
    expect(result).toBe('Custom leave?');
    addSpy.mockRestore();
  });
});
