"""Agents package for dialogue, retrieval and memory curation.

This package will host both Person 2 (dialogue + retrieval) and Person 3
(memory curation) agents once their branches are merged together. The exports
below cover only Person 3's contribution; the Person 2 exports will be added
when ``feature/dialogue-retrieval`` is merged.
"""

from .memory_curator import (
    LLMMemoryExtractor,
    MemoryExtractor,
    extract_memory_operations,
)
from .profile_consolidator import generate_user_profile_patch

__all__ = [
    "LLMMemoryExtractor",
    "MemoryExtractor",
    "extract_memory_operations",
    "generate_user_profile_patch",
]
