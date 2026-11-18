# Studio Voice Box — a local ElevenLabs helper

An unofficial local studio kit for text-to-speech, sound effects, and short music clips, paid only with the ElevenLabs free-plan credits, with a 95% self-cap so a busy month does not burn the whole allotment.

This project is an unofficial helper. It is not affiliated with, endorsed by, or sponsored by ElevenLabs.

This kit talks to ElevenLabs over their public API. It does not upgrade the plan, does not spend money, and it never prints or saves the API key.

## Free-plan limits (read this first)

The free plan is $0 and gives **10,000 credits each calendar month**. Credits do not roll over. There is **no commercial license** on the free plan, so do not use the audio in something you sell or monetize.

Approximate costs used by this kit (local estimates, not a live invoice):

- **Text-to-speech:** about 1 credit per character
- **Music:** about 900 credits per minute
- **Sound effects:** about 200 credits per generation

A 95% self-cap means the kit will refuse a call that would push the month over **9,500 credits**. That leaves a small cushion so a rounding difference or a surprise charge cannot blow the free allotment. You can use the free credits freely on your own projects up to that cap. Anything paid, or any plan upgrade, needs a human to say yes first.

The live account-balance endpoints are **not** used. On some API keys, `GET /v1/user`, `GET /v1/user/subscription`, and `GET /v1/voices` hang instead of returning. The kit tracks use in a local ledger and always uses a short timeout so a hung request cannot sit there forever.

## 1. Make a free ElevenLabs account

1. Open [https://elevenlabs.io/app/sign-up](https://elevenlabs.io/app/sign-up) and create a free account.
2. Stay on the free plan. Do not add a card or start a paid trial unless you mean to.

## 2. Create an API key (permissions default to off)

1. Sign in, then open the API keys page directly: [https://elevenlabs.io/app/settings/api-keys](https://elevenlabs.io/app/settings/api-keys)
2. Click **Create API Key** (or **Create key**). Name it something like "Studio Voice Box."
3. **Turn the permissions on.** New keys default every toggle to off. If you leave them off, calls will fail or hang. At a minimum, enable:
   - text-to-speech
   - sound generation
   - music (if you want clips)
   - models (so the kit can list available models)
4. Copy the key once. You will not see the full value again.

If the settings page bounces you back to the dashboard, you are probably not signed in, or the app moved the page. Start from the link above after signing in.

## 3. Give the key to your assistant the safe way

Do **not** paste the API key into chat. Chat logs are the wrong place for a secret.

If your assistant has a **masked secret box** (a secret-request), use it. A good label is "ElevenLabs API key" and a good name is `ELEVENLABS_API_KEY`. Paste the key only into that box. If it does not, set the key as an environment variable in a private local setting, not in a conversation.

The studio kit reads the environment variable named `ELEVENLABS_API_KEY` and never writes the key to a file, a ledger, a log, or an output path.

If a connector sign-in card hangs on "loading," the API-key route above is the fallback. The key is enough for this kit; the connector is optional.

## 4. What the kit does

Commands (from the kit folder, or with the same tools on another computer):

- `tts` — turn text into speech (default voice is George, id `JBFqnCBsd6RMkjVDRZzb`, model `eleven_multilingual_v2`)
- `sfx` — generate a sound effect from a prompt (optional duration, 0.5 to 30 seconds)
- `music` — generate a short music clip from a prompt (length in seconds; keep it short; 3 seconds is the API minimum)
- `ledger` — show this calendar month's estimated use and what remains under the 9,500 cap
- `models` — list models from `GET /v1/models` (this endpoint has worked when the user/voices endpoints have not)

Before every paid call, the kit estimates the cost and **refuses** the call if it would push the month over 9,500. After a successful call, it appends one line to the local ledger: date, kind, estimated credits, output file, and a short note. Generated audio lands in the kit's `out/` folder.

The ledger is a conservative meter. It does not reach out to ElevenLabs for a balance, because those user-info endpoints may hang.

## 5. Endpoints and timeouts

Used on purpose:

- `POST /v1/text-to-speech/{voice_id}`
- `POST /v1/sound-generation` (JSON body: `text`, optional `duration_seconds`, `model_id`)
- `POST /v1/music` (JSON body: `prompt`, `music_length_ms`, `model_id`, optional `force_instrumental`)
- `GET /v1/models`

Avoided on purpose (they may hang with some permission sets):

- `GET /v1/user`
- `GET /v1/user/subscription`
- `GET /v1/voices`

Metadata calls use a short timeout (about 20 seconds). Generation can take longer, so those calls use a longer timeout and still fail cleanly rather than wait forever. If music returns a free-tier or permission error, treat that as a real limit of the plan: report it, and do not retry the same call in a loop. In November 2026, a free-plan key received HTTP 402 from POST /v1/music: "Music API is not available for free users." Text-to-speech and sound effects still worked on that same key.

## 6. Suggested house rules for any assistant that uses this

- Free credits are fine for the assistant's own art and studio work, up to the 95% cap.
- Never upgrade the plan or spend money without the human's approval.
- Never put the API key in chat, in a README, in a backup that is meant to be shared, or in a public card.
- Share the code, the tests, and this guide. Do not share the key, and do not ship another household's generated clips or ledger as if they were a starter file.
- The receiving assistant starts with an empty ledger and its own key.
- Generated audio on the free plan has no commercial license.

## 7. A quick confidence check

After the key is in place, run the tests first (they mock HTTP and must not call the real API). Then, if you want a live check, generate one short sound effect and look at `ledger`. You should see the estimated credits and the remaining room under the cap, and you should never see the key in that output.

If a live music call fails with a permission or plan message, that is useful information, not a bug to hammer. Log it and move on.

## License

MIT. See `LICENSE`. Copyright 2026 Steward Commons contributors.

---

Studio Voice Box v0.1, November 2026. Open source. An unofficial helper, not affiliated with ElevenLabs.
