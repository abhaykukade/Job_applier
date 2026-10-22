"""Turn LLM edit proposals into reviewable suggestions, and apply the accepted ones."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from job_applier.pipeline.ai import TailorEdit
from job_applier.profile.models import Resume

_TERM = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*[A-Za-z0-9+#]|[A-Za-z]")
_COMMON = {"I", "A", "The", "An", "And", "Led", "Built", "Drove", "Owned", "Designed", "Developed"}


@dataclass
class Proposed:
    kind: str
    target: dict
    before: str
    after: str
    rationale: str
    claims: list[str] = field(default_factory=list)


def new_terms(text: str, resume_text: str) -> list[str]:
    """Technology-looking words in `text` that the resume never mentions.

    A safety net in case the model rewrites a bullet with a skill but forgets to flag it.
    """
    known = resume_text.lower()
    found = []
    words = _TERM.findall(text)
    for i, word in enumerate(words):
        techy = any(c.isdigit() or c in "+#." for c in word) or any(c.isupper() for c in word[1:])
        capitalized = word[0].isupper() and i > 0 and word not in _COMMON
        if (techy or capitalized) and len(word) > 1 and word.lower().strip(".") not in known:
            found.append(word.strip("."))
    return list(dict.fromkeys(found))


def _valid_order(order: list[int] | None, size: int) -> bool:
    return order is not None and sorted(order) == list(range(size)) and order != list(range(size))


def to_proposed(edit: TailorEdit, resume: Resume) -> Proposed | None:
    """Validate indices and compute before/after text. Returns None for unusable edits."""
    resume_text = resume.plain_text()
    claims = [edit.claim] if edit.claim else []
    if edit.kind == "summary" and edit.new_text:
        claims += [t for t in new_terms(edit.new_text, resume_text) if t not in claims]
        return Proposed("summary", {}, resume.summary, edit.new_text.strip(), edit.rationale, claims)

    if edit.kind == "bullet" and edit.new_text and edit.experience_index is not None:
        i, j = edit.experience_index, edit.bullet_index
        if not (0 <= i < len(resume.experience)) or j is None or not (0 <= j < len(resume.experience[i].bullets)):
            return None
        claims += [t for t in new_terms(edit.new_text, resume_text) if t not in claims]
        return Proposed(
            "bullet", {"exp": i, "bullet": j}, resume.experience[i].bullets[j], edit.new_text.strip(),
            edit.rationale, claims,
        )

    if edit.kind == "bullets_reorder" and edit.experience_index is not None:
        i = edit.experience_index
        if not (0 <= i < len(resume.experience)):
            return None
        bullets = resume.experience[i].bullets
        if not _valid_order(edit.new_order, len(bullets)):
            return None
        return Proposed(
            "bullets_reorder", {"exp": i, "order": edit.new_order}, "\n".join(bullets),
            "\n".join(bullets[k] for k in edit.new_order or []), edit.rationale,
        )

    if edit.kind == "skill_add" and edit.new_text and resume.skills:
        g = edit.skill_group_index if edit.skill_group_index is not None else 0
        if not (0 <= g < len(resume.skills)):
            return None
        skill = edit.new_text.strip()
        if skill.lower() in (s.lower() for s in resume.skills[g].items):
            return None
        mentioned = skill.lower() in resume_text.lower()
        group = resume.skills[g]
        return Proposed(
            "skill_add", {"group": g, "skill": skill}, ", ".join(group.items),
            ", ".join([*group.items, skill]), edit.rationale, [] if mentioned else [edit.claim or skill],
        )

    if edit.kind == "skills_reorder":
        g = edit.skill_group_index if edit.skill_group_index is not None else 0
        if not (0 <= g < len(resume.skills)):
            return None
        items = resume.skills[g].items
        if not _valid_order(edit.new_order, len(items)):
            return None
        return Proposed(
            "skills_reorder", {"group": g, "order": edit.new_order}, ", ".join(items),
            ", ".join(items[k] for k in edit.new_order or []), edit.rationale,
        )
    return None


def apply_edits(resume: Resume, edits: list[tuple[str, dict, str]]) -> Resume:
    """Apply accepted (kind, target, after) edits to a copy of the master resume.

    Text rewrites run before reorders, since reorder indices refer to the original positions.
    """
    out = resume.model_copy(deep=True)
    priority = {"summary": 0, "bullet": 0, "skill_add": 1, "bullets_reorder": 2, "skills_reorder": 2}
    for kind, target, after in sorted(edits, key=lambda e: priority.get(e[0], 9)):
        if kind == "summary":
            out.summary = after
        elif kind == "bullet":
            out.experience[target["exp"]].bullets[target["bullet"]] = after
        elif kind == "skill_add":
            out.skills[target["group"]].items.append(target["skill"])
        elif kind == "bullets_reorder":
            exp = out.experience[target["exp"]]
            exp.bullets = [exp.bullets[k] for k in target["order"]]
        elif kind == "skills_reorder":
            group = out.skills[target["group"]]
            original = group.items[: len(target["order"])]
            group.items = [original[k] for k in target["order"]] + group.items[len(target["order"]):]
    return out
