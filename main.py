import os
os.environ.setdefault("HF_HOME", "./hf_cache")

import io
import uuid
import logging
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config from env ────────────────────────────────────────────────────────────
SUPABASE_BUCKET  = os.environ.get("SUPABASE_BUCKET", "audio")
DEFAULT_VOICE    = os.environ.get("DEFAULT_VOICE", "af_heart")
DEFAULT_LANG     = os.environ.get("LANG_CODE", "a")
SAMPLE_RATE      = 24000

# ── Language code map ──────────────────────────────────────────────────────────
LANG_MAP = {
    "a": "🇺🇸 American English",
    "b": "🇬🇧 British English",
    "e": "🇪🇸 Spanish",
    "f": "🇫🇷 French",
    "h": "🇮🇳 Hindi",
    "i": "🇮🇹 Italian",
    "j": "🇯🇵 Japanese",   # requires: pip install misaki[ja]
    "p": "🇧🇷 Brazilian Portuguese",
    "z": "🇨🇳 Mandarin Chinese",  # requires: pip install misaki[zh]
}

# ── Voice registry ─────────────────────────────────────────────────────────────
VOICES = {
    "american_female": ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky"],
    "american_male":   ["am_adam", "am_michael"],
    "british_female":  ["bf_emma", "bf_isabella"],
    "british_male":    ["bm_george", "bm_lewis"],
}
ALL_VOICES = [v for group in VOICES.values() for v in group]

# ── Pipeline cache (one per lang_code, lazy-loaded) ───────────────────────────
_pipelines: dict = {}
supabase: Client = None


def get_pipeline(lang_code: str):
    """Return a cached KPipeline for the given lang_code, creating it if needed."""
    if lang_code not in _pipelines:
        if lang_code not in LANG_MAP:
            raise ValueError(f"Unknown lang_code '{lang_code}'. Valid: {list(LANG_MAP)}")
        logger.info(f"Loading pipeline for lang_code='{lang_code}' ({LANG_MAP[lang_code]})...")
        from kokoro import KPipeline
        _pipelines[lang_code] = KPipeline(lang_code=lang_code)
        logger.info(f"✅ Pipeline '{lang_code}' ready")
    return _pipelines[lang_code]


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global supabase

    logger.info("Connecting to Supabase...")
    supabase = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    # Pre-load default language pipeline at startup
    get_pipeline(DEFAULT_LANG)
    logger.info(f"🚀 Ready — default voice: {DEFAULT_VOICE}, lang: {DEFAULT_LANG}")

    yield

    _pipelines.clear()
    logger.info("Shutdown complete")


app = FastAPI(title="Kokoro TTS", version="2.0.0", lifespan=lifespan)


# ── Schemas ────────────────────────────────────────────────────────────────────
class TTSRequest(BaseModel):
    text: str = Field(
        ..., min_length=1, max_length=10000,
        description="Text to synthesize. Supports phoneme overrides e.g. [word](/fəˈnɛtɪk/)"
    )
    voice: Optional[str] = Field(
        default=None,
        description=f"Kokoro voice ID. Defaults to DEFAULT_VOICE env var (currently '{DEFAULT_VOICE}')"
    )
    speed: float = Field(
        default=1.0, ge=0.5, le=2.0,
        description="Speech speed multiplier"
    )
    lang_code: Optional[str] = Field(
        default=None,
        description=f"Language code. Defaults to LANG_CODE env var (currently '{DEFAULT_LANG}'). "
                    f"Options: {list(LANG_MAP.keys())}"
    )
    split_pattern: Optional[str] = Field(
        default=r"\n+",
        description="Regex pattern to split long text into chunks. Use null to disable."
    )


class TTSResponse(BaseModel):
    url: str
    filename: str
    duration_seconds: float
    voice: str
    lang_code: str


# ── Core synthesis ─────────────────────────────────────────────────────────────
def _synthesize(text: str, voice: str, speed: float, lang_code: str, split_pattern: Optional[str]) -> np.ndarray:
    """Blocking synthesis — run via run_in_threadpool."""
    pipeline = get_pipeline(lang_code)

    kwargs = {"voice": voice, "speed": speed}
    if split_pattern:
        kwargs["split_pattern"] = split_pattern

    chunks = []
    for _, gs, _, audio in _enumerate_generator(pipeline(text, **kwargs)):
        logger.info(f"  chunk: {gs[:60]}...")
        chunks.append(audio)

    if not chunks:
        raise RuntimeError("KPipeline returned no audio chunks")
    return np.concatenate(chunks)


def _enumerate_generator(gen):
    """Normalize generator output whether it yields (gs, ps, audio) or (i, gs, ps, audio)."""
    for item in gen:
        if len(item) == 3:
            gs, ps, audio = item
            yield None, gs, ps, audio
        else:
            i, gs, ps, audio = item
            yield i, gs, ps, audio


def _to_wav_bytes(audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, format="WAV")
    return buf.getvalue()


def _upload(filename: str, wav_bytes: bytes) -> str:
    supabase.storage.from_(SUPABASE_BUCKET).upload(
        path=filename,
        file=wav_bytes,
        file_options={"content-type": "audio/wav"},
    )
    return supabase.storage.from_(SUPABASE_BUCKET).get_public_url(filename)


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.post("/tts", response_model=TTSResponse)
async def tts(req: TTSRequest):
    voice     = req.voice     or DEFAULT_VOICE
    lang_code = req.lang_code or DEFAULT_LANG

    # Validate voice (only for built-in English voices; custom .pt not validated here)
    if voice not in ALL_VOICES:
        raise HTTPException(400, detail=f"Unknown voice '{voice}'. See GET /voices")

    # Validate lang_code
    if lang_code not in LANG_MAP:
        raise HTTPException(400, detail=f"Unknown lang_code '{lang_code}'. See GET /languages")

    # 1. Synthesize (blocking → threadpool)
    try:
        audio = await run_in_threadpool(
            _synthesize, req.text, voice, req.speed, lang_code, req.split_pattern
        )
    except Exception as exc:
        logger.exception("TTS synthesis failed")
        raise HTTPException(500, detail=f"TTS error: {exc}") from exc

    # 2. Encode WAV
    wav_bytes = _to_wav_bytes(audio)

    # 3. Upload to Supabase
    filename = f"tts_{uuid.uuid4()}.wav"
    try:
        url = await run_in_threadpool(_upload, filename, wav_bytes)
    except Exception as exc:
        logger.exception("Supabase upload failed")
        raise HTTPException(500, detail=f"Storage error: {exc}") from exc

    duration = round(len(audio) / SAMPLE_RATE, 2)
    logger.info(f"✅ {filename} | {duration}s | voice={voice} lang={lang_code}")

    return TTSResponse(
        url=url,
        filename=filename,
        duration_seconds=duration,
        voice=voice,
        lang_code=lang_code,
    )


@app.get("/health")
async def health():
    return {
        "ok": True,
        "loaded_pipelines": list(_pipelines.keys()),
        "default_voice": DEFAULT_VOICE,
        "default_lang": DEFAULT_LANG,
        "bucket": SUPABASE_BUCKET,
    }


@app.get("/voices")
async def voices():
    return {
        "default": DEFAULT_VOICE,
        "all": ALL_VOICES,
        "grouped": VOICES,
    }


@app.get("/languages")
async def languages():
    return {
        "default": DEFAULT_LANG,
        "available": LANG_MAP,
        "note": "Japanese (j) and Mandarin (z) require extra pip deps — see README",
    }
