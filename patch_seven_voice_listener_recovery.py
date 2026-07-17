#!/usr/bin/env python3
"""Patch Seven Voice for listener recovery, service auto-join, and idle gating.

This is the Letta Larynx companion patch for Seven Voice installs that run as
long-lived Discord voice bridges. It hardens the receive side without changing
Seven Voice's core architecture:

- centralizes receive listener arming in arm_voice_listener(force=True)
- makes !listen re-arm the Discord voice receive sink, not just flip a boolean
- adds a watchdog that re-arms the sink if the receive library reports it stopped
- optionally auto-joins a configured voice channel on process startup/restart
- keeps the open-mic RMS silence gate used for noisy/idle rooms

Usage:
    python3 patch_seven_voice_listener_recovery.py /path/to/seven_voice.py

Run from inside the Seven Voice repo with no argument to patch ./seven_voice.py.
Idempotent: safe to run repeatedly. A timestamped backup is created first.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path


class PatchError(RuntimeError):
    pass


def replace_once(data: str, old: str, new: str, label: str) -> str:
    count = data.count(old)
    if count != 1:
        raise PatchError(f"{label} pattern found {count} times, expected exactly 1")
    return data.replace(old, new)


def insert_before_once(data: str, marker: str, insertion: str, label: str) -> str:
    count = data.count(marker)
    if count != 1:
        raise PatchError(f"{label} marker found {count} times, expected exactly 1")
    return data.replace(marker, insertion + marker)


def patch(data: str) -> str:
    if "import numpy as np" not in data:
        raise PatchError("expected `import numpy as np` not found; refusing to patch blind")

    # Early Larynx releases added this field without a default. Seven Voice V2's
    # offline tests construct Config directly, so the required field broke those
    # callers even though Config.from_env() still worked at runtime. Upgrade old
    # patched files as well as using the compatible declaration for fresh ones.
    new = data
    if "    voice_channel_id: int | None\n" in new:
        new = replace_once(
            new,
            "    voice_channel_id: int | None\n",
            "    voice_channel_id: int | None = None\n",
            "voice channel config default",
        )

    already_done = all(
        marker in new
        for marker in (
            "voice_channel_id",
            "def arm_voice_listener",
            "def voice_watchdog",
            "SILENCE_RMS_THRESHOLD",
        )
    )
    if already_done:
        return new

    if "voice_channel_id: int | None = None" not in new:
        new = replace_once(
            new,
            "    text_channel_id: int | None\n",
            "    text_channel_id: int | None\n    voice_channel_id: int | None = None\n",
            "voice channel config field",
        )

    if "silence_rms_threshold: float" not in new:
        new = replace_once(
            new,
            "    min_utterance_sec: float = 0.6\n    chunk_chars: int = 700",
            "    min_utterance_sec: float = 0.6\n    silence_rms_threshold: float = 200.0\n    idle_rearm_sec: float = 0.0\n    chunk_chars: int = 700",
            "listener tuning config fields",
        )
    elif "idle_rearm_sec: float" not in new:
        new = replace_once(
            new,
            "    silence_rms_threshold: float = 200.0\n",
            "    silence_rms_threshold: float = 200.0\n    idle_rearm_sec: float = 0.0\n",
            "idle re-arm config field",
        )

    if "voice_channel_id=parse_optional_int" not in new:
        new = replace_once(
            new,
            '            text_channel_id=parse_optional_int(os.getenv("TEXT_CHANNEL_ID", "")),\n',
            '            text_channel_id=parse_optional_int(os.getenv("TEXT_CHANNEL_ID", "")),\n            voice_channel_id=parse_optional_int(os.getenv("VOICE_CHANNEL_ID", "")),\n',
            "voice channel env setting",
        )

    if "silence_rms_threshold=float" not in new:
        new = replace_once(
            new,
            '            min_utterance_sec=float(os.getenv("MIN_UTTERANCE_SEC", "0.6")),\n            chunk_chars=int(os.getenv("CHUNK_CHARS", "700")),',
            '            min_utterance_sec=float(os.getenv("MIN_UTTERANCE_SEC", "0.6")),\n            silence_rms_threshold=float(os.getenv("SILENCE_RMS_THRESHOLD", "200")),\n            idle_rearm_sec=float(os.getenv("IDLE_REARM_SEC", "0")),\n            chunk_chars=int(os.getenv("CHUNK_CHARS", "700")),',
            "listener tuning env settings",
        )
    elif "idle_rearm_sec=float" not in new:
        new = replace_once(
            new,
            '            silence_rms_threshold=float(os.getenv("SILENCE_RMS_THRESHOLD", "200")),\n',
            '            silence_rms_threshold=float(os.getenv("SILENCE_RMS_THRESHOLD", "200")),\n            idle_rearm_sec=float(os.getenv("IDLE_REARM_SEC", "0")),\n',
            "idle re-arm env setting",
        )

    if "def feed(self, pcm: bytes, silence_rms_threshold" not in new:
        new = replace_once(
            new,
            '''    def feed(self, pcm: bytes) -> None:\n        with self.lock:\n            self.chunks.append(pcm)\n            self.last_packet = time.time()''',
            '''    def feed(self, pcm: bytes, silence_rms_threshold: float = 0.0) -> bool:\n        if silence_rms_threshold > 0:\n            samples = np.frombuffer(pcm, dtype=np.int16)\n            if not samples.size:\n                return False\n            rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))\n            if rms < silence_rms_threshold:\n                return False\n        with self.lock:\n            self.chunks.append(pcm)\n            self.last_packet = time.time()\n        return True''',
            "audio buffer RMS gate",
        )

    if "def has_pending_audio" not in new:
        new = replace_once(
            new,
            '''    def take_if_silent(self, silence_sec: float) -> bytes | None:\n        with self.lock:\n            if not self.chunks or time.time() - self.last_packet < silence_sec:\n                return None\n            data = b"".join(self.chunks)\n            self.chunks.clear()\n            return data\n''',
            '''    def take_if_silent(self, silence_sec: float) -> bytes | None:\n        with self.lock:\n            if not self.chunks or time.time() - self.last_packet < silence_sec:\n                return None\n            data = b"".join(self.chunks)\n            self.chunks.clear()\n            return data\n\n    def has_pending_audio(self) -> bool:\n        with self.lock:\n            return bool(self.chunks)\n\n    def seconds_since_packet(self) -> float | None:\n        with self.lock:\n            if not self.last_packet:\n                return None\n            return time.time() - self.last_packet\n''',
            "audio buffer idle helpers",
        )

    if "self._listener_armed_at" not in new:
        new = replace_once(
            new,
            "        self._workers_started = False\n",
            "        self._workers_started = False\n        self._listener_armed_at = 0.0\n",
            "listener armed timestamp",
        )

    if "auto-joined voice channel from VOICE_CHANNEL_ID" not in new:
        new = replace_once(
            new,
            '''        if self.config.text_channel_id and self.text_channel is None:\n            channel = self.get_channel(self.config.text_channel_id) or await self.fetch_channel(self.config.text_channel_id)\n            if isinstance(channel, discord.abc.Messageable):\n                self.text_channel = channel\n                logging.info("bound text channel from TEXT_CHANNEL_ID=%s", self.config.text_channel_id)\n''',
            '''        if self.config.text_channel_id and self.text_channel is None:\n            channel = self.get_channel(self.config.text_channel_id) or await self.fetch_channel(self.config.text_channel_id)\n            if isinstance(channel, discord.abc.Messageable):\n                self.text_channel = channel\n                logging.info("bound text channel from TEXT_CHANNEL_ID=%s", self.config.text_channel_id)\n        if self.config.voice_channel_id and (not self.voice or not self.voice.is_connected()):\n            try:\n                channel = self.get_channel(self.config.voice_channel_id) or await self.fetch_channel(self.config.voice_channel_id)\n                if isinstance(channel, discord.VoiceChannel):\n                    self.voice = await channel.connect(cls=voice_recv.VoiceRecvClient)\n                    self.arm_voice_listener(force=True)\n                    logging.info("auto-joined voice channel from VOICE_CHANNEL_ID=%s", self.config.voice_channel_id)\n                    if self.text_channel:\n                        await self.text_channel.send("Reconnected voice listener.")\n                else:\n                    logging.warning("VOICE_CHANNEL_ID=%s did not resolve to a VoiceChannel: %r", self.config.voice_channel_id, channel)\n            except Exception:\n                logging.exception("auto-join voice channel failed")\n''',
            "startup voice auto-join",
        )

    if "asyncio.create_task(self.voice_watchdog())" not in new:
        new = replace_once(
            new,
            "            asyncio.create_task(self.stt_flusher())\n",
            "            asyncio.create_task(self.stt_flusher())\n            asyncio.create_task(self.voice_watchdog())\n",
            "watchdog task startup",
        )

    if "def arm_voice_listener" not in new:
        new = insert_before_once(
            new,
            "    def load_whisper(self) -> None:\n",
            '''    def arm_voice_listener(self, *, force: bool = False) -> None:\n        if not self.voice or not self.voice.is_connected():\n            return\n        if self.voice.is_listening():\n            if not force:\n                return\n            self.voice.stop_listening()\n        self.voice.listen(voice_recv.BasicSink(self.on_voice_packet))\n        enable_dave_decrypt(self.voice)\n        self._listener_armed_at = time.time()\n        logging.info("voice receive listener armed")\n\n''',
            "arm_voice_listener insertion",
        )

    if "self.audio.feed(data.pcm, self.config.silence_rms_threshold)" not in new:
        new = replace_once(
            new,
            "        if user and user.id in self.config.human_user_ids and self.listening:\n            self.audio.feed(data.pcm)",
            "        if user and user.id in self.config.human_user_ids and self.listening:\n            self.audio.feed(data.pcm, self.config.silence_rms_threshold)",
            "packet handler RMS gate call",
        )

    if "def voice_watchdog" not in new:
        new = insert_before_once(
            new,
            "    async def synth(self, text: str) -> str:\n",
            '''    async def voice_watchdog(self) -> None:\n        """Keep Discord's receive sink alive across long idle stretches.\n\n        Some voice receive sessions can remain connected but stop delivering\n        packets after a Discord voice websocket hiccup. Re-arm the sink if the\n        library reports that receive has stopped. Optional idle refresh is off\n        by default; open-mic silence is handled by RMS gating in AudioBuffer.\n        """\n        while True:\n            await asyncio.sleep(5)\n            if not self.voice or not self.voice.is_connected() or self.text_channel is None:\n                continue\n            try:\n                if not self.voice.is_listening():\n                    logging.warning("voice receive listener was inactive; re-arming")\n                    self.arm_voice_listener(force=True)\n                    continue\n                if self.config.idle_rearm_sec <= 0 or self.audio.has_pending_audio():\n                    continue\n                idle_for = self.audio.seconds_since_packet()\n                since_arm = time.time() - self._listener_armed_at\n                idle_enough = idle_for is None or idle_for >= self.config.idle_rearm_sec\n                if idle_enough and since_arm >= self.config.idle_rearm_sec:\n                    idle_label = "no packets yet" if idle_for is None else f"idle for {idle_for:.0f}s"\n                    logging.info("voice receive %s; refreshing listener", idle_label)\n                    self.arm_voice_listener(force=True)\n            except Exception:\n                logging.exception("voice watchdog failed")\n\n''',
            "voice watchdog insertion",
        )

    if "self.arm_voice_listener(force=True)\n            self.text_channel = msg.channel" not in new:
        new = replace_once(
            new,
            '''            if self.voice and self.voice.is_connected():\n                await self.voice.move_to(target)\n            else:\n                self.voice = await target.connect(cls=voice_recv.VoiceRecvClient)\n                self.voice.listen(voice_recv.BasicSink(self.on_voice_packet))\n                enable_dave_decrypt(self.voice)\n            self.text_channel = msg.channel''',
            '''            if self.voice and self.voice.is_connected():\n                await self.voice.move_to(target)\n            else:\n                self.voice = await target.connect(cls=voice_recv.VoiceRecvClient)\n            self.arm_voice_listener(force=True)\n            self.text_channel = msg.channel''',
            "join command listener re-arm",
        )

    if "elif command == \"listen\":\n            self.listening = True\n            self.arm_voice_listener(force=True)" not in new:
        new = replace_once(
            new,
            '''        elif command == "listen":\n            self.listening = True\n            await msg.channel.send("Listening again.")''',
            '''        elif command == "listen":\n            self.listening = True\n            self.arm_voice_listener(force=True)\n            await msg.channel.send("Listening again.")''',
            "listen command listener re-arm",
        )

    if "voice_channel_id={config.voice_channel_id}" not in new:
        new = replace_once(
            new,
            '        print(f"text_channel_id={config.text_channel_id}")\n',
            '        print(f"text_channel_id={config.text_channel_id}")\n        print(f"voice_channel_id={config.voice_channel_id}")\n',
            "check-config voice channel output",
        )

    if "silence_rms_threshold={config.silence_rms_threshold}" not in new:
        new = replace_once(
            new,
            '        print(f"whisper_model={config.whisper_model}")\n',
            '        print(f"whisper_model={config.whisper_model}")\n        print(f"silence_rms_threshold={config.silence_rms_threshold}")\n        print(f"idle_rearm_sec={config.idle_rearm_sec}")\n',
            "check-config listener tuning output",
        )
    elif "idle_rearm_sec={config.idle_rearm_sec}" not in new:
        new = replace_once(
            new,
            '        print(f"silence_rms_threshold={config.silence_rms_threshold}")\n',
            '        print(f"silence_rms_threshold={config.silence_rms_threshold}")\n        print(f"idle_rearm_sec={config.idle_rearm_sec}")\n',
            "check-config idle re-arm output",
        )

    return new


