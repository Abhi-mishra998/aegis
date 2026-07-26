import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { logger } from './logger';

// setupTests.js forces import.meta.env.DEV=true so warn/info/debug should
// emit during the whole suite. error() is unconditional either way.
describe('logger', () => {
  let errSpy;
  let warnSpy;
  let infoSpy;
  let logSpy;

  beforeEach(() => {
    errSpy  = vi.spyOn(console, 'error').mockImplementation(() => {});
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    infoSpy = vi.spyOn(console, 'info').mockImplementation(() => {});
    logSpy  = vi.spyOn(console, 'log').mockImplementation(() => {});
  });
  afterEach(() => {
    errSpy.mockRestore();
    warnSpy.mockRestore();
    infoSpy.mockRestore();
    logSpy.mockRestore();
  });

  it('forwards error() unconditionally', () => {
    logger.error('boom', 42);
    expect(errSpy).toHaveBeenCalledWith('boom', 42);
  });

  it('forwards warn/info/debug when DEV is truthy', () => {
    logger.warn('w');
    logger.info('i');
    logger.debug('d');
    expect(warnSpy).toHaveBeenCalledWith('w');
    expect(infoSpy).toHaveBeenCalledWith('i');
    expect(logSpy).toHaveBeenCalledWith('d');
  });
});
