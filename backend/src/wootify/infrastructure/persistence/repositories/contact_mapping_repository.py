"""Persistent, instance-scoped contact identities independent of Chatwoot identifiers."""

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from wootify.infrastructure.persistence.models import ContactMapping


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
        return str(rows[0][0]) if len(rows) == 1 else None

    def save(
        self,
        instance_id: str,
        platform_contact_id: str,
        chatwoot_contact_id: str,
        *,
        platform_contact_type: str | None = None,
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
        row.chatwoot_contact_id = str(chatwoot_contact_id)
        if platform_contact_type:
            row.platform_contact_type = str(platform_contact_type).lower()
        self.db.add(row)
        self.db.flush()
        return row

    def delete(self, instance_id: str, platform_contact_id: str) -> None:
        row = self.get(instance_id, platform_contact_id)
        if row is not None:
            self.db.delete(row)
            self.db.flush()
