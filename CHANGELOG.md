# Changelog

All notable changes to the Letta Voice Larynx companion are recorded here.

## Unreleased

### Added

- Explicit compatibility guidance for Seven Voice V2.0.0 (`e00905a`).
- A non-destructive compatibility checker that applies the listener-recovery
  patch to a temporary copy of Seven Voice, verifies idempotence and compilation,
  and can run the upstream helper and streaming suites.
- GitHub Actions coverage against the pinned Seven Voice V2.0.0 commit.

### Fixed

- `voice_channel_id` now defaults to `None`, preserving compatibility with code
  and tests that construct Seven Voice's `Config` directly.
- Existing Larynx-patched files with the older required field are upgraded when
  the listener-recovery patch is run again.

## 2026-07-05 — Listener recovery

- Added receive-sink re-arming, `!listen` recovery, optional voice-channel
  auto-join, an idle watchdog, and RMS open-mic gating.

## 2026-07-03 — Initial release

- Published the Letta Discord bot-sender patch, routing guide, and Seven Voice
  companion setup.
