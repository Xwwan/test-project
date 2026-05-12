import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
TASKS = DOCS / "tasks"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ProjectBasicsTest(unittest.TestCase):
    def assert_contains_all(self, content: str, terms: list[str], context: str) -> None:
        for term in terms:
            self.assertIn(term, content, f"{term!r} missing from {context}")

    def test_required_design_and_task_documents_exist_and_are_not_empty(self):
        required_files = [
            DOCS / "design-doc.md",
            TASKS / "person-1-foundation.md",
            TASKS / "person-2-dialogue-retrieval.md",
            TASKS / "person-3-orchestration-curator.md",
        ]

        for path in required_files:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertTrue(path.exists(), f"Missing required document: {path.relative_to(ROOT)}")
                self.assertTrue(path.is_file(), f"Expected a file: {path.relative_to(ROOT)}")
                self.assertGreater(
                    path.stat().st_size,
                    500,
                    f"Document looks unexpectedly small: {path.relative_to(ROOT)}",
                )

    def test_task_documents_share_core_project_conventions(self):
        shared_terms = [
            "conda activate toy",
            "data/Model.md",
            "data/User.md",
            "prompts/dialogue_agent.md",
            "prompts/memory_retrieval_workflow.md",
            "prompts/memory_curator.md",
            "prompts/profile_consolidator.md",
            "request_id = req_<uuid4_hex>",
            "turn_id = turn_<uuid4_hex>",
            "ISO 8601",
        ]

        for path in sorted(TASKS.glob("person-*.md")):
            with self.subTest(path=path.relative_to(ROOT)):
                self.assert_contains_all(read_text(path), shared_terms, str(path.relative_to(ROOT)))

    def test_memory_items_schema_contract_is_preserved(self):
        person_1 = read_text(TASKS / "person-1-foundation.md")

        required_columns = [
            "id INTEGER PRIMARY KEY AUTOINCREMENT",
            "summary TEXT NOT NULL",
            "content TEXT NOT NULL",
            "memory_type TEXT DEFAULT 'event'",
            "references_json TEXT DEFAULT '[]'",
            "tags_json TEXT DEFAULT '[]'",
            "metadata_json TEXT DEFAULT '{}'",
            "source TEXT DEFAULT 'conversation'",
            "confidence REAL DEFAULT 0.8",
            "importance REAL DEFAULT 0.5",
            "sensitivity TEXT DEFAULT 'normal'",
            "status TEXT DEFAULT 'active'",
            "superseded_by INTEGER",
            "conflict_with_json TEXT DEFAULT '[]'",
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP",
            "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP",
            "valid_from DATETIME",
            "valid_until DATETIME",
            "last_accessed_at DATETIME",
            "last_verified_at DATETIME",
        ]

        self.assertIn("CREATE TABLE memory_items", person_1)
        self.assert_contains_all(person_1, required_columns, "memory_items schema")

    def test_lightweight_memory_contract_is_consistent_across_tasks(self):
        required_fields = [
            "id",
            "summary",
            "tags_json",
            "memory_type",
            "references_json",
            "created_at",
            "importance",
        ]

        for filename in [
            "person-1-foundation.md",
            "person-2-dialogue-retrieval.md",
            "person-3-orchestration-curator.md",
        ]:
            with self.subTest(filename=filename):
                content = read_text(TASKS / filename)
                has_lightweight_contract = (
                    "lightweight_memory_items" in content
                    or "list_lightweight_memory_items" in content
                )
                self.assertTrue(has_lightweight_contract, f"Missing lightweight memory contract in {filename}")
                self.assert_contains_all(content, required_fields, f"lightweight memory contract in {filename}")

    def test_person_1_foundation_interfaces_are_documented(self):
        content = read_text(TASKS / "person-1-foundation.md")

        required_interfaces = [
            "def init_db() -> None",
            "def create_memory_item(payload: dict) -> dict",
            "def update_memory_item(memory_id: int, payload: dict) -> dict",
            'def list_lightweight_memory_items(status: str = "active") -> list[dict]',
            "def get_memory_items_by_ids(ids: list[int]) -> list[dict]",
            "def apply_memory_operations(operations: list[dict]) -> list[dict]",
            "def read_model_profile() -> str",
            "def read_user_profile() -> str",
            "def write_user_profile(content: str) -> None",
            "def apply_user_profile_patch(patch: dict) -> str",
            "def append_turn(conversation_id: str, turn: dict) -> str",
            "def get_recent_history(conversation_id: str, limit: int = 20) -> list[dict]",
            "def get_compact_history(conversation_id: str) -> str",
            "def update_compact_history(conversation_id: str, compact: str) -> None",
        ]

        self.assert_contains_all(content, required_interfaces, "Person 1 interfaces")

    def test_person_2_agent_interfaces_and_decision_values_are_documented(self):
        content = read_text(TASKS / "person-2-dialogue-retrieval.md")

        required_terms = [
            "def retrieve_relevant_memory_ids(",
            "def generate_initial_reply(input_data: dict) -> dict",
            "def generate_followup_reply(input_data: dict) -> dict",
            '"strategy": "llm_direct_judgement"',
            "decision = followup | no_followup",
            "followup_type = supplement | correction | none",
            "System / Developer Instruction",
            "Retrieved Events",
            "no_followup",
        ]

        self.assert_contains_all(content, required_terms, "Person 2 agent contract")

    def test_person_3_orchestration_interfaces_and_api_routes_are_documented(self):
        content = read_text(TASKS / "person-3-orchestration-curator.md")

        required_terms = [
            "def create_request(conversation_id: str, user_message: str) -> dict",
            "def mark_initial_reply(request_id: str, reply: str) -> None",
            "def mark_retrieval_pending(request_id: str) -> None",
            "def mark_retrieval_completed(request_id: str, retrieved_items: list[dict]) -> None",
            "def get_pending_followup_requests() -> list[dict]",
            "def mark_followup_decision(request_id: str, decision: dict) -> None",
            "def mark_failed(request_id: str, reason: str) -> None",
            "POST /chat",
            "GET /followups/pending",
            "POST /followups/{request_id}/run",
            "POST /memory/curate",
            "def extract_memory_operations(",
            "def generate_user_profile_patch(memory_items: list[dict], current_user_profile: str) -> dict",
        ]

        self.assert_contains_all(content, required_terms, "Person 3 orchestration contract")

    def test_request_lifecycle_statuses_are_documented(self):
        content = read_text(TASKS / "person-3-orchestration-curator.md")

        required_statuses = [
            "received",
            "initial_reply_generated",
            "retrieval_pending",
            "retrieval_completed",
            "followup_generated",
            "no_followup_needed",
            "completed",
            "failed",
        ]

        self.assert_contains_all(content, required_statuses, "request lifecycle statuses")

    def test_memory_operation_names_are_consistent_across_foundation_and_curator_docs(self):
        required_operations = [
            "create",
            "update",
            "archive",
            "link",
            "conflict_mark",
            "merge",
        ]

        for filename in ["person-1-foundation.md", "person-3-orchestration-curator.md"]:
            with self.subTest(filename=filename):
                self.assert_contains_all(read_text(TASKS / filename), required_operations, filename)

    def test_person_boundaries_are_explicit(self):
        expectations = {
            "person-1-foundation.md": [
                "不负责调用模型",
                "不负责生成用户回复",
                "不负责 API 路由编排",
            ],
            "person-2-dialogue-retrieval.md": [
                "不负责直接写 SQL",
                "不负责管理 `request_id` 生命周期",
                "不负责更新 `User.md`",
            ],
            "person-3-orchestration-curator.md": [
                "不负责实现数据库底层 SQL",
                "不负责 Persona 文件底层读写",
                "不负责 Dialogue Agent 的具体回复逻辑",
            ],
        }

        for filename, terms in expectations.items():
            with self.subTest(filename=filename):
                self.assert_contains_all(read_text(TASKS / filename), terms, filename)


if __name__ == "__main__":
    unittest.main()
