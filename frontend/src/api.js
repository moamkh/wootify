/**
 * Backwards-compatible API barrel.
 *
 * New code should import from shared/api or its feature facade. Keeping this
 * module means integrations and bookmarked source imports continue to work.
 */
export * from './shared/api/index.js';
