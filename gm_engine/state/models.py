from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def gen_uuid() -> str:
    return str(uuid4())


def _created_at() -> Column[DateTime]:
    return Column(DateTime, nullable=False, server_default=func.now())


def _updated_at() -> Column[DateTime]:
    return Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    name = Column(String(255), nullable=False)
    # NOTE: "metadata" is reserved by SQLAlchemy's DeclarativeBase.
    meta = Column("metadata", JSON, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()


class Player(Base):
    __tablename__ = "players"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    data = Column(JSON, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    campaign = relationship("Campaign")


class Character(Base):
    __tablename__ = "characters"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    attrs = Column(JSON, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    campaign = relationship("Campaign")


class NPC(Base):
    __tablename__ = "npcs"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    attrs = Column(JSON, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    campaign = relationship("Campaign")


class Location(Base):
    __tablename__ = "locations"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    meta = Column("metadata", JSON, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    campaign = relationship("Campaign")


class Quest(Base):
    __tablename__ = "quests"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    details = Column(JSON, nullable=True)
    status = Column(String(50), nullable=False, default="open")
    created_at = _created_at()
    updated_at = _updated_at()

    campaign = relationship("Campaign")


class Faction(Base):
    __tablename__ = "factions"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    info = Column(JSON, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    campaign = relationship("Campaign")


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    owner_id = Column(String(36), nullable=False)
    owner_type = Column(String(50), nullable=False)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    data = Column(JSON, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    campaign = relationship("Campaign")


class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    data = Column(JSON, nullable=True)
    created_at = _created_at()
    updated_at = _updated_at()

    campaign = relationship("Campaign")


class InteractionLog(Base):
    __tablename__ = "interaction_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=False, index=True)
    timestamp = Column(DateTime, server_default=func.now(), nullable=False)
    entry = Column(JSON, nullable=False)

    campaign = relationship("Campaign")


class DelayedEvent(Base):
    __tablename__ = "delayed_events"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    campaign_id = Column(String(36), ForeignKey("campaigns.id"), nullable=False, index=True)
    due_at = Column(DateTime, nullable=False)
    status = Column(String(20), nullable=False, server_default="pending")
    attempts = Column(Integer, nullable=False, server_default="0")
    last_error = Column(Text, nullable=True)
    payload = Column(JSON, nullable=False)
    created_at = _created_at()
    updated_at = _updated_at()

    campaign = relationship("Campaign")
