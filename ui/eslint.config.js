import js from '@eslint/js';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import unusedImports from 'eslint-plugin-unused-imports';
import globals from 'globals';

// Flat config (ESLint v9+). Do not add a .eslintrc.* — flat config replaces it.
//
// Rule intent:
//   * `unused-imports/no-unused-imports` — auto-removable dead imports.
//   * `unused-imports/no-unused-vars`    — dead locals with the same
//     `argsIgnorePattern: '^_'` convention already used in the codebase
//     (e.g. `catch (_) {}` and `map((_, i) => …)`).
//   * `complexity: 10` — enforces the audit target. Existing offenders
//     will surface as warnings so they can be split without blocking builds.
//   * jsx-a11y kept at plugin-recommended defaults; downgrade specifics
//     if the current code fails a rule the team accepts (e.g. label-has-associated-control
//     for aria-label-only inputs).

export default [
  {
    ignores: [
      'dist/**',
      'node_modules/**',
      'public/**',
      'test-results/**',
      'playwright-report/**',
      'coverage/**',
      'drive.mjs',
      'scripts/**',
      // Auto-generated / third-party assets
      '*.config.js',
    ],
  },

  js.configs.recommended,

  {
    files: ['src/**/*.{js,jsx}'],
    plugins: {
      react,
      'react-hooks': reactHooks,
      'jsx-a11y': jsxA11y,
      'unused-imports': unusedImports,
    },
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.es2024,
        // Vite injects import.meta.env at build time
        importMeta: 'readonly',
      },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    settings: {
      react: { version: '18.3' },
    },
    rules: {
      // React
      ...react.configs.recommended.rules,
      ...react.configs['jsx-runtime'].rules,
      'react/prop-types': 'off',
      'react/no-unknown-property': ['error', { ignore: ['jsx', 'global'] }],
      // React HTML-escapes all text nodes at render, so raw ' or " in JSX
      // text is not an injection vector — the rule is purely stylistic.
      // Codebase preference: readable prose over &apos;/&quot; entities.
      'react/no-unescaped-entities': 'off',

      // React Hooks
      ...reactHooks.configs.recommended.rules,

      // jsx-a11y (recommended set)
      ...jsxA11y.configs.recommended.rules,
      // Codebase uses aria-label extensively for icon buttons; that satisfies
      // label-has-associated-control's intent, but the rule flags it anyway.
      'jsx-a11y/label-has-associated-control': ['warn', {
        assert: 'either',
      }],

      // Unused imports / vars
      'no-unused-vars': 'off',
      'unused-imports/no-unused-imports': 'error',
      'unused-imports/no-unused-vars': ['warn', {
        vars: 'all',
        varsIgnorePattern: '^_',
        args: 'after-used',
        argsIgnorePattern: '^_',
        caughtErrors: 'all',
        caughtErrorsIgnorePattern: '^_',
      }],

      // Complexity — surface >10 as warnings (audit target).
      complexity: ['warn', 10],

      // Sanity
      'no-undef': 'error',
      'no-empty': ['warn', { allowEmptyCatch: true }],
      'no-useless-escape': 'warn',
      'no-constant-condition': ['warn', { checkLoops: false }],

      // These are noisy in a live SaaS UI and produce more toil than value:
      'no-inner-declarations': 'off',
    },
  },
];
