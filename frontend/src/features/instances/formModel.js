/** Normalize feature override rows into the editor's lookup shape. */
export function toFeatureMap(overrides = []) {
  const out = {};
  for (const item of overrides) {
    if (!item?.feature_key) continue;
    out[item.feature_key] = Boolean(item.requested_enabled);
  }
  return out;
}
