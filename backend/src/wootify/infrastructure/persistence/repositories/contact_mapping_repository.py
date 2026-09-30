"""Persistent, instance-scoped contact identities independent of Chatwoot identifiers."""

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from wootify.infrastructure.persistence.models import ContactAlias, ContactMapping


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

    def unique_platform_id_for_chatwoot(self, instance_id: str, chatwoot_contact_id: str, *, chatwoot_scope: str | None = None) -> str | None:
        """Never guess when one Chatwoot contact represents multiple platform peers."""
        rows = (
            self.db.query(ContactMapping)
            .filter(
                ContactMapping.instance_id == str(instance_id),
                ContactMapping.chatwoot_contact_id == str(chatwoot_contact_id),
            )
            .all()
        )
        peers = {str(row.platform_contact_id) for row in rows if not chatwoot_scope or not row.chatwoot_scope or row.chatwoot_scope == chatwoot_scope}
        aliases = self.db.query(ContactAlias).filter_by(
            instance_id=str(instance_id), chatwoot_contact_id=str(chatwoot_contact_id),
        ).all()
        peers.update(str(row.platform_contact_id) for row in aliases if not chatwoot_scope or row.chatwoot_scope == chatwoot_scope)
        if len(peers) > 1:
            raise ContactMappingConflict("multiple_platform_peers_for_chatwoot_contact")
        return next(iter(peers), None)

    def get_verified_alias(self, instance_id: str, chatwoot_contact_id: str, chatwoot_scope: str) -> ContactAlias | None:
        return self.db.query(ContactAlias).filter_by(
            instance_id=str(instance_id), chatwoot_contact_id=str(chatwoot_contact_id),
            chatwoot_scope=chatwoot_scope,
        ).one_or_none()

    def save_verified_alias(
        self, instance_id: str, platform_contact_id: str, chatwoot_contact_id: str,
        *, chatwoot_scope: str, verification_method: str,
    ) -> ContactAlias:
        """Record a second contact only after the caller proves its peer identity."""
        canonical = self.get(instance_id, platform_contact_id)
        if canonical is None or (canonical.chatwoot_scope and canonical.chatwoot_scope != chatwoot_scope):
            raise ContactMappingConflict("contact_alias_requires_scoped_canonical_mapping")
        existing_peer = self.unique_platform_id_for_chatwoot(
            instance_id, chatwoot_contact_id, chatwoot_scope=chatwoot_scope,
        )
        if existing_peer and existing_peer != str(platform_contact_id):
            raise ContactMappingConflict("contact_alias_owned_by_other_platform_peer")
        row = self.get_verified_alias(instance_id, chatwoot_contact_id, chatwoot_scope)
        if row is None:
            try:
                with self.db.begin_nested():
                    row = ContactAlias(
                        instance_id=str(instance_id),
                        chatwoot_scope=chatwoot_scope,
                        chatwoot_contact_id=str(chatwoot_contact_id),
                        platform_contact_id=str(platform_contact_id),
                        verification_method=verification_method,
                    )
                    self.db.add(row)
                    self.db.flush()
            except IntegrityError:
                row = self.get_verified_alias(instance_id, chatwoot_contact_id, chatwoot_scope)
                if row is None:
                    raise
        if row.platform_contact_id != str(platform_contact_id):
            raise ContactMappingConflict("contact_alias_owned_by_other_platform_peer")
        return row

    def save(
        self,
        instance_id: str,
        platform_contact_id: str,
        chatwoot_contact_id: str,
        *,
        platform_contact_type: str | None = None,
        chatwoot_scope: str | None = None,
    ) -> ContactMapping:
        existing_peer = self.unique_platform_id_for_chatwoot(
            instance_id, chatwoot_contact_id, chatwoot_scope=chatwoot_scope,
        )
        if existing_peer and existing_peer != str(platform_contact_id):
            raise ContactMappingConflict("chatwoot_contact_owned_by_other_platform_peer")
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
