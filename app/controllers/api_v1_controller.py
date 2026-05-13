"""
Module Overview
---------------
Purpose: HTTP route handlers and API endpoint orchestration.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import base64
import binascii
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.api_v1 import (
    AutoCreateInboxResponse,
    ConversationListResponse,
    ConversationResponse,
    CreateInboxResponse,
    EnterpriseAutoCreateInboxResponse,
    EnterpriseCatalogResponse,
    EnterpriseDocumentAssetResponse,
    EnterpriseDocumentAssetPatchRequest,
    EnterpriseDocumentListResponse,
    EnterpriseManualGroupCreateRequest,
    EnterpriseManualGroupListResponse,
    EnterpriseManualGroupManualsResponse,
    EnterpriseManualGroupResponse,
    EnterpriseManualGroupUpdateRequest,
    EnterpriseRouteInboxResponse,
    EnterpriseSessionListResponse,
    EnterpriseSessionResponse,
    EnterpriseSmsSyncConfigPatchRequest,
    EnterpriseSmsSyncConfigResponse,
    EnterpriseSmsSyncRunResponse,
    FeatureDefinitionResponse,
    GenericMessageResponse,
    InstanceCreateRequest,
    InstanceListResponse,
    InstancePatchRequest,
    InstanceResponse,
    MessageMappingListResponse,
    MessageMappingResponse,
    PlatformTypeResponse,
    SimulatePlatformEventRequest,
)
from app.services.bridge_service import BridgeService
from app.services.conversation_mapping_service import ConversationMappingService
from app.services.enterprise_bale_service import EnterpriseBaleService
from app.services.enterprise_telegram_service import EnterpriseTelegramService
from app.services.enterprise_document_service import EnterpriseDocumentService
from app.services.enterprise_manual_group_service import EnterpriseManualGroupService
from app.services.instance_service import InstanceService
from app.services.message_mapping_service import MessageMappingService
from app.services.platform_registry_service import PlatformRegistryService

router = APIRouter(prefix='/api/v1', tags=['api-v1'])

platform_registry = PlatformRegistryService()
instances = InstanceService()
bridge = BridgeService()
enterprise = EnterpriseBaleService()
enterprise_telegram = EnterpriseTelegramService()
enterprise_documents = EnterpriseDocumentService()
enterprise_manual_groups = EnterpriseManualGroupService()
conversations = ConversationMappingService()
messages = MessageMappingService()
logger = logging.getLogger('app.controllers.api_v1')


def _raise_http_error(
    *,
    status_code: int,
    detail: str,
    endpoint: str,
    exc: Optional[Exception] = None,
    **context: Any,
) -> None:
    """Log endpoint failures and raise an HTTPException."""
    context_text = ' '.join(f'{key}={value}' for key, value in context.items() if value is not None)
    if status_code >= 500:
        if exc is not None:
            logger.exception(
                'endpoint=%s status=%s detail=%s %s',
                endpoint,
                status_code,
                detail,
                context_text,
            )
        else:
            logger.error('endpoint=%s status=%s detail=%s %s', endpoint, status_code, detail, context_text)
    else:
        if exc is not None:
            logger.warning(
                'endpoint=%s status=%s detail=%s error=%s %s',
                endpoint,
                status_code,
                detail,
                str(exc),
                context_text,
            )
        else:
            logger.warning('endpoint=%s status=%s detail=%s %s', endpoint, status_code, detail, context_text)

    raise HTTPException(status_code=status_code, detail=detail)


async def _maybe_auto_create_inbox(
    *,
    db: Session,
    instance_key: str,
    chatwoot_payload: Optional[dict[str, Any]],
) -> Optional[AutoCreateInboxResponse]:
    """Best-effort auto-create inbox step used during instance save flows."""
    cfg = chatwoot_payload if isinstance(chatwoot_payload, dict) else {}
    if not bool(cfg.get('auto_create')):
        return None

    try:
        data = await bridge.create_chatwoot_inbox(db, instance_key)
        return AutoCreateInboxResponse(
            attempted=True,
            created=bool(data.get('created')),
            inbox_id=data.get('inbox_id'),
            detail=(
                'created'
                if data.get('created')
                else 'existing_inbox_webhook_updated'
                if data.get('webhook_updated')
                else 'existing_inbox_reused'
            ),
        )
    except ValueError as exc:
        logger.warning(
            'auto-create inbox skipped instance_key=%s error=%s',
            instance_key,
            str(exc),
        )
        return AutoCreateInboxResponse(
            attempted=True,
            created=False,
            detail=str(exc),
        )
    except Exception:
        logger.exception('auto-create inbox failed instance_key=%s', instance_key)
        return AutoCreateInboxResponse(
            attempted=True,
            created=False,
            detail='auto_create_failed',
        )


async def _maybe_auto_create_enterprise_inboxes(
    *,
    db: Session,
    instance: Optional[InstanceResponse],
) -> Optional[list[EnterpriseAutoCreateInboxResponse]]:
    """Best-effort enterprise route inbox creation during instance save flows."""
    if instance is None:
        return None

    platform_key = instance.platform_type_key
    platform_metadata = instance.platform_metadata if isinstance(instance.platform_metadata, dict) else {}
    results: list[EnterpriseAutoCreateInboxResponse] = []

    if platform_key == 'bale_enterprise':
        for route_key, auto_key in (
            ('customer_service', 'enterprise_customer_service_auto_create'),
            ('sales', 'enterprise_sales_auto_create'),
        ):
            if not bool(platform_metadata.get(auto_key)):
                continue
            try:
                data = await enterprise.create_route_inbox(db, instance.instance_key, route_key)
                results.append(
                    EnterpriseAutoCreateInboxResponse(
                        route_key=route_key,
                        attempted=True,
                        created=bool(data.get('created')),
                        inbox_id=data.get('inbox_id'),
                        detail=(
                            'created'
                            if data.get('created')
                            else 'existing_inbox_webhook_updated'
                            if data.get('webhook_updated')
                            else 'existing_inbox_reused'
                        ),
                    )
                )
            except ValueError as exc:
                logger.warning(
                    'enterprise auto-create inbox skipped instance_key=%s route=%s error=%s',
                    instance.instance_key,
                    route_key,
                    str(exc),
                )
                results.append(
                    EnterpriseAutoCreateInboxResponse(
                        route_key=route_key,
                        attempted=True,
                        created=False,
                        detail=str(exc),
                    )
                )
            except Exception:
                logger.exception(
                    'enterprise auto-create inbox failed instance_key=%s route=%s',
                    instance.instance_key,
                    route_key,
                )
                results.append(
                    EnterpriseAutoCreateInboxResponse(
                        route_key=route_key,
                        attempted=True,
                        created=False,
                        detail='auto_create_failed',
                    )
                )

    if platform_key == 'telegram_enterprise':
        routes = platform_metadata.get('enterprise_routes') or []
        for route in routes:
            if not isinstance(route, dict):
                continue
            route_key = route.get('route_key')
            if not route_key or not bool(route.get('auto_create')):
                continue
            try:
                data = await enterprise_telegram.create_route_inbox(db, instance.instance_key, route_key)
                results.append(
                    EnterpriseAutoCreateInboxResponse(
                        route_key=route_key,
                        attempted=True,
                        created=bool(data.get('created')),
                        inbox_id=data.get('inbox_id'),
                        detail=(
                            'created'
                            if data.get('created')
                            else 'existing_inbox_webhook_updated'
                            if data.get('webhook_updated')
                            else 'existing_inbox_reused'
                        ),
                    )
                )
            except ValueError as exc:
                logger.warning(
                    'enterprise telegram auto-create inbox skipped instance_key=%s route=%s error=%s',
                    instance.instance_key,
                    route_key,
                    str(exc),
                )
                results.append(
                    EnterpriseAutoCreateInboxResponse(
                        route_key=route_key,
                        attempted=True,
                        created=False,
                        detail=str(exc),
                    )
                )
            except Exception:
                logger.exception(
                    'enterprise telegram auto-create inbox failed instance_key=%s route=%s',
                    instance.instance_key,
                    route_key,
                )
                results.append(
                    EnterpriseAutoCreateInboxResponse(
                        route_key=route_key,
                        attempted=True,
                        created=False,
                        detail='auto_create_failed',
                    )
                )

    return results or None


@router.get('/platform-types', response_model=list[PlatformTypeResponse])
def list_platform_types(db: Session = Depends(get_db)):
    """List platform types."""
    try:
        return platform_registry.list_platform_types(db)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='list_platform_types', exc=exc)


@router.get('/features', response_model=list[FeatureDefinitionResponse])
def list_features(db: Session = Depends(get_db)):
    """List features."""
    try:
        return platform_registry.list_features(db)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='list_features', exc=exc)


@router.get('/instances', response_model=InstanceListResponse)
def list_instances(db: Session = Depends(get_db)):
    """List instances."""
    try:
        return InstanceListResponse(items=instances.list_instances(db))
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='list_instances', exc=exc)


@router.post('/instances', response_model=InstanceResponse, status_code=status.HTTP_201_CREATED)
async def create_instance(payload: InstanceCreateRequest, db: Session = Depends(get_db)):
    """Create instance."""
    try:
        response = instances.create_instance(db, payload)
        auto_create_result = await _maybe_auto_create_inbox(
            db=db,
            instance_key=response.instance_key,
            chatwoot_payload=payload.chatwoot,
        )
        enterprise_auto_create_results = await _maybe_auto_create_enterprise_inboxes(
            db=db,
            instance=response,
        )
        if auto_create_result is not None:
            refreshed = instances.get_instance(db, response.instance_key)
            if refreshed is not None:
                response = refreshed
            response = response.model_copy(update={'auto_create_inbox': auto_create_result})
        if enterprise_auto_create_results is not None:
            refreshed = instances.get_instance(db, response.instance_key)
            if refreshed is not None:
                response = refreshed
            response = response.model_copy(update={'enterprise_auto_create_inboxes': enterprise_auto_create_results})
        return response
    except ValueError as exc:
        _raise_http_error(
            status_code=400,
            detail=str(exc),
            endpoint='create_instance',
            exc=exc,
            instance_key=payload.instance_key,
        )
    except Exception as exc:
        _raise_http_error(
            status_code=500,
            detail='internal server error',
            endpoint='create_instance',
            exc=exc,
            instance_key=payload.instance_key,
        )


@router.get('/instances/{instance_key}', response_model=InstanceResponse)
def get_instance(instance_key: str, db: Session = Depends(get_db)):
    """Get instance."""
    try:
        row = instances.get_instance(db, instance_key)
        if not row:
            _raise_http_error(
                status_code=404,
                detail='instance not found',
                endpoint='get_instance',
                instance_key=instance_key,
            )
        return row
    except HTTPException:
        raise
    except Exception as exc:
        _raise_http_error(
            status_code=500,
            detail='internal server error',
            endpoint='get_instance',
            exc=exc,
            instance_key=instance_key,
        )


@router.patch('/instances/{instance_key}', response_model=InstanceResponse)
async def patch_instance(instance_key: str, payload: InstancePatchRequest, db: Session = Depends(get_db)):
    """Patch instance."""
    try:
        row = instances.update_instance(db, instance_key, payload)
        auto_create_result = await _maybe_auto_create_inbox(
            db=db,
            instance_key=instance_key,
            chatwoot_payload=payload.chatwoot,
        )
        enterprise_auto_create_results = await _maybe_auto_create_enterprise_inboxes(db=db, instance=row)
    except ValueError as exc:
        _raise_http_error(
            status_code=400,
            detail=str(exc),
            endpoint='patch_instance',
            exc=exc,
            instance_key=instance_key,
        )
    except Exception as exc:
        _raise_http_error(
            status_code=500,
            detail='internal server error',
            endpoint='patch_instance',
            exc=exc,
            instance_key=instance_key,
        )

    if not row:
        _raise_http_error(
            status_code=404,
            detail='instance not found',
            endpoint='patch_instance',
            instance_key=instance_key,
        )
    if auto_create_result is not None:
        refreshed = instances.get_instance(db, instance_key)
        if refreshed is not None:
            row = refreshed
        row = row.model_copy(update={'auto_create_inbox': auto_create_result})
    if enterprise_auto_create_results is not None:
        refreshed = instances.get_instance(db, instance_key)
        if refreshed is not None:
            row = refreshed
        row = row.model_copy(update={'enterprise_auto_create_inboxes': enterprise_auto_create_results})
    return row


@router.delete('/instances/{instance_key}', response_model=GenericMessageResponse)
def delete_instance(instance_key: str, db: Session = Depends(get_db)):
    """Delete instance."""
    try:
        if not instances.delete_instance(db, instance_key):
            _raise_http_error(
                status_code=404,
                detail='instance not found',
                endpoint='delete_instance',
                instance_key=instance_key,
            )
        return GenericMessageResponse(message='deleted', status='ok')
    except HTTPException:
        raise
    except Exception as exc:
        _raise_http_error(
            status_code=500,
            detail='internal server error',
            endpoint='delete_instance',
            exc=exc,
            instance_key=instance_key,
        )


@router.post('/instances/{instance_key}/chatwoot/inbox', response_model=CreateInboxResponse)
async def create_chatwoot_inbox(instance_key: str, db: Session = Depends(get_db)):
    """Create chatwoot inbox."""
    try:
        runtime = _require_instance_runtime(db, instance_key)
        if runtime.platform_type.key in {'bale_enterprise', 'telegram_enterprise'}:
            _raise_http_error(
                status_code=400,
                detail='use the enterprise route inbox endpoint for enterprise instances',
                endpoint='create_chatwoot_inbox',
                instance_key=instance_key,
            )
        data = await bridge.create_chatwoot_inbox(db, instance_key)
        return CreateInboxResponse(
            created=bool(data.get('created')),
            inbox_id=data.get('inbox_id'),
            inbox=data.get('inbox') if isinstance(data.get('inbox'), dict) else None,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        _raise_http_error(
            status_code=400,
            detail=str(exc),
            endpoint='create_chatwoot_inbox',
            exc=exc,
            instance_key=instance_key,
        )
    except Exception as exc:
        _raise_http_error(
            status_code=500,
            detail='internal server error',
            endpoint='create_chatwoot_inbox',
            exc=exc,
            instance_key=instance_key,
        )


@router.post('/webhooks/chatwoot/{instance_key}', response_model=GenericMessageResponse)
async def webhook_chatwoot(instance_key: str, payload: dict[str, Any], db: Session = Depends(get_db)):
    """Webhook chatwoot."""
    return await _handle_chatwoot_webhook(db, instance_key, payload, route_key=None)


@router.post('/webhooks/chatwoot/{instance_key}/enterprise/{route_key}', response_model=GenericMessageResponse)
async def webhook_chatwoot_enterprise_route(
    instance_key: str,
    route_key: str,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
):
    """Route-specific Chatwoot webhook for Bale Enterprise inboxes."""
    return await _handle_chatwoot_webhook(db, instance_key, payload, route_key=route_key)


async def _handle_chatwoot_webhook(
    db: Session,
    instance_key: str,
    payload: dict[str, Any],
    *,
    route_key: Optional[str],
) -> GenericMessageResponse:
    """Shared Chatwoot webhook handler."""
    try:
        runtime = _resolve_chatwoot_webhook_runtime(db, instance_key, payload)
        resolved_instance_key = runtime.instance.instance_key
        if runtime.platform_type.key == 'bale_enterprise':
            result = await enterprise.receive_chatwoot_webhook(db, resolved_instance_key, payload)
        elif runtime.platform_type.key == 'telegram_enterprise':
            result = await enterprise_telegram.receive_chatwoot_webhook(db, resolved_instance_key, payload)
        else:
            result = await bridge.receive_chatwoot_webhook(db, resolved_instance_key, payload)
        return GenericMessageResponse(
            message=str(result.get('message') or 'ok'),
            detail=result.get('detail'),
            status=result.get('status'),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        _raise_http_error(
            status_code=404,
            detail=str(exc),
            endpoint='webhook_chatwoot',
            exc=exc,
            instance_key=instance_key,
        )
    except RuntimeError as exc:
        logger.warning(
            'endpoint=webhook_chatwoot status=200 detail=delivery_failed instance_key=%s route_key=%s error=%s',
            instance_key,
            route_key,
            str(exc),
        )
        return GenericMessageResponse(
            message='delivery_failed',
            detail=str(exc),
            status='failed',
        )
    except Exception as exc:
        _raise_http_error(
            status_code=500,
            detail='internal server error',
            endpoint='webhook_chatwoot',
            exc=exc,
            instance_key=instance_key,
            route_key=route_key,
        )

@router.post('/simulate/platform/{instance_key}', response_model=GenericMessageResponse)
async def simulate_platform_event(instance_key: str, payload: SimulatePlatformEventRequest, db: Session = Depends(get_db)):
    """Simulate platform event."""
    try:
        event = {
            'chat_id': payload.chat_id,
            'from_name': payload.from_name,
            'text': payload.text,
            'platform_message_id': payload.platform_message_id,
            'parent_platform_message_id': payload.parent_platform_message_id,
            'attachments': [],
        }

        for item in payload.attachments:
            raw = base64.b64decode(item.content_base64.encode())
            event['attachments'].append(
                {
                    'filename': item.filename,
                    'content': raw,
                    'content_type': item.content_type,
                }
            )

        result = await bridge.ingest_platform_event(db, instance_key, event)
        return GenericMessageResponse(
            message=str(result.get('message') or 'ok'),
            detail=result.get('detail'),
            status=result.get('status'),
        )
    except binascii.Error as exc:
        _raise_http_error(
            status_code=400,
            detail='invalid simulated attachment payload',
            endpoint='simulate_platform_event',
            exc=exc,
            instance_key=instance_key,
        )
    except ValueError as exc:
        _raise_http_error(
            status_code=400,
            detail=str(exc),
            endpoint='simulate_platform_event',
            exc=exc,
            instance_key=instance_key,
        )
    except Exception as exc:
        _raise_http_error(
            status_code=500,
            detail='internal server error',
            endpoint='simulate_platform_event',
            exc=exc,
            instance_key=instance_key,
        )


def _require_instance_runtime(db: Session, instance_key: str):
    """Require instance runtime."""
    runtime = instances.get_runtime_instance(db, instance_key)
    if not runtime:
        _raise_http_error(
            status_code=404,
            detail='instance not found',
            endpoint='_require_instance_runtime',
            instance_key=instance_key,
        )
    return runtime


def _resolve_chatwoot_webhook_runtime(db: Session, instance_key: str, payload: dict[str, Any]):
    """Resolve a Chatwoot webhook runtime, including stale callback-url fallbacks."""
    runtime = instances.get_runtime_instance(db, instance_key)
    if runtime:
        return runtime

    inbox_id, inbox_name = _extract_chatwoot_webhook_inbox(payload)
    matches: list[str] = []
    for row in instances.list_instances(db):
        if _instance_matches_chatwoot_inbox(row, inbox_id=inbox_id, inbox_name=inbox_name):
            matches.append(row.instance_key)

    unique_matches = list(dict.fromkeys(matches))
    if len(unique_matches) == 1:
        resolved_key = unique_matches[0]
        runtime = instances.get_runtime_instance(db, resolved_key)
        if runtime is not None:
            logger.warning(
                'chatwoot webhook resolved by inbox mapping requested_instance_key=%s resolved_instance_key=%s inbox_id=%s inbox_name=%s',
                instance_key,
                resolved_key,
                inbox_id,
                inbox_name,
            )
            return runtime

    if len(unique_matches) > 1:
        logger.warning(
            'chatwoot webhook runtime ambiguous requested_instance_key=%s inbox_id=%s inbox_name=%s matches=%s',
            instance_key,
            inbox_id,
            inbox_name,
            ','.join(unique_matches),
        )

    _raise_http_error(
        status_code=404,
        detail='instance not found',
        endpoint='_resolve_chatwoot_webhook_runtime',
        instance_key=instance_key,
        inbox_id=inbox_id,
        inbox_name=inbox_name,
    )


def _extract_chatwoot_webhook_inbox(payload: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Extract inbox id/name candidates from a Chatwoot webhook payload."""
    conversation = payload.get('conversation') if isinstance(payload.get('conversation'), dict) else {}
    inbox = payload.get('inbox') if isinstance(payload.get('inbox'), dict) else {}
    conversation_meta = conversation.get('meta') if isinstance(conversation.get('meta'), dict) else {}
    meta_inbox = conversation_meta.get('inbox') if isinstance(conversation_meta.get('inbox'), dict) else {}

    inbox_id = _normalize_optional_string(
        conversation.get('inbox_id')
        or payload.get('inbox_id')
        or inbox.get('id')
        or meta_inbox.get('id')
    )
    inbox_name = _normalize_optional_string(
        conversation.get('inbox_name')
        or inbox.get('name')
        or meta_inbox.get('name')
    )
    return inbox_id, inbox_name


