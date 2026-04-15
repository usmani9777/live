import asyncio
import logging
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from speechmatics.models import (
    ConnectionSettings,
    TranscriptionConfig,
    AudioSettings,
    RTSpeakerDiarizationConfig,
)
from speechmatics.client import WebsocketClient

load_dotenv()

# 1. --- CONFIGURATION ---
SPEECHMATICS_URL = os.getenv("SPEECHMATICS_URL", "wss://eu2.rt.speechmatics.com/v2")
SPEECHMATICS_API_KEY = os.getenv("SPEECHMATICS_API_KEY")
if not SPEECHMATICS_API_KEY:
    raise RuntimeError("SPEECHMATICS_API_KEY environment variable is not set")

SAMPLE_RATE = 16000
_CHUNK_BYTES = 4096

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("live-transcription")
# Silence noisy third-party loggers
logging.getLogger("speechmatics").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.INFO)

app = FastAPI(title="Live Transcription API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 2. --- HELPER CLASSES ---
class _QueueStream:
    """Bridges asyncio.Queue to the Speechmatics stream interface.

    Implements both the async-iterator protocol and a ``read()`` method,
    which is required by ``speechmatics.helpers.read_in_chunks``.
    """

    def __init__(self, queue: asyncio.Queue):
        self.queue = queue

    def __aiter__(self):
        return self

    async def __anext__(self):
        chunk = await self.queue.get()
        if chunk is None:
            raise StopAsyncIteration
        return chunk

    async def read(self, size: int = -1) -> bytes:
        """Read the next chunk from the queue; returns b'' on end-of-stream."""
        chunk = await self.queue.get()
        if chunk is None:
            return b""
        return chunk


# 3. --- MAIN WEBSOCKET ENDPOINT ---
@app.websocket("/ws/transcribe")
async def websocket_live_transcribe(websocket: WebSocket):
    await websocket.accept()
    log.info("[ws] client connected")

    audio_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
    speaker_map: dict[str, str] = {}
    connected = True

    FLUSH_DELAY = 1.5  # seconds of silence before flushing a segment

    state = {"last_sm_speaker": "S1"}
    speaker_buffers: dict[str, list[str]] = {}
    flush_timers: dict[str, asyncio.TimerHandle] = {}

    def map_speaker(sm_label: str) -> str:
        if sm_label not in speaker_map:
            n = len(speaker_map) + 1
            speaker_map[sm_label] = f"speaker_{n}"
            log.info(f"[sm] {sm_label} → Speaker {n}")
        return speaker_map[sm_label]

    async def send_safe(data: dict):
        try:
            if connected:
                await websocket.send_json(data)
        except Exception:
            pass

    def flush(speaker_label: str):
        words = speaker_buffers.pop(speaker_label, [])
        if not words or not connected:
            return
        text = " ".join(words)
        log.info(f"[sm] [final] [{speaker_label}] '{text}'")
        asyncio.create_task(
            send_safe({"type": "final", "text": text, "speaker": speaker_label, "source": "mic"})
        )

    def _cancel_flush_timer(speaker_label: str):
        handle = flush_timers.pop(speaker_label, None)
        if handle:
            handle.cancel()

    def _schedule_flush(speaker_label: str):
        """Reset the silence timer; flush only after FLUSH_DELAY seconds of no new words."""
        _cancel_flush_timer(speaker_label)
        loop = asyncio.get_event_loop()
        flush_timers[speaker_label] = loop.call_later(
            FLUSH_DELAY,
            lambda: asyncio.ensure_future(_async_flush(speaker_label)),
        )

    async def _async_flush(speaker_label: str):
        flush_timers.pop(speaker_label, None)
        flush(speaker_label)

    # --- SPEECHMATICS EVENT HANDLERS ---
    def on_partial(msg):
        if not connected:
            return
        results = msg.get("results", [])
        if not results:
            return

        current_words = []
        current_sm_speaker = state["last_sm_speaker"]

        for result in results:
            if result.get("type") != "word":
                continue
            alts = result.get("alternatives", [])
            if not alts:
                continue
            current_sm_speaker = alts[0].get("speaker") or current_sm_speaker
            current_words.append(alts[0].get("content", ""))

        if current_words:
            speaker_label = map_speaker(current_sm_speaker)
            partial_text = " ".join(current_words)
            prefix = " ".join(speaker_buffers.get(speaker_label, []))
            full_display = f"{prefix} {partial_text}".strip()
            asyncio.create_task(
                send_safe({"type": "partial", "text": full_display, "speaker": speaker_label, "source": "mic"})
            )

    def on_transcript(msg):
        results = msg.get("results", [])
        for result in results:
            rtype = result.get("type", "word")
            alts = result.get("alternatives", [])
            if not alts:
                continue

            content = alts[0].get("content", "")
            raw_speaker = alts[0].get("speaker")
            log.debug(f"[sm] word={content!r} raw_speaker={raw_speaker!r} type={rtype}")
            sm_speaker = raw_speaker or state["last_sm_speaker"]
            speaker_label = map_speaker(sm_speaker)

            if rtype == "punctuation":
                buf = speaker_buffers.get(speaker_label)
                if buf:
                    buf[-1] += content
                continue

            state["last_sm_speaker"] = sm_speaker

            # Speaker change: immediately flush the outgoing speaker
            for other in list(speaker_buffers.keys()):
                if other != speaker_label:
                    _cancel_flush_timer(other)
                    flush(other)

            speaker_buffers.setdefault(speaker_label, []).append(content)
            # Reset silence timer on every new confirmed word
            _schedule_flush(speaker_label)

    # --- CORE LOOPS ---
    async def receive_loop():
        nonlocal connected
        try:
            while True:
                data = await websocket.receive_bytes()
                await audio_queue.put(data)
        except WebSocketDisconnect:
            log.info("[ws] client disconnected")
        finally:
            connected = False
            await audio_queue.put(None)

    async def sm_loop():
        settings = ConnectionSettings(url=SPEECHMATICS_URL, auth_token=SPEECHMATICS_API_KEY)
        audio_settings = AudioSettings(encoding="pcm_f32le", sample_rate=SAMPLE_RATE, chunk_size=_CHUNK_BYTES)
        config = TranscriptionConfig(
            language="en",
            operating_point="enhanced",
            diarization="speaker",
            enable_partials=True,
            speaker_diarization_config=RTSpeakerDiarizationConfig(max_speakers=10),
        )

        client = WebsocketClient(settings)
        client.add_event_handler("AddPartialTranscript", on_partial)
        client.add_event_handler("AddTranscript", on_transcript)

        try:
            await client.run(_QueueStream(audio_queue), config, audio_settings)
        except Exception as e:
            log.error(f"[sm] error: {e}", exc_info=True)

    await asyncio.gather(receive_loop(), sm_loop())


# 4. --- EXECUTION ---
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)