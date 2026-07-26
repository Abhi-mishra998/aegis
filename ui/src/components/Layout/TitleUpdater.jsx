import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { titleForPath } from '../../lib/pageTitles';

/**
 * Sets document.title on every route change. Mount once inside <BrowserRouter>.
 *
 * Fixes the audit finding that all 57 pages left the browser tab reading
 * "Aegis — AI governance & runtime security platform" (from index.html),
 * so power users with 10+ tabs couldn't distinguish them. Centralised here
 * so no per-page code is required — a new route only needs an entry in
 * lib/pageTitles.js.
 */
export default function TitleUpdater() {
  const { pathname } = useLocation();
  useEffect(() => {
    document.title = titleForPath(pathname);
  }, [pathname]);
  return null;
}
