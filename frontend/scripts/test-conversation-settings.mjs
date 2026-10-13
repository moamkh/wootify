import assert from 'node:assert/strict';
import { createPayload } from '../src/features/instances/formPayload.js';
import { managedInboxes, inboxReopenEnabled, setInboxReopen } from '../src/features/instances/conversationSettings.js';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { build } from 'esbuild';

for (const platform of ['bale', 'bale_pv_enterprise', 'eitaa_pv_enterprise', 'instagram_pv_enterprise', 'telegram']) {
  const form = { platform_type_key: platform, instance_key: 'test', chatwoot_inbox_id: '57', chatwoot_reopen_conversation: false };
  for (const patch of [true, false]) {
    const enabled = setInboxReopen(form, '57', true);
    const payload = createPayload(enabled, { patch });
    assert.equal(payload.chatwoot.inbox_id, 57);
    assert.equal(payload.chatwoot.reopen_conversation_by_inbox['57'], true);
    assert.equal(inboxReopenEnabled(enabled, 57), true);
    assert.equal(createPayload(setInboxReopen(enabled, '57', false), { patch }).chatwoot.reopen_conversation_by_inbox['57'], false);
  }
}
for (const platform of ['bale_enterprise', 'telegram_enterprise']) {
  const form = { platform_type_key: platform, enterprise_sales_inbox_id: '58', enterprise_customer_service_inbox_id: '57',
    enterprise_routes: [{ route_key: 'support', inbox_id: '57' }, { route_key: 'sales', inbox_id: '58' }], chatwoot_reopen_conversation: false };
  const enabled = setInboxReopen(form, 57, true);
  assert.equal(managedInboxes(enabled).length, 2);
  assert.equal(inboxReopenEnabled(enabled, 58), false);
  assert.equal(createPayload(enabled, { patch: true }).chatwoot.reopen_conversation_by_inbox['57'], true);
}
const bundle = await build({ entryPoints: ['src/features/instances/ConversationSettings.jsx'], bundle: true, write: false, format: 'esm', external: ['react'] });
// Resolve the single React import before evaluating the in-memory JSX bundle.
const source = bundle.outputFiles[0].text.replace(/import React from "react";/g, 'const React = globalThis.__conversationTestReact;');
globalThis.__conversationTestReact = React;
const Component = (await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)).default;
const html = renderToStaticMarkup(React.createElement(Component, { form: { platform_type_key: 'eitaa_pv_enterprise', chatwoot_inbox_id: 57 }, setForm() {}, busy: false }));
assert.match(html, /Change anytime/);
assert.match(html, /Reopen resolved conversation/);
assert.doesNotMatch(html, /disabled/);
console.log('All platform payloads, inbox isolation, on/off updates and editable UI passed.');
