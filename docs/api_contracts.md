# API 与跨模块 JSON 约定

本文档列出 Person 3 在 `src/api/`、`src/services/`、`src/coordinator/` 与
`src/agents/memory_curator.py` 中实现的对外契约，供另外两位开发者对齐。

> 任何字段调整请先更新本文档并同步其他两位开发者。

---

## 1. HTTP API 总览

| Method | Path | 描述 |
| --- | --- | --- |
| `POST` | `/chat` | 处理一条用户消息，返回首条回复 |
| `GET`  | `/followups/pending` | 列出等待 followup 决策的请求 |
| `POST` | `/followups/{request_id}/run` | 触发某个请求的二次回复判断 |
| `POST` | `/memory/curate` | 对一段对话运行 Memory Curator 并落库 |
| `POST` | `/memory/profile/refresh` | 运行 Profile Consolidator 并按需更新 `User.md` |
| `GET`  | `/healthz` | 健康检查 |

请求和响应均为 `application/json; charset=utf-8`。错误统一返回：

```json
{"error": {"message": "human readable reason"}}
```

错误状态码：

- `400`：请求体不合 schema 或参数非法。
- `404`：路径不存在或 `request_id` 未知。
- `409`：状态机不允许该操作（例如 followup 前还没完成 retrieval）。
- `500`：服务器内部异常。

---

## 2. `POST /chat`

请求：

```json
{
  "conversation_id": "string",
  "message": "string"
}
```

响应：

```json
{
  "request_id": "req_<uuid4_hex>",
  "turn_id": "turn_<uuid4_hex>",
  "conversation_id": "string",
  "reply": "string",
  "retrieval_status": "completed | pending | failed",
  "retrieved_memory_ids": [int]
}
```

约定：

- `request_id` / `turn_id` 由 Request Coordinator 生成，绑定整次请求。
- MVP 始终同步完成 retrieval，因此 `retrieval_status` 当前总是
  `completed`；保留字段以便后续切异步。
- 成功后请求状态进入 `retrieval_completed`，会出现在
  `/followups/pending` 列表里，直到调用 `/followups/{request_id}/run`。

---

## 3. `GET /followups/pending`

响应：

```json
{
  "pending": [
    {
      "request_id": "req_xxx",
      "conversation_id": "string",
      "status": "retrieval_completed",
      "updated_at": "ISO-8601 UTC"
    }
  ]
}
```

---

## 4. `POST /followups/{request_id}/run`

请求体可省略或为 `{}`。

响应：

```json
{
  "request_id": "req_xxx",
  "conversation_id": "string",
  "decision": "followup | no_followup",
  "followup_type": "supplement | correction | none",
  "reply": "string"
}
```

约定：

- 只能在请求状态为 `retrieval_completed` 时调用；否则返回 `409`。
- 若 `decision=followup` 且 `reply` 非空，服务会自动把这条回复作为
  `assistant` turn 追加到 `conversation_turns`（metadata `turn_kind=followup`）。
- 当 `decision=no_followup`，`followup_type` 固定为 `none`，`reply` 固定为 `""`。

---

## 5. `POST /memory/curate`

请求：

```json
{
  "conversation_id": "string",
  "history_limit": 50
}
```

响应：

```json
{
  "conversation_id": "string",
  "operations": [
    {
      "operation": "create | update | archive | link | conflict_mark | merge",
      "target_id": null,
      "payload": { /* Memory Curator operation payload */ }
    }
  ],
  "applied": [ /* 来自 Person 1 apply_memory_operations 的结果 */ ]
}
```

约定：

- `operations` 即 Memory Curator 调用 LLM 后归一化得到的列表；为空时
  `applied` 也为空。
- `payload` 字段集合见下方第 6 节。

---

## 6. Memory Curator 输出 schema

参考 `docs/tasks/person-3-orchestration-curator.md` 第 5.3 节。Person 3
归一化后保证：

```json
{
  "operations": [
    {
      "operation": "create | update | archive | link | conflict_mark | merge",
      "target_id": null,
      "payload": {
        "summary": "string",
        "content": "string",
        "memory_type": "fact | preference | task | event | constraint | profile_update",
        "references_json": [],
        "tags_json": [],
        "metadata_json": {},
        "confidence": 0.0,
        "importance": 0.0,
        "sensitivity": "normal | sensitive | high_risk",
        "status": "active"
      }
    }
  ]
}
```

校验规则：

- `operation` 必须在白名单内。
- `target_id` 若非空必须是正整数。
- `create` 操作的 `payload.summary` 与 `payload.content` 不能为空。
- 不在白名单内的 `memory_type` / `sensitivity` / `status` 会抛 `ValueError`。

---

## 7. `POST /memory/profile/refresh`

请求体为空 `{}`。

响应：

```json
{
  "should_update": true,
  "patch": {
    "operation": "replace",
    "content": "new User.md content"
  },
  "reason": "string",
  "new_profile": "string | null"
}
```

约定：

- `should_update=false` 时 `patch` 与 `new_profile` 均为 `null`。
- `should_update=true` 时服务会调用 `apply_user_profile_patch` 写入
  `data/User.md`，`new_profile` 即写入后的内容。
- 与 Person 1 的 `apply_user_profile_patch` 兼容（支持 `content` /
  `operations` / `append` / `prepend` / `replace`）。

---

## 8. Request Coordinator 内部状态机

```
received
  → initial_reply_generated
  → retrieval_pending          (Dialogue Service 调用前)
  → retrieval_completed
  → followup_generated | no_followup_needed
failed                          (任意阶段失败)
```

`retrieval_completed` 是 `/followups/{request_id}/run` 的必要前置。终态
为 `followup_generated` / `no_followup_needed` / `failed`。

---

## 9. Dialogue Service 对 Person 1 / Person 2 的依赖

所有依赖通过 `DialogueDependencies` 注入，包括但不限于：

- Person 1：`read_model_profile`、`read_user_profile`、`apply_user_profile_patch`、
  `append_turn`、`get_recent_history`、`get_compact_history`、
  `update_compact_history`、`list_lightweight_memory_items`、
  `get_memory_items_by_ids`、`apply_memory_operations`。
- Person 2：`generate_initial_reply`、`generate_followup_reply`、
  `retrieve_relevant_memory_ids`。
- Person 3：`extract_memory_operations`、`generate_user_profile_patch`、
  `model_client`（任何符合 Person 2 `ModelClient` Protocol 的对象）。

生产环境下 `DialogueDependencies()` 不传入参数即可，Dialogue Service
会按需 lazy import 真实实现；测试可注入 fake。
