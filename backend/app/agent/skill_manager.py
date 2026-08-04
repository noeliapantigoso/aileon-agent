"""
Manages SKILL.md files — loads them, picks relevant ones, and persists
runtime-created skills to Firestore (Cloud Run containers are ephemeral,
so the local filesystem only holds skills baked into the Docker image).

Two layers:
  1. Built-in skills  — .md files in agent/backend/skills/ (in the image, always available)
  2. Custom skills    — stored in Firestore collection `{prefix}_skills`
                        (survive container restarts, created/updated at runtime)

Custom skills with the same name as a built-in override the built-in.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"


# ── Frontmatter parsing ───────────────────────────────────────────────────────

def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter from body. Returns (meta_dict, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    front = text[3:end].strip()
    body = text[end + 4:].strip()
    meta: dict[str, Any] = {}
    for line in front.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            raw = val.strip()
            if raw.startswith("[") and raw.endswith("]"):
                items = [x.strip().strip("\"'") for x in raw[1:-1].split(",")]
                meta[key.strip()] = [i for i in items if i]
            else:
                meta[key.strip()] = raw.strip("\"'")
    return meta, body


def _normalize(text: str) -> str:
    """Lowercase + strip accents for accent-insensitive trigger matching."""
    nfd = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower().strip())
    return re.sub(r"-{2,}", "-", slug).strip("-")


def _skill_from_text(text: str, name_fallback: str, source: str) -> dict[str, Any]:
    meta, body = _parse_frontmatter(text)
    return {
        "name": meta.get("name", name_fallback),
        "description": meta.get("description", ""),
        "triggers": meta.get("triggers", []),
        "body": body,
        "source": source,
        "content": text,
    }


# ── SkillManager class ────────────────────────────────────────────────────────

class SkillManager:
    """
    Loads, caches, and persists skills.

    Built-in skills come from the local filesystem (baked into the image).
    Custom skills are stored in Firestore and merged on top of built-ins.
    """

    def __init__(
        self,
        firestore_client: Any = None,
        collection_prefix: str = "assistant",
    ) -> None:
        self._db = firestore_client
        self._prefix = collection_prefix
        self._cache: list[dict[str, Any]] | None = None

    def _collection(self):
        return self._db.collection(f"{self._prefix}_skills")

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load_builtin(self) -> dict[str, dict[str, Any]]:
        """Load skills from the local filesystem (Docker image)."""
        skills: dict[str, dict[str, Any]] = {}
        if not _SKILLS_DIR.exists():
            return skills
        for path in sorted(_SKILLS_DIR.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
                skill = _skill_from_text(text, path.stem, source="builtin")
                skills[skill["name"]] = skill
            except Exception as exc:
                logger.warning("Failed to load built-in skill %s: %s", path.name, exc)
        return skills

    def _load_custom(self) -> dict[str, dict[str, Any]]:
        """Load custom skills from Firestore."""
        skills: dict[str, dict[str, Any]] = {}
        if self._db is None:
            return skills
        try:
            docs = self._collection().stream()
            for doc in docs:
                data = doc.to_dict() or {}
                content = data.get("content", "")
                if content:
                    skill = _skill_from_text(content, doc.id, source="custom")
                    skills[skill["name"]] = skill
        except Exception as exc:
            logger.warning("Failed to load custom skills from Firestore: %s", exc)
        return skills

    def load_all(self) -> list[dict[str, Any]]:
        """Load built-ins + custom skills. Custom overrides built-in by name."""
        builtin = self._load_builtin()
        custom = self._load_custom()
        merged = {**builtin, **custom}  # custom wins on name collision
        self._cache = list(merged.values())
        return self._cache

    def get_cached(self) -> list[dict[str, Any]]:
        if self._cache is None:
            return self.load_all()
        return self._cache

    def invalidate_cache(self) -> None:
        self._cache = None

    # ── Relevance detection ───────────────────────────────────────────────────

    def get_relevant(self, user_message: str) -> list[dict[str, Any]]:
        """Return skills matching the user message. Falls back to all if none match."""
        all_skills = self.get_cached()
        if not user_message or len(user_message.strip()) < 3:
            return all_skills
        msg_norm = _normalize(user_message)
        matched = [
            s for s in all_skills
            if any(_normalize(t) in msg_norm for t in s.get("triggers", []))
        ]
        return matched if matched else all_skills

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, name: str, content: str, reason: str = "") -> str:
        """
        Create or update a skill. Persists to Firestore so it survives restarts.
        Returns the skill name/slug.
        """
        slug = _slugify(name)
        if self._db is None:
            raise RuntimeError("Firestore not available — cannot persist skill")
        now = datetime.now(timezone.utc).isoformat()
        doc_ref = self._collection().document(slug)
        existing = doc_ref.get()
        payload = {
            "name": slug,
            "content": content,
            "reason": reason,
            "updated_at": now,
        }
        if not existing.exists:
            payload["created_at"] = now
            doc_ref.set(payload)
        else:
            doc_ref.update(payload)
        self.invalidate_cache()
        logger.info("Skill '%s' saved to Firestore. Reason: %s", slug, reason)
        return slug

    def delete(self, name: str) -> bool:
        """
        Delete a custom skill from Firestore.
        Built-in skills (from the image) cannot be deleted this way — use `save`
        to override them with an empty or corrected version.
        Returns True if deleted, False if not found.
        """
        if self._db is None:
            raise RuntimeError("Firestore not available")
        slug = _slugify(name)
        doc_ref = self._collection().document(slug)
        if not doc_ref.get().exists:
            return False
        doc_ref.delete()
        self.invalidate_cache()
        logger.info("Skill '%s' deleted from Firestore", slug)
        return True

    def list_custom(self) -> list[str]:
        """Return names of all custom (Firestore) skills."""
        if self._db is None:
            return []
        try:
            return [doc.id for doc in self._collection().stream()]
        except Exception:
            return []


# ── Prompt formatting ─────────────────────────────────────────────────────────

def format_skills_for_prompt(skills: list[dict[str, Any]]) -> str:
    """Render a list of skills as a prompt section."""
    if not skills:
        return ""
    parts = ["## Active Skills\n"]
    parts.append(
        "The following skill guides are loaded for this conversation. "
        "Follow their procedures when the relevant situation arises.\n"
    )
    for skill in skills:
        parts.append(f"### Skill: {skill['name']}\n")
        parts.append(skill["body"])
        parts.append("")
    return "\n".join(parts)
