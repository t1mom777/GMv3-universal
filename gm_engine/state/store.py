from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy import func

from gm_engine.rlm.types import StateReadSpec, StateWriteSpec, TurnContext
from gm_engine.state import models
from gm_engine.state.db import make_db


@dataclass
class WorldStateStore:
    """DB-backed world state store.

    - Single source of truth: SQL DB.
    - Reads/writes are invoked by the RLM controller.
    - Writes are applied transactionally.
    """

    db_path: Path

    def __post_init__(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = make_db(self.db_path)
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            engine = self._db.engine()
            async with engine.begin() as conn:
                await conn.run_sync(models.Base.metadata.create_all)
            self._schema_ready = True

    async def ensure_campaign(self, ctx: TurnContext, *, name: str | None = None) -> None:
        """Ensure a campaign row exists for this ctx (dev convenience)."""
        await self.ensure_schema()
        async with self._db.sessionmaker()() as sess:
            async with sess.begin():
                row = await sess.get(models.Campaign, ctx.campaign_id)
                if row is not None:
                    return
                sess.add(
                    models.Campaign(
                        id=ctx.campaign_id,
                        name=name or f"Campaign {ctx.campaign_id}",
                        meta=None,
                    )
                )

    async def list_player_profiles(self, campaign_id: str) -> list[dict[str, str]]:
        """Return player profiles stored for a campaign."""
        await self.ensure_schema()
        async with self._db.sessionmaker()() as sess:
            q = (
                select(models.Player)
                .where(models.Player.campaign_id == campaign_id)
                .order_by(models.Player.created_at.asc(), models.Player.id.asc())
            )
            rows = (await sess.execute(q)).scalars().all()
            out: list[dict[str, str]] = []
            seen: set[str] = set()
            for r in rows:
                d = r.data if isinstance(r.data, dict) else {}
                pid = str(d.get("player_id") or "").strip()
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                display = str(d.get("display_name") or r.name or pid).strip() or pid
                voice_profile = str(d.get("voice_profile") or "").strip()
                out.append(
                    {
                        "player_id": pid,
                        "display_name": display,
                        "voice_profile": voice_profile,
                    }
                )
                if len(out) >= 8:
                    break
            return out

    async def upsert_player_profiles(self, campaign_id: str, profiles: list[dict[str, Any]]) -> int:
        """Create/update player profiles for a campaign, preserving existing history."""
        cleaned: list[dict[str, str]] = []
        seen: set[str] = set()
        for p in list(profiles or []):
            pid = str((p or {}).get("player_id") or "").strip()
            if not pid or pid in seen:
                continue
            seen.add(pid)
            display = str((p or {}).get("display_name") or pid).strip() or pid
            voice_profile = str((p or {}).get("voice_profile") or "").strip()
            cleaned.append(
                {
                    "player_id": pid[:64],
                    "display_name": display[:64],
                    "voice_profile": voice_profile[:120],
                }
            )
            if len(cleaned) >= 8:
                break
        if not cleaned:
            return 0

        await self.ensure_schema()
        now = datetime.now(timezone.utc).isoformat()
        async with self._db.sessionmaker()() as sess:
            async with sess.begin():
                q = select(models.Player).where(models.Player.campaign_id == campaign_id)
                rows = (await sess.execute(q)).scalars().all()
                by_pid: dict[str, models.Player] = {}
                for r in rows:
                    d = r.data if isinstance(r.data, dict) else {}
                    pid = str(d.get("player_id") or "").strip()
                    if pid and pid not in by_pid:
                        by_pid[pid] = r

                for p in cleaned:
                    pid = p["player_id"]
                    display = p["display_name"] or pid
                    voice_profile = p["voice_profile"]
                    row = by_pid.get(pid)
                    if row is None:
                        sess.add(
                            models.Player(
                                campaign_id=campaign_id,
                                name=display,
                                data={
                                    "player_id": pid,
                                    "display_name": display,
                                    "voice_profile": voice_profile,
                                    "last_seen_at": now,
                                },
                            )
                        )
                        continue
                    d = row.data if isinstance(row.data, dict) else {}
                    d["player_id"] = pid
                    d["display_name"] = display
                    d["voice_profile"] = voice_profile
                    d["last_seen_at"] = now
                    row.name = display
                    row.data = d
                    sess.add(row)
        return len(cleaned)

    async def ensure_player_profile(
        self,
        ctx: TurnContext,
        *,
        display_name: str | None = None,
        voice_profile: str | None = None,
    ) -> None:
        """Ensure a single player profile exists and is marked seen for this turn."""
        pid = str(ctx.player_id or "").strip()
        if not pid:
            return
        display = str(display_name or pid).strip() or pid
        voice = str(voice_profile or "").strip()
        await self.upsert_player_profiles(
            ctx.campaign_id,
            [{"player_id": pid, "display_name": display, "voice_profile": voice}],
        )

    async def read(self, ctx: TurnContext, reads: list[StateReadSpec]) -> dict[str, Any]:
        await self.ensure_schema()
        out: dict[str, Any] = {"campaign_id": ctx.campaign_id}
        async with self._db.sessionmaker()() as sess:
            for spec in reads:
                kind = spec.kind
                params = spec.params

                if kind == "campaign_snapshot":
                    q = select(models.Campaign).where(models.Campaign.id == ctx.campaign_id)
                    row = (await sess.execute(q)).scalars().first()
                    out["campaign"] = (
                        {"id": row.id, "name": row.name, "meta": row.meta} if row is not None else None
                    )

                elif kind == "characters":
                    limit = int(params.get("limit", 100))
                    q = (
                        select(models.Character)
                        .where(models.Character.campaign_id == ctx.campaign_id)
                        .limit(limit)
                    )
                    rows = (await sess.execute(q)).scalars().all()
                    out["characters"] = [{"id": c.id, "name": c.name, "attrs": c.attrs} for c in rows]

                elif kind == "interaction_log":
                    limit = int(params.get("limit", 100))
                    fetch_limit = max(limit * 5, 200)
                    q = (
                        select(models.InteractionLog)
                        .where(models.InteractionLog.campaign_id == ctx.campaign_id)
                        .order_by(models.InteractionLog.id.desc())
                        .limit(fetch_limit)
                    )
                    rows = (await sess.execute(q)).scalars().all()
                    entries = [r.entry for r in reversed(rows)]
                    session_id = str(params.get("session_id") or "").strip()
                    if session_id:
                        entries = [
                            e
                            for e in entries
                            if isinstance(e, dict) and str(e.get("session_id") or "").strip() == session_id
                        ]
                    player_id = str(params.get("player_id") or "").strip()
                    if player_id:
                        entries = [
                            e
                            for e in entries
                            if isinstance(e, dict) and str(e.get("player_id") or "").strip() == player_id
                        ]
                    out["interaction_log"] = entries[-max(1, limit) :]

                elif kind == "delayed_events":
                    limit = int(params.get("limit", 100))
                    q = (
                        select(models.DelayedEvent)
                        .where(models.DelayedEvent.campaign_id == ctx.campaign_id)
                        .limit(limit)
                    )
                    rows = (await sess.execute(q)).scalars().all()
                    out["delayed_events"] = [
                        {
                            "id": e.id,
                            "due_at": e.due_at.isoformat(),
                            "status": e.status,
                            "attempts": e.attempts,
                            "last_error": e.last_error,
                            "payload": e.payload,
                        }
                        for e in rows
                    ]
                else:
                    out.setdefault("unknown_reads", []).append({"kind": kind, "params": params})

        return out

    async def apply_writes(self, ctx: TurnContext, writes: list[StateWriteSpec]) -> None:
        await self.ensure_schema()
        async with self._db.sessionmaker()() as sess:
            async with sess.begin():
                for spec in writes:
                    kind = spec.kind
                    p = spec.params

                    if kind == "append_log":
                        sess.add(models.InteractionLog(campaign_id=ctx.campaign_id, entry=p["entry"]))
                        continue

                    if kind == "schedule_delayed_event":
                        sess.add(
                            models.DelayedEvent(
                                campaign_id=ctx.campaign_id,
                                due_at=p["due_at"],
                                payload=p["payload"],
                                status=p.get("status", "pending"),
                                attempts=int(p.get("attempts", 0)),
                                last_error=p.get("last_error"),
                            )
                        )
                        continue

                    # Generic CRUD (limited)
                    op = p.get("op")
                    model_name = p.get("model")
                    model_cls = _model_for_name(model_name)
                    if model_cls is None:
                        continue

                    if op == "insert":
                        obj = dict(p.get("obj") or {})
                        obj["campaign_id"] = ctx.campaign_id
                        sess.add(model_cls(**obj))
                    elif op == "update":
                        pk = p.get("id")
                        fields = p.get("fields") or {}
                        if not pk:
                            continue
                        row = await sess.get(model_cls, pk)
                        if row is None:
                            continue
                        for k, v in fields.items():
                            setattr(row, k, v)
                        sess.add(row)
                    elif op == "delete":
                        pk = p.get("id")
                        if pk:
                            await sess.execute(delete(model_cls).where(model_cls.id == pk))

    async def schedule_delayed_event(self, ctx: TurnContext, ev: dict) -> None:
        # Compatibility shim
        await self.apply_writes(
            ctx,
            [
                StateWriteSpec(
                    kind="schedule_delayed_event",
                    params={"due_at": ev["due_at"], "payload": ev.get("payload", {})},
                )
            ],
        )

    async def append_log(self, ctx: TurnContext, record: dict) -> None:
        await self.apply_writes(ctx, [StateWriteSpec(kind="append_log", params={"entry": record})])

    async def clear_interaction_log(self, ctx: TurnContext) -> int:
        """Delete all interaction_log entries for a campaign. Returns number of rows deleted."""
        await self.ensure_schema()
        async with self._db.sessionmaker()() as sess:
            async with sess.begin():
                q = select(func.count()).select_from(models.InteractionLog).where(
                    models.InteractionLog.campaign_id == ctx.campaign_id
                )
                n = int((await sess.execute(q)).scalar() or 0)
                await sess.execute(
                    delete(models.InteractionLog).where(models.InteractionLog.campaign_id == ctx.campaign_id)
                )
                return n

    async def clear_interaction_log_filtered(
        self,
        ctx: TurnContext,
        *,
        session_id: str | None = None,
        player_id: str | None = None,
    ) -> int:
        """Delete interaction_log entries by session/player filters. Returns number of rows deleted."""
        session_id = str(session_id or "").strip() or None
        player_id = str(player_id or "").strip() or None
        if not session_id and not player_id:
            return await self.clear_interaction_log(ctx)

        await self.ensure_schema()
        async with self._db.sessionmaker()() as sess:
            async with sess.begin():
                q = (
                    select(models.InteractionLog)
                    .where(models.InteractionLog.campaign_id == ctx.campaign_id)
                    .order_by(models.InteractionLog.id.desc())
                )
                rows = (await sess.execute(q)).scalars().all()
                ids: list[int] = []
                for r in rows:
                    e = r.entry if isinstance(r.entry, dict) else {}
                    if session_id and str(e.get("session_id") or "").strip() != session_id:
                        continue
                    if player_id and str(e.get("player_id") or "").strip() != player_id:
                        continue
                    ids.append(int(r.id))

                if not ids:
                    return 0
                await sess.execute(delete(models.InteractionLog).where(models.InteractionLog.id.in_(ids)))
                return len(ids)

    async def clear_delayed_events(self, ctx: TurnContext) -> int:
        """Delete all delayed_events for a campaign. Returns number of rows deleted."""
        await self.ensure_schema()
        async with self._db.sessionmaker()() as sess:
            async with sess.begin():
                q = select(func.count()).select_from(models.DelayedEvent).where(
                    models.DelayedEvent.campaign_id == ctx.campaign_id
                )
                n = int((await sess.execute(q)).scalar() or 0)
                await sess.execute(
                    delete(models.DelayedEvent).where(models.DelayedEvent.campaign_id == ctx.campaign_id)
                )
                return n

    async def latest_campaign_id(self) -> str | None:
        """Best-effort: campaign with the most recent interaction_log entry (by max id)."""
        await self.ensure_schema()
        async with self._db.sessionmaker()() as sess:
            q = (
                select(
                    models.InteractionLog.campaign_id,
                    func.max(models.InteractionLog.id).label("max_id"),
                )
                .group_by(models.InteractionLog.campaign_id)
                .order_by(func.max(models.InteractionLog.id).desc())
                .limit(1)
            )
            row = (await sess.execute(q)).first()
            if not row:
                return None
            return str(row[0])


def _model_for_name(name: str | None):
    if not name:
        return None
    return {
        "campaigns": models.Campaign,
        "players": models.Player,
        "characters": models.Character,
        "npcs": models.NPC,
        "locations": models.Location,
        "quests": models.Quest,
        "factions": models.Faction,
        "inventory_items": models.InventoryItem,
        "timeline_events": models.TimelineEvent,
    }.get(name)
