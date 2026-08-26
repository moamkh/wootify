/**
 * Public API barrel. Feature code can import a focused facade (auth,
 * instances, enterprise, or platforms); legacy callers can keep importing
 * every helper from this module.
 */
export { API_BASE } from './client.js';
export * from './auth.js';
export * from './instances.js';
export * from './enterprise.js';
export * from './platforms.js';
