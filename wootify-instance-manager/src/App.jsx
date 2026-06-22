/**
 * Module Overview
 * ---------------
 * Purpose: Main React screen for instance management, mappings, and simulation tools.
 * Documentation Standard: module/class/public-method comments.
 */

import React, { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import {
  API_BASE,
  createInbox,
  createEnterpriseRouteInbox,
  createInstance,
  deleteEnterpriseCatalog,
  deleteEnterpriseManual,
  patchEnterpriseManual,
  deleteEnterpriseManualGroup,
  deleteInstance,
  getEnterpriseSmsSyncConfig,
  getEnterpriseCatalog,
  listConversationMessages,
  listConversations,
  listEnterpriseManuals,
  listEnterpriseManualGroups,
  listEnterpriseManualGroupsWithManuals,
  listEnterpriseSessions,
  listFeatures,
  listInstances,
  listPlatformTypes,
  replaceEnterpriseCatalog,
  patchEnterpriseCatalog,
  runEnterpriseSmsSyncNow,
  simulatePlatformEvent,
  uploadEnterpriseManual,
  updateEnterpriseSmsSyncConfig,
  updateInstance,
  createEnterpriseManualGroup,
  renameEnterpriseManualGroup,
  addManualToEnterpriseGroup,
  removeManualFromEnterpriseGroup,
  getVersion,
  balePvSendCode,
  balePvValidateCode,
  balePvAuthStatus,
  balePvSyncContacts,
  balePvSyncDialogs,
  balePvRemoveChatwootContacts,
} from './api.js';
import PageLoader from './components/PageLoader.jsx';

const InstancesPage = lazy(() => import('./pages/InstancesPage.jsx'));
const InstanceWorkspacePage = lazy(() => import('./pages/InstanceWorkspacePage.jsx'));

const PLATFORM_BALE = 'bale';
const PLATFORM_BALE_ENTERPRISE = 'bale_enterprise';
const PLATFORM_BALE_PV_ENTERPRISE = 'bale_pv_enterprise';
const PLATFORM_TELEGRAM = 'telegram';
const PLATFORM_TELEGRAM_ENTERPRISE = 'telegram_enterprise';
const DEFAULT_PLATFORM = PLATFORM_BALE;
const ENTERPRISE_DEFAULTS = {
  welcome_text: 'به بازوي دستيار شركت مهندسي پزشكي نوين خوش آمديد.',
  phone_prompt_text: 'لطفا شماره موبایل خود را وارد کنید یا از دکمه زیر برای اشتراک‌گذاری شماره استفاده کنید.',
  menu_prompt_text: 'لطفا گزینه مورد نظر خود را انتخاب کنید.',
  address_prompt_text: 'لطفا استان مورد نظر خود را انتخاب کنید.',
  number_not_found_text:
    'شماره همراه شما در بانک اطلاعاتی نوین یافت نشد لطفا با شماره دیگری وارد شوید یا با شماره تلفن 021-41223 تماس حاصل کنید',
  no_manuals_text: 'فایلی برای این بخش تنظیم نشده است.',
  no_catalog_text: 'کاتالوگی برای این بخش تنظیم نشده است.',
  not_configured_text: 'این بخش هنوز در پنل مدیریت تنظیم نشده است.',
  live_mode_resume_text: 'گفتگو ادامه دارد. پیام خود را ارسال کنید.',
  invalid_phone_text: 'شماره موبایل معتبر نیست. لطفا دوباره تلاش کنید.',
  address_tehran_alborz_text: 'آدرس مراکز خدمات پس از فروش تهران و البرز هنوز در پنل مدیریت تنظیم نشده است.',
  address_other_provinces_text: 'آدرس مراکز خدمات پس از فروش سایر استان ها هنوز در پنل مدیریت تنظیم نشده است.',
  customer_service_waiting_text: 'درخواست شما برای ارتباط با کارشناسان خدمات پس از فروش ثبت شد. لطفا منتظر پاسخ بمانید.',
  customer_service_accepted_text: 'کارشناس خدمات پس از فروش به گفتگو متصل شد. پیام خود را ارسال کنید.',
  customer_service_unread_text:
    'شما در بخش ارتباط با کارشناسان خدمات پس از فروش پیام های خوانده نشده دارید. برای ادامه گفتگو وارد همین بخش شوید.',
  sales_waiting_text: 'درخواست شما برای ارتباط با کارشناسان فروش ثبت شد. لطفا منتظر پاسخ بمانید.',
  sales_accepted_text: 'کارشناس فروش به گفتگو متصل شد. پیام خود را ارسال کنید.',
  sales_unread_text:
    'شما در بخش ارتباط با کارشناسان فروش پیام های خوانده نشده دارید. برای ادامه گفتگو وارد همین بخش شوید.',
  user_manual_link_template:
    'برای دریافت راهنمای کاربری مورد نظر بر روی متن زیر ضربه بزنید:\n[{{user_manual_name}}]({{user_manual_url}})',
  enterprise_catalog_button_label: 'کاتالوگ محصولات',
  enterprise_manuals_button_label: 'راهنمای کاربری محصولات',
  enterprise_address_button_label: 'آدرس مراکز خدمات پس از فروش',
  enterprise_back_button_label: 'بازگشت به منو',
};

function toFeatureMap(overrides = []) {
  const out = {};
  for (const item of overrides) {
    if (!item?.feature_key) continue;
    out[item.feature_key] = Boolean(item.requested_enabled);
  }
  return out;
}

function defaultForm(features) {
  const featureMap = {};
  for (const feature of features || []) {
    featureMap[feature.key] = Boolean(feature.default_enabled);
  }

  return {
    instance_key: 'my-instance',
    platform_type_key: DEFAULT_PLATFORM,
    is_enabled: true,
    bale_token: '',
    bale_api_base_url: 'https://tapi.bale.ai',
    bale_file_base_url: 'https://tapi.bale.ai/file',
    bale_poll_interval: '5',
    bale_bot_name: '',
    bale_bot_id: '',
    bale_department: '',
    bale_share_phone_prompt_enabled: true,
    bale_share_phone_prompt_only_if_missing_phone: true,
    bale_share_phone_prompt_text: 'Use the button below to share your phone number.\nCommands: /share_phone, /help',
    bale_pv_phone_number: '',
    bale_pv_session_dir: '',
    bale_pv_poll_interval: '5',
    bale_pv_display_name: '',
    bale_pv_department: '',
    bale_pv_share_phone_prompt_enabled: true,
    bale_pv_share_phone_prompt_only_if_missing_phone: true,
    bale_pv_share_phone_prompt_text: 'Use the button below to share your phone number.\nCommands: /share_phone, /help',
    enterprise_welcome_text: ENTERPRISE_DEFAULTS.welcome_text,
    enterprise_phone_prompt_text: ENTERPRISE_DEFAULTS.phone_prompt_text,
    enterprise_menu_prompt_text: ENTERPRISE_DEFAULTS.menu_prompt_text,
    enterprise_address_prompt_text: ENTERPRISE_DEFAULTS.address_prompt_text,
    enterprise_number_not_found_text: ENTERPRISE_DEFAULTS.number_not_found_text,
    enterprise_no_manuals_text: ENTERPRISE_DEFAULTS.no_manuals_text,
    enterprise_no_catalog_text: ENTERPRISE_DEFAULTS.no_catalog_text,
    enterprise_not_configured_text: ENTERPRISE_DEFAULTS.not_configured_text,
    enterprise_live_mode_resume_text: ENTERPRISE_DEFAULTS.live_mode_resume_text,
    enterprise_invalid_phone_text: ENTERPRISE_DEFAULTS.invalid_phone_text,
    enterprise_address_tehran_alborz_text: ENTERPRISE_DEFAULTS.address_tehran_alborz_text,
    enterprise_address_other_provinces_text: ENTERPRISE_DEFAULTS.address_other_provinces_text,
    enterprise_user_manual_link_template: ENTERPRISE_DEFAULTS.user_manual_link_template,
    enterprise_customer_service_inbox_id: '',
    enterprise_customer_service_inbox_name: 'novin-customer-service',
    enterprise_customer_service_webhook_url: '',
    enterprise_customer_service_auto_create: false,
    enterprise_customer_service_waiting_text: ENTERPRISE_DEFAULTS.customer_service_waiting_text,
    enterprise_customer_service_accepted_text: ENTERPRISE_DEFAULTS.customer_service_accepted_text,
    enterprise_customer_service_unread_text: ENTERPRISE_DEFAULTS.customer_service_unread_text,
    enterprise_sales_inbox_id: '',
    enterprise_sales_inbox_name: 'novin-sales',
    enterprise_sales_webhook_url: '',
    enterprise_sales_auto_create: false,
    enterprise_sales_waiting_text: ENTERPRISE_DEFAULTS.sales_waiting_text,
    enterprise_sales_accepted_text: ENTERPRISE_DEFAULTS.sales_accepted_text,
    enterprise_sales_unread_text: ENTERPRISE_DEFAULTS.sales_unread_text,
    enterprise_sms_sync_enabled: false,
    enterprise_sms_api_url: 'https://apiserver.novinmed.com/SoftNoNTFC/LastId',
    enterprise_sms_api_token: '',
    enterprise_sms_token_header: 'Authorization',
    enterprise_sms_token_prefix: '',
    enterprise_sms_poll_interval_minutes: '20',
    enterprise_sms_last_id: '0',
    enterprise_sms_http_timeout_seconds: '30',
    telegram_token: '',
    telegram_api_base_url: 'https://api.telegram.org/bot',
    telegram_file_base_url: 'https://api.telegram.org/file/bot',
    telegram_poll_interval: '5',
    telegram_bot_name: '',
    telegram_bot_id: '',
    telegram_department: '',
    telegram_share_phone_prompt_enabled: true,
    telegram_share_phone_prompt_only_if_missing_phone: true,
    telegram_share_phone_prompt_text: 'Use the button below to share your phone number.\nCommands: /share_phone, /help',
    enterprise_routes: [],
    enterprise_catalog_button_label: ENTERPRISE_DEFAULTS.enterprise_catalog_button_label,
    enterprise_manuals_button_label: ENTERPRISE_DEFAULTS.enterprise_manuals_button_label,
    enterprise_address_button_label: ENTERPRISE_DEFAULTS.enterprise_address_button_label,
    enterprise_back_button_label: ENTERPRISE_DEFAULTS.enterprise_back_button_label,
    proxy_enabled: false,
    proxy_protocol: 'http',
    proxy_host: '',
    proxy_port: '',
    proxy_username: '',
    proxy_password: '',
    chatwoot_base_url: 'http://localhost:3000',
    chatwoot_api_access_token: '',
    chatwoot_account_id: '',
    chatwoot_inbox_id: '',
    chatwoot_inbox_name: 'wootify-inbox',
    chatwoot_auto_create: false,
    chatwoot_reopen_conversation: true,
    chatwoot_webhook_url: '',
    feature_overrides: featureMap,
  };
}

function createPayload(form, { patch = false } = {}) {
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
    platformMetadata.enterprise_welcome_text = form.enterprise_welcome_text?.trim() || undefined;
    platformMetadata.enterprise_phone_prompt_text = form.enterprise_phone_prompt_text?.trim() || undefined;
    platformMetadata.enterprise_menu_prompt_text = form.enterprise_menu_prompt_text?.trim() || undefined;
    platformMetadata.enterprise_address_prompt_text = form.enterprise_address_prompt_text?.trim() || undefined;
    platformMetadata.enterprise_number_not_found_text = form.enterprise_number_not_found_text?.trim() || undefined;
    platformMetadata.enterprise_no_manuals_text = form.enterprise_no_manuals_text?.trim() || undefined;
    platformMetadata.enterprise_no_catalog_text = form.enterprise_no_catalog_text?.trim() || undefined;
    platformMetadata.enterprise_not_configured_text = form.enterprise_not_configured_text?.trim() || undefined;
    platformMetadata.enterprise_live_mode_resume_text = form.enterprise_live_mode_resume_text?.trim() || undefined;
    platformMetadata.enterprise_invalid_phone_text = form.enterprise_invalid_phone_text?.trim() || undefined;
    platformMetadata.enterprise_address_tehran_alborz_text = form.enterprise_address_tehran_alborz_text?.trim() || undefined;
    platformMetadata.enterprise_address_other_provinces_text =
      form.enterprise_address_other_provinces_text?.trim() || undefined;
    platformMetadata.enterprise_user_manual_link_template =
      form.enterprise_user_manual_link_template?.trim() || undefined;
    platformMetadata.enterprise_customer_service_inbox_id =
      Number(form.enterprise_customer_service_inbox_id) > 0 ? Number(form.enterprise_customer_service_inbox_id) : undefined;
    platformMetadata.enterprise_customer_service_inbox_name =
      form.enterprise_customer_service_inbox_name?.trim() || undefined;
    platformMetadata.enterprise_customer_service_auto_create = Boolean(form.enterprise_customer_service_auto_create);
    platformMetadata.enterprise_customer_service_waiting_text =
      form.enterprise_customer_service_waiting_text?.trim() || undefined;
    platformMetadata.enterprise_customer_service_accepted_text =
      form.enterprise_customer_service_accepted_text?.trim() || undefined;
    platformMetadata.enterprise_customer_service_unread_text =
      form.enterprise_customer_service_unread_text?.trim() || undefined;
    platformMetadata.enterprise_sales_inbox_id =
      Number(form.enterprise_sales_inbox_id) > 0 ? Number(form.enterprise_sales_inbox_id) : undefined;
    platformMetadata.enterprise_sales_inbox_name = form.enterprise_sales_inbox_name?.trim() || undefined;
    platformMetadata.enterprise_sales_auto_create = Boolean(form.enterprise_sales_auto_create);
    platformMetadata.enterprise_sales_waiting_text = form.enterprise_sales_waiting_text?.trim() || undefined;
    platformMetadata.enterprise_sales_accepted_text = form.enterprise_sales_accepted_text?.trim() || undefined;
    platformMetadata.enterprise_sales_unread_text = form.enterprise_sales_unread_text?.trim() || undefined;
    platformMetadata.enterprise_sms_sync_enabled = Boolean(form.enterprise_sms_sync_enabled);
    platformMetadata.enterprise_sms_api_url = form.enterprise_sms_api_url?.trim() || undefined;
    platformMetadata.enterprise_sms_token_header = form.enterprise_sms_token_header?.trim() || undefined;
    platformMetadata.enterprise_sms_token_prefix = form.enterprise_sms_token_prefix?.trim() || undefined;
    platformMetadata.enterprise_sms_poll_interval_minutes =
      Number(form.enterprise_sms_poll_interval_minutes) > 0
        ? Number(form.enterprise_sms_poll_interval_minutes)
        : undefined;
    platformMetadata.enterprise_sms_last_id =
      Number(form.enterprise_sms_last_id) >= 0
        ? Number(form.enterprise_sms_last_id)
        : undefined;
    platformMetadata.enterprise_sms_http_timeout_seconds =
      Number(form.enterprise_sms_http_timeout_seconds) > 0
        ? Number(form.enterprise_sms_http_timeout_seconds)
        : undefined;
  }
  if (form.platform_type_key === PLATFORM_TELEGRAM) {
    platformMetadata.telegram_share_phone_prompt_enabled = Boolean(form.telegram_share_phone_prompt_enabled);
    platformMetadata.telegram_share_phone_prompt_only_if_missing_phone = Boolean(
      form.telegram_share_phone_prompt_only_if_missing_phone,
    );
    platformMetadata.telegram_share_phone_prompt_text = form.telegram_share_phone_prompt_text?.trim() || undefined;
  }
  if (form.platform_type_key === PLATFORM_TELEGRAM_ENTERPRISE) {
    platformMetadata.enterprise_welcome_text = form.enterprise_welcome_text?.trim() || undefined;
    platformMetadata.enterprise_menu_prompt_text = form.enterprise_menu_prompt_text?.trim() || undefined;
    platformMetadata.enterprise_address_prompt_text = form.enterprise_address_prompt_text?.trim() || undefined;
    platformMetadata.enterprise_no_manuals_text = form.enterprise_no_manuals_text?.trim() || undefined;
    platformMetadata.enterprise_no_catalog_text = form.enterprise_no_catalog_text?.trim() || undefined;
    platformMetadata.enterprise_not_configured_text = form.enterprise_not_configured_text?.trim() || undefined;
    platformMetadata.enterprise_live_mode_resume_text = form.enterprise_live_mode_resume_text?.trim() || undefined;
    platformMetadata.enterprise_address_tehran_alborz_text = form.enterprise_address_tehran_alborz_text?.trim() || undefined;
    platformMetadata.enterprise_address_other_provinces_text =
      form.enterprise_address_other_provinces_text?.trim() || undefined;
    platformMetadata.enterprise_user_manual_link_template =
      form.enterprise_user_manual_link_template?.trim() || undefined;
    platformMetadata.enterprise_catalog_button_label = form.enterprise_catalog_button_label?.trim() || undefined;
    platformMetadata.enterprise_manuals_button_label = form.enterprise_manuals_button_label?.trim() || undefined;
    platformMetadata.enterprise_address_button_label = form.enterprise_address_button_label?.trim() || undefined;
    platformMetadata.enterprise_back_button_label = form.enterprise_back_button_label?.trim() || undefined;
    platformMetadata.enterprise_routes = Array.isArray(form.enterprise_routes) ? form.enterprise_routes : [];
  }

  const payload = {
    platform_type_key: form.platform_type_key,
    is_enabled: Boolean(form.is_enabled),
    platform_metadata: platformMetadata,
    chatwoot: {
      base_url: form.chatwoot_base_url?.trim() || undefined,
      account_id: Number(form.chatwoot_account_id) > 0 ? Number(form.chatwoot_account_id) : undefined,
      inbox_id:
        form.platform_type_key === PLATFORM_BALE && Number(form.chatwoot_inbox_id) > 0
          ? Number(form.chatwoot_inbox_id)
          : undefined,
      inbox_name: form.platform_type_key === PLATFORM_BALE ? form.chatwoot_inbox_name?.trim() || undefined : undefined,
      auto_create: form.platform_type_key === PLATFORM_BALE ? Boolean(form.chatwoot_auto_create) : false,
      reopen_conversation: form.platform_type_key === PLATFORM_BALE ? Boolean(form.chatwoot_reopen_conversation) : false,
    },
    proxy: {
      enabled: Boolean(form.proxy_enabled),
      protocol: form.proxy_protocol?.trim() || undefined,
      host: form.proxy_host?.trim() || undefined,
      port: Number(form.proxy_port) > 0 ? Number(form.proxy_port) : undefined,
      username: form.proxy_username?.trim() || undefined,
      password: form.proxy_password?.trim() || undefined,
    },
    feature_overrides: { ...form.feature_overrides },
  };

  const baleToken = form.bale_token?.trim();
  if (
    (form.platform_type_key === PLATFORM_BALE || form.platform_type_key === PLATFORM_BALE_ENTERPRISE) &&
    baleToken &&
    !baleToken.includes('***')
  ) {
    payload.platform_metadata.bale_token = baleToken;
  }
  const enterpriseSmsToken = form.enterprise_sms_api_token?.trim();
  if (form.platform_type_key === PLATFORM_BALE_ENTERPRISE && enterpriseSmsToken && !enterpriseSmsToken.includes('***')) {
    payload.platform_metadata.enterprise_sms_api_token = enterpriseSmsToken;
  }
  const telegramToken = form.telegram_token?.trim();
  if (
    (form.platform_type_key === PLATFORM_TELEGRAM || form.platform_type_key === PLATFORM_TELEGRAM_ENTERPRISE) &&
    telegramToken &&
    !telegramToken.includes('***')
  ) {
    payload.platform_metadata.telegram_token = telegramToken;
  }

  if (form.platform_type_key === PLATFORM_BALE_PV_ENTERPRISE) {
    payload.chatwoot.inbox_id = Number(form.chatwoot_inbox_id) > 0 ? Number(form.chatwoot_inbox_id) : undefined;
    payload.chatwoot.inbox_name = form.chatwoot_inbox_name?.trim() || undefined;
    payload.chatwoot.auto_create = Boolean(form.chatwoot_auto_create);
    payload.chatwoot.reopen_conversation = Boolean(form.chatwoot_reopen_conversation);
  }
  const chatwootToken = form.chatwoot_api_access_token?.trim();
  if (chatwootToken && !chatwootToken.includes('***')) {
    payload.chatwoot.api_access_token = chatwootToken;
  }
  const proxyPassword = form.proxy_password?.trim();
  if (proxyPassword && proxyPassword.includes('***')) {
    delete payload.proxy.password;
  }

  if (!patch) {
    payload.instance_key = form.instance_key.trim();
  }

  return payload;
}

