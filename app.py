import os
import streamlit as st
import streamlit.components.v1 as components

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

st.set_page_config(
    page_title="Live Transcription",
    page_icon="🎤",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- CONFIG ---
def get_backend_url() -> str:
    try:
        return st.secrets["BACKEND_WS_URL"]
    except (KeyError, FileNotFoundError):
        return os.getenv("BACKEND_WS_URL", "ws://localhost:8000/ws/transcribe")


# --- SIDEBAR ---
with st.sidebar:
    st.title("⚙️ Settings")
    backend_url = st.text_input(
        "Backend WebSocket URL",
        value=get_backend_url(),
        help="WebSocket URL of the deployed FastAPI backend",
    )
    max_speakers = st.slider("Max speakers", min_value=1, max_value=6, value=2)
    st.divider()

    st.markdown("### 📋 How to use")
    st.markdown(
        """
1. Set the **Backend URL** above.
2. Click **Start Recording** in the main panel.
3. Allow microphone access when prompted.
4. Speak — transcription appears in real-time.
5. Click **Stop** when finished.
        """
    )
    st.divider()

    st.markdown("### 🚀 Deploy backend")
    st.markdown(
        """
Deploy `live_transcription.py` on any of these platforms and paste the URL above:

- [Railway](https://railway.app)
- [Render](https://render.com)
- [Fly.io](https://fly.io)

Set `SPEECHMATICS_API_KEY` as an environment variable on the backend service.
        """
    )


# --- MAIN UI ---
st.title("🎤 Live Transcription")
st.caption("Real-time speech-to-text with speaker diarization powered by Speechmatics")

# Inject backend URL into the JS component via a data attribute
transcript_component = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  :root {{
    --bg:       #0e1117;
    --card:     #1a1d27;
    --border:   #2d3147;
    --accent:   #ff4b4b;
    --s1:       #4ade80;
    --s2:       #60a5fa;
    --s3:       #facc15;
    --s4:       #f472b6;
    --s5:       #a78bfa;
    --s6:       #fb923c;
    --text:     #f0f2f6;
    --muted:    #6b7a99;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', 'Source Sans Pro', sans-serif;
    padding: 0 4px;
  }}

  /* ── Controls ── */
  .controls {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 14px;
  }}

  button {{
    padding: 9px 22px;
    border: none;
    border-radius: 7px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: opacity .15s, transform .1s;
  }}
  button:active {{ transform: scale(.97); }}

  #startBtn {{
    background: var(--accent);
    color: #fff;
  }}
  #startBtn:disabled {{ opacity: .4; cursor: not-allowed; transform: none; }}

  #stopBtn {{
    background: var(--border);
    color: var(--text);
    display: none;
  }}
  #stopBtn.visible {{ display: inline-block; }}

  /* ── Status badge ── */
  #status {{
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--muted);
    margin-left: 4px;
  }}
  .dot {{
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--muted);
    flex-shrink: 0;
  }}
  .dot.live {{
    background: var(--accent);
    animation: blink 1.1s infinite;
  }}
  @keyframes blink {{ 0%,100% {{ opacity:1 }} 50% {{ opacity:.25 }} }}

  /* ── Transcript box ── */
  #transcript {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 18px 20px;
    min-height: 320px;
    max-height: 520px;
    overflow-y: auto;
    line-height: 1.75;
    font-size: 14.5px;
  }}

  #placeholder {{
    color: var(--muted);
    text-align: center;
    padding: 70px 0;
    font-size: 13px;
    user-select: none;
  }}

  /* ── Utterance blocks ── */
  .utterance {{
    display: flex;
    flex-direction: column;
    gap: 3px;
    margin-bottom: 14px;
    padding: 10px 14px;
    border-radius: 8px;
    background: rgba(255,255,255,.03);
    border-left: 3px solid transparent;
  }}
  .utterance.s1 {{ border-color: var(--s1); }}
  .utterance.s2 {{ border-color: var(--s2); }}
  .utterance.s3 {{ border-color: var(--s3); }}
  .utterance.s4 {{ border-color: var(--s4); }}
  .utterance.s5 {{ border-color: var(--s5); }}
  .utterance.s6 {{ border-color: var(--s6); }}

  .speaker-tag {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
  }}
  .s1 .speaker-tag {{ color: var(--s1); }}
  .s2 .speaker-tag {{ color: var(--s2); }}
  .s3 .speaker-tag {{ color: var(--s3); }}
  .s4 .speaker-tag {{ color: var(--s4); }}
  .s5 .speaker-tag {{ color: var(--s5); }}
  .s6 .speaker-tag {{ color: var(--s6); }}

  /* ── Partial (in-progress) row ── */
  .partial-row {{
    padding: 6px 14px;
    margin-bottom: 6px;
    border-left: 2px solid var(--border);
    color: var(--muted);
    font-style: italic;
    font-size: 13.5px;
  }}