def _instance_matches_chatwoot_inbox(
    row: InstanceResponse,
    *,
    inbox_id: Optional[str],
    inbox_name: Optional[str],
) -> bool:
    """Check whether an instance references a Chatwoot inbox id or name."""
    chatwoot = row.chatwoot if isinstance(row.chatwoot, dict) else {}
    platform_metadata = row.platform_metadata if isinstance(row.platform_metadata, dict) else {}
    configured_pairs = [
        (
            _normalize_optional_string(chatwoot.get('inbox_id')),
            _normalize_optional_string(chatwoot.get('inbox_name')),
        ),
        (
            _normalize_optional_string(platform_metadata.get('enterprise_customer_service_inbox_id')),
            _normalize_optional_string(platform_metadata.get('enterprise_customer_service_inbox_name')),
        ),
        (
            _normalize_optional_string(platform_metadata.get('enterprise_sales_inbox_id')),
            _normalize_optional_string(platform_metadata.get('enterprise_sales_inbox_name')),
        ),
    ]

    # Add dynamic telegram_enterprise routes
    routes = platform_metadata.get('enterprise_routes') or []
    if isinstance(routes, list):
        for route in routes:
            if isinstance(route, dict):
                configured_pairs.append((
                    _normalize_optional_string(route.get('inbox_id')),
                    _normalize_optional_string(route.get('inbox_name')),
                ))

    normalized_inbox_name = str(inbox_name or '').strip().casefold() or None
    for configured_id, configured_name in configured_pairs:
        if inbox_id and configured_id and configured_id == inbox_id:
            return True
        if normalized_inbox_name and configured_name and configured_name.casefold() == normalized_inbox_name:
            return True
    return False


