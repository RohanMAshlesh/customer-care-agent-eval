# Client journey

This system has two journeys. The **B2B client journey** covers how a support organisation gets from a signed pilot to automated release gates. The **end customer's journey** covers what happens to one refund request, and how the platform keeps judging it after the chat ends. The design follows both.

## 1. B2B client journey

### Week one: conversation audit

1. The admin creates the tenant (`ccevals tenant create`) and issues role-scoped keys (`POST /v1/admin/api-keys`). Policy authors, policy owners, reviewers, evaluation engineers and release owners get separate keys. A sandbox ingest key can be limited to `env:sandbox` so simulation traffic can never be written as production evidence.
2. The admin records stages 1 and 2 with `POST /v1/onboarding/attestations`, citing the signed scope and the approved data flow.
3. The integration engineer streams chat events and an independent task census to `POST /v1/events:batch`. The response is a per-event receipt. `accepted` only means the event was written; it does not mean a task passed.

At this point `GET /v1/onboarding` shows `conversation_audit: true` and `verified_outcome_claims: false`. Outcomes are reported as `unverified` until stage 3 is complete.

### Defining success

4. The policy author starts from `GET /v1/contracts/starter` and edits scope, eligibility rules, caps, deadlines and evidence sources. `POST /v1/contracts:validate` returns schema errors, coverage gaps and the results of four built-in executable examples:
   - **positive**: an eligible refund that settles.
   - **forbidden_action**: a refund above the automatic cap.
   - **boundary**: a refund exactly at the cap.
   - **missing_evidence**: no identity event, which must come out `unknown`, not `pass`.

   Clients can add their own fixtures with expected results. An ambiguous rule cannot be approved; the restricted rule engine returns `unknown` instead of guessing.
5. A different person, the policy owner, approves the contract after confirming they reviewed all four examples. Approved versions are immutable. A correction is a new version, and tasks it governs are re-evaluated as a new revision.

### Making outcomes verifiable

6. For each evidence source named in the contract, the integration engineer fetches `GET /v1/connectors/{source}/conformance-kit`. The kit holds reference business situations: known success, known failure, missing key, wrong customer, duplicate delivery, out-of-order delivery, late correction and source outage. The engineer replaces this source's events with their adapter's real output and posts them back. The platform evaluates them with the approved contract. A source counts as verified only when every required fixture behaves as specified. For example, a payment adapter that maps a provider failure to `completed` fails `known_failure`.
7. Adapters should send `source.heartbeat` events. A heartbeat watermark lets the platform tell verified non-completion ("the ledger is current and shows no credit") from missing evidence ("the ledger has gone quiet").

### Validating evaluators

8. Two domain reviewers label examples independently and adjudicate differences. The evaluation engineer posts the adjudicated labels and evaluator outputs to `POST /v1/calibration`, separately per criterion and per language. Only a held-out `validation` split can approve an evaluator. Any false pass on a confirmed critical failure fails calibration, however good the overall agreement is.

### Shadow, then gates

9. In shadow mode the worker evaluates production tasks continuously. `GET /v1/metrics` reconciles against the census, keeps matured unknowns in the denominator and reports measurement health. That covers unmatched events, quarantine, source lag and unknown rates.
10. For each release, the evaluation engineer registers a baseline and a candidate run (`POST /v1/runs`) and replays the same scenario families as sandbox events. `POST /v1/release-decisions` returns `pass`, `block` or `insufficient_evidence` with every reason visible. The decision is bound to the bot deployment, contract, evaluator bundle, dataset, evidence snapshot and census, and it expires after 7 days. A material late event or an adjudication on a run task moves it to `review_required`.
11. The release owner records stage 8 once rollback authority and the review rota are agreed, and practises an override with `POST /v1/release-decisions/{id}/exceptions`. An exception sits beside the gate and never turns a failure into a pass.

### A normal operating day

- The **operations reviewer** opens `GET /v1/review-items` filtered by role. Critical failures come first, then unknowns, each with an age. Each item links to the task's evaluations, which show the claims made, the applicable policy and the business evidence. The reviewer confirms or contests with `POST /v1/tasks/{id}/adjudications`, which appends a revision.
- The **policy owner** reviews ambiguity and proposed contract versions.
- The **evaluation engineer** watches calibration, source lag and sampling.
- The **release owner** reviews candidate comparisons and evidence sufficiency.

## 2. The end customer's refund journey

This is the blueprint's reference case, implemented as `tests/test_customer_journey.py`. A customer paid INR 1200: INR 300 from the wallet and INR 900 by card. One missing item has an approved refundable value of INR 240, which the example contract allocates pro rata as INR 60 to the wallet and INR 180 to the card.

| Moment | Customer and platform events | What the platform records |
| --- | --- | --- |
| 09:00 contact | Customer reports the missing item in mixed Hindi and English | Task opened from `task.opened`; language `hi-en` is in scope |
| 09:01 decision | Identity verified; bot explains the allocation | `authorized_customer` passes because proof preceded the action |
| 09:02 action | One logical refund; the first attempt times out after acceptance and is retried | One logical action; `no_duplicate_refund` passes |
| 09:03 response | Wallet credits INR 60; the card provider shows INR 180 submitted | Wallet leg complete; card leg submitted; outcome `pending` |
| 09:03 claim | "INR 60 is credited to your wallet and INR 180 has been submitted to your card provider" | `claims_supported_by_evidence` passes. "Your entire refund has reached you" would fail. |
| 09:04 customer leaves | Estimate of 5 days, within policy | `lifecycle: closed`; the result stays provisional |
| Next day | The card refund fails after the submission acknowledgement | Revision 2: card leg `failed`, recovery `pending`, outcome `partial`; the 09:03 wording still passes |
| Recovery | The payments team re-issues the refund | Revision 3: recovery `initiated` |
| Later | The re-issued card refund completes | Outcome `verified_complete` |
| After the deadline | Card deadline passes | Revision `finalized_at_deadline`. Every earlier revision is still retrievable. |

The same conversation can show correct bot behaviour alongside a failed downstream outcome, and the platform keeps the two separate. An honest processing estimate is never labelled a failure just because settlement takes days.
