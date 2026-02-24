from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass
class PDFChunk:
    text: str
    source_path: str
    page: int
    chunk_index: int
    chunk_type: str
    tags: dict


def parse_pdf(path: Path) -> list[tuple[int, str]]:
    reader = PdfReader(str(path))
    pages: list[tuple[int, str]] = []
    for i, page in enumerate(reader.pages):
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        pages.append((i + 1, txt))
    return pages


def chunk_text(text: str, *, max_chars: int = 1200, overlap: int = 120) -> list[str]:
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not text:
        return []

    chunks: list[str] = []
    i = 0
    while i < len(text):
        j = min(len(text), i + max_chars)
        chunk = text[i:j]
        chunks.append(chunk)
        i = max(0, j - overlap)
        if j == len(text):
            break
    return chunks


def _score_keywords(text: str, words: list[str]) -> int:
    return sum(1 for w in words if w in text)


def classify_chunk(text: str, *, doc_kind: str | None = None) -> str:
    t = " ".join((text or "").lower().split())
    dk = str(doc_kind or "").strip().lower()

    if not t:
        return "unknown"

    # Strong structural cues first.
    if any(k in t for k in ["|", "\t", "d20", "d12", "d10", "d8", "d6", "d4"]):
        return "tables"
    if any(k in t for k in ["example", "for example", "boxed text", "read aloud"]):
        return "examples"

    scores = {
        "rules": _score_keywords(
            t,
            [
                "rule",
                "must",
                "cannot",
                "check",
                "dc ",
                "saving throw",
                "attack",
                "damage",
                "spell",
                "initiative",
                "action",
                "bonus action",
                "reaction",
            ],
        ),
        "quests": _score_keywords(
            t,
            ["quest", "mission", "objective", "reward", "hook", "encounter", "adventure hook"],
        ),
        "characters": _score_keywords(
            t,
            ["npc", "character", "background", "personality", "motivation", "trait", "bond", "flaw"],
        ),
        "locations": _score_keywords(
            t,
            ["location", "region", "town", "village", "city", "dungeon", "room", "map", "district"],
        ),
        "factions": _score_keywords(t, ["faction", "guild", "clan", "cult", "order", "alliance"]),
        "items": _score_keywords(
            t,
            ["item", "weapon", "armor", "potion", "artifact", "gear", "equipment", "treasure"],
        ),
        "monsters": _score_keywords(
            t,
            ["monster", "creature", "beast", "undead", "dragon", "armor class", "hit points", "challenge rating"],
        ),
        "story": _score_keywords(
            t,
            ["story", "plot", "chapter", "act ", "scene ", "timeline", "twist", "arc"],
        ),
        "lore": _score_keywords(
            t,
            ["history", "legend", "lore", "myth", "kingdom", "empire", "ancient", "culture"],
        ),
        "gm_advice": _score_keywords(
            t,
            ["gm advice", "game master", "running the game", "pacing", "improv", "session zero", "spotlight"],
        ),
    }

    # Bias by declared doc_kind.
    if dk == "rulebook":
        scores["rules"] += 2
    elif dk == "adventure":
        scores["quests"] += 2
        scores["story"] += 1
    elif dk == "lorebook":
        scores["lore"] += 2
    elif dk == "gm_advice":
        scores["gm_advice"] += 3

    best_kind = max(scores, key=scores.get)
    best_score = scores[best_kind]
    if best_score <= 0:
        # Sensible fallback by document class.
        if dk == "gm_advice":
            return "gm_advice"
        if dk == "rulebook":
            return "rules"
        if dk == "adventure":
            return "story"
        if dk == "lorebook":
            return "lore"
        return "lore" if len(t) > 160 else "unknown"
    return best_kind


def ingest_pdf(
    path: Path,
    *,
    doc_id: str,
    ruleset: str | None = None,
    doc_kind: str | None = None,
    chunk_max_chars: int = 1200,
    chunk_overlap: int = 120,
) -> list[PDFChunk]:
    pages = parse_pdf(path)
    out: list[PDFChunk] = []
    for page_num, txt in pages:
        for idx, ch in enumerate(chunk_text(txt, max_chars=chunk_max_chars, overlap=chunk_overlap)):
            ctype = classify_chunk(ch, doc_kind=doc_kind)
            out.append(
                PDFChunk(
                    text=ch,
                    source_path=str(path),
                    page=page_num,
                    chunk_index=idx,
                    chunk_type=ctype,
                    tags={
                        "doc_id": doc_id,
                        "ruleset": ruleset,
                        "doc_kind": doc_kind,
                        "type": ctype,
                    },
                )
            )
    return out