def _normalize_optional_string(value: Any) -> Optional[str]:
    """Normalize optional scalar values into trimmed strings."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _conv_to_response(row) -> ConversationResponse:
    """Conv to response."""
    return ConversationResponse(
        id=row.id,
        instance_id=row.instance_id,
        platform_conversation_id=row.platform_conversation_id,
        chatwoot_conversation_id=row.chatwoot_conversation_id,
        chatwoot_contact_id=row.chatwoot_contact_id,
        chatwoot_inbox_id=row.chatwoot_inbox_id,
        is_active=bool(getattr(row, 'is_active', True)),
        last_activity_at=row.last_activity_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _msg_to_response(row) -> MessageMappingResponse:
    """Msg to response."""
    return MessageMappingResponse(
        id=row.id,
        conversation_id=row.conversation_id,
        direction=row.direction.value,
        chatwoot_message_id=row.chatwoot_message_id,
        platform_message_id=row.platform_message_id,
        chatwoot_parent_message_id=row.chatwoot_parent_message_id,
        platform_parent_message_id=row.platform_parent_message_id,
        message_kind=row.message_kind.value,
        status=row.status.value,
        error_code=row.error_code,
        error_detail=row.error_detail,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _enterprise_asset_to_response(row) -> EnterpriseDocumentAssetResponse:
    """Convert an enterprise asset row to its response schema."""
    return EnterpriseDocumentAssetResponse(
        id=row.id,
        asset_type=row.asset_type.value,
        display_name=row.display_name,
        link_url=str(row.link_url or ''),
        original_filename=row.original_filename,
        content_type=row.content_type,
        size_bytes=int(row.size_bytes or 0),
        sort_order=int(row.sort_order or 0),
        is_active=bool(row.is_active),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _enterprise_session_to_response(row) -> EnterpriseSessionResponse:
    """Convert an enterprise session row to its response schema."""
    user = row.user
    gre_status = getattr(user, 'gre_status', None)
    current_state = getattr(user, 'current_state', None)
    return EnterpriseSessionResponse(
        id=row.id,
        route_key=row.route_key,
        platform_chat_id=user.platform_chat_id,
        display_name=user.display_name,
        phone_number=getattr(user, 'phone_number', None),
        gre_status=gre_status.value if gre_status is not None else None,
        current_state=current_state.value if hasattr(current_state, 'value') else str(current_state or 'root'),
        chatwoot_conversation_id=row.chatwoot_conversation_id,
        chatwoot_contact_id=row.chatwoot_contact_id,
        chatwoot_inbox_id=row.chatwoot_inbox_id,
        status=row.status.value,
        user_present=bool(row.user_present),
        accepted_notice_sent=bool(row.accepted_notice_sent),
        unread_notice_sent=bool(row.unread_notice_sent),
        unread_count=int(row.unread_count or 0),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _enterprise_manual_group_to_response(row) -> EnterpriseManualGroupResponse:
    """Convert an enterprise manual group row to its response schema."""
    return EnterpriseManualGroupResponse(
        id=row.id,
        name=row.name,
        sort_order=int(row.sort_order or 0),
        is_active=bool(row.is_active),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get('/instances/{instance_key}/conversations', response_model=ConversationListResponse)
def list_instance_conversations(instance_key: str, q: Optional[str] = None, db: Session = Depends(get_db)):
    """List instance conversations."""
    try:
        runtime = _require_instance_runtime(db, instance_key)
        rows = conversations.list_for_instance(db, runtime.instance.id)
        if q:
            needle = q.strip().lower()
            rows = [
                row
                for row in rows
                if needle in row.platform_conversation_id.lower() or needle in row.chatwoot_conversation_id.lower()
            ]
        return ConversationListResponse(items=[_conv_to_response(item) for item in rows])
    except HTTPException:
        raise
    except Exception as exc:
        _raise_http_error(
            status_code=500,
            detail='internal server error',
            endpoint='list_instance_conversations',
            exc=exc,
            instance_key=instance_key,
        )


@router.get('/instances/{instance_key}/enterprise/manuals', response_model=EnterpriseDocumentListResponse)
def list_enterprise_manuals(instance_key: str, db: Session = Depends(get_db)):
    """List enterprise manual assets for an instance."""
    try:
        rows = enterprise_documents.list_manuals(db, instance_key)
        return EnterpriseDocumentListResponse(items=[_enterprise_asset_to_response(item) for item in rows])
    except ValueError as exc:
        _raise_http_error(status_code=400, detail=str(exc), endpoint='list_enterprise_manuals', exc=exc, instance_key=instance_key)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='list_enterprise_manuals', exc=exc, instance_key=instance_key)


@router.post('/instances/{instance_key}/enterprise/manuals', response_model=EnterpriseDocumentAssetResponse, status_code=status.HTTP_201_CREATED)
async def upload_enterprise_manual(
    instance_key: str,
    display_name: str = Form(...),
    link_url: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload an enterprise manual asset."""
    try:
        row = await enterprise_documents.upload_manual(
            db,
            instance_key,
            display_name=display_name,
            link_url=link_url,
            upload=file,
        )
        return _enterprise_asset_to_response(row)
    except ValueError as exc:
        _raise_http_error(status_code=400, detail=str(exc), endpoint='upload_enterprise_manual', exc=exc, instance_key=instance_key)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='upload_enterprise_manual', exc=exc, instance_key=instance_key)


