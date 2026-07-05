#!/usr/bin/env python3
"""Optionally patch Seven Voice to ignore open-mic silence before buffering.

Legacy note: for new installs, prefer patch_seven_voice_listener_recovery.py.
That newer patch includes this RMS silence gate plus listener re-arm/watchdog
recovery for stale Discord receive sinks.

Some Discord voice receive setups keep delivering low-level "silence"/room tone
packets while nobody is speaking. If those packets are buffered as speech, the
bridge may appear to stop transcribing after an idle stretch: it is waiting for a
clean silence gap that never arrives, or it feeds Whisper a giant mostly-silent
utterance.

This patch adds a simple RMS gate before Seven Voice buffers PCM frames from
human users. Frames below SILENCE_RMS_THRESHOLD are treated as silence and not
added to the utterance buffer.

Usage:
    python3 patch_seven_voice_idle_gate.py /path/to/seven_voice.py

Run from inside the Seven Voice repo with no argument to patch ./seven_voice.py.
Idempotent: safe to run repeatedly. A timestamped backup is created first.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

MARKER = "SILENCE_RMS_THRESHOLD"

REPLACEMENTS: list[tuple[str, str, str]] = [
    (
        "config field",
        '    min_utterance_sec: float = 0.6\n    chunk_chars: int = 700',
        '    min_utterance_sec: float = 0.6\n    silence_rms_threshold: float = 200.0\n    chunk_chars: int = 700',
    ),
    (
        "env setting",
        '            min_utterance_sec=float(os.getenv("MIN_UTTERANCE_SEC", "0.6")),\n            chunk_chars=int(os.getenv("CHUNK_CHARS", "700")),',
        '            min_utterance_sec=float(os.getenv("MIN_UTTERANCE_SEC", "0.6")),\n            silence_rms_threshold=float(os.getenv("SILENCE_RMS_THRESHOLD", "200")),\n            chunk_chars=int(os.getenv("CHUNK_CHARS", "700")),',
    ),
    (
        "audio buffer gate",
        '''    def feed(self, pcm: bytes) -> None:\n        with self.lock:\n            self.chunks.append(pcm)\n            self.last_packet = time.time()''',
        '''    def feed(self, pcm: bytes, silence_rms_threshold: float = 0.0) -> bool:\n        if silence_rms_threshold > 0:\n            samples = np.frombuffer(pcm, dtype=np.int16)\n            if not samples.size:\n                return False\n            rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))\n            if rms < silence_rms_threshold:\n                return False\n        with self.lock:\n            self.chunks.append(pcm)\n            self.last_packet = time.time()\n        return True''',
    ),
    (
        "packet handler",
        '        if user and user.id in self.config.human_user_ids and self.listening:\n            self.audio.feed(data.pcm)',
        '        if user and user.id in self.config.human_user_ids and self.listening:\n            self.audio.feed(data.pcm, self.config.silence_rms_threshold)',
    ),
    (
        "config output",
        '        print(f"whisper_model={config.whisper_model}")\n        return',
        '        print(f"whisper_model={config.whisper_model}")\n        print(f"silence_rms_threshold={config.silence_rms_threshold}")\n        return',
    ),
]


def main() -> int:
    target = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path("seven_voice.py")
    if not target.is_file():
        print(f"error: {target} not found")
        print("Run from inside the Seven Voice repo, or pass /path/to/seven_voice.py")
        return 1

    data = target.read_text(encoding="utf-8", errors="replace")
    if MARKER in data:
        print("already patched — nothing to do")
        print("tune with SILENCE_RMS_THRESHOLD=200 in Seven Voice's .env if needed")
        return 0

    if "import numpy as np" not in data:
        print("error: expected `import numpy as np` not found; refusing to patch blind")
        return 1

    new_data = data
    for label, old, new in REPLACEMENTS:
        count = new_data.count(old)
        if count != 1:
            print(f"error: {label} pattern found {count} times, expected exactly 1")
            print("Seven Voice may have changed; patch manually or file an issue")
            return 1
        new_data = new_data.replace(old, new)

    backup = target.with_name(f"{target.name}.bak-{time.strftime('%Y%m%dT%H%M%S')}-pre-idle-gate")
    shutil.copy2(target, backup)
    target.write_text(new_data, encoding="utf-8")

    print(f"backup: {backup}")
    print("patched OK")
    print("next steps:")
    print("  1. add SILENCE_RMS_THRESHOLD=200 to Seven Voice's .env (optional; 200 is the default)")
    print("  2. restart the Seven Voice bridge")
    print("  3. if quiet speech is dropped, lower it (try 100); if room noise accumulates, raise it (try 300-500)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
