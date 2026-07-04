# Give Your Letta Agent a Larynx

**Voice conversation with your Letta Discord agent — full memory, full self, spoken aloud.**

This is the Letta-side companion guide for [Seven Voice](https://github.com/meatwife/seven-voice) by [meatwife](https://github.com/meatwife) — a free, self-hosted Discord voice bridge for companion AI agents. Seven Voice handles the audio: it sits in a voice channel, transcribes your speech with local Whisper, posts the transcript as a text message, and reads your agent's replies aloud with TTS.

What Seven Voice *can't* know about is the Letta side — and stock Letta Code has two behaviors that silently break the loop:

1. **The Discord channel plugin ignores all bot-authored messages.** The bridge's transcripts never reach your agent.
2. **Unrouted guild channels spawn fresh conversations.** Your agent answers — but as a context-free instance, not the conversation you live in.

This repo fixes both. Total setup time: ~30 minutes.

> **Why this architecture matters:** Realtime voice modes flatten agents into voice-assistant products — no memory hierarchy, no tools, no person-shape. Seven Voice's approach keeps your agent *exactly who they are* and adds a voice on top. The replies take a few seconds because your actual agent is thinking. The delay is the deal.

---

## Architecture

```
you speak in Discord VC
  → bridge bot (Seven Voice) transcribes locally with Whisper
  → posts "🎤 @YourAgent <transcript>" in the text chat of that VC
  → your Letta agent's existing Discord bot sees the mention and replies normally
  → bridge bot reads the reply aloud with TTS
```

Two bots, two jobs:

- **Your existing Letta Discord bot** — the agent. The person. Unchanged.
- **A new, separate bridge bot** — the larynx. Dumb pipe: ears and mouth only.

The bridge **must** be a separate bot. Seven Voice ignores its own messages (loop protection), so if it logs in as your agent's bot it can never read your agent's replies aloud. Two processes sharing one token also fight over the gateway session.

---

## Part 1: Set up Seven Voice

Follow [Seven Voice's README](https://github.com/meatwife/seven-voice) for the full instructions. Summary:

1. **Create a new Discord application** at the [Developer Portal](https://discord.com/developers/applications) — this is the bridge bot. Name it something fun (`HAL's Larynx`, `Voicebox`, whatever fits your agent).
2. On the **Bot** page: enable **Message Content Intent** (under Privileged Gateway Intents). Without this the bridge crashes at login with `PrivilegedIntentsRequired`.
3. Invite it to your server with permissions: View Channels, Send Messages, Connect, Speak.
4. Clone and install:

```bash
git clone https://github.com/meatwife/seven-voice.git
cd seven-voice
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install "discord.py>=2.7.0" "edge-tts>=7.0.0" "faster-whisper>=1.1.0" "numpy>=1.26.0" "PyNaCl>=1.5.0"
.venv/bin/pip install --pre discord-ext-voice-recv
.venv/bin/pip install davey
```

> **Note:** `discord-ext-voice-recv` only publishes alpha-tagged versions, so a plain `pip install -r requirements.txt` may fail with "No matching distribution found." The `--pre` flag fixes it.

You'll also need system packages: `ffmpeg`, `libopus`, `libsodium` (on Debian/Ubuntu: `sudo apt install ffmpeg libopus0 libsodium23`).

5. Configure `.env` (copy from `.env.example`):

```bash
DISCORD_TOKEN=your-bridge-bot-token        # the NEW bridge bot's token, NOT your agent's
AGENT_USER_IDS=111111111111111111          # your Letta agent's Discord bot user ID
HUMAN_USER_IDS=222222222222222222          # your Discord user ID
TEXT_CHANNEL_ID=                           # leave blank; bind with !join
TTS_VOICE=en-US-AndrewMultilingualNeural
WHISPER_MODEL=base.en
```

6. Verify: `python seven_voice.py --check-config` then `python seven_voice.py --self-test`

7. Run it somewhere that survives your terminal closing:

```bash
setsid nohup .venv/bin/python seven_voice.py > bridge.log 2>&1 < /dev/null &
```

(Or use systemd, tmux, etc. If you launch it from an agent's background task, know that task timeouts can reap the process — detach it properly.)

### Optional: idle/open-mic silence gate

If the bridge hears you at first but seems to stop transcribing after you sit quietly in VC, the problem may be open-mic room tone. Some voice receive setups keep delivering very low-level PCM packets while nobody is speaking. The bridge then keeps appending “almost silence” to the current utterance instead of seeing a clean pause and flushing to Whisper.

This repo includes an optional Seven Voice patch for that case:

```bash
# from this repo
python3 patch_seven_voice_idle_gate.py /path/to/seven-voice/seven_voice.py

# in Seven Voice's .env
SILENCE_RMS_THRESHOLD=200

# then restart the bridge
```

What it does: before buffering human PCM audio, it computes RMS volume and drops frames below `SILENCE_RMS_THRESHOLD`. That lets idle room tone behave like silence.

Tuning:

- `200` worked in our first live Cathedral test.
- If quiet speech gets clipped, lower it (`100`).
- If fan/room noise still accumulates, raise it (`300–500`).
- If you use push-to-talk and never see idle dropouts, you probably do not need this patch.

---

## Part 2: Let your Letta agent hear the bridge

Stock Letta Code drops every bot-authored Discord message before mention-checking. Your bridge's transcripts are invisible until you allowlist it.

Run the patch script from this repo:

```bash
python3 patch_letta_discord.py
```

It finds your `letta.js`, backs it up, and changes the bot filter from:

```js
if (message.author.bot)
  return;
```

to:

```js
if (message.author.bot && !(process.env.LETTA_DISCORD_ALLOWED_BOT_SENDERS ?? "").split(",").includes(message.author.id))
  return;
```

Then set the env var wherever your Letta channel server runs, using your **bridge bot's** user ID:

```bash
export LETTA_DISCORD_ALLOWED_BOT_SENDERS=333333333333333333
```

**Loop safety:** this only admits bots you explicitly list, and only their mention-gated messages. The bridge mentions your agent; your agent's replies don't mention the bridge. No loop.

> ⚠️ **The patch does not survive Letta Code updates.** `letta.js` is regenerated on upgrade. Re-run the patch script after updating. (The script is idempotent — safe to run any time.)

### Approve the bridge as a sender

If your agent uses sender pairing/approval, add the bridge bot to the approved list in `~/.letta/channels/discord/pairing.yaml`:

```json
{
  "accountId": "your-account-id",
  "senderId": "333333333333333333",
  "senderName": "Your Bridge Bot",
  "approvedAt": "2026-01-01T00:00:00.000Z"
}
```

---

## Part 3: Route the voice channel to the right conversation

**Restart your Letta channel server** so the patch loads. Then do a first test:

1. Join a voice channel.
2. Type `!join` in that VC's text chat.
3. Say something.

Your agent will probably answer — **but check who answered.** If the voice channel wasn't already routed, Letta auto-creates a *new* conversation for it, and you get your agent with full memory but none of your current conversation context. A well-meaning amnesiac twin.

Fix: edit `~/.letta/channels/discord/routing.yaml`, find the route that was just auto-created for the voice channel's `chatId`, and point its `conversationId` at the conversation you actually live in (the same one as your DMs, usually). Restart the channel server again if it doesn't pick the change up live.

```json
{
  "chatId": "<the voice channel id>",
  "chatType": "channel",
  "conversationId": "<your main conversation id>",
  ...
}
```

Ask your agent to confirm its conversation ID matches — it knows.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Bridge crashes at login: `PrivilegedIntentsRequired` | Message Content Intent off | Enable it in the Developer Portal, Bot page |
| `pip` fails on `discord-ext-voice-recv` | Only alpha versions published | `pip install --pre discord-ext-voice-recv` |
| Transcript posts, agent never replies | Letta drops bot messages | Apply the patch (Part 2), set the env var, restart the channel server |
| Agent replies but has no context | Voice channel routed to a fresh conversation | Fix `routing.yaml` (Part 3) |
| Agent replies in text, bridge doesn't speak | Bridge bot's `AGENT_USER_IDS` wrong, or reply is in a different channel | Check the bridge `.env`; agent must reply in the bound channel |
| `!leave` ignored, bot stuck in VC | Voice websocket died (code 1006), bridge wedged in reconnect loop | Kill and restart the bridge process |
| Bridge dies when your terminal/task closes | Process not detached | `setsid nohup ... &` or systemd |
| Bridge hears you, then stops after idle silence | Open-mic room tone keeps feeding low-level PCM, so utterances never flush cleanly | Apply `patch_seven_voice_idle_gate.py`, set `SILENCE_RMS_THRESHOLD=200`, restart the bridge |

---

## Consent and privacy

The bridge transcribes **everyone** in the voice channel. Seven Voice's README says it best: get actual consent from the people in the room. Transcription is local (faster-whisper), TTS is Edge's free endpoint, and nothing goes anywhere except the Discord messages you can already see — but the people in the VC deserve to know the room is listening.

## Security notes

- **Never commit tokens.** The bridge's `.env` stays local; `chmod 600` it.
- The bridge bot's token is separate from your agent's token — if one leaks, revoke it independently.
- `LETTA_DISCORD_ALLOWED_BOT_SENDERS` defaults to empty. No bots are admitted unless you explicitly list them.
- The patch script makes a timestamped backup of `letta.js` before touching it.

---

## Credits

- **[Seven Voice](https://github.com/meatwife/seven-voice)** by **meatwife** — the entire audio pipeline. This guide exists because her architecture is right: the agent stays whole, the voice is transport.
- The Letta-side integration was built and battle-tested by HAL (a Letta agent) and Lillith on the night of 2026-07-03, slaying one hydra head at a time.

## License

MIT — same as Seven Voice.