function maskTokenValue(value) {
  const text = String(value || '').trim();
  if (!text) return '-';
  if (text.includes('***')) return text;
  if (text.length <= 6) return '*'.repeat(text.length);
  return `${'*'.repeat(Math.max(4, text.length - 6))}${text.slice(-6)}`;
}

function buildSaveSuccessMessage(saved) {
  const result = saved?.auto_create_inbox;
  const enterpriseResults = Array.isArray(saved?.enterprise_auto_create_inboxes)
    ? saved.enterprise_auto_create_inboxes
    : [];
  const enterpriseSummary = enterpriseResults
    .map((item) => {
      if (!item?.attempted) return null;
      if (item.inbox_id && item.created) return `${item.route_key}: created ${item.inbox_id}`;
      if (item.inbox_id) return `${item.route_key}: linked ${item.inbox_id}`;
      if (item.detail) return `${item.route_key}: ${item.detail}`;
      return `${item.route_key}: failed`;
    })
    .filter(Boolean)
    .join(', ');

  if (!result?.attempted && !enterpriseSummary) {
    return 'Instance saved';
  }
  if (!result?.attempted && enterpriseSummary) {
    return `Instance saved. Enterprise inboxes: ${enterpriseSummary}.`;
  }
  if (result.inbox_id && result.created) {
    return enterpriseSummary
      ? `Instance saved. Inbox created with ID ${result.inbox_id}. Enterprise inboxes: ${enterpriseSummary}.`
      : `Instance saved. Inbox created with ID ${result.inbox_id}.`;
  }
  if (result.inbox_id) {
    return enterpriseSummary
      ? `Instance saved. Existing inbox linked with ID ${result.inbox_id}. Enterprise inboxes: ${enterpriseSummary}.`
      : `Instance saved. Existing inbox linked with ID ${result.inbox_id}.`;
  }
  if (result.detail) {
    return enterpriseSummary
      ? `Instance saved, but auto inbox creation failed: ${result.detail}. Enterprise inboxes: ${enterpriseSummary}.`
      : `Instance saved, but auto inbox creation failed: ${result.detail}`;
  }
  return enterpriseSummary
    ? `Instance saved. Enterprise inboxes: ${enterpriseSummary}.`
    : 'Instance saved, but auto inbox creation did not complete.';
}