@router.delete('/instances/{instance_key}/enterprise/manuals/{asset_id}', response_model=GenericMessageResponse)
def delete_enterprise_manual(instance_key: str, asset_id: str, db: Session = Depends(get_db)):
    """Delete an enterprise manual asset."""
    try:
        deleted = enterprise_documents.delete_asset(db, instance_key, asset_id)
        if not deleted:
            _raise_http_error(status_code=404, detail='asset not found', endpoint='delete_enterprise_manual', instance_key=instance_key, asset_id=asset_id)
        return GenericMessageResponse(message='deleted', status='ok')
    except HTTPException:
        raise
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='delete_enterprise_manual', exc=exc, instance_key=instance_key, asset_id=asset_id)


@router.patch('/instances/{instance_key}/enterprise/manuals/{asset_id}', response_model=EnterpriseDocumentAssetResponse)
def patch_enterprise_manual(
    instance_key: str,
    asset_id: str,
    request: EnterpriseDocumentAssetPatchRequest,
    db: Session = Depends(get_db),
):
    """Patch enterprise manual metadata (display name and/or link URL)."""
    try:
        row = enterprise_documents.update_manual_metadata(
            db,
            instance_key,
            asset_id,
            display_name=request.display_name,
            link_url=request.link_url,
        )
        if not row:
            _raise_http_error(
                status_code=404,
                detail='asset not found',
                endpoint='patch_enterprise_manual',
                instance_key=instance_key,
                asset_id=asset_id,
            )
        return _enterprise_asset_to_response(row)
    except HTTPException:
        raise
    except ValueError as exc:
        _raise_http_error(
            status_code=400,
            detail=str(exc),
            endpoint='patch_enterprise_manual',
            exc=exc,
            instance_key=instance_key,
            asset_id=asset_id,
        )
    except Exception as exc:
        _raise_http_error(
            status_code=500,
            detail='internal server error',
            endpoint='patch_enterprise_manual',
            exc=exc,
            instance_key=instance_key,
            asset_id=asset_id,
        )


