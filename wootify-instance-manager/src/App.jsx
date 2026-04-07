/**
 * Module Overview
 * ---------------
 * Purpose: Main React screen for instance management, mappings, and simulation tools.
 * Documentation Standard: module/class/public-method comments.
 */

import React, { useEffect, useMemo, useState } from 'react';
import {
  API_BASE,
  createInbox,
  createEnterpriseRouteInbox,
  createInstance,
  deleteEnterpriseCatalog,
  deleteEnterpriseManual,
  deleteInstance,
  getEnterpriseSmsSyncConfig,
  getEnterpriseCatalog,
  listConversationMessages,
  listConversations,
  listEnterpriseManuals,
  listEnterpriseSessions,
  listFeatures,
  listInstances,
  listPlatformTypes,
  replaceEnterpriseCatalog,
  runEnterpriseSmsSyncNow,
  simulatePlatformEvent,
  uploadEnterpriseManual,
  updateEnterpriseSmsSyncConfig,
  updateInstance,
} from './api.js';

const PLATFORM_BALE = 'bale';
const PLATFORM_BALE_ENTERPRISE = 'bale_enterprise';
const PLATFORM_TELEGRAM = 'telegram';
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
    chatwoot_reopen_conversation: false,
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
    platformMetadata.telegram_api_base_url = form.telegram_api_base_url?.trim() || undefined;
    platformMetadata.telegram_file_base_url = form.telegram_file_base_url?.trim() || undefined;
    platformMetadata.telegram_poll_interval =
      Number(form.telegram_poll_interval) > 0 ? Number(form.telegram_poll_interval) : undefined;
    platformMetadata.telegram_bot_name = form.telegram_bot_name?.trim() || undefined;
    platformMetadata.telegram_bot_id = form.telegram_bot_id?.trim() || undefined;
    platformMetadata.telegram_department = form.telegram_department?.trim() || undefined;
    platformMetadata.telegram_share_phone_prompt_enabled = Boolean(form.telegram_share_phone_prompt_enabled);
    platformMetadata.telegram_share_phone_prompt_only_if_missing_phone = Boolean(
      form.telegram_share_phone_prompt_only_if_missing_phone,
    );
    platformMetadata.telegram_share_phone_prompt_text = form.telegram_share_phone_prompt_text?.trim() || undefined;
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
  if (form.platform_type_key === PLATFORM_TELEGRAM && telegramToken && !telegramToken.includes('***')) {
    payload.platform_metadata.telegram_token = telegramToken;
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
  const [enterpriseCatalog, setEnterpriseCatalog] = useState(null);
  const [enterpriseSessions, setEnterpriseSessions] = useState([]);
  const [manualDisplayName, setManualDisplayName] = useState('');
  const [manualLinkUrl, setManualLinkUrl] = useState('');
  const [manualFile, setManualFile] = useState(null);
  const [catalogDisplayName, setCatalogDisplayName] = useState('');
  const [catalogLinkUrl, setCatalogLinkUrl] = useState('');
  const [catalogFile, setCatalogFile] = useState(null);

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
  const isBalePlatform = form.platform_type_key === PLATFORM_BALE || form.platform_type_key === PLATFORM_BALE_ENTERPRISE;
  const isStandardBalePlatform = form.platform_type_key === PLATFORM_BALE;
  const isEnterpriseBalePlatform = form.platform_type_key === PLATFORM_BALE_ENTERPRISE;
  const isTelegramPlatform = form.platform_type_key === PLATFORM_TELEGRAM;
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
      setEnterpriseCatalog(null);
      setEnterpriseSessions([]);
      return;
    }

    const row = instanceMap[instanceKey];
    if (row?.platform_type_key !== PLATFORM_BALE_ENTERPRISE) {
      setEnterpriseManuals([]);
      setEnterpriseCatalog(null);
      setEnterpriseSessions([]);
      return;
    }

    const [manuals, catalog, sessions] = await Promise.all([
      listEnterpriseManuals(instanceKey),
      getEnterpriseCatalog(instanceKey),
      listEnterpriseSessions(instanceKey),
    ]);
    setEnterpriseManuals(manuals || []);
    setEnterpriseCatalog(catalog || null);
    setEnterpriseSessions(sessions || []);
  }

  useEffect(() => {
    refreshBootstrap();
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
  }

  function onNewInstance() {
    setSelectedKey('');
    setForm(defaultForm(features));
    setEnterpriseManuals([]);
    setEnterpriseCatalog(null);
    setEnterpriseSessions([]);
    setManualDisplayName('');
    setManualLinkUrl('');
    setManualFile(null);
    setCatalogDisplayName('');
    setCatalogLinkUrl('');
    setCatalogFile(null);
    setViewMode('detail');
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
      setForm((prev) => ({
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
      }));
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

  async function onCreateEnterpriseInbox(routeKey, instanceKey = selectedKey) {
    if (!instanceKey) return;
    setBusy(true);
    try {
      const response = await createEnterpriseRouteInbox(instanceKey, routeKey);
      alert(JSON.stringify(response));
      await refreshInstances();
      if (selectedKey === instanceKey) {
        await loadEnterpriseResources(instanceKey);
        setForm((prev) => ({
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
        }));
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
    if (!manualDisplayName.trim() || !manualLinkUrl.trim() || !manualFile) {
      alert('Manual display name, link URL, and PDF file are required');
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

  async function onReplaceCatalog(ev) {
    ev.preventDefault();
    if (!selectedKey) {
      alert('Save the instance first');
      return;
    }
    if (!catalogFile) {
      alert('Catalog PDF file is required');
      return;
    }
    setBusy(true);
    try {
      if (!catalogLinkUrl.trim()) {
        alert('Catalog link URL is required');
        return;
      }
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

  return (
    <div className="page">
      <header className="header">
        <div>
          <h1>Wootify Admin Console</h1>
          <p className="muted">Manage instances, capabilities, and Chatwoot/platform mapping records.</p>
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

      <div className={`grid ${isDetailView ? 'detail-grid' : ''}`}>
        {isDetailView ? (
          <section className="card detail-hero">
            <div className="row between">
              <div>
                <h2>{selectedKey || 'New Instance'}</h2>
                <p className="muted">Instance dashboard and configuration.</p>
              </div>
              <span className={`status-pill ${form.is_enabled ? 'good' : 'warn'}`}>{form.is_enabled ? 'Enabled' : 'Disabled'}</span>
            </div>
            {isBalePlatform ? (
              <div className="instance-token">{maskTokenValue(form.bale_token || selectedInstance?.platform_metadata?.bale_token)}</div>
            ) : null}
            {isTelegramPlatform ? (
              <div className="instance-token">
                {maskTokenValue(form.telegram_token || selectedInstance?.platform_metadata?.telegram_token)}
              </div>
            ) : null}
            <div className="instance-meta">
              {isBalePlatform ? (
                <>
                  <div>
                    <span className="k">Bot Name</span>
                    <span className="v">{form.bale_bot_name || '-'}</span>
                  </div>
                  <div>
                    <span className="k">Bot ID</span>
                    <span className="v">{form.bale_bot_id || '-'}</span>
                  </div>
                  <div>
                    <span className="k">Department</span>
                    <span className="v">{form.bale_department || '-'}</span>
                  </div>
                </>
              ) : null}
              {isTelegramPlatform ? (
                <>
                  <div>
                    <span className="k">Bot Name</span>
                    <span className="v">{form.telegram_bot_name || '-'}</span>
                  </div>
                  <div>
                    <span className="k">Bot ID</span>
                    <span className="v">{form.telegram_bot_id || '-'}</span>
                  </div>
                  <div>
                    <span className="k">Department</span>
                    <span className="v">{form.telegram_department || '-'}</span>
                  </div>
                </>
              ) : null}
              <div>
                <span className="k">Features Enabled</span>
                <span className="v">{enabledFeatureCount}</span>
              </div>
              <div>
                <span className="k">Conversations</span>
                <span className="v">{conversations.length}</span>
              </div>
              <div>
                <span className="k">Selected Mapping Rows</span>
                <span className="v">{mappings.length}</span>
              </div>
            </div>
            {selectedInstance ? (
              <div className="list-actions">
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => onToggleEnabled(selectedInstance.instance_key, selectedInstance.is_enabled)}
                >
                  {selectedInstance.is_enabled ? 'Disable' : 'Enable'}
                </button>
                {selectedInstance.platform_type_key === PLATFORM_BALE_ENTERPRISE ? (
                  <>
                    <button className="btn" disabled={busy} onClick={() => onCreateEnterpriseInbox('customer_service')}>
                      Service Inbox
                    </button>
                    <button className="btn" disabled={busy} onClick={() => onCreateEnterpriseInbox('sales')}>
                      Sales Inbox
                    </button>
                  </>
                ) : (
                  <button className="btn" disabled={busy} onClick={() => onCreateInbox(selectedInstance.instance_key)}>
                    Create Inbox
                  </button>
                )}
                <button className="btn danger" disabled={busy} onClick={() => onDelete(selectedInstance.instance_key)}>
                  Delete
                </button>
              </div>
            ) : null}
          </section>
        ) : null}
        {isDetailView ? (
          <section className="card">
          <div className="row between">
            <h2>{selectedKey ? 'Edit Instance' : 'Create Instance'}</h2>
            <button className="btn secondary" onClick={onNewInstance} disabled={busy}>
              New
            </button>
          </div>

          <form className="form" onSubmit={onSave}>
            <div className="row">
              <label>
                Instance Key
                <input
                  value={form.instance_key}
                  onChange={(e) => setForm((s) => ({ ...s, instance_key: e.target.value }))}
                  disabled={Boolean(selectedKey)}
                  required
                />
              </label>
              <label>
                Platform
                <select
                  value={form.platform_type_key}
                  onChange={(e) => setForm((s) => ({ ...s, platform_type_key: e.target.value }))}
                >
                  {platformTypes.map((item) => (
                    <option key={item.key} value={item.key}>
                      {item.display_name || item.key}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <label className="checkbox">
              <input
                type="checkbox"
                checked={form.is_enabled}
                onChange={(e) => setForm((s) => ({ ...s, is_enabled: e.target.checked }))}
              />
              Instance enabled
            </label>

            {isBalePlatform ? (
              <>
                <h3>{isEnterpriseBalePlatform ? 'Bale Enterprise Metadata' : 'Bale Metadata'}</h3>
                <div className="row">
                  <label>
                    Bale Token
                    <input
                      value={form.bale_token}
                      onChange={(e) => setForm((s) => ({ ...s, bale_token: e.target.value }))}
                      placeholder="123456:abc..."
                    />
                  </label>
                  <label>
                    Poll Interval
                    <input
                      value={form.bale_poll_interval}
                      onChange={(e) => setForm((s) => ({ ...s, bale_poll_interval: e.target.value }))}
                      placeholder="5"
                    />
                  </label>
                </div>
                <label>
                  Bale API Base URL
                  <input
                    value={form.bale_api_base_url}
                    onChange={(e) => setForm((s) => ({ ...s, bale_api_base_url: e.target.value }))}
                  />
                </label>
                <label>
                  Bale File Base URL
                  <input
                    value={form.bale_file_base_url}
                    onChange={(e) => setForm((s) => ({ ...s, bale_file_base_url: e.target.value }))}
                  />
                </label>
                <div className="row">
                  <label>
                    Bot Name
                    <input
                      value={form.bale_bot_name}
                      onChange={(e) => setForm((s) => ({ ...s, bale_bot_name: e.target.value }))}
                      placeholder="Support Bot"
                    />
                  </label>
                  <label>
                    Bot ID
                    <input
                      value={form.bale_bot_id}
                      onChange={(e) => setForm((s) => ({ ...s, bale_bot_id: e.target.value }))}
                      placeholder="bot_12345"
                    />
                  </label>
                </div>
                <label>
                  Department
                  <input
                    value={form.bale_department}
                    onChange={(e) => setForm((s) => ({ ...s, bale_department: e.target.value }))}
                    placeholder="Customer Support"
                  />
                </label>

                {isStandardBalePlatform ? (
                  <>
                    <h3>Bale Phone Prompt</h3>
                    <label className="checkbox">
                      <input
                        type="checkbox"
                        checked={Boolean(form.bale_share_phone_prompt_enabled)}
                        onChange={(e) => setForm((s) => ({ ...s, bale_share_phone_prompt_enabled: e.target.checked }))}
                      />
                      Enable share-phone prompt
                    </label>
                    <label className="checkbox">
                      <input
                        type="checkbox"
                        checked={Boolean(form.bale_share_phone_prompt_only_if_missing_phone)}
                        onChange={(e) =>
                          setForm((s) => ({ ...s, bale_share_phone_prompt_only_if_missing_phone: e.target.checked }))
                        }
                      />
                      Send prompt only if Chatwoot contact has no phone number
                    </label>
                    <label>
                      Share-Phone Prompt Text
                      <textarea
                        rows={3}
                        value={form.bale_share_phone_prompt_text}
                        onChange={(e) => setForm((s) => ({ ...s, bale_share_phone_prompt_text: e.target.value }))}
                      />
                    </label>
                  </>
                ) : null}

                {isEnterpriseBalePlatform ? (
                  <>
                    <h3>Enterprise Messages</h3>
                    <label>
                      Welcome Text
                      <textarea
                        rows={3}
                        value={form.enterprise_welcome_text}
                        onChange={(e) => setForm((s) => ({ ...s, enterprise_welcome_text: e.target.value }))}
                      />
                    </label>
                    <label>
                      Phone Prompt Text
                      <textarea
                        rows={3}
                        value={form.enterprise_phone_prompt_text}
                        onChange={(e) => setForm((s) => ({ ...s, enterprise_phone_prompt_text: e.target.value }))}
                      />
                    </label>
                    <label>
                      Root Menu Prompt
                      <textarea
                        rows={3}
                        value={form.enterprise_menu_prompt_text}
                        onChange={(e) => setForm((s) => ({ ...s, enterprise_menu_prompt_text: e.target.value }))}
                      />
                    </label>
                    <label>
                      Address Menu Prompt
                      <textarea
                        rows={3}
                        value={form.enterprise_address_prompt_text}
                        onChange={(e) => setForm((s) => ({ ...s, enterprise_address_prompt_text: e.target.value }))}
                      />
                    </label>
                    <label>
                      Number Not Found Text
                      <textarea
                        rows={3}
                        value={form.enterprise_number_not_found_text}
                        onChange={(e) => setForm((s) => ({ ...s, enterprise_number_not_found_text: e.target.value }))}
                      />
                    </label>
                    <label>
                      Invalid Phone Text
                      <textarea
                        rows={3}
                        value={form.enterprise_invalid_phone_text}
                        onChange={(e) => setForm((s) => ({ ...s, enterprise_invalid_phone_text: e.target.value }))}
                      />
                    </label>
                    <label>
                      No Manuals Text
                      <textarea
                        rows={3}
                        value={form.enterprise_no_manuals_text}
                        onChange={(e) => setForm((s) => ({ ...s, enterprise_no_manuals_text: e.target.value }))}
                      />
                    </label>
                    <label>
                      No Catalog Text
                      <textarea
                        rows={3}
                        value={form.enterprise_no_catalog_text}
                        onChange={(e) => setForm((s) => ({ ...s, enterprise_no_catalog_text: e.target.value }))}
                      />
                    </label>
                    <label>
                      Missing Configuration Fallback
                      <textarea
                        rows={3}
                        value={form.enterprise_not_configured_text}
                        onChange={(e) => setForm((s) => ({ ...s, enterprise_not_configured_text: e.target.value }))}
                      />
                    </label>
                    <label>
                      Live Session Resume Text
                      <textarea
                        rows={3}
                        value={form.enterprise_live_mode_resume_text}
                        onChange={(e) =>
                          setForm((s) => ({ ...s, enterprise_live_mode_resume_text: e.target.value }))
                        }
                      />
                    </label>

                    <h3>Enterprise Addresses</h3>
                    <label>
                      Tehran and Alborz Text
                      <textarea
                        rows={4}
                        value={form.enterprise_address_tehran_alborz_text}
                        onChange={(e) => setForm((s) => ({ ...s, enterprise_address_tehran_alborz_text: e.target.value }))}
                      />
                    </label>
                    <label>
                      Other Provinces Text
                      <textarea
                        rows={4}
                        value={form.enterprise_address_other_provinces_text}
                        onChange={(e) => setForm((s) => ({ ...s, enterprise_address_other_provinces_text: e.target.value }))}
                      />
                    </label>
                  </>
                ) : null}
              </>
            ) : null}
            {isTelegramPlatform ? (
              <>
                <h3>Telegram Metadata</h3>
                <div className="row">
                  <label>
                    Telegram Token
                    <input
                      value={form.telegram_token}
                      onChange={(e) => setForm((s) => ({ ...s, telegram_token: e.target.value }))}
                      placeholder="123456:abc..."
                    />
                  </label>
                  <label>
                    Poll Interval
                    <input
                      value={form.telegram_poll_interval}
                      onChange={(e) => setForm((s) => ({ ...s, telegram_poll_interval: e.target.value }))}
                      placeholder="5"
                    />
                  </label>
                </div>
                <label>
                  Telegram API Base URL
                  <input
                    value={form.telegram_api_base_url}
                    onChange={(e) => setForm((s) => ({ ...s, telegram_api_base_url: e.target.value }))}
                  />
                </label>
                <label>
                  Telegram File Base URL
                  <input
                    value={form.telegram_file_base_url}
                    onChange={(e) => setForm((s) => ({ ...s, telegram_file_base_url: e.target.value }))}
                  />
                </label>
                <div className="row">
                  <label>
                    Bot Name
                    <input
                      value={form.telegram_bot_name}
                      onChange={(e) => setForm((s) => ({ ...s, telegram_bot_name: e.target.value }))}
                      placeholder="Support Bot"
                    />
                  </label>
                  <label>
                    Bot ID
                    <input
                      value={form.telegram_bot_id}
                      onChange={(e) => setForm((s) => ({ ...s, telegram_bot_id: e.target.value }))}
                      placeholder="bot_12345"
                    />
                  </label>
                </div>
                <label>
                  Department
                  <input
                    value={form.telegram_department}
                    onChange={(e) => setForm((s) => ({ ...s, telegram_department: e.target.value }))}
                    placeholder="Customer Support"
                  />
                </label>

                <h3>Telegram Phone Prompt</h3>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={Boolean(form.telegram_share_phone_prompt_enabled)}
                    onChange={(e) => setForm((s) => ({ ...s, telegram_share_phone_prompt_enabled: e.target.checked }))}
                  />
                  Enable share-phone prompt
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={Boolean(form.telegram_share_phone_prompt_only_if_missing_phone)}
                    onChange={(e) =>
                      setForm((s) => ({ ...s, telegram_share_phone_prompt_only_if_missing_phone: e.target.checked }))
                    }
                  />
                  Send prompt only if Chatwoot contact has no phone number
                </label>
                <label>
                  Share-Phone Prompt Text
                  <textarea
                    rows={3}
                    value={form.telegram_share_phone_prompt_text}
                    onChange={(e) => setForm((s) => ({ ...s, telegram_share_phone_prompt_text: e.target.value }))}
                  />
                </label>
              </>
            ) : null}

            <h3>Proxy</h3>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={Boolean(form.proxy_enabled)}
                onChange={(e) => setForm((s) => ({ ...s, proxy_enabled: e.target.checked }))}
              />
              Enable per-instance platform proxy
            </label>
            <div className="row">
              <label>
                Protocol
                <select
                  value={form.proxy_protocol}
                  onChange={(e) => setForm((s) => ({ ...s, proxy_protocol: e.target.value }))}
                  disabled={!form.proxy_enabled}
                >
                  <option value="http">http</option>
                  <option value="https">https</option>
                  <option value="socks5">socks5</option>
                </select>
              </label>
              <label>
                Port
                <input
                  value={form.proxy_port}
                  onChange={(e) => setForm((s) => ({ ...s, proxy_port: e.target.value }))}
                  placeholder="8080"
                  disabled={!form.proxy_enabled}
                />
              </label>
            </div>
            <label>
              Host
              <input
                value={form.proxy_host}
                onChange={(e) => setForm((s) => ({ ...s, proxy_host: e.target.value }))}
                placeholder="127.0.0.1"
                disabled={!form.proxy_enabled}
              />
            </label>
            <div className="row">
              <label>
                Username
                <input
                  value={form.proxy_username}
                  onChange={(e) => setForm((s) => ({ ...s, proxy_username: e.target.value }))}
                  disabled={!form.proxy_enabled}
                />
              </label>
              <label>
                Password
                <input
                  value={form.proxy_password}
                  onChange={(e) => setForm((s) => ({ ...s, proxy_password: e.target.value }))}
                  disabled={!form.proxy_enabled}
                />
              </label>
            </div>

            <h3>Chatwoot</h3>
            <label>
              Base URL
              <input
                value={form.chatwoot_base_url}
                onChange={(e) => setForm((s) => ({ ...s, chatwoot_base_url: e.target.value }))}
              />
            </label>
            <label>
              API Access Token
              <input
                value={form.chatwoot_api_access_token}
                onChange={(e) => setForm((s) => ({ ...s, chatwoot_api_access_token: e.target.value }))}
              />
            </label>
            <label>
              Account ID
              <input
                value={form.chatwoot_account_id}
                onChange={(e) => setForm((s) => ({ ...s, chatwoot_account_id: e.target.value }))}
              />
            </label>
            {isStandardBalePlatform ? (
              <>
                <div className="row">
                  <label>
                    Inbox ID
                    <input
                      value={form.chatwoot_inbox_id}
                      onChange={(e) => setForm((s) => ({ ...s, chatwoot_inbox_id: e.target.value }))}
                    />
                  </label>
                  <label>
                    Inbox Name
                    <input
                      value={form.chatwoot_inbox_name}
                      onChange={(e) => setForm((s) => ({ ...s, chatwoot_inbox_name: e.target.value }))}
                    />
                  </label>
                </div>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={form.chatwoot_auto_create}
                    onChange={(e) => setForm((s) => ({ ...s, chatwoot_auto_create: e.target.checked }))}
                  />
                  Auto create Chatwoot inbox
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={form.chatwoot_reopen_conversation}
                    onChange={(e) => setForm((s) => ({ ...s, chatwoot_reopen_conversation: e.target.checked }))}
                  />
                  Reopen resolved Chatwoot conversation on inbound reply
                </label>
              </>
            ) : null}
            {!isEnterpriseBalePlatform ? (
              <label>
                Webhook URL
                <div className="row">
                  <input
                    value={form.chatwoot_webhook_url}
                    readOnly
                    placeholder="Save the instance to generate the webhook URL"
                  />
                  <button
                    type="button"
                    className="btn secondary"
                    disabled={!form.chatwoot_webhook_url}
                    onClick={async () => {
                      try {
                        await copyTextToClipboard(form.chatwoot_webhook_url);
                        alert('Webhook URL copied');
                      } catch (e) {
                        alert(e?.message || String(e));
                      }
                    }}
                  >
                    Copy
                  </button>
                </div>
              </label>
            ) : null}

            {isEnterpriseBalePlatform ? (
              <>
                <h3>Customer Service Route</h3>
                <div className="row">
                  <label>
                    Inbox ID
                    <input
                      value={form.enterprise_customer_service_inbox_id}
                      onChange={(e) => setForm((s) => ({ ...s, enterprise_customer_service_inbox_id: e.target.value }))}
                    />
                  </label>
                  <label>
                    Inbox Name
                    <input
                      value={form.enterprise_customer_service_inbox_name}
                      onChange={(e) =>
                        setForm((s) => ({ ...s, enterprise_customer_service_inbox_name: e.target.value }))
                      }
                    />
                  </label>
                </div>
                <label>
                  Customer Service Webhook URL
                  <div className="row">
                    <input
                      value={form.enterprise_customer_service_webhook_url}
                      readOnly
                      placeholder="Save the instance to generate the customer service webhook URL"
                    />
                    <button
                      type="button"
                      className="btn secondary"
                      disabled={!form.enterprise_customer_service_webhook_url}
                      onClick={async () => {
                        try {
                          await copyTextToClipboard(form.enterprise_customer_service_webhook_url);
                          alert('Customer service webhook URL copied');
                        } catch (e) {
                          alert(e?.message || String(e));
                        }
                      }}
                    >
                      Copy
                    </button>
                  </div>
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={Boolean(form.enterprise_customer_service_auto_create)}
                    onChange={(e) =>
                      setForm((s) => ({ ...s, enterprise_customer_service_auto_create: e.target.checked }))
                    }
                  />
                  Auto create customer service inbox
                </label>
                <label>
                  Waiting Text
                  <textarea
                    rows={3}
                    value={form.enterprise_customer_service_waiting_text}
                    onChange={(e) =>
                      setForm((s) => ({ ...s, enterprise_customer_service_waiting_text: e.target.value }))
                    }
                  />
                </label>
                <label>
                  Accepted Text
                  <textarea
                    rows={3}
                    value={form.enterprise_customer_service_accepted_text}
                    onChange={(e) =>
                      setForm((s) => ({ ...s, enterprise_customer_service_accepted_text: e.target.value }))
                    }
                  />
                </label>
                <label>
                  Unread Notification Text
                  <textarea
                    rows={3}
                    value={form.enterprise_customer_service_unread_text}
                    onChange={(e) =>
                      setForm((s) => ({ ...s, enterprise_customer_service_unread_text: e.target.value }))
                    }
                  />
                </label>
                <button
                  type="button"
                  className="btn"
                  disabled={busy || !selectedKey}
                  onClick={() => onCreateEnterpriseInbox('customer_service')}
                >
                  Create or Link Customer Service Inbox
                </button>

                <h3>Sales Route</h3>
                <div className="row">
                  <label>
                    Inbox ID
                    <input
                      value={form.enterprise_sales_inbox_id}
                      onChange={(e) => setForm((s) => ({ ...s, enterprise_sales_inbox_id: e.target.value }))}
                    />
                  </label>
                  <label>
                    Inbox Name
                    <input
                      value={form.enterprise_sales_inbox_name}
                      onChange={(e) => setForm((s) => ({ ...s, enterprise_sales_inbox_name: e.target.value }))}
                    />
                  </label>
                </div>
                <label>
                  Sales Webhook URL
                  <div className="row">
                    <input
                      value={form.enterprise_sales_webhook_url}
                      readOnly
                      placeholder="Save the instance to generate the sales webhook URL"
                    />
                    <button
                      type="button"
                      className="btn secondary"
                      disabled={!form.enterprise_sales_webhook_url}
                      onClick={async () => {
                        try {
                          await copyTextToClipboard(form.enterprise_sales_webhook_url);
                          alert('Sales webhook URL copied');
                        } catch (e) {
                          alert(e?.message || String(e));
                        }
                      }}
                    >
                      Copy
                    </button>
                  </div>
                </label>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={Boolean(form.enterprise_sales_auto_create)}
                    onChange={(e) => setForm((s) => ({ ...s, enterprise_sales_auto_create: e.target.checked }))}
                  />
                  Auto create sales inbox
                </label>
                <label>
                  Waiting Text
                  <textarea
                    rows={3}
                    value={form.enterprise_sales_waiting_text}
                    onChange={(e) => setForm((s) => ({ ...s, enterprise_sales_waiting_text: e.target.value }))}
                  />
                </label>
                <label>
                  Accepted Text
                  <textarea
                    rows={3}
                    value={form.enterprise_sales_accepted_text}
                    onChange={(e) => setForm((s) => ({ ...s, enterprise_sales_accepted_text: e.target.value }))}
                  />
                </label>
                <label>
                  Unread Notification Text
                  <textarea
                    rows={3}
                    value={form.enterprise_sales_unread_text}
                    onChange={(e) => setForm((s) => ({ ...s, enterprise_sales_unread_text: e.target.value }))}
                  />
                </label>
                <button
                  type="button"
                  className="btn"
                  disabled={busy || !selectedKey}
                  onClick={() => onCreateEnterpriseInbox('sales')}
                >
                  Create or Link Sales Inbox
                </button>

                <h3>External SMS Sync (Novin)</h3>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={Boolean(form.enterprise_sms_sync_enabled)}
                    onChange={(e) => setForm((s) => ({ ...s, enterprise_sms_sync_enabled: e.target.checked }))}
                  />
                  Enable SMS sync to Bale users by shared phone number
                </label>
                <label>
                  API URL
                  <input
                    value={form.enterprise_sms_api_url}
                    onChange={(e) => setForm((s) => ({ ...s, enterprise_sms_api_url: e.target.value }))}
                    placeholder="https://apiserver.novinmed.com/SoftNoNTFC/LastId"
                  />
                </label>
                <label>
                  API Token
                  <input
                    value={form.enterprise_sms_api_token}
                    onChange={(e) => setForm((s) => ({ ...s, enterprise_sms_api_token: e.target.value }))}
                    placeholder="token value"
                  />
                </label>
                <div className="row">
                  <label>
                    Token Header
                    <input
                      value={form.enterprise_sms_token_header}
                      onChange={(e) => setForm((s) => ({ ...s, enterprise_sms_token_header: e.target.value }))}
                      placeholder="Authorization"
                    />
                  </label>
                  <label>
                    Token Prefix
                    <input
                      value={form.enterprise_sms_token_prefix}
                      onChange={(e) => setForm((s) => ({ ...s, enterprise_sms_token_prefix: e.target.value }))}
                      placeholder="(empty for raw token)"
                    />
                  </label>
                </div>
                <div className="row">
                  <label>
                    Poll Interval (minutes)
                    <input
                      value={form.enterprise_sms_poll_interval_minutes}
                      onChange={(e) =>
                        setForm((s) => ({ ...s, enterprise_sms_poll_interval_minutes: e.target.value }))
                      }
                      placeholder="20"
                    />
                  </label>
                  <label>
                    HTTP Timeout (seconds)
                    <input
                      value={form.enterprise_sms_http_timeout_seconds}
                      onChange={(e) =>
                        setForm((s) => ({ ...s, enterprise_sms_http_timeout_seconds: e.target.value }))
                      }
                      placeholder="30"
                    />
                  </label>
                </div>
                <label>
                  LastId Cursor
                  <input
                    value={form.enterprise_sms_last_id}
                    onChange={(e) => setForm((s) => ({ ...s, enterprise_sms_last_id: e.target.value }))}
                    placeholder="0"
                  />
                </label>
                <div className="row">
                  <button
                    type="button"
                    className="btn"
                    disabled={busy || !selectedKey}
                    onClick={onSaveEnterpriseSmsSync}
                  >
                    Save SMS Sync Config
                  </button>
                  <button
                    type="button"
                    className="btn secondary"
                    disabled={busy || !selectedKey}
                    onClick={onRunEnterpriseSmsSyncNow}
                  >
                    Run SMS Sync Now
                  </button>
                </div>
              </>
            ) : null}

            <h3>Features</h3>
            {features.map((feature) => {
              const supported = isFeatureSupported(feature.key);
              const runtimeOverride = (instanceMap[selectedKey]?.feature_overrides || []).find(
                (item) => item.feature_key === feature.key,
              );
              const reason = !supported
                ? 'unsupported for selected platform'
                : runtimeOverride?.disabled_reason || '';
              return (
                <label className="checkbox" key={feature.key} title={feature.description}>
                  <input
                    type="checkbox"
                    checked={Boolean(form.feature_overrides[feature.key])}
                    disabled={!supported}
                    onChange={(e) =>
                      setForm((s) => ({
                        ...s,
                        feature_overrides: { ...s.feature_overrides, [feature.key]: e.target.checked },
                      }))
                    }
                  />
                  {feature.display_name}
                  {reason ? ` (${reason})` : ''}
                </label>
              );
            })}

            <button className="btn primary" type="submit" disabled={busy || loading}>
              {selectedKey ? 'Update Instance' : 'Create Instance'}
            </button>
          </form>
          </section>
        ) : null}

        {!isDetailView ? (
          <section className="card instance-browser">
          <h2>Instances</h2>
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
          {loading ? (
            <div className="muted">Loading...</div>
          ) : filteredInstances.length === 0 ? (
            <div className="muted">No instances found.</div>
          ) : (
            <div className="instance-card-grid">
              {filteredInstances.map((item) => {
                const isSelected = selectedKey === item.instance_key;
                const statusLabel = item.is_enabled ? 'Enabled' : 'Disabled';
                const isTelegram = item.platform_type_key === PLATFORM_TELEGRAM;
                const botName = isTelegram
                  ? item.platform_metadata?.telegram_bot_name || '-'
                  : item.platform_metadata?.bale_bot_name || '-';
                const botId = isTelegram
                  ? item.platform_metadata?.telegram_bot_id || '-'
                  : item.platform_metadata?.bale_bot_id || '-';
                const department = isTelegram
                  ? item.platform_metadata?.telegram_department || '-'
                  : item.platform_metadata?.bale_department || '-';
                const maskedToken = maskTokenValue(
                  isTelegram ? item.platform_metadata?.telegram_token : item.platform_metadata?.bale_token,
                );

                return (
                  <article
                    key={item.instance_key}
                    className={`instance-card ${item.is_enabled ? 'enabled' : 'disabled'} ${isSelected ? 'selected' : ''}`}
                    onClick={() => openInstanceDetail(item.instance_key)}
                  >
                    <div className="instance-card-head">
                      <h3>{item.instance_key}</h3>
                      <span className={`status-pill ${item.is_enabled ? 'good' : 'warn'}`}>{statusLabel}</span>
                    </div>

                    <div className="instance-token">{maskedToken}</div>

                    <div className="instance-meta">
                      <div>
                        <span className="k">Bot</span>
                        <span className="v">{botName}</span>
                      </div>
                      <div>
                        <span className="k">Bot ID</span>
                        <span className="v">{botId}</span>
                      </div>
                      <div>
                        <span className="k">Department</span>
                        <span className="v">{department}</span>
                      </div>
                      <div>
                        <span className="k">Platform</span>
                        <span className="v">{item.platform_type_key}</span>
                      </div>
                      <div>
                        <span className="k">Account</span>
                        <span className="v">{item.chatwoot?.account_id ?? '-'}</span>
                      </div>
                      <div>
                        <span className="k">Inbox</span>
                        <span className="v">{item.chatwoot?.inbox_id ?? '-'}</span>
                      </div>
                    </div>

                    <div className="list-actions">
                      <button
                        className="btn primary"
                        disabled={busy}
                        onClick={(e) => {
                          e.stopPropagation();
                          openInstanceDetail(item.instance_key);
                        }}
                      >
                        Open
                      </button>
                      <button
                        className="btn"
                        disabled={busy}
                        onClick={(e) => {
                          e.stopPropagation();
                          onToggleEnabled(item.instance_key, item.is_enabled);
                        }}
                      >
                        {item.is_enabled ? 'Disable' : 'Enable'}
                      </button>
                      <button
                        className="btn"
                        disabled={busy}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (item.platform_type_key === PLATFORM_BALE_ENTERPRISE) {
                            onCreateEnterpriseInbox('customer_service', item.instance_key);
                          } else {
                            onCreateInbox(item.instance_key);
                          }
                        }}
                      >
                        {item.platform_type_key === PLATFORM_BALE_ENTERPRISE ? 'Service Inbox' : 'Create Inbox'}
                      </button>
                      {item.platform_type_key === PLATFORM_BALE_ENTERPRISE ? (
                        <button
                          className="btn"
                          disabled={busy}
                          onClick={(e) => {
                          e.stopPropagation();
                            onCreateEnterpriseInbox('sales', item.instance_key);
                          }}
                        >
                          Sales Inbox
                        </button>
                      ) : null}
                      <button
                        className="btn danger"
                        disabled={busy}
                        onClick={(e) => {
                          e.stopPropagation();
                          onDelete(item.instance_key);
                        }}
                      >
                        Delete
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
          </section>
        ) : null}

        {isDetailView && !isEnterpriseBalePlatform ? (
          <section className="card">
            <h2>Mapping Explorer</h2>
          <div className="form">
            <div className="row">
              <label>
                Instance
                <select value={selectedKey} onChange={(e) => openInstanceDetail(e.target.value)}>
                  <option value="">Select instance</option>
                  {instances.map((item) => (
                    <option key={item.instance_key} value={item.instance_key}>
                      {item.instance_key}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Search
                <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="conversation id" />
              </label>
            </div>
            <button className="btn" disabled={busy || !selectedKey} onClick={onSearchConversations}>
              Filter
            </button>

            <div className="list">
              {conversations.map((item) => (
                <div
                  key={item.id}
                  className={`list-item ${selectedConversationId === item.id ? 'active' : ''}`}
                  onClick={() => setSelectedConversationId(item.id)}
                  >
                    <div className="list-main">
                      <div className="list-title">platform:{item.platform_conversation_id}</div>
                      <div className="list-meta">
                        chatwoot:{item.chatwoot_conversation_id} {item.is_active === false ? '(historical)' : '(current)'}
                      </div>
                    </div>
                  </div>
                ))}
              </div>

            {selectedConversationId ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Direction</th>
                      <th>Chatwoot Msg</th>
                      <th>Platform Msg</th>
                      <th>Reply (cw/platform)</th>
                      <th>Status</th>
                      <th>Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mappings.map((item) => (
                      <tr key={item.id}>
                        <td>{item.direction}</td>
                        <td>{item.chatwoot_message_id || '-'}</td>
                        <td>{item.platform_message_id || '-'}</td>
                        <td>
                          {item.chatwoot_parent_message_id || '-'} / {item.platform_parent_message_id || '-'}
                        </td>
                        <td>{item.status}</td>
                        <td>{new Date(item.created_at).toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </div>
          </section>
        ) : null}

        {isDetailView && isEnterpriseBalePlatform ? (
          <section className="card">
            <h2>Enterprise Assets</h2>
            {!selectedKey ? <div className="muted">Save the instance before uploading manuals or the catalog.</div> : null}

            <div className="form">
              <h3>Manuals</h3>
              <div className="row">
                <label>
                  Display Name
                  <input value={manualDisplayName} onChange={(e) => setManualDisplayName(e.target.value)} />
                </label>
                <label>
                  Link URL
                  <input
                    type="url"
                    placeholder="https://example.com/manual"
                    value={manualLinkUrl}
                    onChange={(e) => setManualLinkUrl(e.target.value)}
                  />
                </label>
                <label>
                  PDF File
                  <input type="file" accept="application/pdf,.pdf" onChange={(e) => setManualFile(e.target.files?.[0] || null)} />
                </label>
              </div>
              <button className="btn" type="button" disabled={busy || !selectedKey} onClick={onUploadManual}>
                Upload Manual
              </button>
              {enterpriseManuals.length === 0 ? (
                <div className="muted">No manuals uploaded.</div>
              ) : (
                <div className="list">
                  {enterpriseManuals.map((item) => (
                    <div key={item.id} className="list-item">
                      <div className="list-main">
                        <div className="list-title">{item.display_name || item.original_filename}</div>
                        <div className="list-meta">
                          {item.original_filename} · {(item.size_bytes / 1024).toFixed(1)} KB
                        </div>
                        <div className="list-meta">
                          Link:{' '}
                          {item.link_url ? (
                            <a href={item.link_url} target="_blank" rel="noreferrer">
                              {item.link_url}
                            </a>
                          ) : (
                            '-'
                          )}
                        </div>
                      </div>
                      <button className="btn danger" type="button" disabled={busy} onClick={() => onDeleteManual(item.id)}>
                        Delete
                      </button>
                    </div>
                  ))}
                </div>
              )}

              <h3>Catalog</h3>
              <div className="row">
                <label>
                  Display Name
                  <input value={catalogDisplayName} onChange={(e) => setCatalogDisplayName(e.target.value)} />
                </label>
                <label>
                  Link URL
                  <input
                    type="url"
                    placeholder="https://example.com/catalog"
                    value={catalogLinkUrl}
                    onChange={(e) => setCatalogLinkUrl(e.target.value)}
                  />
                </label>
                <label>
                  PDF File
                  <input type="file" accept="application/pdf,.pdf" onChange={(e) => setCatalogFile(e.target.files?.[0] || null)} />
                </label>
              </div>
              <div className="list-actions">
                <button className="btn" type="button" disabled={busy || !selectedKey} onClick={onReplaceCatalog}>
                  Upload or Replace Catalog
                </button>
                <button className="btn danger" type="button" disabled={busy || !enterpriseCatalog} onClick={onDeleteCatalog}>
                  Delete Catalog
                </button>
              </div>
              {enterpriseCatalog ? (
                <div className="list-item">
                  <div className="list-main">
                    <div className="list-title">{enterpriseCatalog.display_name || enterpriseCatalog.original_filename}</div>
                    <div className="list-meta">
                      {enterpriseCatalog.original_filename} · {(enterpriseCatalog.size_bytes / 1024).toFixed(1)} KB
                    </div>
                    <div className="list-meta">
                      Link:{' '}
                      {enterpriseCatalog.link_url ? (
                        <a href={enterpriseCatalog.link_url} target="_blank" rel="noreferrer">
                          {enterpriseCatalog.link_url}
                        </a>
                      ) : (
                        '-'
                      )}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="muted">No catalog uploaded.</div>
              )}
            </div>
          </section>
        ) : null}

        {isDetailView && isEnterpriseBalePlatform ? (
          <section className="card">
            <h2>Enterprise Sessions</h2>
            {enterpriseSessions.length === 0 ? (
              <div className="muted">No enterprise sessions yet.</div>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Route</th>
                      <th>Chat ID</th>
                      <th>Phone</th>
                      <th>Conversation</th>
                      <th>Status</th>
                      <th>Present</th>
                      <th>Unread</th>
                      <th>Updated</th>
                    </tr>
                  </thead>
                  <tbody>
                    {enterpriseSessions.map((item) => (
                      <tr key={item.id}>
                        <td>{item.route_key}</td>
                        <td>{item.platform_chat_id}</td>
                        <td>{item.phone_number || '-'}</td>
                        <td>{item.chatwoot_conversation_id}</td>
                        <td>{item.status}</td>
                        <td>{item.user_present ? 'yes' : 'no'}</td>
                        <td>{item.unread_count}</td>
                        <td>{new Date(item.updated_at).toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        ) : null}

        {isDetailView && !isEnterpriseBalePlatform ? (
          <section className="card">
            <h2>Simulate Platform Event</h2>
          <form className="form" onSubmit={onSimulate}>
            <div className="row">
              <label>
                Instance Key
                <input
                  value={simEvent.instance_key}
                  onChange={(e) => setSimEvent((s) => ({ ...s, instance_key: e.target.value }))}
                />
              </label>
              <label>
                Chat ID
                <input value={simEvent.chat_id} onChange={(e) => setSimEvent((s) => ({ ...s, chat_id: e.target.value }))} />
              </label>
            </div>
            <div className="row">
              <label>
                Platform Message ID
                <input
                  value={simEvent.platform_message_id}
                  onChange={(e) => setSimEvent((s) => ({ ...s, platform_message_id: e.target.value }))}
                />
              </label>
              <label>
                Parent Platform Message ID
                <input
                  value={simEvent.parent_platform_message_id}
                  onChange={(e) => setSimEvent((s) => ({ ...s, parent_platform_message_id: e.target.value }))}
                />
              </label>
            </div>
            <label>
              Text
              <textarea value={simEvent.text} onChange={(e) => setSimEvent((s) => ({ ...s, text: e.target.value }))} rows={3} />
            </label>
            <button className="btn primary" type="submit" disabled={busy}>
              Simulate
            </button>
          </form>
          </section>
        ) : null}
      </div>
    </div>
  );
}


