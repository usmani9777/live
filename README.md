# 🎤 Live Transcription

Real-time speech-to-text with speaker diarization, powered by the [Speechmatics](https://speechmatics.com) Real-Time API.

---

## Overview

The project is split into two parts that run independently:

```
Browser (mic) ──WebSocket──▶ FastAPI Backend ──WebSocket──▶ Speechmatics RT API
                                    │
                             streams back partial
                             & final transcripts
                                    │
                    Streamlit Frontend ◀──────────────────
```

| Layer | File | Tech |
|---|---|---|
| Frontend UI | `app.py` | Streamlit + vanilla JS |
| Backend API | `live_transcription.py` | FastAPI + WebSocket |
| Speech engine | Speechmatics cloud | RT WebSocket API |

---

## Workflow — Step by Step

### 1. User opens the Streamlit frontend (`app.py`)
- A Streamlit page renders with a **Start Recording** button embedded in a raw HTML/JS component.
- The sidebar lets the user configure the **backend WebSocket URL** and **max speakers**.

### 2. User clicks "Start Recording"
- The browser requests **microphone access** via `getUserMedia`.
- An `AudioContext` is created at **16 kHz, mono**.
- A `ScriptProcessor` node captures raw audio in **4096-sample chunks** and converts them to 32-bit float PCM (`pcm_f32le`).
- Each chunk is sent as a binary frame over a **WebSocket** to the FastAPI backend.

### 3. FastAPI backend receives audio (`live_transcription.py`)
- The `/ws/transcribe` WebSocket endpoint accepts the connection.
- Two async tasks run concurrently via `asyncio.gather`:
  - **`receive_loop`** — reads incoming binary audio frames from the browser and pushes them into an `asyncio.Queue`.
  - **`sm_loop`** — connects to Speechmatics and streams audio from that queue.

### 4. Audio is streamed to Speechmatics
- A `_QueueStream` object wraps the `asyncio.Queue` and exposes both an async-iterator interface and a `read()` method — satisfying the Speechmatics SDK's stream contract.
- The backend opens a WebSocket to `wss://eu2.rt.speechmatics.com/v2` using:
  - `encoding: pcm_f32le`, `sample_rate: 16000`
  - `diarization: speaker` with up to 10 speakers
  - `operating_point: enhanced` for higher accuracy
  - Partial transcripts enabled

### 5. Speechmatics returns transcripts
Two event handlers process results from Speechmatics:

- **`on_partial` (`AddPartialTranscript`)** — fired continuously as the model predicts words in real time. The backend immediately forwards these to the browser as:
  ```json
  { "type": "partial", "speaker": "speaker_1", "text": "hello wor", "source": "mic" }
  ```

- **`on_transcript` (`AddTranscript`)** — fired when Speechmatics has confirmed (finalised) a word. Words are accumulated in a per-speaker buffer.

### 6. Debounced flushing
Rather than breaking on every punctuation mark, the backend uses a **1.5-second silence debounce**:
- Every confirmed word resets a `call_later` timer for that speaker.
- If no new words arrive for 1.5 seconds, the buffer is flushed and sent to the browser as a final segment:
  ```json
  { "type": "final", "speaker": "speaker_1", "text": "Hello, how are you doing today", "source": "mic" }
  ```
- A **speaker change** (different speaker label on the next word) triggers an immediate flush of the previous speaker's buffer.

### 7. Frontend renders the transcript
- **Partial** messages update a live italic preview row per speaker.
- **Final** messages replace the partial row with a styled utterance block, colour-coded per speaker (up to 6 colours).
- The transcript box auto-scrolls as new content arrives.

### 8. User clicks "Stop"
- The browser closes the WebSocket and stops the mic stream.
- The backend's `receive_loop` catches the disconnect, puts a `None` sentinel into the queue, and signals `sm_loop` to shut down cleanly.

---

## Project Structure

```
.
├── app.py                  # Streamlit frontend
├── live_transcription.py   # FastAPI backend + Speechmatics integration
├── requirements.txt        # Python dependencies
├── Procfile                # Deploy command for Railway / Render / Fly.io
└── .env                    # Local secrets (not committed)
```

---

## Setup

### Prerequisites
- Python 3.11+
- A [Speechmatics](https://speechmatics.com) account with an API key

### Local development

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Create a .env file
echo "SPEECHMATICS_API_KEY=your_key_here" > .env

# 3. Start the backend (terminal 1)
uvicorn live_transcription:app --host 0.0.0.0 --port 8000

# 4. Start the frontend (terminal 2)
streamlit run app.py
```

Then open [http://localhost:8501](http://localhost:8501) and set the backend URL to `ws://localhost:8000/ws/transcribe`.

---

## Deployment

The backend (`live_transcription.py`) can be deployed to any platform that supports WebSockets:

| Platform | Notes |
|---|---|
| [Railway](https://railway.app) | Add `SPEECHMATICS_API_KEY` env var; `Procfile` is used automatically |
| [Render](https://render.com) | Use the `Procfile` start command; enable WebSocket support |
| [Fly.io](https://fly.io) | Run `fly launch`; set the secret with `fly secrets set SPEECHMATICS_API_KEY=...` |

The frontend (`app.py`) can be deployed to [Streamlit Community Cloud](https://streamlit.io/cloud) for free. Set `BACKEND_WS_URL` in the app's secrets to point at your deployed backend.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SPEECHMATICS_API_KEY` | ✅ Backend | Your Speechmatics RT API key |
| `SPEECHMATICS_URL` | ❌ | Defaults to `wss://eu2.rt.speechmatics.com/v2` |
| `BACKEND_WS_URL` | ❌ Frontend | Defaults to `ws://localhost:8000/ws/transcribe` |
| `PORT` | ❌ | HTTP port for the backend (default: 8000) |

---

## Tech Stack

- **[Speechmatics RT API](https://docs.speechmatics.com/rt-api-ref)** — real-time ASR + speaker diarization
- **[FastAPI](https://fastapi.tiangolo.com)** + **[uvicorn](https://www.uvicorn.org)** — async WebSocket backend
- **[Streamlit](https://streamlit.io)** — frontend UI
- **Web Audio API** — in-browser PCM capture at 16 kHz
