import '@testing-library/jest-dom/vitest';

// Vite's `import.meta.env.DEV` is missing under vitest by default because
// vitest doesn't run through the Vite build. Force DEV=true so lib/logger's
// warn/info/debug fire during tests — tests then don't get silent-log
// surprises when they assert on side effects.
if (typeof import.meta !== 'undefined' && import.meta.env) {
  import.meta.env.DEV = true;
}
