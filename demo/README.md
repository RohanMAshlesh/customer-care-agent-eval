# Evidence Desk demo

A 3-minute guided tour for people with no background in AI evaluation. It opens with the problem and the answer, then walks through four steps, each ending with a one-line takeaway:

1. **One refund**: follow a customer's INR 240 refund from chat to bank, side by side with what a transcript-only review would conclude.
2. **What it catches**: nine mistakes that sound fine in a chat, such as a double refund, money sent to the wrong account or a leaked card number.
3. **Ship or not**: the launch check for a new bot version, which answers yes, no or "not enough proof yet".
4. **Getting started**: the five setup steps a company goes through, with a four-item readiness checklist.

Technical detail (every check, the raw evidence and the statistics) sits behind "For specialists" sections.

Every verdict, gate decision and readiness state comes from the Python engine in this repository. Nothing is computed or invented in the browser.

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

This rewrites `demo/public/data/*.json`.

## Deploy to Vercel

Import the repository in Vercel and set **Root Directory** to `demo`. Vercel reads `vercel.json`, runs `npm run build` and serves `dist/`. No environment variables or server are needed.

Or deploy from this folder with the Vercel CLI:

```bash
npx vercel --prod
```
