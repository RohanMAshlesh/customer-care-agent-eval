# Evidence Desk demo

A 3-minute guided tour for people with no background in AI evaluation. Its message is **resolution, not conversation**: AI support is usually graded on how a chat sounds, while customers judge it on whether their problem was fixed. Evidence Desk checks each case in the systems where the work happens (payments, ServiceNow, logistics, sign-in) and follows it until the outcome is proven.

The tour opens with the situation, the complication and the answer, then walks through four steps. Each step ends with a one-line takeaway.

1. **Four cases**: four customers whose chats looked resolved but whose problems were not, replayed moment by moment. Each moment shows what the customer saw, what a transcript-only review would conclude, what Evidence Desk concludes, and the records behind the scenes.
   - **Priya, the refund that bounced** (money): the card refund fails at the bank the day after a perfect chat.
   - **Kavya, the escalation that never happened** (safety): the bot says "I've escalated this", but the ServiceNow call failed silently.
   - **Rohit, the ticket in the wrong queue** (service request): a replacement ticket is filed with the refunds team at low priority; he has to chase and the 72-hour target is missed.
   - **Neha, "It works now, thanks!"** (account access): she says sign-in works; the sign-in logs show she never got in.
2. **What it catches**: nine broken promises that sound fine in a chat, from a missing escalation and a wrong queue to a double refund and a leaked card number.
3. **Ship or not**: the launch check for a new bot version, which answers yes, no or "not enough proof yet".
4. **Getting started**: the five setup steps a company goes through, with a four-item readiness checklist.

Technical detail (every check, the raw evidence and the verdict history) sits behind "For specialists" sections. Individual cases can be linked directly, for example `#journey-kavya`.

Every verdict, ticket status, gate decision and readiness state was produced by the Python engine in this repository. The customers, ticket numbers, amounts and policies are illustrative, and the "transcript review" lines are written to show what a chat-only review would typically conclude; they are not engine output.

## Run locally

```bash
npm install
```

```bash
npm run dev
```

## Regenerate the data

From the repository root, after `pip install -e ".[dev]"`:

```bash
python demo/scripts/export_data.py
```

This rewrites `demo/src/data/*.json`. The data is bundled into the build, so the deployed site makes no runtime data requests. Case narration lives in `demo/scripts/story_copy.py`; the cases themselves are defined in `src/ccevals/packs/reference_cases.py`.

## Deploy to Vercel

Import the repository in Vercel and set **Root Directory** to `demo`. Vercel reads `vercel.json`, runs `npm run build` and serves `dist/`. No environment variables or server are needed.

Or deploy from this folder with the Vercel CLI:

```bash
npx vercel --prod
```
