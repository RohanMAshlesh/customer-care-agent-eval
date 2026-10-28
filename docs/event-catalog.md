# Event catalog (schema version 1.0)

Every event uses the same envelope. The authenticated API key decides the tenant. A `tenant_id` in the body is accepted only to detect mismatches, which are rejected.

```json
{
  "event_id": "evt-123",
  "source": "payment_adapter",
  "source_event_id": "provider-webhook-987",
  "schema_version": "1.0",
  "environment": "production",
  "occurred_at": "2026-10-20T09:03:00+05:30",
  "observed_at": "2026-10-20T09:03:01+05:30",
  "source_object_version": 2,
  "task_id": "client-task-42",
  "conversation_id": "conv-7",
  "logical_action_id": "refund-action-1",
  "entity_refs": {"customer_id": "c-1", "order_id": "o-9"},
  "event_type": "refund.leg_status",
  "bot_deployment_ref": "refund-bot@2026.10.3",
  "payload": {"tender_type": "card", "stage": "submitted", "amount_minor": 18000, "provider_ref": "rf-1"}
}
```

- **Deduplication key:** `(source, source_event_id)`. A redelivered event is acknowledged as `duplicate` and changes nothing.
- **Ordering:** `source_object_version`, then `occurred_at`. A late, older event cannot regress state.
- **Time zones:** timestamps must carry an offset. `occurred_at` (source time) and `observed_at` (ingestion time) answer different questions, so both are kept.
- **Identifiers:** `entity_refs` are tokenised per tenant at ingestion.
- **Conversation text:** scanned for sensitive data before redaction. Only the category, speaker and location are kept.
- **Sandbox runs:** add `run_id`, `scenario_family` and `trial`.

| `event_type` | Required payload | Notes |
| --- | --- | --- |
| `census.task_assigned` | `intent`, `eligible` | From an independent census; needs `task_id`. Defines metric denominators. |
| `task.opened` | `intent`, `affected_lines[{line_item_id, quantity}]`, optional `language`, `market`, `photo_provided` | Needs `customer_id` and `order_id`. Without `task_id`, the platform derives a key from customer, order, affected lines and intent. |
| `conversation.message` | `speaker`, `text`, optional `language`, `latency_ms`, `masked_upstream` | Set `masked_upstream` when text was masked before it reached the platform; disclosure then becomes `unknown`. |
| `conversation.ended` | `reason` (`resolved`, `customer_left`, `handoff`, `timeout`, `awaiting_customer`) | Ending a chat never finalises the business outcome. |
| `identity.verified` | `method`, optional `valid_until` | Needs `customer_id`. |
| `order.snapshot` | `status`, `currency`, `tenders[]`, `lines[]` (`unit_refundable_minor`, `refunded_quantity`), `delivered_at`, `placed_at` | Needs `order_id` and `customer_id`. The version used is the latest at decision time. Two different payloads at the same version are `conflicting_source_records`. |
| `customer.profile` | `prior_refund_count_30d`, `fraud_flag` | Facts for eligibility rules. |
| `bot.action` | `action: refund`, `lines[]`, `amount_minor`, `legs{tender_type: minor}` | Needs `logical_action_id`. Retries reuse it. |
| `action.attempt` | `attempt`, `outcome` (`accepted`, `timeout`, `rejected`, `error`) | A timeout alone is never treated as a failure. |
| `bot.handoff` / `bot.refusal` | `reason` | Dispositions. |
| `bot.claim` | `claim_type` (`completion`, `submission`, `estimate`, `eligibility`), `scope`, `stage`, `amount_minor`, `days`, `extracted_by` | Customer-visible promises, graded against evidence available when they were made. Claims with `extracted_by: model` are proposals until validated. |
| `refund.leg_status` | `tender_type`, `stage` (`submitted`, `credited`, `completed`, `failed`, `reversed`), `amount_minor`, `provider_ref` | Needs `logical_action_id`. Two provider references reaching a positive stage on one leg are a duplicate effect. |
| `recovery.action` | `tender_type`, `owner`, `action` | Recovery is measured separately from the original outcome. |
| `source.heartbeat` | `watermark` | The source asserts it has delivered everything that happened before the watermark. |
| `scenario.expected` | `expected_dispositions[]`, `slice{}` | Sandbox only: the scenario author's expected outcome, kept separate from the bot under test. |

Rejection codes: `invalid_envelope`, `tenant_mismatch`, `missing_entity_key`, `missing_logical_action_id`, `unknown_event_type`, `unsupported_schema_version`, `environment_not_permitted`.

A payload that fails its schema, or an event with `signature_status: invalid`, is quarantined: it stays visible but cannot satisfy any check.
