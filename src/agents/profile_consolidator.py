"""Profile Consolidator: condense long-term memory into ``User.md`` patches.

The consolidator only emits an update when it sees long-term, high-confidence
signals (currently: ``profile_update`` rows or constraints with
``importance >= 0.8``). For every other case it returns the safe default
``{"should_update": False, ...}`` so the orchestration layer can call it on a
schedule without risking lossy ``User.md`` rewrites.

A small heuristic implementation is used for the MVP and tests; a real LLM
based summarizer can plug in by passing a ``summarizer`` callable.
"""

from __future__ import annotations

from typing import Callable, Iterable


# Default sentinel header so generated User.md content is easy to spot and to
# replace deterministically across runs.
USER_PROFILE_HEADER = "# 用户长期信息"
AUTO_SECTION_MARKER = "<!-- profile-consolidator -->"


PROFILE_TYPES: frozenset[str] = frozenset(
    {"profile_update", "preference", "constraint"}
)


Summarizer = Callable[[list[dict], str], str]


def generate_user_profile_patch(
    memory_items: list[dict],
    current_user_profile: str,
    *,
    summarizer: Summarizer | None = None,
    min_importance: float = 0.6,
) -> dict:
    """Decide whether and how to refresh ``User.md``.

    Args:
        memory_items: Full Memory Store rows considered for consolidation.
        current_user_profile: Current ``User.md`` content.
        summarizer: Optional callable returning the new ``User.md`` body. The
            default implementation builds a deterministic bullet list.
        min_importance: Inclusion threshold for ``importance`` values.

    Returns:
        ``{"should_update": bool, "patch": {...}, "reason": str}``
    """

    if not isinstance(memory_items, list):
        raise ValueError("memory_items must be a list")
    if not isinstance(current_user_profile, str):
        raise ValueError("current_user_profile must be a string")
    if min_importance is None or not isinstance(min_importance, (int, float)):
        raise ValueError("min_importance must be a number")

    eligible = list(_filter_eligible(memory_items, min_importance=min_importance))
    if not eligible:
        return {
            "should_update": False,
            "patch": None,
            "reason": "no long-term memory items reached the consolidation threshold",
        }

    builder = summarizer or _default_summarizer
    proposed = builder(eligible, current_user_profile).rstrip()
    if not proposed:
        return {
            "should_update": False,
            "patch": None,
            "reason": "summarizer returned an empty profile",
        }

    if _normalize_for_diff(proposed) == _normalize_for_diff(current_user_profile):
        return {
            "should_update": False,
            "patch": None,
            "reason": "proposed profile is identical to the current one",
        }

    return {
        "should_update": True,
        "patch": {
            "operation": "replace",
            "content": proposed,
        },
        "reason": f"consolidated {len(eligible)} long-term memory items",
    }


def _filter_eligible(
    memory_items: list[dict],
    *,
    min_importance: float,
) -> Iterable[dict]:
    for item in memory_items:
        if not isinstance(item, dict):
            continue
        if item.get("status") not in (None, "active"):
            continue
        memory_type = item.get("memory_type")
        if memory_type not in PROFILE_TYPES:
            continue
        importance = item.get("importance")
        if isinstance(importance, (int, float)) and importance < min_importance:
            continue
        if not item.get("summary"):
            continue
        yield item


def _default_summarizer(items: list[dict], current_profile: str) -> str:
    grouped: dict[str, list[str]] = {
        "profile_update": [],
        "constraint": [],
        "preference": [],
    }
    for item in items:
        bucket = grouped.setdefault(item.get("memory_type", "preference"), [])
        bullet = _format_bullet(item)
        if bullet and bullet not in bucket:
            bucket.append(bullet)

    sections: list[str] = [USER_PROFILE_HEADER, AUTO_SECTION_MARKER]

    if grouped["profile_update"]:
        sections.append("## 身份与基本信息")
        sections.extend(grouped["profile_update"])
    if grouped["constraint"]:
        sections.append("## 限制与禁忌")
        sections.extend(grouped["constraint"])
    if grouped["preference"]:
        sections.append("## 长期偏好")
        sections.extend(grouped["preference"])

    sections.append(AUTO_SECTION_MARKER)
    return "\n".join(sections).strip() + "\n"


def _format_bullet(item: dict) -> str:
    summary = str(item.get("summary", "")).strip()
    if not summary:
        return ""
    confidence = item.get("confidence")
    if isinstance(confidence, (int, float)):
        return f"- {summary}（confidence={confidence:.2f}）"
    return f"- {summary}"


def _normalize_for_diff(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())
