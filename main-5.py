import os, gc, time
os.environ.setdefault("HF_HOME", "./hf_cache")

import torch
torch.set_num_threads(1)
torch.set_grad_enabled(False)

import io
import uuid
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from supabase import create_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "audio")
DEFAULT_VOICE   = os.environ.get("DEFAULT_VOICE", "af_heart")
DEFAULT_LANG    = os.environ.get("LANG_CODE", "a")
SAMPLE_RATE     = 24000
UNLOAD_AFTER    = int(os.environ.get("UNLOAD_AFTER_SECONDS", "60"))

LANG_MAP = {
    "a": "American English", "b": "British English",
    "e": "Spanish",          "f": "French",
    "h": "Hindi",            "i": "Italian",
    "j": "Japanese",         "p": "Brazilian Portuguese",
    "z": "Mandarin",
}

VOICES = {
    "american_female": ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky"],
    "american_male":   ["am_adam", "am_michael"],
    "british_female":  ["bf_emma", "bf_isabella"],
    "british_male":    ["bm_george", "bm_lewis"],
}
ALL_VOICES = [v for group in VOICES.values() for v in group]

# ── State ──────────────────────────────────────────────────────────────────────
_pipelines     = {}
_last_request  = 0.0
_supabase      = None


# ── Pipeline helpers ───────────────────────────────────────────────────────────
def get_pipeline(lang_code: str):
    if lang_code not in _pipelines:
        logger.info(f"Loading Kokoro pipeline lang='{lang_code}'...")
        from kokoro import KPipeline
        p = KPipeline(lang_code=lang_code)
        for m in p.model.modules():
            if hasattr(m, "weight") and m.weight is not None:
                m.weight.data = m.weight.data.half()
        _pipelines[lang_code] = p
        gc.collect()
        logger.info(f"✅ Pipeline '{lang_code}' ready (float16)")
    return _pipelines[lang_code]


def unload_all():
    if _pipelines:
        _pipelines.clear()
        gc.collect()
        logger.info("♻️  Pipelines unloaded — RAM freed")


async def unload_watcher():
    while True:
        await asyncio.sleep(30)
        if _pipelines and time.time() - _last_request > UNLOAD_AFTER:
            unload_all()


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _supabase
    logger.info("Connecting to Supabase...")
    _supabase = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )
    logger.info(f"🚀 Ready — model loads on first request, unloads after {UNLOAD_AFTER}s idle")
    task = asyncio.create_task(unload_watcher())
    yield
    task.cancel()
    unload_all()


app = FastAPI(title="Kokoro TTS", version="3.0.0", lifespan=lifespan)


# ── Schemas ────────────────────────────────────────────────────────────────────
class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)
    voice: Optional[str] = Field(default=None)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    lang_code: Optional[str] = Field(default=None)
    split_pattern: Optional[str] = Field(default=r"\n+")


class TTSResponse(BaseModel):
    url: str
    filename: str
    duration_seconds: float
    voice: str
    lang_code: str


# ── Core ───────────────────────────────────────────────────────────────────────
def _synthesize(text, voice, speed, lang_code, split_pattern):
    p = get_pipeline(lang_code)
    kwargs = {"voice": voice, "speed": speed}
    if split_pattern:
        kwargs["split_pattern"] = split_pattern
    chunks = [item[-1] for item in p(text, **kwargs)]
    if not chunks:
        raise RuntimeError("No audio chunks returned")
    return np.concatenate(chunks)


def _to_wav(audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, format="WAV")
    return buf.getvalue()


def _upload(filename: str, wav: bytes) -> str:
    _supabase.storage.from_(SUPABASE_BUCKET).upload(
        path=filename, file=wav,
        file_options={"content-type": "audio/wav"},
    )
    return _supabase.storage.from_(SUPABASE_BUCKET).get_public_url(filename)


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.post("/tts", response_model=TTSResponse)
async def tts(req: TTSRequest):
    global _last_request
    _last_request = time.time()

    voice     = req.voice     or DEFAULT_VOICE
    lang_code = req.lang_code or DEFAULT_LANG

    if voice not in ALL_VOICES:
        raise HTTPException(400, f"Unknown voice '{voice}'")
    if lang_code not in LANG_MAP:
        raise HTTPException(400, f"Unknown lang_code '{lang_code}'")

    loop = asyncio.get_event_loop()

    try:
        audio = await loop.run_in_executor(
            None, lambda: _synthesize(req.text, voice, req.speed, lang_code, req.split_pattern)
        )
    except Exception as e:
        logger.exception("Synthesis failed")
        raise HTTPException(500, f"TTS error: {e}")

    wav      = _to_wav(audio)
    filename = f"tts_{uuid.uuid4()}.wav"

    try:
        url = await loop.run_in_executor(None, lambda: _upload(filename, wav))
    except Exception as e:
        logger.exception("Upload failed")
        raise HTTPException(500, f"Storage error: {e}")

    _last_request = time.time()
    duration = round(len(audio) / SAMPLE_RATE, 2)
    logger.info(f"✅ {filename} | {duration}s | {voice}")

    return TTSResponse(
        url=url, filename=filename,
        duration_seconds=duration, voice=voice, lang_code=lang_code,
    )


@app.get("/health")
async def health():
    return {
        "ok": True,
        "model_loaded": bool(_pipelines),
        "loaded_langs": list(_pipelines.keys()),
        "idle_seconds": round(time.time() - _last_request) if _last_request else None,
        "unloads_after_seconds": UNLOAD_AFTER,
        "default_voice": DEFAULT_VOICE,
        "default_lang": DEFAULT_LANG,
    }


@app.get("/voices")
async def voices():
    return {"default": DEFAULT_VOICE, "all": ALL_VOICES, "grouped": VOICES}


@app.get("/languages")
async def languages():
    return {"default": DEFAULT_LANG, "available": LANG_MAP}