def main() -> int:
    target = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path("seven_voice.py")
    if not target.is_file():
        print(f"error: {target} not found")
        print("Run from inside the Seven Voice repo, or pass /path/to/seven_voice.py")
        return 1

    data = target.read_text(encoding="utf-8", errors="replace")
    try:
        new_data = patch(data)
    except PatchError as exc:
        print(f"error: {exc}")
        print("Seven Voice may have changed; patch manually or file an issue")
        return 1

    if new_data == data:
        print("already patched — nothing to do")
        print("ensure Seven Voice .env has TEXT_CHANNEL_ID/VOICE_CHANNEL_ID if you want service auto-join")
        return 0

    backup = target.with_name(f"{target.name}.bak-{time.strftime('%Y%m%dT%H%M%S')}-pre-listener-recovery")
    shutil.copy2(target, backup)
    target.write_text(new_data, encoding="utf-8")

    print(f"backup: {backup}")
    print("patched OK")
    print("next steps:")
    print("  1. add TEXT_CHANNEL_ID=<text channel id> and optionally VOICE_CHANNEL_ID=<voice channel id> to Seven Voice's .env")
    print("  2. tune SILENCE_RMS_THRESHOLD=200 only if idle room tone or quiet speech needs adjustment")
    print("  3. leave IDLE_REARM_SEC=0 unless receive goes stale without is_listening() changing")
    print("  4. restart the Seven Voice bridge")
    print("  5. if TTS works but transcripts stop later, run !listen to force a receive-sink re-arm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
