# Evidence Desk demo

A static, interactive walkthrough of the evaluation service. It has five views:

- **Refund journey**: replay the blueprint's INR 1,200 refund moment by moment and watch verdict revisions.
- **Edge-case lab**: 15 scenarios, including duplicates, out-of-order webhooks, a wrong account and missing evidence.
- **Release gate**: change a candidate bot's quality and latency and see pass, block or insufficient evidence.
- **Client onboarding**: real API responses captured while a tenant moves from pilot to release gates.
- **How it works**: the pipeline and its principles.

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
