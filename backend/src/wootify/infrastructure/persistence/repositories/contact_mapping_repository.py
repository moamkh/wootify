"""Persistent, instance-scoped contact identities independent of Chatwoot identifiers."""

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from wootify.infrastructure.persistence.models import ContactMapping


class ContactMappingConflict(RuntimeError):
    """An identity is ambiguous; delivery must stop instead of guessing."""


class ContactMappingRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, instance_id: str, platform_contact_id: str) -> ContactMapping | None:
        return (
            self.db.query(ContactMapping)
            .filter(
                ContactMapping.instance_id == str(instance_id),
                ContactMapping.platform_contact_id == str(platform_contact_id),
            )
            .one_or_none()
        )

    def unique_platform_id_for_chatwoot(self, instance_id: str, chatwoot_contact_id: str) -> str | None:
        """Never guess when one Chatwoot contact represents multiple platform peers."""
        rows = (
            self.db.query(ContactMapping.platform_contact_id)
            .filter(
                ContactMapping.instance_id == str(instance_id),
                ContactMapping.chatwoot_contact_id == str(chatwoot_contact_id),
            )
            .limit(2)
            .all()
        )
        if len(rows) > 1:
            raise ContactMappingConflict("multiple_platform_peers_for_chatwoot_contact")
        return str(rows[0][0]) if rows else None

    def save(
        self,
        instance_id: str,
        platform_contact_id: str,
        chatwoot_contact_id: str,
        *,
        platform_contact_type: str | None = None,
        chatwoot_scope: str | None = None,
    ) -> ContactMapping:
        row = self.get(instance_id, platform_contact_id)
        if row is None:
            row = ContactMapping(
                instance_id=str(instance_id),
                platform_contact_id=str(platform_contact_id),
                chatwoot_contact_id=str(chatwoot_contact_id),
            )
            try:
                # Two pollers can discover the same peer concurrently. Keep a
                # uniqueness race from aborting the surrounding transaction.
                with self.db.begin_nested():
                    self.db.add(row)
                    self.db.flush()
            except IntegrityError:
                row = self.get(instance_id, platform_contact_id)
                if row is None:
                    raise
        if row.chatwoot_contact_id != str(chatwoot_contact_id):
            raise ContactMappingConflict("contact_mapping_rebind_requires_verified_recovery")
        if chatwoot_scope and row.chatwoot_scope and row.chatwoot_scope != chatwoot_scope:
            raise ContactMappingConflict("chatwoot_account_changed_for_contact_mapping")
        if chatwoot_scope:
            row.chatwoot_scope = chatwoot_scope
        if platform_contact_type:
            peer_type = str(platform_contact_type).lower()
            peer_type = "user" if peer_type == "private" else peer_type
            current_type = "user" if row.platform_contact_type == "private" else row.platform_contact_type
            if current_type and current_type != "unknown" and peer_type != "unknown" and current_type != peer_type:
                raise ContactMappingConflict("platform_peer_type_conflict")
            if peer_type != "unknown":
                row.platform_contact_type = peer_type
        self.db.add(row)
        self.db.flush()
        return row

    def delete(self, instance_id: str, platform_contact_id: str) -> None:
        row = self.get(instance_id, platform_contact_id)
        if row is not None:
            self.db.delete(row)
            self.db.flush()
