from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from gm_engine.app.settings import AppSettings
from gm_engine.app.settings_store import SettingsStore
from gm_engine.logging.events import EventLogger
from gm_engine.knowledge.store import KnowledgeStore
from gm_engine.rlm.types import Budget, NarrationPlan, RetrievalSpec, StateReadSpec, StateWriteSpec, TurnContext
from gm_engine.state.store import WorldStateStore


class LLMProvider(Protocol):
    async def complete(self, *, system: str, user: str, temperature: float = 0.2) -> str: ...


@dataclass
class RLMController:
    """Recursive Language Model controller.

    Key properties:
    - Pipecat (or any client) can only call `handle_turn`.
    - LLM calls happen only through this controller via LLMProvider.
    - Recursion is bounded and auditable.
    """

    llm: LLMProvider
    state: WorldStateStore
    knowledge: KnowledgeStore
    logger: EventLogger
    budget: Budget = Budget()
    settings_store: SettingsStore | None = None

    def _settings(self) -> AppSettings:
        if self.settings_store is None:
            return AppSettings()
        return self.settings_store.get()

    def _render(self, template: str, vars: dict[str, str]) -> str:
        pat = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")

        def repl(m: re.Match[str]) -> str:
            return vars.get(m.group(1), "")

        return pat.sub(repl, template)

    def _format_memory(self, state_view: dict[str, Any], *, max_turns: int) -> str:
        entries = state_view.get("interaction_log") or []
        if not isinstance(entries, list):
            return ""
        entries = entries[-max_turns:]
        lines: list[str] = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            if e.get("kind") == "turn":
                pid = str(e.get("player_id") or "").strip()
                pt = str(e.get("player_text") or "").strip()
                gt = str(e.get("gm_text") or "").strip()
                if pt:
                    who = f"PLAYER({pid})" if pid else "PLAYER"
                    lines.append(f"{who}: {pt}")
                if gt:
                    lines.append(f"GM: {gt}")
                for fu in (e.get("followups") or []) if isinstance(e.get("followups"), list) else []:
                    fu_s = str(fu or "").strip()
                    if fu_s:
                        lines.append(f"GM: {fu_s}")
            else:
                # Unknown entry kind; keep compact JSON.
                try:
                    lines.append(json.dumps(e, ensure_ascii=True, separators=(",", ":")))
                except Exception:
                    pass
        return "\n".join(lines).strip()

    def _format_snippets(self, knowledge_hits: list[dict], *, max_snippets: int = 3) -> str:
        out: list[str] = []
        for h in knowledge_hits[:max_snippets]:
            meta = h.get("meta") if isinstance(h, dict) else None
            meta = meta if isinstance(meta, dict) else {}
            doc_id = meta.get("doc_id") or ""
            doc_kind = meta.get("doc_kind") or ""
            ruleset = meta.get("ruleset") or ""
            page = meta.get("page") or ""
            ctype = meta.get("type") or ""
            hdr = " ".join(
                p
                for p in [
                    str(doc_id),
                    str(doc_kind) if doc_kind else "",
                    str(ruleset) if ruleset else "",
                    f"p{page}" if page else "",
                    str(ctype),
                ]
                if p
            ).strip()
            hdr = f"[{hdr}]" if hdr else "[knowledge]"
            txt = str(h.get("text") or "").strip() if isinstance(h, dict) else ""
            if not txt:
                continue
            out.append(f"{hdr}\n{txt}")
        return ("\n\n".join(out)).strip()

    def _knowledge_sources(self, knowledge_hits: list[dict], *, max_sources: int = 5) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for h in knowledge_hits:
            if not isinstance(h, dict):
                continue
            meta = h.get("meta") if isinstance(h.get("meta"), dict) else {}
            doc_id = str(meta.get("doc_id") or "").strip() or "unknown_doc"
            page = str(meta.get("page") or "").strip()
            ctype = str(meta.get("type") or "").strip()
            src = doc_id
            if page:
                src += f" p{page}"
            if ctype:
                src += f" {ctype}"
            if src in seen:
                continue
            seen.add(src)
            out.append(src)
            if len(out) >= max_sources:
                break
        return out

    def _lang_base(self, tag: str | None) -> str:
        s = str(tag or "").strip().replace("_", "-").lower()
        if not s:
            return ""
        return s.split("-", 1)[0]

    def _looks_englishish(self, text: str) -> bool:
        t = str(text or "").strip()
        if not t:
            return False
        low = t.lower()
        # Strong non-Latin signal => not English.
        if any(
            ("\u0400" <= ch <= "\u04ff")
            or ("\u0600" <= ch <= "\u06ff")
            or ("\u0900" <= ch <= "\u097f")
            or ("\u4e00" <= ch <= "\u9fff")
            or ("\u3040" <= ch <= "\u30ff")
            or ("\uac00" <= ch <= "\ud7af")
            for ch in low
        ):
            return False
        words = re.findall(r"[a-zA-Z']+", low)
        if not words:
            return False
        common = {
            "the",
            "and",
            "you",
            "your",
            "are",
            "is",
            "to",
            "of",
            "in",
            "with",
            "for",
            "what",
            "next",
            "start",
            "roll",
            "check",
        }
        hits = sum(1 for w in words if w in common)
        return hits >= 2 or (hits >= 1 and len(words) >= 6)

    async def handle_turn(self, ctx: TurnContext) -> NarrationPlan:
        started = time.perf_counter()
        self.logger.turn_started(ctx)

        # Dev convenience: ensure campaign row exists before any FK-backed writes.
        await self.state.ensure_campaign(ctx)
        # Keep player registry in DB in sync with campaign setup/player turns.
        try:
            s = self._settings()
            display_name = ctx.player_id
            voice_profile = ""
            for p in list(getattr(s.voice, "player_profiles", []) or []):
                pid = str(getattr(p, "player_id", "") or "").strip()
                if pid != str(ctx.player_id or "").strip():
                    continue
                dn = str(getattr(p, "display_name", "") or "").strip()
                vp = str(getattr(p, "voice_profile", "") or "").strip()
                if dn:
                    display_name = dn
                voice_profile = vp
                break
            await self.state.ensure_player_profile(
                ctx,
                display_name=display_name,
                voice_profile=voice_profile,
            )
        except Exception:
            # Player profile sync is best-effort and should not block gameplay.
            pass

        # Depth-0 reasoning: interpret intent and decide required reads.
        plan = await self._rlm_step(ctx, depth=0, llm_calls=0)

        # Always append a minimal interaction log entry (DB is the continuity spine).
        plan = NarrationPlan(
            immediate_text=plan.immediate_text,
            followups=plan.followups,
            writes=list(plan.writes)
            + [
                StateWriteSpec(
                    kind="append_log",
                    params={
                        "entry": {
                            "kind": "turn",
                            "campaign_id": ctx.campaign_id,
                            "session_id": ctx.session_id,
                            "turn_id": ctx.turn_id,
                            "player_id": ctx.player_id,
                            "locale": ctx.locale,
                            "player_text": ctx.transcript_text,
                            "gm_text": plan.immediate_text,
                            "followups": plan.followups,
                        }
                    },
                )
            ],
            delayed_events=plan.delayed_events,
            debug=plan.debug,
        )

        # Commit state writes (transaction) BEFORE narration so reality is consistent.
        await self.state.apply_writes(ctx, plan.writes)

        # Fire-and-forget background tasks (never block narration).
        asyncio.create_task(self._background_post_turn(ctx, plan))

        self.logger.turn_finished(ctx, latency_ms=int((time.perf_counter() - started) * 1000))
        return plan

    async def _background_post_turn(self, ctx: TurnContext, plan: NarrationPlan) -> None:
        try:
            self.logger.append_narration(ctx, plan)
            for ev in plan.delayed_events:
                await self.state.schedule_delayed_event(ctx, ev)
        except Exception as e:  # pragma: no cover
            self.logger.error(ctx, "background_post_turn_failed", {"error": str(e)})

    async def _rlm_step(self, ctx: TurnContext, *, depth: int, llm_calls: int) -> NarrationPlan:
        settings = self._settings()
        if depth > self.budget.max_depth:
            self.logger.error(ctx, "max_depth_exceeded", {"depth": depth})
            return NarrationPlan(
                immediate_text="I pause—something about this situation is unclear. Let’s simplify and continue.",
                followups=[],
                writes=[],
                delayed_events=[],
                debug={"depth": depth, "reason": "max_depth_exceeded"},
            )

        # 1) Interpret intent (fast). If uncertain, use one LLM call to classify.
        intent = await self._interpret_intent(ctx, llm_calls)
        llm_calls = intent.llm_calls_used

        # 2) Decide what knowledge/state is required.
        reads, retrievals = intent.state_reads, intent.retrievals

        # 3) Perform reads (bounded).
        state_view = await self.state.read(ctx, reads)

        knowledge_hits = []
        if retrievals and self.budget.max_qdrant_queries_per_turn > 0:
            for spec in retrievals[: self.budget.max_qdrant_queries_per_turn]:
                knowledge_hits.extend(await self.knowledge.search(ctx, spec))

        # 4) Resolve immediate consequences.
        resolution = await self._resolve(ctx, state_view, knowledge_hits, llm_calls)
        llm_calls = resolution.llm_calls_used

        # 5) Detect unresolved effects; recurse if needed.
        if resolution.needs_followup and depth < self.budget.max_depth:
            self.logger.event(ctx, "rlm_recurse", {"depth": depth + 1, "reason": resolution.recurse_reason})
            follow = await self._rlm_step(ctx, depth=depth + 1, llm_calls=llm_calls)
            # Merge: immediate narration stays, followups appended.
            return NarrationPlan(
                immediate_text=resolution.immediate_text,
                followups=resolution.followups + [follow.immediate_text] + follow.followups,
                writes=resolution.writes + follow.writes,
                delayed_events=resolution.delayed_events + follow.delayed_events,
                debug={
                    "depth": depth,
                    "knowledge_enabled": bool(settings.knowledge.enabled),
                    "retrieval_queries": len(retrievals),
                    "knowledge_hits": len(knowledge_hits),
                    "knowledge_sources": self._knowledge_sources(knowledge_hits, max_sources=5),
                    "response_language_mode": str(settings.prompts.response_language_mode or "player"),
                    "response_language_tag": str(ctx.locale or ""),
                    "followup": follow.debug or {},
                },
            )

        # 6-9) Return plan for commit + narration.
        return NarrationPlan(
            immediate_text=resolution.immediate_text,
            followups=resolution.followups,
            writes=resolution.writes,
            delayed_events=resolution.delayed_events,
            debug={
                "depth": depth,
                "knowledge_enabled": bool(settings.knowledge.enabled),
                "retrieval_queries": len(retrievals),
                "knowledge_hits": len(knowledge_hits),
                "knowledge_sources": self._knowledge_sources(knowledge_hits, max_sources=5),
                "response_language_mode": str(settings.prompts.response_language_mode or "player"),
                "response_language_tag": str(ctx.locale or ""),
            },
        )

    # -------------------- reasoning primitives --------------------

    @dataclass(frozen=True)
    class _Intent:
        kind: str
        state_reads: list[StateReadSpec]
        retrievals: list[RetrievalSpec]
        llm_calls_used: int

    @dataclass(frozen=True)
    class _Resolution:
        immediate_text: str
        followups: list[str]
        writes: list[StateWriteSpec]
        delayed_events: list[dict]
        needs_followup: bool
        recurse_reason: str | None
        llm_calls_used: int

    async def _interpret_intent(self, ctx: TurnContext, llm_calls: int) -> _Intent:
        # Deterministic fast path: minimal heuristic intent extraction.
        s = self._settings()
        text = ctx.transcript_text.strip().lower()
        reads = [StateReadSpec(kind="campaign_snapshot", params={})]
        if s.prompts.include_memory and s.prompts.memory_turns > 0:
            reads.append(StateReadSpec(kind="interaction_log", params={"limit": int(s.prompts.memory_turns)}))
        retrievals: list[RetrievalSpec] = []

        if s.knowledge.enabled:
            # Always query knowledge once per turn when enabled, so the GM stays grounded
            # in the rulebook/lore index even for short action utterances.
            is_question = "?" in text or any(
                text.startswith(w) for w in ["what", "why", "how", "who", "where", "when", "can ", "do ", "does "]
            )
            looks_rules = any(k in text for k in ["rule", "how does", "can i", "allowed", "attack", "spell", "damage"])
            looks_gm_advice = any(k in text for k in ["how to run", "gm", "game master", "session", "pacing", "improv"])
            looks_char = any(k in text for k in ["npc", "character", "who is", "who's", "who are"])
            looks_loc = any(k in text for k in ["location", "where is", "where's", "town", "city", "village", "dungeon"])
            looks_quest = any(k in text for k in ["quest", "mission", "objective", "hook", "reward"])
            looks_faction = any(k in text for k in ["faction", "guild", "clan", "cult", "order"])
            looks_item = any(k in text for k in ["item", "weapon", "armor", "potion", "artifact"])
            looks_monster = any(k in text for k in ["monster", "creature", "beast", "dragon", "undead"])
            looks_story = any(k in text for k in ["story", "plot", "scene", "chapter"])

            filters: dict[str, Any] = {}
            if s.knowledge.active_doc_ids:
                filters["doc_id"] = list(s.knowledge.active_doc_ids)
            if looks_gm_advice:
                filters["doc_kind"] = "gm_advice"
            elif looks_rules:
                filters["type"] = "rules"
            else:
                # Route to likely chunk types when possible; otherwise search broadly.
                types: list[str] = []
                if looks_char:
                    types.append("characters")
                if looks_loc:
                    types.append("locations")
                if looks_quest:
                    types.append("quests")
                if looks_faction:
                    types.append("factions")
                if looks_item:
                    types.append("items")
                if looks_monster:
                    types.append("monsters")
                if looks_story:
                    types.append("story")
                if not types:
                    if is_question:
                        types = ["lore", "story", "characters", "locations", "quests"]
                    else:
                        types = ["rules", "lore", "story", "examples", "tables"]
                filters["type"] = types

            retrievals.append(
                RetrievalSpec(
                    query=ctx.transcript_text,
                    top_k=int(s.knowledge.top_k) if s.knowledge.top_k > 0 else 5,
                    filters=filters or None,
                )
            )

        # If short/ambiguous, use LLM once to classify (bounded).
        if len(text) < 12 and llm_calls < self.budget.max_llm_calls_per_turn:
            self.logger.event(ctx, "llm_call", {"phase": "intent_classify"})
            out = await self.llm.complete(
                system=s.prompts.intent_classify_system,
                user=ctx.transcript_text,
                temperature=0.0,
            )
            llm_calls += 1
            kind = out.strip().splitlines()[0][:64]
        else:
            kind = "action" if any(v in text for v in ["i ", "we ", "attack", "go", "take", "use"]) else "question"

        return self._Intent(kind=kind, state_reads=reads, retrievals=retrievals, llm_calls_used=llm_calls)

    async def _resolve(self, ctx: TurnContext, state_view: dict, knowledge_hits: list[dict], llm_calls: int) -> _Resolution:
        # Prefer deterministic templates; use LLM only when needed.
        s = self._settings()

        if llm_calls < self.budget.max_llm_calls_per_turn:
            self.logger.event(ctx, "llm_call", {"phase": "resolve"})
            memory = ""
            if s.prompts.include_memory and s.prompts.memory_turns > 0:
                memory = self._format_memory(state_view, max_turns=int(s.prompts.memory_turns))
            if not memory:
                memory = "(empty)"

            snippets = self._format_snippets(knowledge_hits, max_snippets=3)
            if not snippets:
                snippets = "(none)"

            try:
                state_json = json.dumps(state_view, ensure_ascii=True)
            except Exception:
                state_json = "{}"

            user = self._render(
                s.prompts.resolve_user_template,
                {
                    "transcript": ctx.transcript_text,
                    "state_json": state_json,
                    "snippets": snippets[:4000],
                    "memory": memory[:4000],
                },
            )
            lang_mode = str(s.prompts.response_language_mode or "").strip().lower()
            detected_lang = str(ctx.locale or "").strip()
            target_lang = detected_lang or "en-US"
            if lang_mode == "locale":
                forced_locale = str(s.voice.locale or detected_lang or "en-US").strip() or "en-US"
                target_lang = forced_locale
                language_policy = (
                    f"Reply ONLY in locale/language {forced_locale}. "
                    "Do not switch languages even if the player speaks another language."
                )
            else:
                if detected_lang:
                    language_policy = (
                        f"Reply ONLY in the player's language ({detected_lang}). "
                        "Never translate to English unless the player spoke English. "
                        "If the player switches language, switch to that language."
                    )
                else:
                    language_policy = (
                        "Reply ONLY in the same language as the player's latest utterance. "
                        "If mixed, use the dominant language and avoid translating to English by default."
                    )
            kb_policy = ""
            if snippets != "(none)":
                kb_policy = (
                    "Use the provided knowledge snippets as the source of truth for rules/lore. "
                    "When applying a rule, cite the snippet header briefly (for example: [doc_id p12])."
                )
            user = (
                f"Detected player language tag: {detected_lang or 'unknown'}\n\n"
                + user
            )
            system_prompt = (
                f"{s.prompts.resolve_system}\n\nLanguage policy: {language_policy}"
                + (f"\n\nKnowledge policy: {kb_policy}" if kb_policy else "")
            )
            out = await self.llm.complete(
                system=system_prompt,
                user=user,
                temperature=0.2,
            )
            llm_calls += 1
            immediate = out.strip()

            # Safety net: if target is non-English but the draft output is English-ish,
            # run a translation post-pass so players hear GM in their language.
            target_base = self._lang_base(target_lang)
            if target_base and target_base != "en" and self._looks_englishish(immediate):
                if llm_calls < self.budget.max_llm_calls_per_turn:
                    self.logger.event(
                        ctx,
                        "llm_call",
                        {"phase": "resolve_translate", "target_lang": target_lang},
                    )
                    trans_system = (
                        "You are a translation post-processor for a tabletop RPG GM. "
                        "Translate the GM response into the target language only. "
                        "Keep it concise and preserve game meaning."
                    )
                    trans_user = (
                        f"Target language tag: {target_lang}\n\n"
                        f"Player original utterance:\n{ctx.transcript_text}\n\n"
                        f"GM draft response:\n{immediate}\n\n"
                        "Return only the translated GM response."
                    )
                    translated = await self.llm.complete(
                        system=trans_system,
                        user=trans_user,
                        temperature=0.0,
                    )
                    llm_calls += 1
                    tr = str(translated or "").strip()
                    if tr:
                        immediate = tr
        else:
            immediate = "Understood. Describe exactly what you do, and I’ll resolve the consequences."

        return self._Resolution(
            immediate_text=immediate,
            followups=[],
            writes=[],
            delayed_events=[],
            needs_followup=False,
            recurse_reason=None,
            llm_calls_used=llm_calls,
        )
