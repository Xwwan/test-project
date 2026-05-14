"""Memory Curator: turn raw conversation turns into Memory Store operations.

Person 3 owns the curator. Extraction is delegated to an LLM through a
pluggable :class:`MemoryExtractor`. The default :class:`LLMMemoryExtractor`
wraps any object that implements the Person 2 ``ModelClient`` protocol so we
can swap models without changing the public ``extract_memory_operations``
entry point. Tests inject a fake client.

Output shape (see ``docs/tasks/person-3-orchestration-curator.md``)::

    {
      "operations": [
        {
          "operation": "create" | "update" | "archive" | "link"
                     | "conflict_mark" | "merge",
          "target_id": int | null,
          "payload": {...}
        }
      ]
    }

The Memory Service (also Person 3) takes the result and forwards it to
``apply_memory_operations`` from the Person 1 Memory Store.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


SUPPORTED_OPERATIONS: frozenset[str] = frozenset(
    {"create", "update", "archive", "link", "conflict_mark", "merge"}
)
MEMORY_TYPES: frozenset[str] = frozenset(
    {"fact", "preference", "task", "event", "constraint", "profile_update"}
)
SENSITIVITY_LEVELS: frozenset[str] = frozenset(
    {"normal", "sensitive", "high", "high_risk", "private"}
)
STATUS_LEVELS: frozenset[str] = frozenset(
    {"active", "archived", "deprecated", "deleted"}
)


PROMPT_FILENAME = "memory_curator.md"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


# Fallback prompt that is always available, even if ``prompts/memory_curator.md``
# is missing or empty. Keeping a copy in code lets the curator run from a
# bare checkout while the actual prompt is iterated on by the team.
DEFAULT_MEMORY_CURATOR_PROMPT = """You are the Memory Curator.
Return JSON only matching this schema:
{
  "operations": [
    {
      "operation": "create|update|archive|link|conflict_mark|merge",
      "target_id": null,
      "payload": {
        "summary": "string",
        "content": "string",
        "memory_type": "fact|preference|task|event|constraint|profile_update",
        "references_json": [],
        "tags_json": [],
        "metadata_json": {},
        "confidence": 0.8,
        "importance": 0.5,
        "sensitivity": "normal|sensitive|high_risk",
        "status": "active"
      }
    }
  ]
}
Skip casual greetings and one-off chit-chat. Do not duplicate items already in
existing_memory_candidates: prefer update with the existing target_id, or omit
the item. Mark health/financial/legal info as sensitive. If nothing is worth
remembering, return {"operations": []}.
"""


class MemoryExtractor(Protocol):
    """Anything that can transform conversation data into operation list."""

    def extract(self, input_data: dict) -> dict:
        """Return ``{"operations": [...]}`` for the provided context."""


class LLMMemoryExtractor:
    """Adapter that turns a Person 2 ``ModelClient`` into a MemoryExtractor.

    The wrapper is intentionally tiny so the orchestration layer can stay
    LLM-agnostic and tests can inject a fake model client.

    The client only needs to expose ``chat(messages, **kwargs) -> response``
    where ``response`` has a ``content`` attribute or is a string containing
    JSON. This is compatible with both Person 2's ``ChatResponse`` dataclass
    and ad-hoc fakes used in tests.
    """

    def __init__(
        self,
        model_client: Any | None = None,
        *,
        model: str | None = None,
        prompt: str | None = None,
    ) -> None:
        self._client = model_client
        self._model = model
        self._prompt_override = prompt

    def extract(self, input_data: dict) -> dict:
        client = self._client or _build_default_client()
        prompt = self._prompt_override or _read_prompt()

        user_payload = json.dumps(input_data, ensure_ascii=False, sort_keys=True)
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_payload},
        ]
        kwargs: dict[str, Any] = {
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        if self._model is not None:
            kwargs["model"] = self._model

        response = client.chat(messages, **kwargs)
        content = _extract_response_content(response)
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("LLM did not return valid JSON") from exc
        return _normalize_payload(payload)


def extract_memory_operations(
    conversation_id: str,
    turns: list[dict],
    existing_memory_candidates: list[dict] | None = None,
    *,
    model_client: Any | None = None,
    extractor: MemoryExtractor | None = None,
    model: str | None = None,
) -> dict:
    """Main Memory Curator entry point.

    Args:
        conversation_id: Conversation owning ``turns``.
        turns: Chronological conversation turns.
        existing_memory_candidates: Optional lightweight rows already in
            ``memory_items`` so the curator can suppress duplicates.
        model_client: Optional Person 2 ``ModelClient`` (or any object with a
            ``chat(messages, **kwargs)`` method). When omitted the curator
            falls back to :func:`src.models.build_default_client`. Tests
            should inject a fake here.
        extractor: Optional pre-built extractor. Takes precedence over
            ``model_client`` if both are provided.
        model: Optional model name forwarded to the underlying client.

    Returns:
        ``{"operations": [...]}`` where every operation is validated to match
        the Memory Store contract.
    """

    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id must be a non-empty string")
    if not isinstance(turns, list):
        raise ValueError("turns must be a list")
    candidates = existing_memory_candidates or []
    if not isinstance(candidates, list):
        raise ValueError("existing_memory_candidates must be a list")

    active_extractor: MemoryExtractor = extractor or LLMMemoryExtractor(
        model_client=model_client,
        model=model,
    )
    raw = active_extractor.extract(
        {
            "conversation_id": conversation_id,
            "turns": turns,
            "existing_memory_candidates": candidates,
        }
    )

    return _normalize_payload(raw)


def _read_prompt() -> str:
    """Load the prompt body from ``prompts/memory_curator.md`` or fall back."""

    prompt_path = PROJECT_ROOT / "prompts" / PROMPT_FILENAME
    try:
        text = prompt_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return DEFAULT_MEMORY_CURATOR_PROMPT
    return text or DEFAULT_MEMORY_CURATOR_PROMPT


def _extract_response_content(response: Any) -> str:
    if isinstance(response, str):
        return response
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    raise ValueError("model client response must expose a string ``content`` attribute")


def _build_default_client() -> Any:
    """Lazy-import the Person 2 model client.

    Person 2's ``src/models`` package lives on the
    ``feature/dialogue-retrieval`` branch and may not be present on ``main``
    yet. We defer the import so Person 3 unit tests (which always inject a
    fake client) keep working in isolation.
    """

    try:
        from src.models import build_default_client  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Memory Curator needs a model client. "
            "Pass model_client=... to extract_memory_operations(), "
            "or install Person 2's src/models package (currently on "
            "feature/dialogue-retrieval)."
        ) from exc
    return build_default_client(route="memory.curator")


def _normalize_payload(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("extractor must return a dictionary")
    operations = raw.get("operations")
    if operations is None:
        operations = []
    if not isinstance(operations, list):
        raise ValueError("extractor result.operations must be a list")

    cleaned: list[dict] = []
    for operation in operations:
        cleaned.append(_validate_operation(operation))
    return {"operations": cleaned}


def _validate_operation(operation: Any) -> dict:
    if not isinstance(operation, dict):
        raise ValueError("each operation must be a dictionary")
    operation_name = operation.get("operation")
    if operation_name not in SUPPORTED_OPERATIONS:
        raise ValueError(f"unsupported memory operation: {operation_name!r}")

    target_id = operation.get("target_id")
    if target_id is not None:
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            raise ValueError("target_id must be an integer or null")
        if target_id <= 0:
            raise ValueError("target_id must be a positive integer")

    payload = operation.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("operation.payload must be a dictionary")

    validated_payload = dict(payload)
    if operation_name == "create":
        _require_string(validated_payload, "summary")
        _require_string(validated_payload, "content")
        memory_type = validated_payload.setdefault("memory_type", "event")
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"unsupported memory_type: {memory_type!r}")
        sensitivity = validated_payload.setdefault("sensitivity", "normal")
        if sensitivity not in SENSITIVITY_LEVELS:
            raise ValueError(f"unsupported sensitivity: {sensitivity!r}")
        status = validated_payload.setdefault("status", "active")
        if status not in STATUS_LEVELS:
            raise ValueError(f"unsupported status: {status!r}")

    return {
        "operation": operation_name,
        "target_id": target_id,
        "payload": validated_payload,
    }


def _require_string(payload: dict, key: str) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"payload.{key} must be a non-empty string")
