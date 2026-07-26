import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import TitleUpdater from './TitleUpdater';

describe('TitleUpdater', () => {
  afterEach(() => {
    cleanup();
    document.title = '';
  });

  it('sets a mapped title for a known route', () => {
    render(
      <MemoryRouter initialEntries={['/incidents']}>
        <TitleUpdater />
      </MemoryRouter>,
    );
    expect(document.title).toBe('Incidents · Aegis');
  });

  it('falls back to the base title for an unknown route', () => {
    render(
      <MemoryRouter initialEntries={['/nowhere']}>
        <TitleUpdater />
      </MemoryRouter>,
    );
    expect(document.title).toBe('Aegis');
  });

  it('picks up dynamic-segment prefix titles', () => {
    render(
      <MemoryRouter initialEntries={['/agents/abc-123']}>
        <TitleUpdater />
      </MemoryRouter>,
    );
    expect(document.title).toBe('Agent · Aegis');
  });
});
