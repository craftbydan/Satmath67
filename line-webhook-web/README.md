# line-webhook-web

A minimal LINE Messaging API webhook, meant to be deployed on Vercel behind
your own domain. No framework, no database — one serverless function that
verifies LINE's signature and echoes back a reply.

## How it works

`api/webhook.js` handles `POST /api/webhook`:

1. Reads the raw request body (needed because signature verification hashes
   the exact bytes LINE sent).
2. Verifies the `X-Line-Signature` header with HMAC-SHA256 using your
   channel secret.
3. For each text message event, replies with the same text via the LINE
   reply API.

Swap the echo in `replyMessage(...)` for whatever logic you want.

## Setup

1. Create a LINE Messaging API channel in the
   [LINE Developers Console](https://developers.line.biz/console/) and grab:
   - Channel secret
   - Channel access token (long-lived)
2. Deploy to Vercel. This repo has other stuff at the root (the Python app
   in `app/`), so when you create the Vercel project you **must** set
   **Settings → General → Root Directory** to `line-webhook-web`. Leaving
   it at the repo root makes Vercel try to build the unrelated Python app
   and the function will crash.
3. Set environment variables in **Settings → Environment Variables** (see
   `.env.example`):
   - `LINE_CHANNEL_SECRET`
   - `LINE_CHANNEL_ACCESS_TOKEN`
4. Redeploy after setting the Root Directory / env vars — they don't apply
   retroactively to a deployment that already ran.
5. Attach your domain, then in the LINE Developers Console set the webhook
   URL to `https://<your-domain>/api/webhook` and click "Verify".

## Local dev

```bash
npm i -g vercel
cd line-webhook-web
vercel dev
```

Use a tunnel (e.g. `ngrok http 3000`) to expose it to LINE for testing, and
set the tunnel URL as the webhook URL temporarily.
