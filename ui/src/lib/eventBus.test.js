import { describe, expect, it, vi } from 'vitest';
import { eventBus } from './eventBus';

describe('eventBus', () => {
  it('delivers events to subscribed listeners', () => {
    const spy = vi.fn();
    const off = eventBus.on('test:event', spy);
    eventBus.emit('test:event', { hello: 'world' });
    expect(spy).toHaveBeenCalledWith({ hello: 'world' });
    off();
  });

  it('does not deliver after unsubscribe', () => {
    const spy = vi.fn();
    const off = eventBus.on('test:event2', spy);
    off();
    eventBus.emit('test:event2', {});
    expect(spy).not.toHaveBeenCalled();
  });

  it('supports multiple listeners on the same event', () => {
    const a = vi.fn();
    const b = vi.fn();
    const offA = eventBus.on('test:multi', a);
    const offB = eventBus.on('test:multi', b);
    eventBus.emit('test:multi', 1);
    expect(a).toHaveBeenCalledWith(1);
    expect(b).toHaveBeenCalledWith(1);
    offA(); offB();
  });
});