@router.get('/instances/{instance_key}/enterprise/catalog', response_model=EnterpriseCatalogResponse)
def get_enterprise_catalog(instance_key: str, db: Session = Depends(get_db)):
    """Get the active enterprise catalog asset."""
    try:
        row = enterprise_documents.get_catalog(db, instance_key)
        return EnterpriseCatalogResponse(item=_enterprise_asset_to_response(row) if row else None)
    except ValueError as exc:
        _raise_http_error(status_code=400, detail=str(exc), endpoint='get_enterprise_catalog', exc=exc, instance_key=instance_key)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='get_enterprise_catalog', exc=exc, instance_key=instance_key)


@router.put('/instances/{instance_key}/enterprise/catalog', response_model=EnterpriseDocumentAssetResponse)
async def replace_enterprise_catalog(
    instance_key: str,
    display_name: Optional[str] = Form(None),
    link_url: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Replace the enterprise catalog asset."""
    try:
        row = await enterprise_documents.replace_catalog(
            db,
            instance_key,
            display_name=display_name,
            link_url=link_url,
            upload=file,
        )
        return _enterprise_asset_to_response(row)
    except ValueError as exc:
        _raise_http_error(status_code=400, detail=str(exc), endpoint='replace_enterprise_catalog', exc=exc, instance_key=instance_key)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='replace_enterprise_catalog', exc=exc, instance_key=instance_key)


@router.delete('/instances/{instance_key}/enterprise/catalog', response_model=GenericMessageResponse)
def delete_enterprise_catalog(instance_key: str, db: Session = Depends(get_db)):
    """Delete the active enterprise catalog asset."""
    try:
        row = enterprise_documents.get_catalog(db, instance_key)
        if not row:
            _raise_http_error(status_code=404, detail='asset not found', endpoint='delete_enterprise_catalog', instance_key=instance_key)
        enterprise_documents.delete_asset(db, instance_key, row.id)
        return GenericMessageResponse(message='deleted', status='ok')
    except HTTPException:
        raise
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='delete_enterprise_catalog', exc=exc, instance_key=instance_key)


@router.get('/instances/{instance_key}/enterprise/manual-groups', response_model=EnterpriseManualGroupListResponse)
def list_enterprise_manual_groups(instance_key: str, db: Session = Depends(get_db)):
    """List manual groups for an instance."""
    try:
        rows = enterprise_manual_groups.list_groups(db, instance_key)
        return EnterpriseManualGroupListResponse(items=[_enterprise_manual_group_to_response(item) for item in rows])
    except ValueError as exc:
        _raise_http_error(status_code=400, detail=str(exc), endpoint='list_enterprise_manual_groups', exc=exc, instance_key=instance_key)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='list_enterprise_manual_groups', exc=exc, instance_key=instance_key)


@router.post('/instances/{instance_key}/enterprise/manual-groups', response_model=EnterpriseManualGroupResponse, status_code=status.HTTP_201_CREATED)
def create_enterprise_manual_group(
    instance_key: str,
    request: EnterpriseManualGroupCreateRequest,
    db: Session = Depends(get_db),
):
    """Create a new manual group for an instance."""
    try:
        row = enterprise_manual_groups.create_group(db, instance_key, request.name)
        return _enterprise_manual_group_to_response(row)
    except ValueError as exc:
        _raise_http_error(status_code=400, detail=str(exc), endpoint='create_enterprise_manual_group', exc=exc, instance_key=instance_key)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='create_enterprise_manual_group', exc=exc, instance_key=instance_key)


@router.put('/instances/{instance_key}/enterprise/manual-groups/{group_id}', response_model=EnterpriseManualGroupResponse)
def update_enterprise_manual_group(
    instance_key: str,
    group_id: str,
    request: EnterpriseManualGroupUpdateRequest,
    db: Session = Depends(get_db),
):
    """Update a manual group (rename)."""
    try:
        row = enterprise_manual_groups.rename_group(db, instance_key, group_id, request.name)
        return _enterprise_manual_group_to_response(row)
    except ValueError as exc:
        _raise_http_error(status_code=400, detail=str(exc), endpoint='update_enterprise_manual_group', exc=exc, instance_key=instance_key, group_id=group_id)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='update_enterprise_manual_group', exc=exc, instance_key=instance_key, group_id=group_id)


@router.delete('/instances/{instance_key}/enterprise/manual-groups/{group_id}', response_model=GenericMessageResponse)
def delete_enterprise_manual_group(instance_key: str, group_id: str, db: Session = Depends(get_db)):
    """Delete a manual group."""
    try:
        deleted = enterprise_manual_groups.delete_group(db, instance_key, group_id)
        if not deleted:
            _raise_http_error(status_code=404, detail='group not found', endpoint='delete_enterprise_manual_group', instance_key=instance_key, group_id=group_id)
        return GenericMessageResponse(message='deleted', status='ok')
    except HTTPException:
        raise
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='delete_enterprise_manual_group', exc=exc, instance_key=instance_key, group_id=group_id)


@router.get('/instances/{instance_key}/enterprise/manual-groups/{group_id}/manuals', response_model=EnterpriseManualGroupManualsResponse)
def list_enterprise_manual_group_manuals(instance_key: str, group_id: str, db: Session = Depends(get_db)):
    """List manuals assigned to a group."""
    try:
        rows = enterprise_manual_groups.list_group_manuals(db, instance_key, group_id)
        return EnterpriseManualGroupManualsResponse(items=[_enterprise_asset_to_response(item) for item in rows])
    except ValueError as exc:
        _raise_http_error(status_code=400, detail=str(exc), endpoint='list_enterprise_manual_group_manuals', exc=exc, instance_key=instance_key, group_id=group_id)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='list_enterprise_manual_group_manuals', exc=exc, instance_key=instance_key, group_id=group_id)


@router.post('/instances/{instance_key}/enterprise/manual-groups/{group_id}/manuals/{asset_id}', response_model=GenericMessageResponse, status_code=status.HTTP_201_CREATED)
def add_manual_to_enterprise_group(
    instance_key: str,
    group_id: str,
    asset_id: str,
    db: Session = Depends(get_db),
):
    """Add a manual to a group."""
    try:
        enterprise_manual_groups.add_manual_to_group(db, instance_key, group_id, asset_id)
        return GenericMessageResponse(message='added', status='ok')
    except ValueError as exc:
        _raise_http_error(status_code=400, detail=str(exc), endpoint='add_manual_to_enterprise_group', exc=exc, instance_key=instance_key, group_id=group_id, asset_id=asset_id)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='add_manual_to_enterprise_group', exc=exc, instance_key=instance_key, group_id=group_id, asset_id=asset_id)


@router.delete('/instances/{instance_key}/enterprise/manual-groups/{group_id}/manuals/{asset_id}', response_model=GenericMessageResponse)
def remove_manual_from_enterprise_group(
    instance_key: str,
    group_id: str,
    asset_id: str,
    db: Session = Depends(get_db),
):
    """Remove a manual from a group."""
    try:
        deleted = enterprise_manual_groups.remove_manual_from_group(db, instance_key, group_id, asset_id)
        if not deleted:
            _raise_http_error(status_code=404, detail='assignment not found', endpoint='remove_manual_from_enterprise_group', instance_key=instance_key, group_id=group_id, asset_id=asset_id)
        return GenericMessageResponse(message='removed', status='ok')
    except HTTPException:
        raise
    except ValueError as exc:
        _raise_http_error(status_code=400, detail=str(exc), endpoint='remove_manual_from_enterprise_group', exc=exc, instance_key=instance_key, group_id=group_id, asset_id=asset_id)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='remove_manual_from_enterprise_group', exc=exc, instance_key=instance_key, group_id=group_id, asset_id=asset_id)


@router.post('/instances/{instance_key}/enterprise/chatwoot/inboxes/{route_key}', response_model=EnterpriseRouteInboxResponse)
async def create_enterprise_route_inbox(instance_key: str, route_key: str, db: Session = Depends(get_db)):
    """Create or link a route-specific enterprise Chatwoot inbox."""
    try:
        data = await enterprise.create_route_inbox(db, instance_key, route_key)
        return EnterpriseRouteInboxResponse(
            route_key=route_key,
            created=bool(data.get('created')),
            webhook_url=data.get('webhook_url'),
            inbox_id=data.get('inbox_id'),
            inbox=data.get('inbox') if isinstance(data.get('inbox'), dict) else None,
        )
    except ValueError as exc:
        _raise_http_error(status_code=400, detail=str(exc), endpoint='create_enterprise_route_inbox', exc=exc, instance_key=instance_key, route_key=route_key)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='create_enterprise_route_inbox', exc=exc, instance_key=instance_key, route_key=route_key)


@router.get('/instances/{instance_key}/enterprise/sessions', response_model=EnterpriseSessionListResponse)
def list_enterprise_sessions(instance_key: str, db: Session = Depends(get_db)):
    """List enterprise live-chat sessions for an instance."""
    try:
        runtime = _require_instance_runtime(db, instance_key)
        if runtime.platform_type.key == 'telegram_enterprise':
            rows = enterprise_telegram.list_sessions(db, instance_key)
        else:
            rows = enterprise.list_sessions(db, instance_key)
        return EnterpriseSessionListResponse(items=[_enterprise_session_to_response(item) for item in rows])
    except HTTPException:
        raise
    except ValueError as exc:
        _raise_http_error(status_code=400, detail=str(exc), endpoint='list_enterprise_sessions', exc=exc, instance_key=instance_key)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='list_enterprise_sessions', exc=exc, instance_key=instance_key)


@router.get('/instances/{instance_key}/enterprise/sms-sync', response_model=EnterpriseSmsSyncConfigResponse)
def get_enterprise_sms_sync_config(instance_key: str, db: Session = Depends(get_db)):
    """Get dedicated enterprise SMS sync configuration for an instance."""
    try:
        data = enterprise.get_sms_sync_config(db, instance_key)
        return EnterpriseSmsSyncConfigResponse(**data)
    except ValueError as exc:
        _raise_http_error(status_code=400, detail=str(exc), endpoint='get_enterprise_sms_sync_config', exc=exc, instance_key=instance_key)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='get_enterprise_sms_sync_config', exc=exc, instance_key=instance_key)


@router.patch('/instances/{instance_key}/enterprise/sms-sync', response_model=EnterpriseSmsSyncConfigResponse)
def patch_enterprise_sms_sync_config(
    instance_key: str,
    payload: EnterpriseSmsSyncConfigPatchRequest,
    db: Session = Depends(get_db),
):
    """Patch dedicated enterprise SMS sync configuration for an instance."""
    try:
        data = enterprise.update_sms_sync_config(db, instance_key, payload.model_dump(exclude_unset=True))
        return EnterpriseSmsSyncConfigResponse(**data)
    except ValueError as exc:
        _raise_http_error(status_code=400, detail=str(exc), endpoint='patch_enterprise_sms_sync_config', exc=exc, instance_key=instance_key)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='patch_enterprise_sms_sync_config', exc=exc, instance_key=instance_key)


@router.post('/instances/{instance_key}/enterprise/sms-sync/run', response_model=EnterpriseSmsSyncRunResponse)
async def run_enterprise_sms_sync(instance_key: str, db: Session = Depends(get_db)):
    """Run enterprise SMS sync immediately for an instance."""
    try:
        result = await enterprise.sync_external_sms_messages(db, instance_key)
        return EnterpriseSmsSyncRunResponse(
            message=str(result.get('message') or 'ok'),
            fetched=int(result.get('fetched') or 0),
            delivered=int(result.get('delivered') or 0),
            dropped=int(result.get('dropped') or 0),
            failed=int(result.get('failed') or 0),
            last_id=int(result.get('last_id')) if result.get('last_id') is not None else None,
            detail=result.get('detail'),
        )
    except ValueError as exc:
        _raise_http_error(status_code=400, detail=str(exc), endpoint='run_enterprise_sms_sync', exc=exc, instance_key=instance_key)
    except Exception as exc:
        _raise_http_error(status_code=500, detail='internal server error', endpoint='run_enterprise_sms_sync', exc=exc, instance_key=instance_key)


@router.get('/instances/{instance_key}/conversations/{conversation_id}', response_model=ConversationResponse)
def get_instance_conversation(instance_key: str, conversation_id: str, db: Session = Depends(get_db)):
    """Get instance conversation."""
    try:
        runtime = _require_instance_runtime(db, instance_key)
        row = conversations.get_for_instance(db, runtime.instance.id, conversation_id)
        if not row:
            _raise_http_error(
                status_code=404,
                detail='conversation not found',
                endpoint='get_instance_conversation',
                instance_key=instance_key,
                conversation_id=conversation_id,
            )
        return _conv_to_response(row)
    except HTTPException:
        raise
    except Exception as exc:
        _raise_http_error(
            status_code=500,
            detail='internal server error',
            endpoint='get_instance_conversation',
            exc=exc,
            instance_key=instance_key,
            conversation_id=conversation_id,
        )


@router.get('/instances/{instance_key}/conversations/{conversation_id}/messages', response_model=MessageMappingListResponse)
def list_conversation_messages(instance_key: str, conversation_id: str, db: Session = Depends(get_db)):
    """List conversation messages."""
    try:
        runtime = _require_instance_runtime(db, instance_key)
        row = conversations.get_for_instance(db, runtime.instance.id, conversation_id)
        if not row:
            _raise_http_error(
                status_code=404,
                detail='conversation not found',
                endpoint='list_conversation_messages',
                instance_key=instance_key,
                conversation_id=conversation_id,
            )

        mapped = messages.list_for_conversation(db, row.id)
        return MessageMappingListResponse(items=[_msg_to_response(item) for item in mapped])
    except HTTPException:
        raise
    except Exception as exc:
        _raise_http_error(
            status_code=500,
            detail='internal server error',
            endpoint='list_conversation_messages',
            exc=exc,
            instance_key=instance_key,
            conversation_id=conversation_id,
        )

