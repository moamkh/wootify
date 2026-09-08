/** Convert the editor state into the API's create/update instance payload. */
import {
  PLATFORM_BALE,
  PLATFORM_BALE_ENTERPRISE,
  PLATFORM_BALE_PV_ENTERPRISE,
  PLATFORM_INSTAGRAM_PV_ENTERPRISE,
  PLATFORM_TELEGRAM,
  PLATFORM_TELEGRAM_ENTERPRISE,
} from '../../app/platforms.js';

export function createPayload(form, { patch = false } = {}) {
  const platformMetadata = {};
  if (form.platform_type_key === PLATFORM_BALE || form.platform_type_key === PLATFORM_BALE_ENTERPRISE) {
    platformMetadata.bale_api_base_url = form.bale_api_base_url?.trim() || undefined;
    platformMetadata.bale_file_base_url = form.bale_file_base_url?.trim() || undefined;
    platformMetadata.bale_poll_interval = Number(form.bale_poll_interval) > 0 ? Number(form.bale_poll_interval) : undefined;
    platformMetadata.bale_bot_name = form.bale_bot_name?.trim() || undefined;
    platformMetadata.bale_bot_id = form.bale_bot_id?.trim() || undefined;
    platformMetadata.bale_department = form.bale_department?.trim() || undefined;
  }
  if (form.platform_type_key === PLATFORM_BALE_PV_ENTERPRISE) {
    platformMetadata.bale_pv_phone_number = form.bale_pv_phone_number?.trim() || undefined;
    platformMetadata.bale_pv_session_dir = form.bale_pv_session_dir?.trim() || undefined;
    platformMetadata.bale_pv_poll_interval = Number(form.bale_pv_poll_interval) > 0 ? Number(form.bale_pv_poll_interval) : undefined;
    platformMetadata.bale_pv_display_name = form.bale_pv_display_name?.trim() || undefined;
    platformMetadata.bale_pv_department = form.bale_pv_department?.trim() || undefined;
    platformMetadata.bale_pv_share_phone_prompt_enabled = Boolean(form.bale_pv_share_phone_prompt_enabled);
    platformMetadata.bale_pv_share_phone_prompt_only_if_missing_phone = Boolean(form.bale_pv_share_phone_prompt_only_if_missing_phone);
    platformMetadata.bale_pv_share_phone_prompt_text = form.bale_pv_share_phone_prompt_text?.trim() || undefined;
  }
  if (form.platform_type_key === PLATFORM_INSTAGRAM_PV_ENTERPRISE) {
    platformMetadata.instagram_username = form.instagram_username?.trim() || undefined;
    const instagramPassword = form.instagram_password;
    if (instagramPassword && !instagramPassword.includes('***')) platformMetadata.instagram_password = instagramPassword;
    const instagramSessionid = form.instagram_sessionid?.trim();
    if (instagramSessionid && !instagramSessionid.includes('***')) platformMetadata.instagram_sessionid = instagramSessionid;
    platformMetadata.instagram_verification_code = form.instagram_verification_code?.trim() || undefined;
    platformMetadata.instagram_totp_seed = form.instagram_totp_seed?.trim() || undefined;
    platformMetadata.instagram_session_dir = form.instagram_session_dir?.trim() || undefined;
    platformMetadata.instagram_poll_interval = Number(form.instagram_poll_interval) > 0 ? Number(form.instagram_poll_interval) : undefined;
    platformMetadata.instagram_display_name = form.instagram_display_name?.trim() || undefined;
    platformMetadata.instagram_department = form.instagram_department?.trim() || undefined;
  }
  if (form.platform_type_key === PLATFORM_TELEGRAM || form.platform_type_key === PLATFORM_TELEGRAM_ENTERPRISE) {
    platformMetadata.telegram_api_base_url = form.telegram_api_base_url?.trim() || undefined;
    platformMetadata.telegram_file_base_url = form.telegram_file_base_url?.trim() || undefined;
    platformMetadata.telegram_poll_interval = Number(form.telegram_poll_interval) > 0 ? Number(form.telegram_poll_interval) : undefined;
    platformMetadata.telegram_bot_name = form.telegram_bot_name?.trim() || undefined;
    platformMetadata.telegram_bot_id = form.telegram_bot_id?.trim() || undefined;
    platformMetadata.telegram_department = form.telegram_department?.trim() || undefined;
  }
  if (form.platform_type_key === PLATFORM_BALE) {
    platformMetadata.bale_share_phone_prompt_enabled = Boolean(form.bale_share_phone_prompt_enabled);
    platformMetadata.bale_share_phone_prompt_only_if_missing_phone = Boolean(form.bale_share_phone_prompt_only_if_missing_phone);
    platformMetadata.bale_share_phone_prompt_text = form.bale_share_phone_prompt_text?.trim() || undefined;
  }
  if (form.platform_type_key === PLATFORM_BALE_ENTERPRISE) {
    for (const key of ['welcome_text', 'phone_prompt_text', 'menu_prompt_text', 'address_prompt_text', 'number_not_found_text', 'no_manuals_text', 'no_catalog_text', 'not_configured_text', 'live_mode_resume_text', 'invalid_phone_text', 'address_tehran_alborz_text', 'address_other_provinces_text', 'user_manual_link_template']) {
      platformMetadata[`enterprise_${key}`] = form[`enterprise_${key}`]?.trim() || undefined;
    }
    for (const route of ['customer_service', 'sales']) {
      platformMetadata[`enterprise_${route}_inbox_id`] = Number(form[`enterprise_${route}_inbox_id`]) > 0 ? Number(form[`enterprise_${route}_inbox_id`]) : undefined;
      platformMetadata[`enterprise_${route}_inbox_name`] = form[`enterprise_${route}_inbox_name`]?.trim() || undefined;
      platformMetadata[`enterprise_${route}_auto_create`] = Boolean(form[`enterprise_${route}_auto_create`]);
      for (const status of ['waiting_text', 'accepted_text', 'unread_text']) {
        platformMetadata[`enterprise_${route}_${status}`] = form[`enterprise_${route}_${status}`]?.trim() || undefined;
      }
    }
    platformMetadata.enterprise_sms_sync_enabled = Boolean(form.enterprise_sms_sync_enabled);
    platformMetadata.enterprise_sms_api_url = form.enterprise_sms_api_url?.trim() || undefined;
    platformMetadata.enterprise_sms_token_header = form.enterprise_sms_token_header?.trim() || undefined;
    platformMetadata.enterprise_sms_token_prefix = form.enterprise_sms_token_prefix?.trim() || undefined;
    platformMetadata.enterprise_sms_poll_interval_minutes = Number(form.enterprise_sms_poll_interval_minutes) > 0 ? Number(form.enterprise_sms_poll_interval_minutes) : undefined;
    platformMetadata.enterprise_sms_last_id = Number(form.enterprise_sms_last_id) >= 0 ? Number(form.enterprise_sms_last_id) : undefined;
    platformMetadata.enterprise_sms_http_timeout_seconds = Number(form.enterprise_sms_http_timeout_seconds) > 0 ? Number(form.enterprise_sms_http_timeout_seconds) : undefined;
  }
  if (form.platform_type_key === PLATFORM_TELEGRAM) {
    platformMetadata.telegram_share_phone_prompt_enabled = Boolean(form.telegram_share_phone_prompt_enabled);
    platformMetadata.telegram_share_phone_prompt_only_if_missing_phone = Boolean(form.telegram_share_phone_prompt_only_if_missing_phone);
    platformMetadata.telegram_share_phone_prompt_text = form.telegram_share_phone_prompt_text?.trim() || undefined;
  }
  if (form.platform_type_key === PLATFORM_TELEGRAM_ENTERPRISE) {
    for (const key of ['welcome_text', 'menu_prompt_text', 'address_prompt_text', 'no_manuals_text', 'no_catalog_text', 'not_configured_text', 'live_mode_resume_text', 'address_tehran_alborz_text', 'address_other_provinces_text', 'user_manual_link_template', 'catalog_button_label', 'manuals_button_label', 'address_button_label', 'back_button_label']) {
      platformMetadata[`enterprise_${key}`] = form[`enterprise_${key}`]?.trim() || undefined;
    }
    platformMetadata.enterprise_routes = Array.isArray(form.enterprise_routes) ? form.enterprise_routes : [];
  }

  const payload = {
    platform_type_key: form.platform_type_key,
    is_enabled: Boolean(form.is_enabled),
    platform_metadata: platformMetadata,
    chatwoot: {
      base_url: form.chatwoot_base_url?.trim() || undefined,
      account_id: Number(form.chatwoot_account_id) > 0 ? Number(form.chatwoot_account_id) : undefined,
      inbox_id: form.platform_type_key === PLATFORM_BALE && Number(form.chatwoot_inbox_id) > 0 ? Number(form.chatwoot_inbox_id) : undefined,
      inbox_name: form.platform_type_key === PLATFORM_BALE ? form.chatwoot_inbox_name?.trim() || undefined : undefined,
      auto_create: form.platform_type_key === PLATFORM_BALE ? Boolean(form.chatwoot_auto_create) : false,
      reopen_conversation: form.platform_type_key === PLATFORM_BALE ? Boolean(form.chatwoot_reopen_conversation) : false,
    },
    proxy: {
      enabled: Boolean(form.proxy_enabled), protocol: form.proxy_protocol?.trim() || undefined,
      host: form.proxy_host?.trim() || undefined, port: Number(form.proxy_port) > 0 ? Number(form.proxy_port) : undefined,
      username: form.proxy_username?.trim() || undefined, password: form.proxy_password?.trim() || undefined,
    },
    feature_overrides: { ...form.feature_overrides },
  };
  const baleToken = form.bale_token?.trim();
  if ((form.platform_type_key === PLATFORM_BALE || form.platform_type_key === PLATFORM_BALE_ENTERPRISE) && baleToken && !baleToken.includes('***')) payload.platform_metadata.bale_token = baleToken;
  const enterpriseSmsToken = form.enterprise_sms_api_token?.trim();
  if (form.platform_type_key === PLATFORM_BALE_ENTERPRISE && enterpriseSmsToken && !enterpriseSmsToken.includes('***')) payload.platform_metadata.enterprise_sms_api_token = enterpriseSmsToken;
  const telegramToken = form.telegram_token?.trim();
  if ((form.platform_type_key === PLATFORM_TELEGRAM || form.platform_type_key === PLATFORM_TELEGRAM_ENTERPRISE) && telegramToken && !telegramToken.includes('***')) payload.platform_metadata.telegram_token = telegramToken;
  if (form.platform_type_key === PLATFORM_BALE_PV_ENTERPRISE || form.platform_type_key === PLATFORM_INSTAGRAM_PV_ENTERPRISE) {
    payload.chatwoot.inbox_id = Number(form.chatwoot_inbox_id) > 0 ? Number(form.chatwoot_inbox_id) : undefined;
    payload.chatwoot.inbox_name = form.chatwoot_inbox_name?.trim() || undefined;
    payload.chatwoot.auto_create = Boolean(form.chatwoot_auto_create);
    payload.chatwoot.reopen_conversation = Boolean(form.chatwoot_reopen_conversation);
  }
  const chatwootToken = form.chatwoot_api_access_token?.trim();
  if (chatwootToken && !chatwootToken.includes('***')) payload.chatwoot.api_access_token = chatwootToken;
  const proxyPassword = form.proxy_password?.trim();
  if (proxyPassword && proxyPassword.includes('***')) delete payload.proxy.password;
  if (!patch) payload.instance_key = form.instance_key.trim();
  return payload;
}
