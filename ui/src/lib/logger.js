// Tiny logger. Purpose: silence the ~25 debug console.warn/info calls
// scattered across poll loops, bridge components, and best-effort fetches
// so a production browser console stays legible for real user-visible
// errors (chunk-load, ErrorBoundary catches, 5xx REQUEST_FAILED).
//
// `error()` is unconditional — users copy-paste console errors into bug
// reports and we want the real ones to survive.
//
// `warn()` / `info()` / `debug()` only emit under Vite's DEV build flag.

const isDev = Boolean(
  typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.DEV,
);

export const logger = {
  error: (...args) => console.error(...args),
  warn:  (...args) => { if (isDev) console.warn(...args);  },
  info:  (...args) => { if (isDev) console.info(...args);  },
  debug: (...args) => { if (isDev) console.log(...args);   },
};

export default logger;