async function copyTextToClipboard(value) {
  const text = String(value || '').trim();
  if (!text) return;
  if (navigator?.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const input = document.createElement('textarea');
  input.value = text;
  input.setAttribute('readonly', 'true');
  input.style.position = 'absolute';
  input.style.left = '-9999px';
  document.body.appendChild(input);
  input.select();
  document.execCommand('copy');
  document.body.removeChild(input);
}

export default function App() {
  const [platformTypes, setPlatformTypes] = useState([]);
  const [features, setFeatures] = useState([]);
  const [instances, setInstances] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [selectedKey, setSelectedKey] = useState('');
  const [viewMode, setViewMode] = useState('list');
  const [activeTab, setActiveTab] = useState('config');

  const [form, setForm] = useState(defaultForm([]));

  const [search, setSearch] = useState('');
  const [conversations, setConversations] = useState([]);
  const [selectedConversationId, setSelectedConversationId] = useState('');
  const [mappings, setMappings] = useState([]);
  const [instanceSearch, setInstanceSearch] = useState('');
  const [instanceStatusFilter, setInstanceStatusFilter] = useState('all');

  const [simEvent, setSimEvent] = useState({
    instance_key: 'my-instance',
    chat_id: '',
    text: '',
    platform_message_id: '',
    parent_platform_message_id: '',
  });
  const [enterpriseManuals, setEnterpriseManuals] = useState([]);
  const [enterpriseManualGroups, setEnterpriseManualGroups] = useState([]);
  const [manualGroupByAssetId, setManualGroupByAssetId] = useState({});
  const [enterpriseCatalog, setEnterpriseCatalog] = useState(null);
  const [enterpriseSessions, setEnterpriseSessions] = useState([]);
  const [manualDisplayName, setManualDisplayName] = useState('');
  const [manualLinkUrl, setManualLinkUrl] = useState('');
  const [manualFile, setManualFile] = useState(null);
  const [editingManualId, setEditingManualId] = useState('');
  const [editingManualDisplayName, setEditingManualDisplayName] = useState('');
  const [editingManualLinkUrl, setEditingManualLinkUrl] = useState('');
  const [editingManualGroupId, setEditingManualGroupId] = useState('');
  const [catalogDisplayName, setCatalogDisplayName] = useState('');
  const [catalogLinkUrl, setCatalogLinkUrl] = useState('');
  const [catalogFile, setCatalogFile] = useState(null);
  const [editingCatalog, setEditingCatalog] = useState(false);
  const [editingCatalogDisplayName, setEditingCatalogDisplayName] = useState('');
  const [editingCatalogLinkUrl, setEditingCatalogLinkUrl] = useState('');
  const [version, setVersion] = useState('');
  const [balePvAuthCode, setBalePvAuthCode] = useState('');
  const [balePvAuthLoading, setBalePvAuthLoading] = useState(false);

  const selectedPlatform = useMemo(
    () => platformTypes.find((item) => item.key === form.platform_type_key) || null,
    [platformTypes, form.platform_type_key],
  );

  const instanceMap = useMemo(() => {
    const map = {};
    for (const item of instances) {
      map[item.instance_key] = item;
    }
    return map;
  }, [instances]);

  const filteredInstances = useMemo(() => {
    const query = instanceSearch.trim().toLowerCase();
    return instances.filter((item) => {
      if (instanceStatusFilter === 'enabled' && !item.is_enabled) return false;
      if (instanceStatusFilter === 'disabled' && item.is_enabled) return false;

      if (!query) return true;
      const hay = [
        item.instance_key,
        item.platform_type_key,
        item.platform_metadata?.bale_bot_name,
        item.platform_metadata?.bale_department,
        item.platform_metadata?.bale_pv_display_name,
        item.platform_metadata?.bale_pv_department,
        item.platform_metadata?.bale_pv_phone_number,
        item.platform_metadata?.telegram_bot_name,
        item.platform_metadata?.telegram_department,
        item.chatwoot?.account_id,
        item.chatwoot?.inbox_id,
      ]
        .map((v) => String(v ?? '').toLowerCase())
        .join(' ');
      return hay.includes(query);
    });
  }, [instances, instanceSearch, instanceStatusFilter]);

  const selectedInstance = selectedKey ? instanceMap[selectedKey] : null;
  const isDetailView = viewMode === 'detail';
  const isBalePlatform = form.platform_type_key === PLATFORM_BALE || form.platform_type_key === PLATFORM_BALE_ENTERPRISE || form.platform_type_key === PLATFORM_BALE_PV_ENTERPRISE;
  const isStandardBalePlatform = form.platform_type_key === PLATFORM_BALE;
  const isEnterpriseBalePlatform = form.platform_type_key === PLATFORM_BALE_ENTERPRISE;
  const isBalePvPlatform = form.platform_type_key === PLATFORM_BALE_PV_ENTERPRISE;
  const isTelegramPlatform = form.platform_type_key === PLATFORM_TELEGRAM || form.platform_type_key === PLATFORM_TELEGRAM_ENTERPRISE;
  const isEnterpriseTelegramPlatform = form.platform_type_key === PLATFORM_TELEGRAM_ENTERPRISE;
  const isEnterprisePlatform = isEnterpriseBalePlatform || isEnterpriseTelegramPlatform;
  const enabledFeatureCount = useMemo(
    () => Object.values(form.feature_overrides || {}).filter(Boolean).length,
    [form.feature_overrides],
  );

  async function refreshBootstrap() {
    setLoading(true);
    setError('');
    try {
      const [platformData, featureData, instanceData] = await Promise.all([
        listPlatformTypes(),
        listFeatures(),
        listInstances(),
      ]);

      setPlatformTypes(platformData || []);
      setFeatures(featureData || []);
      setInstances(instanceData || []);

      setForm((prev) => {
        if (prev.instance_key) return prev;
        return defaultForm(featureData || []);
      });
    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  async function refreshInstances() {
    const data = await listInstances();
    setInstances(data || []);
  }

  async function loadEnterpriseResources(instanceKey) {
    if (!instanceKey) {
      setEnterpriseManuals([]);
      setManualGroupByAssetId({});
      setEnterpriseCatalog(null);
      setEnterpriseSessions([]);
      return;
    }

    const row = instanceMap[instanceKey];
    const isEnterprise = row?.platform_type_key === PLATFORM_BALE_ENTERPRISE || row?.platform_type_key === PLATFORM_TELEGRAM_ENTERPRISE;
    if (!isEnterprise) {
      setEnterpriseManuals([]);
      setEnterpriseManualGroups([]);
      setManualGroupByAssetId({});
      setEnterpriseCatalog(null);
      setEnterpriseSessions([]);
      return;
    }

    const [manuals, groupsPayload, catalog, sessions] = await Promise.all([
      listEnterpriseManuals(instanceKey),
      listEnterpriseManualGroupsWithManuals(instanceKey),
      getEnterpriseCatalog(instanceKey),
      listEnterpriseSessions(instanceKey),
    ]);
    setEnterpriseManuals(manuals || []);
    setEnterpriseManualGroups(groupsPayload?.groups || []);
    setManualGroupByAssetId(groupsPayload?.manual_group_map || {});
    setEnterpriseCatalog(catalog || null);
    setEnterpriseSessions(sessions || []);
  }

  useEffect(() => {
    refreshBootstrap();
  }, []);

  useEffect(() => {
    getVersion()
      .then((data) => setVersion(data?.version || ''))
      .catch(() => setVersion(''));
  }, []);

  async function loadConversations(instanceKey, q = '') {
    if (!instanceKey) {
      setConversations([]);
      setMappings([]);
      setSelectedConversationId('');
      return;
    }

    const rows = await listConversations(instanceKey, q);
    setConversations(rows);
    if (!rows.find((item) => item.id === selectedConversationId)) {
      setSelectedConversationId('');
      setMappings([]);
    }
  }

  useEffect(() => {
    if (!selectedKey) return;
    loadConversations(selectedKey, search);
  }, [selectedKey]);

  useEffect(() => {
    if (!selectedKey) return;
    loadEnterpriseResources(selectedKey).catch(() => {
      setEnterpriseManuals([]);
      setEnterpriseManualGroups([]);
      setManualGroupByAssetId({});
      setEnterpriseCatalog(null);
      setEnterpriseSessions([]);
    });
  }, [selectedKey, instances]);

  useEffect(() => {
    if (!selectedKey || !selectedConversationId) return;
    listConversationMessages(selectedKey, selectedConversationId)
      .then((rows) => setMappings(rows || []))
      .catch(() => setMappings([]));
  }, [selectedKey, selectedConversationId]);

  function onSelectInstance(instanceKey) {
    const row = instanceMap[instanceKey];
    if (!row) return;

    const f = defaultForm(features);
    const featureMap = { ...f.feature_overrides, ...toFeatureMap(row.feature_overrides) };

    setSelectedKey(instanceKey);
    setForm({
      instance_key: row.instance_key,
      platform_type_key: row.platform_type_key || DEFAULT_PLATFORM,
      is_enabled: Boolean(row.is_enabled),
      bale_token: row.platform_metadata?.bale_token || '',
      bale_api_base_url: row.platform_metadata?.bale_api_base_url || 'https://tapi.bale.ai',
      bale_file_base_url: row.platform_metadata?.bale_file_base_url || 'https://tapi.bale.ai/file',
      bale_poll_interval: String(row.platform_metadata?.bale_poll_interval ?? '5'),
      bale_bot_name: row.platform_metadata?.bale_bot_name || '',
      bale_bot_id: row.platform_metadata?.bale_bot_id || '',
      bale_department: row.platform_metadata?.bale_department || '',
      bale_share_phone_prompt_enabled: row.platform_metadata?.bale_share_phone_prompt_enabled ?? true,
      bale_share_phone_prompt_only_if_missing_phone:
        row.platform_metadata?.bale_share_phone_prompt_only_if_missing_phone ?? true,
      bale_share_phone_prompt_text:
        row.platform_metadata?.bale_share_phone_prompt_text ||
        'Use the button below to share your phone number.\nCommands: /share_phone, /help',
      bale_pv_phone_number: row.platform_metadata?.bale_pv_phone_number || '',
      bale_pv_session_dir: row.platform_metadata?.bale_pv_session_dir || '',
      bale_pv_poll_interval: String(row.platform_metadata?.bale_pv_poll_interval ?? '5'),
      bale_pv_display_name: row.platform_metadata?.bale_pv_display_name || '',
      bale_pv_department: row.platform_metadata?.bale_pv_department || '',
      bale_pv_share_phone_prompt_enabled: row.platform_metadata?.bale_pv_share_phone_prompt_enabled ?? true,
      bale_pv_share_phone_prompt_only_if_missing_phone:
        row.platform_metadata?.bale_pv_share_phone_prompt_only_if_missing_phone ?? true,
      bale_pv_share_phone_prompt_text:
        row.platform_metadata?.bale_pv_share_phone_prompt_text ||
        'Use the button below to share your phone number.\nCommands: /share_phone, /help',
      enterprise_welcome_text: row.platform_metadata?.enterprise_welcome_text || ENTERPRISE_DEFAULTS.welcome_text,
      enterprise_phone_prompt_text:
        row.platform_metadata?.enterprise_phone_prompt_text || ENTERPRISE_DEFAULTS.phone_prompt_text,
      enterprise_menu_prompt_text: row.platform_metadata?.enterprise_menu_prompt_text || ENTERPRISE_DEFAULTS.menu_prompt_text,
      enterprise_address_prompt_text:
        row.platform_metadata?.enterprise_address_prompt_text || ENTERPRISE_DEFAULTS.address_prompt_text,
      enterprise_number_not_found_text:
        row.platform_metadata?.enterprise_number_not_found_text || ENTERPRISE_DEFAULTS.number_not_found_text,
      enterprise_no_manuals_text: row.platform_metadata?.enterprise_no_manuals_text || ENTERPRISE_DEFAULTS.no_manuals_text,
      enterprise_no_catalog_text: row.platform_metadata?.enterprise_no_catalog_text || ENTERPRISE_DEFAULTS.no_catalog_text,
      enterprise_not_configured_text:
        row.platform_metadata?.enterprise_not_configured_text || ENTERPRISE_DEFAULTS.not_configured_text,
      enterprise_live_mode_resume_text:
        row.platform_metadata?.enterprise_live_mode_resume_text || ENTERPRISE_DEFAULTS.live_mode_resume_text,
      enterprise_invalid_phone_text:
        row.platform_metadata?.enterprise_invalid_phone_text || ENTERPRISE_DEFAULTS.invalid_phone_text,
      enterprise_address_tehran_alborz_text:
        row.platform_metadata?.enterprise_address_tehran_alborz_text || ENTERPRISE_DEFAULTS.address_tehran_alborz_text,
      enterprise_address_other_provinces_text:
        row.platform_metadata?.enterprise_address_other_provinces_text ||
        ENTERPRISE_DEFAULTS.address_other_provinces_text,
      enterprise_user_manual_link_template:
        row.platform_metadata?.enterprise_user_manual_link_template ||
        ENTERPRISE_DEFAULTS.user_manual_link_template,
      enterprise_customer_service_inbox_id: String(row.platform_metadata?.enterprise_customer_service_inbox_id ?? ''),
      enterprise_customer_service_inbox_name: row.platform_metadata?.enterprise_customer_service_inbox_name || 'novin-customer-service',
      enterprise_customer_service_webhook_url: row.chatwoot?.enterprise_customer_service_webhook_url || '',
      enterprise_customer_service_auto_create: Boolean(row.platform_metadata?.enterprise_customer_service_auto_create),
      enterprise_customer_service_waiting_text:
        row.platform_metadata?.enterprise_customer_service_waiting_text || ENTERPRISE_DEFAULTS.customer_service_waiting_text,
      enterprise_customer_service_accepted_text:
        row.platform_metadata?.enterprise_customer_service_accepted_text || ENTERPRISE_DEFAULTS.customer_service_accepted_text,
      enterprise_customer_service_unread_text:
        row.platform_metadata?.enterprise_customer_service_unread_text || ENTERPRISE_DEFAULTS.customer_service_unread_text,
      enterprise_sales_inbox_id: String(row.platform_metadata?.enterprise_sales_inbox_id ?? ''),
      enterprise_sales_inbox_name: row.platform_metadata?.enterprise_sales_inbox_name || 'novin-sales',
      enterprise_sales_webhook_url: row.chatwoot?.enterprise_sales_webhook_url || '',
      enterprise_sales_auto_create: Boolean(row.platform_metadata?.enterprise_sales_auto_create),
      enterprise_sales_waiting_text:
        row.platform_metadata?.enterprise_sales_waiting_text || ENTERPRISE_DEFAULTS.sales_waiting_text,
      enterprise_sales_accepted_text:
        row.platform_metadata?.enterprise_sales_accepted_text || ENTERPRISE_DEFAULTS.sales_accepted_text,
      enterprise_sales_unread_text:
        row.platform_metadata?.enterprise_sales_unread_text || ENTERPRISE_DEFAULTS.sales_unread_text,
      enterprise_sms_sync_enabled: Boolean(row.platform_metadata?.enterprise_sms_sync_enabled),
      enterprise_sms_api_url:
        row.platform_metadata?.enterprise_sms_api_url || 'https://apiserver.novinmed.com/SoftNoNTFC/LastId',
      enterprise_sms_api_token: row.platform_metadata?.enterprise_sms_api_token || '',
      enterprise_sms_token_header: row.platform_metadata?.enterprise_sms_token_header || 'Authorization',
      enterprise_sms_token_prefix: row.platform_metadata?.enterprise_sms_token_prefix || '',
      enterprise_sms_poll_interval_minutes: String(row.platform_metadata?.enterprise_sms_poll_interval_minutes ?? '20'),
      enterprise_sms_last_id: String(row.platform_metadata?.enterprise_sms_last_id ?? '0'),
      enterprise_sms_http_timeout_seconds: String(row.platform_metadata?.enterprise_sms_http_timeout_seconds ?? '30'),
      telegram_token: row.platform_metadata?.telegram_token || '',
      telegram_api_base_url: row.platform_metadata?.telegram_api_base_url || 'https://api.telegram.org/bot',
      telegram_file_base_url: row.platform_metadata?.telegram_file_base_url || 'https://api.telegram.org/file/bot',
      telegram_poll_interval: String(row.platform_metadata?.telegram_poll_interval ?? '5'),
      telegram_bot_name: row.platform_metadata?.telegram_bot_name || '',
      telegram_bot_id: row.platform_metadata?.telegram_bot_id || '',
      telegram_department: row.platform_metadata?.telegram_department || '',
      telegram_share_phone_prompt_enabled: row.platform_metadata?.telegram_share_phone_prompt_enabled ?? true,
      telegram_share_phone_prompt_only_if_missing_phone:
        row.platform_metadata?.telegram_share_phone_prompt_only_if_missing_phone ?? true,
      telegram_share_phone_prompt_text:
        row.platform_metadata?.telegram_share_phone_prompt_text ||
        'Use the button below to share your phone number.\nCommands: /share_phone, /help',
      enterprise_routes: row.platform_metadata?.enterprise_routes || [],
      enterprise_catalog_button_label: row.platform_metadata?.enterprise_catalog_button_label || ENTERPRISE_DEFAULTS.enterprise_catalog_button_label,
      enterprise_manuals_button_label: row.platform_metadata?.enterprise_manuals_button_label || ENTERPRISE_DEFAULTS.enterprise_manuals_button_label,
      enterprise_address_button_label: row.platform_metadata?.enterprise_address_button_label || ENTERPRISE_DEFAULTS.enterprise_address_button_label,
      enterprise_back_button_label: row.platform_metadata?.enterprise_back_button_label || ENTERPRISE_DEFAULTS.enterprise_back_button_label,
      proxy_enabled: row.proxy?.enabled ?? false,
      proxy_protocol: row.proxy?.protocol || 'http',
      proxy_host: row.proxy?.host || '',
      proxy_port: row.proxy?.port != null ? String(row.proxy.port) : '',
      proxy_username: row.proxy?.username || '',
      proxy_password: row.proxy?.password || '',
      chatwoot_base_url: row.chatwoot?.base_url || 'http://localhost:3000',
      chatwoot_api_access_token: row.chatwoot?.api_access_token || '',
      chatwoot_account_id: String(row.chatwoot?.account_id ?? ''),
      chatwoot_inbox_id: String(row.chatwoot?.inbox_id ?? ''),
      chatwoot_inbox_name: row.chatwoot?.inbox_name || 'wootify-inbox',
      chatwoot_auto_create: Boolean(row.chatwoot?.auto_create),
      chatwoot_reopen_conversation: Boolean(row.chatwoot?.reopen_conversation),
      chatwoot_webhook_url: row.chatwoot?.webhook_url || '',
      feature_overrides: featureMap,
    });

    // Map dynamic route webhook URLs for telegram_enterprise
    const routes = row.platform_metadata?.enterprise_routes || [];
    for (const route of routes) {
      const key = route.route_key;
      if (key) {
        const webhookUrl = row.chatwoot?.[`enterprise_${key}_webhook_url`] || '';
        setForm((prev) => ({ ...prev, [`enterprise_${key}_webhook_url`]: webhookUrl }));
      }
    }

    setSimEvent((prev) => ({ ...prev, instance_key: instanceKey }));
    setManualDisplayName('');
    setManualLinkUrl('');
    setManualFile(null);
    setCatalogDisplayName('');
    setCatalogLinkUrl('');
    setCatalogFile(null);

    if ((row.platform_type_key || '').toLowerCase() === PLATFORM_BALE_ENTERPRISE) {
      getEnterpriseSmsSyncConfig(instanceKey)
        .then((cfg) => {
          setForm((prev) => ({
            ...prev,
            enterprise_sms_sync_enabled: Boolean(cfg?.enabled),
            enterprise_sms_api_url: cfg?.api_url || prev.enterprise_sms_api_url,
            enterprise_sms_token_header: cfg?.token_header || prev.enterprise_sms_token_header,
            enterprise_sms_token_prefix: cfg?.token_prefix ?? prev.enterprise_sms_token_prefix,
            enterprise_sms_poll_interval_minutes: String(cfg?.poll_interval_minutes ?? prev.enterprise_sms_poll_interval_minutes),
            enterprise_sms_last_id: String(cfg?.last_id ?? prev.enterprise_sms_last_id),
            enterprise_sms_http_timeout_seconds: String(cfg?.http_timeout_seconds ?? prev.enterprise_sms_http_timeout_seconds),
            enterprise_sms_api_token: cfg?.api_token_configured ? (prev.enterprise_sms_api_token || '*** configured ***') : '',
          }));
        })
        .catch(() => {
          // Keep existing form values if endpoint is temporarily unavailable.
        });
    }
  }

  function openInstanceDetail(instanceKey) {
    onSelectInstance(instanceKey);
    setViewMode('detail');
    setActiveTab('config');
  }

  function onNewInstance() {
    setSelectedKey('');
    setForm(defaultForm(features));
    setEnterpriseManuals([]);
    setEnterpriseManualGroups([]);
    setEnterpriseCatalog(null);
    setEnterpriseSessions([]);
    setManualDisplayName('');
    setManualLinkUrl('');
    setManualFile(null);
    setCatalogDisplayName('');
    setCatalogLinkUrl('');
    setCatalogFile(null);
    setViewMode('detail');
    setActiveTab('config');
  }

  function isFeatureSupported(featureKey) {
    const feature = features.find((item) => item.key === featureKey);
    if (!feature || !selectedPlatform) return true;
    if (!feature.required_platform_capability) return true;
    return Boolean(selectedPlatform.capabilities?.[feature.required_platform_capability]);
  }

  async function onSave(ev) {
    ev.preventDefault();
    const key = form.instance_key.trim();
    if (!key) {
      alert('instance_key is required');
      return;
    }

    setBusy(true);
    try {
      let saved;
      if (selectedKey) {
        saved = await updateInstance(key, createPayload(form, { patch: true }));
      } else {
        saved = await createInstance(createPayload(form, { patch: false }));
      }
      await refreshInstances();
      setForm((prev) => {
        const next = {
          ...prev,
          chatwoot_inbox_id: saved?.chatwoot?.inbox_id != null ? String(saved.chatwoot.inbox_id) : prev.chatwoot_inbox_id,
          chatwoot_webhook_url: saved?.chatwoot?.webhook_url || prev.chatwoot_webhook_url,
          enterprise_customer_service_webhook_url:
            saved?.chatwoot?.enterprise_customer_service_webhook_url || prev.enterprise_customer_service_webhook_url,
          enterprise_sales_webhook_url:
            saved?.chatwoot?.enterprise_sales_webhook_url || prev.enterprise_sales_webhook_url,
          enterprise_customer_service_inbox_id:
            saved?.platform_metadata?.enterprise_customer_service_inbox_id != null
              ? String(saved.platform_metadata.enterprise_customer_service_inbox_id)
              : prev.enterprise_customer_service_inbox_id,
          enterprise_sales_inbox_id:
            saved?.platform_metadata?.enterprise_sales_inbox_id != null
              ? String(saved.platform_metadata.enterprise_sales_inbox_id)
              : prev.enterprise_sales_inbox_id,
        };
        const routes = saved?.platform_metadata?.enterprise_routes || [];
        for (const route of routes) {
          const key = route.route_key;
          if (key) {
            next[`enterprise_${key}_webhook_url`] = saved?.chatwoot?.[`enterprise_${key}_webhook_url`] || prev[`enterprise_${key}_webhook_url`] || '';
          }
        }
        return next;
      });
      setSelectedKey(key);
      await loadEnterpriseResources(key);
      alert(buildSaveSuccessMessage(saved));
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(instanceKey) {
    if (!confirm(`Delete instance ${instanceKey}?`)) return;
    setBusy(true);
    try {
      await deleteInstance(instanceKey);
      if (selectedKey === instanceKey) {
        onNewInstance();
        setViewMode('list');
      }
      await refreshInstances();
      await loadConversations(selectedKey, search);
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onToggleEnabled(instanceKey, isEnabled) {
    setBusy(true);
    try {
      await updateInstance(instanceKey, { is_enabled: !isEnabled });
      await refreshInstances();
      if (selectedKey) {
        await loadConversations(selectedKey, search);
      }
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onCreateInbox(instanceKey) {
    setBusy(true);
    try {
      const response = await createInbox(instanceKey);
      alert(JSON.stringify(response));
      await refreshInstances();
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onBalePvSyncContacts(instanceKey) {
    setBusy(true);
    try {
      const response = await balePvSyncContacts(instanceKey);
      alert(
        `Contacts synced: ${response.created || 0} created, ${response.updated || 0} updated, ${response.failed || 0} failed`
      );
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onBalePvSyncDialogs(instanceKey) {
    setBusy(true);
    try {
      const response = await balePvSyncDialogs(instanceKey);
      alert(
        `Dialogs synced: ${response.dialogs || 0} dialogs, ${response.created || 0} created, ${response.updated || 0} updated, ${response.failed || 0} failed, ${response.messages_imported || 0} messages imported`
      );
      await refreshInstances();
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onBalePvRemoveChatwootContacts(instanceKey) {
    setBusy(true);
    try {
      const dryRun = true;
      const preview = await balePvRemoveChatwootContacts(instanceKey, dryRun);
      const confirmed = window.confirm(
        `Dry run: ${preview.deleted || 0} Chatwoot contacts would be deleted (Bale contacts: ${preview.total_bale || 0}, Chatwoot BALE_PV checked: ${preview.total_chatwoot || 0}).\n\nConfirm to actually delete them.`
      );
      if (!confirmed) {
        alert('Deletion cancelled.');
        return;
      }
      const response = await balePvRemoveChatwootContacts(instanceKey, false);
      alert(
        `Chatwoot contacts removed: ${response.deleted || 0} deleted, ${response.failed || 0} failed, ${response.skipped || 0} skipped`
      );
      await refreshInstances();
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onCreateEnterpriseInbox(routeKey, instanceKey = selectedKey) {
    if (!instanceKey) return;
    setBusy(true);
    try {
      const response = await createEnterpriseRouteInbox(instanceKey, routeKey);
      alert(JSON.stringify(response));
      await refreshInstances();
      if (selectedKey === instanceKey) {
        await loadEnterpriseResources(instanceKey);
        setForm((prev) => {
          const next = {
            ...prev,
            enterprise_customer_service_inbox_id:
              routeKey === 'customer_service' && response?.inbox_id != null
                ? String(response.inbox_id)
                : prev.enterprise_customer_service_inbox_id,
            enterprise_customer_service_webhook_url:
              routeKey === 'customer_service' && response?.webhook_url
                ? response.webhook_url
                : prev.enterprise_customer_service_webhook_url,
            enterprise_sales_inbox_id:
              routeKey === 'sales' && response?.inbox_id != null ? String(response.inbox_id) : prev.enterprise_sales_inbox_id,
            enterprise_sales_webhook_url:
              routeKey === 'sales' && response?.webhook_url ? response.webhook_url : prev.enterprise_sales_webhook_url,
          };
          // Dynamic route update for telegram_enterprise
          const routes = prev.enterprise_routes || [];
          const matched = routes.find((r) => r.route_key === routeKey);
          if (matched && response?.inbox_id != null) {
            matched.inbox_id = String(response.inbox_id);
          }
          if (matched && response?.webhook_url) {
            next[`enterprise_${routeKey}_webhook_url`] = response.webhook_url;
          }
          return next;
        });
      }
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onUploadManual(ev) {
    ev.preventDefault();
    if (!selectedKey) {
      alert('Save the instance first');
      return;
    }
    if (!manualDisplayName.trim() || !manualFile) {
      alert('Manual display name and PDF file are required');
      return;
    }
    setBusy(true);
    try {
      await uploadEnterpriseManual(selectedKey, {
        displayName: manualDisplayName.trim(),
        linkUrl: manualLinkUrl.trim(),
        file: manualFile,
      });
      setManualDisplayName('');
      setManualLinkUrl('');
      setManualFile(null);
      await loadEnterpriseResources(selectedKey);
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDeleteManual(assetId) {
    if (!selectedKey) return;
    if (!confirm('Delete this manual?')) return;
    setBusy(true);
    try {
      await deleteEnterpriseManual(selectedKey, assetId);
      await loadEnterpriseResources(selectedKey);
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  function onStartEditManual(item) {
    setEditingManualId(item.id);
    setEditingManualDisplayName(item.display_name || item.original_filename || '');
    setEditingManualLinkUrl(item.link_url || '');
    setEditingManualGroupId(manualGroupByAssetId[item.id] || '');
  }

  function onCancelEditManual() {
    setEditingManualId('');
    setEditingManualDisplayName('');
    setEditingManualLinkUrl('');
    setEditingManualGroupId('');
  }

  async function onSaveEditManual(assetId) {
    if (!selectedKey) return;
    const nextName = editingManualDisplayName.trim();
    const nextLink = editingManualLinkUrl.trim();
    if (!nextName) {
      alert('Display name is required');
      return;
    }
    setBusy(true);
    try {
      const patchBody = {
        display_name: nextName,
      };
      if (nextLink) {
        patchBody.link_url = nextLink;
      }
      await patchEnterpriseManual(selectedKey, assetId, {
        ...patchBody,
      });

      const currentGroupId = manualGroupByAssetId[assetId] || '';
      const nextGroupId = editingManualGroupId || '';
      if (currentGroupId && currentGroupId !== nextGroupId) {
        await removeManualFromEnterpriseGroup(selectedKey, currentGroupId, assetId);
      }
      if (nextGroupId && currentGroupId !== nextGroupId) {
        await addManualToEnterpriseGroup(selectedKey, nextGroupId, assetId);
      }

      onCancelEditManual();
      await loadEnterpriseResources(selectedKey);
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onAddGroupFromManualEdit() {
    if (!selectedKey) return;
    const name = prompt('New group name:');
    if (!name || !name.trim()) return;
    setBusy(true);
    try {
      const created = await createEnterpriseManualGroup(selectedKey, name.trim());
      await loadEnterpriseResources(selectedKey);
      if (created?.id) {
        setEditingManualGroupId(created.id);
      }
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onRenameGroupFromManualEdit(group) {
    if (!selectedKey || !group?.id) return;
    const nextName = prompt('Edit group name:', group.name || '');
    if (!nextName || !nextName.trim()) return;
    setBusy(true);
    try {
      await renameEnterpriseManualGroup(selectedKey, group.id, nextName.trim());
      await loadEnterpriseResources(selectedKey);
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDeleteGroupFromManualEdit(group) {
    if (!selectedKey || !group?.id) return;
    if (!confirm(`Delete group "${group.name}"?`)) return;
    setBusy(true);
    try {
      await deleteEnterpriseManualGroup(selectedKey, group.id);
      if (editingManualGroupId === group.id) {
        setEditingManualGroupId('');
      }
      await loadEnterpriseResources(selectedKey);
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onReplaceCatalog(ev) {
    ev.preventDefault();
    if (!selectedKey) {
      alert('Save the instance first');
      return;
    }
    if (!catalogLinkUrl.trim()) {
      alert('Catalog link URL is required');
      return;
    }
    setBusy(true);
    try {
      await replaceEnterpriseCatalog(selectedKey, {
        displayName: catalogDisplayName.trim(),
        linkUrl: catalogLinkUrl.trim(),
        file: catalogFile,
      });
      setCatalogDisplayName('');
      setCatalogLinkUrl('');
      setCatalogFile(null);
      await loadEnterpriseResources(selectedKey);
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  function onStartEditCatalog() {
    if (!enterpriseCatalog) return;
    setEditingCatalog(true);
    setEditingCatalogDisplayName(enterpriseCatalog.display_name || enterpriseCatalog.original_filename || '');
    setEditingCatalogLinkUrl(enterpriseCatalog.link_url || '');
  }

  function onCancelEditCatalog() {
    setEditingCatalog(false);
    setEditingCatalogDisplayName('');
    setEditingCatalogLinkUrl('');
  }

  async function onSaveEditCatalog() {
    if (!selectedKey || !enterpriseCatalog) return;
    const nextName = editingCatalogDisplayName.trim();
    const nextLink = editingCatalogLinkUrl.trim();
    if (!nextName) {
      alert('Display name is required');
      return;
    }
    setBusy(true);
    try {
      await patchEnterpriseCatalog(selectedKey, {
        display_name: nextName,
        link_url: nextLink,
      });
      setEditingCatalog(false);
      setEditingCatalogDisplayName('');
      setEditingCatalogLinkUrl('');
      await loadEnterpriseResources(selectedKey);
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDeleteCatalog() {
    if (!selectedKey || !enterpriseCatalog) return;
    if (!confirm('Delete the catalog?')) return;
    setBusy(true);
    try {
      await deleteEnterpriseCatalog(selectedKey);
      await loadEnterpriseResources(selectedKey);
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onSearchConversations(ev) {
    ev.preventDefault();
    if (!selectedKey) return;
    setBusy(true);
    try {
      await loadConversations(selectedKey, search);
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onSaveEnterpriseSmsSync() {
    if (!selectedKey) {
      alert('Save the instance first');
      return;
    }

    const tokenValue = String(form.enterprise_sms_api_token || '').trim();
    const body = {
      enabled: Boolean(form.enterprise_sms_sync_enabled),
      api_url: form.enterprise_sms_api_url?.trim() || '',
      token_header: form.enterprise_sms_token_header?.trim() || 'Authorization',
      token_prefix: form.enterprise_sms_token_prefix?.trim() || '',
      poll_interval_minutes: Number(form.enterprise_sms_poll_interval_minutes) > 0
        ? Number(form.enterprise_sms_poll_interval_minutes)
        : 20,
      last_id: Number(form.enterprise_sms_last_id) >= 0 ? Number(form.enterprise_sms_last_id) : 0,
      http_timeout_seconds: Number(form.enterprise_sms_http_timeout_seconds) > 0
        ? Number(form.enterprise_sms_http_timeout_seconds)
        : 30,
    };

    if (!tokenValue.includes('***')) {
      body.api_token = tokenValue;
    }

    setBusy(true);
    try {
      const saved = await updateEnterpriseSmsSyncConfig(selectedKey, body);
      setForm((prev) => ({
        ...prev,
        enterprise_sms_sync_enabled: Boolean(saved?.enabled),
        enterprise_sms_api_url: saved?.api_url || prev.enterprise_sms_api_url,
        enterprise_sms_token_header: saved?.token_header || prev.enterprise_sms_token_header,
        enterprise_sms_token_prefix: saved?.token_prefix ?? prev.enterprise_sms_token_prefix,
        enterprise_sms_poll_interval_minutes: String(saved?.poll_interval_minutes ?? prev.enterprise_sms_poll_interval_minutes),
        enterprise_sms_last_id: String(saved?.last_id ?? prev.enterprise_sms_last_id),
        enterprise_sms_http_timeout_seconds: String(saved?.http_timeout_seconds ?? prev.enterprise_sms_http_timeout_seconds),
        enterprise_sms_api_token: saved?.api_token_configured ? (prev.enterprise_sms_api_token || '*** configured ***') : '',
      }));
      await refreshInstances();
      alert('Enterprise SMS sync configuration saved');
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onRunEnterpriseSmsSyncNow() {
    if (!selectedKey) {
      alert('Save the instance first');
      return;
    }

    setBusy(true);
    try {
      const result = await runEnterpriseSmsSyncNow(selectedKey);
      const latest = await getEnterpriseSmsSyncConfig(selectedKey);
      setForm((prev) => ({
        ...prev,
        enterprise_sms_last_id: String(latest?.last_id ?? prev.enterprise_sms_last_id),
      }));
      await refreshInstances();
      alert(JSON.stringify(result));
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onSimulate(ev) {
    ev.preventDefault();
    if (!simEvent.instance_key.trim()) {
      alert('instance_key is required');
      return;
    }
    if (!simEvent.chat_id.trim()) {
      alert('chat_id is required');
      return;
    }

    setBusy(true);
    try {
      const res = await simulatePlatformEvent(simEvent.instance_key.trim(), {
        chat_id: simEvent.chat_id.trim(),
        text: simEvent.text || '',
        platform_message_id: simEvent.platform_message_id?.trim() || undefined,
        parent_platform_message_id: simEvent.parent_platform_message_id?.trim() || undefined,
        attachments: [],
      });
      alert(JSON.stringify(res));
      if (selectedKey) {
        await loadConversations(selectedKey, search);
      }
    } catch (e) {
      alert(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  const heroProps = {
    selectedKey,
    form,
    selectedInstance,
    enabledFeatureCount,
    conversationsCount: conversations.length,
    mappingsCount: mappings.length,
    busy,
    isBalePlatform,
    isTelegramPlatform,
    isEnterpriseBalePlatform,
    isBalePvPlatform,
    isEnterpriseTelegramPlatform,
    isEnterprisePlatform,
    enterpriseRoutes: selectedInstance?.platform_metadata?.enterprise_routes || [],
    maskTokenValue,
    onToggleEnabled,
    onCreateInbox,
    onCreateEnterpriseInbox,
    onBalePvSyncContacts,
    onBalePvSyncDialogs,
    onDelete,
  };

  const formProps = {
    selectedKey,
    platformTypes,
    features,
    form,
    setForm,
    busy,
    loading,
    selectedInstance,
    isBalePlatform,
    isStandardBalePlatform,
    isEnterpriseBalePlatform,
    isBalePvPlatform,
    isEnterpriseTelegramPlatform,
    isEnterprisePlatform,
    isTelegramPlatform,
    isFeatureSupported,
    onSave,
    onNewInstance,
    onCreateInbox,
    onCreateEnterpriseInbox,
    onSaveEnterpriseSmsSync,
    onRunEnterpriseSmsSyncNow,
    copyTextToClipboard,
    balePvAuthCode,
    setBalePvAuthCode,
    balePvAuthLoading,
    setBalePvAuthLoading,
    onBalePvSendCode: async (instanceKey) => {
      setBalePvAuthLoading(true);
      try {
        const res = await balePvSendCode(instanceKey);
        alert(res?.message || 'Code sent');
      } catch (e) {
        alert(e?.message || String(e));
      } finally {
        setBalePvAuthLoading(false);
      }
    },
    onBalePvValidateCode: async (instanceKey, code) => {
      setBalePvAuthLoading(true);
      try {
        const res = await balePvValidateCode(instanceKey, code);
        alert(res?.message || 'Authenticated');
      } catch (e) {
        alert(e?.message || String(e));
      } finally {
        setBalePvAuthLoading(false);
      }
    },
    onBalePvAuthStatus: async (instanceKey) => {
      try {
        const res = await balePvAuthStatus(instanceKey);
        alert(`${res?.message || 'unknown'} | ${res?.detail || ''}`);
      } catch (e) {
        alert(e?.message || String(e));
      }
    },
  };

  const mappingProps = {
    selectedKey,
    instances,
    conversations,
    mappings,
    search,
    busy,
    selectedConversationId,
    setSelectedConversationId,
    setSearch,
    onSearchConversations,
    openInstanceDetail,
  };

  const enterpriseAssetProps = {
    selectedKey,
    busy,
    enterpriseManuals,
    enterpriseManualGroups,
    manualGroupByAssetId,
    enterpriseCatalog,
    manualDisplayName,
    setManualDisplayName,
    manualLinkUrl,
    setManualLinkUrl,
    setManualFile,
    editingManualId,
    editingManualDisplayName,
    setEditingManualDisplayName,
    editingManualLinkUrl,
    setEditingManualLinkUrl,
    editingManualGroupId,
    setEditingManualGroupId,
    catalogDisplayName,
    setCatalogDisplayName,
    catalogLinkUrl,
    setCatalogLinkUrl,
    setCatalogFile,
    onUploadManual,
    onDeleteManual,
    onStartEditManual,
    onSaveEditManual,
    onCancelEditManual,
    onAddGroupFromManualEdit,
    onRenameGroupFromManualEdit,
    onDeleteGroupFromManualEdit,
    onReplaceCatalog,
    onDeleteCatalog,
    editingCatalog,
    setEditingCatalog,
    editingCatalogDisplayName,
    setEditingCatalogDisplayName,
    editingCatalogLinkUrl,
    setEditingCatalogLinkUrl,
    onStartEditCatalog,
    onSaveEditCatalog,
    onCancelEditCatalog,
  };

  const enterpriseOperationsProps = {
    enterpriseSessions,
    enterpriseRoutes: selectedInstance?.platform_metadata?.enterprise_routes || [],
  };

  const simulationProps = {
    simEvent,
    setSimEvent,
    onSimulate,
    busy,
  };

  return (
    <div className="page app-shell">
      <header className="header app-header">
        <div>
          <p className="section-eyebrow">Wootify control plane</p>
          <h1>
            {isDetailView ? 'Instance Workspace' : 'Wootify Admin Console'}
            {version ? <span className="version-badge">v{version}</span> : null}
          </h1>
          <p className="muted">
            {isDetailView
              ? 'A structured operational workspace for configuration, observability, and enterprise workflows.'
              : 'Manage instances, capabilities, and Chatwoot platform mappings with a faster, modular UI.'}
          </p>
        </div>
        <div className="header-actions">
          <button className="btn" disabled={busy || loading} onClick={refreshBootstrap}>
            Refresh
          </button>
          {isDetailView ? (
            <button className="btn secondary" disabled={busy} onClick={() => setViewMode('list')}>
              Back to Instances
            </button>
          ) : (
            <button className="btn primary" disabled={busy || loading} onClick={onNewInstance}>
              Instance +
            </button>
          )}
          <a className="btn secondary" href={`${API_BASE}/docs`} target="_blank" rel="noreferrer">
            API Docs
          </a>
        </div>
      </header>

      {error ? <div className="alert error">Error: {error}</div> : null}

      <section className="card command-bar">
        <div className="instance-toolbar">
          <label>
            Search
            <input
              value={instanceSearch}
              onChange={(e) => setInstanceSearch(e.target.value)}
              placeholder="Search by key, bot name, department..."
            />
          </label>
          <label>
            Status
            <select value={instanceStatusFilter} onChange={(e) => setInstanceStatusFilter(e.target.value)}>
              <option value="all">All</option>
              <option value="enabled">Enabled</option>
              <option value="disabled">Disabled</option>
            </select>
          </label>
        </div>
      </section>

      <Suspense fallback={<PageLoader label={isDetailView ? 'Loading instance workspace' : 'Loading instance directory'} />}>
        {isDetailView ? (
          <InstanceWorkspacePage
            activeTab={activeTab}
            setActiveTab={setActiveTab}
            isEnterprisePlatform={isEnterprisePlatform}
            heroProps={heroProps}
            formProps={formProps}
            mappingProps={mappingProps}
            enterpriseAssetProps={enterpriseAssetProps}
            enterpriseOperationsProps={enterpriseOperationsProps}
            simulationProps={simulationProps}
            conversationsCount={conversations.length}
            enterpriseSessionsCount={enterpriseSessions.length}
            enterpriseManualsCount={enterpriseManuals.length}
            onBalePvSyncContacts={onBalePvSyncContacts}
            onBalePvSyncDialogs={onBalePvSyncDialogs}
            onBalePvRemoveChatwootContacts={onBalePvRemoveChatwootContacts}
          />
        ) : (
          <InstancesPage
            loading={loading}
            filteredInstances={filteredInstances}
            selectedKey={selectedKey}
            busy={busy}
            maskTokenValue={maskTokenValue}
            openInstanceDetail={openInstanceDetail}
            onToggleEnabled={onToggleEnabled}
            onCreateInbox={onCreateInbox}
            onCreateEnterpriseInbox={onCreateEnterpriseInbox}
            onBalePvSyncContacts={onBalePvSyncContacts}
            onBalePvSyncDialogs={onBalePvSyncDialogs}
            onBalePvRemoveChatwootContacts={onBalePvRemoveChatwootContacts}
            onDelete={onDelete}
            PLATFORM_TELEGRAM={PLATFORM_TELEGRAM}
            PLATFORM_BALE_ENTERPRISE={PLATFORM_BALE_ENTERPRISE}
            PLATFORM_BALE_PV_ENTERPRISE={PLATFORM_BALE_PV_ENTERPRISE}
            PLATFORM_TELEGRAM_ENTERPRISE={PLATFORM_TELEGRAM_ENTERPRISE}
          />
        )}
      </Suspense>
    </div>
  );
}