</style>
</head>
<body>
  <div class="controls">
    <button id="startBtn" onclick="startRecording()">🎤 Start Recording</button>
    <button id="stopBtn"  onclick="stopRecording()">⏹ Stop</button>
    <div id="status">
      <div class="dot" id="dot"></div>
      <span id="statusText">Ready</span>
    </div>
  </div>

  <div id="transcript">
    <div id="placeholder">Press "Start Recording" to begin live transcription</div>
    <div id="content"></div>
  </div>

<script>
const WS_URL      = "{backend_url}";
const SPEAKER_CLS = ['s1','s2','s3','s4','s5','s6'];

let ws           = null;
let audioCtx     = null;
let processor    = null;
let micStream    = null;
let speakerIndex = {{}};
let colorCounter = 0;

function speakerClass(label) {{
  if (!(label in speakerIndex)) {{
    speakerIndex[label] = SPEAKER_CLS[colorCounter % SPEAKER_CLS.length];
    colorCounter++;
  }}
  return speakerIndex[label];
}}

function setStatus(text, live = false) {{
  document.getElementById('statusText').textContent = text;
  document.getElementById('dot').className = 'dot' + (live ? ' live' : '');
}}

function showContent() {{
  document.getElementById('placeholder').style.display = 'none';
  document.getElementById('content').style.display     = 'block';
}}

/* ── Partial transcript ── */
function updatePartial(speaker, text) {{
  const id  = 'partial-' + speaker;
  let   row = document.getElementById(id);
  if (!row) {{
    row    = document.createElement('div');
    row.id = id;
    row.className = 'partial-row';
    document.getElementById('content').appendChild(row);
  }}
  const cls  = speakerClass(speaker);
  const name = speaker.replace(/_/g, ' ');
  row.innerHTML = `<strong class="${{cls}}" style="font-size:10px;text-transform:uppercase;letter-spacing:.06em">${{name}}</strong>: ${{text}}`;
}}

/* ── Final transcript ── */
function addFinal(speaker, text) {{
  const partial = document.getElementById('partial-' + speaker);
  if (partial) partial.remove();

  const cls  = speakerClass(speaker);
  const name = speaker.replace(/_/g, ' ');
  const div  = document.createElement('div');
  div.className = `utterance ${{cls}}`;
  div.innerHTML = `<span class="speaker-tag">${{name}}</span><span>${{text}}</span>`;
  document.getElementById('content').appendChild(div);

  const box = document.getElementById('transcript');
  box.scrollTop = box.scrollHeight;
}}

/* ── Start ── */
async function startRecording() {{
  document.getElementById('startBtn').disabled = true;
  setStatus('Connecting…');

  try {{
    ws = new WebSocket(WS_URL);

    ws.onopen = async () => {{
      setStatus('Live', true);
      document.getElementById('stopBtn').classList.add('visible');
      showContent();

      micStream = await navigator.mediaDevices.getUserMedia({{ audio: true }});
      audioCtx  = new AudioContext({{ sampleRate: 16000 }});
      const src = audioCtx.createMediaStreamSource(micStream);
      processor = audioCtx.createScriptProcessor(4096, 1, 1);

      processor.onaudioprocess = (e) => {{
        if (ws && ws.readyState === WebSocket.OPEN) {{
          ws.send(e.inputBuffer.getChannelData(0).buffer.slice(0));
        }}
      }};

      src.connect(processor);
      processor.connect(audioCtx.destination);
    }};

    ws.onmessage = (e) => {{
      const msg = JSON.parse(e.data);
      if      (msg.type === 'partial') updatePartial(msg.speaker, msg.text);
      else if (msg.type === 'final')   addFinal(msg.speaker, msg.text);
    }};

    ws.onerror = () => {{
      setStatus('⚠ Cannot reach backend — check the URL in the sidebar');
      cleanup();
    }};

    ws.onclose = () => {{ if (audioCtx) cleanup(); }};

  }} catch (err) {{
    setStatus('Error: ' + err.message);
    cleanup();
  }}
}}

/* ── Stop ── */
function stopRecording() {{
  cleanup();
  setStatus('Stopped');
}}

/* ── Cleanup ── */
function cleanup() {{
  if (processor)  {{ processor.disconnect();  processor = null; }}
  if (audioCtx)   {{ audioCtx.close();        audioCtx  = null; }}
  if (micStream)  {{ micStream.getTracks().forEach(t => t.stop()); micStream = null; }}
  if (ws)         {{ ws.close();              ws        = null; }}
  document.getElementById('startBtn').disabled = false;
  document.getElementById('stopBtn').classList.remove('visible');
}}
</script>
</body>
</html>
"""

components.html(transcript_component, height=640, scrolling=False)

st.divider()
c1, c2 = st.columns(2)
c1.caption("Powered by [Speechmatics](https://speechmatics.com) Real-Time API")
c2.caption("Backend: FastAPI + WebSocket · Frontend: Streamlit")
