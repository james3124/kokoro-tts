# kokoro-tts

Self-hosted Kokoro-82M TTS FastAPI service — deployed on Render, audio stored in Supabase Storage.

## Stack

| Layer | Tech |
|---|---|
| TTS engine | [hexgrad/kokoro](https://github.com/hexgrad/kokoro) (82M params, Apache 2.0) |
| API | FastAPI + Uvicorn |
| Hosting | Render (free or Basic tier) |
| Storage | Supabase Storage |

---

## Deploy to Render

### 1. Push to GitHub
```bash
git init && git add . && git commit -m "init"
git remote add origin https://github.com/YOU/kokoro-tts
git push -u origin main
```

### 2. Create Render Web Service
- **New → Web Service** → connect repo
- Render auto-detects `render.yaml`

### 3. Set these in Render Dashboard → Environment
| Key | Value |
|---|---|
| `SUPABASE_URL` | `https://xxxx.supabase.co` |
| `SUPABASE_KEY` | your service role key |
| `DEFAULT_VOICE` | e.g. `af_heart` (optional, default is af_heart) |
| `LANG_CODE` | e.g. `a` for American EN (optional) |

### 4. Supabase bucket
Create a bucket named **`audio`** → set to **Public**.

---

## RAM Notes
| Render Plan | RAM | Notes |
|---|---|---|
| Free | 512 MB | May OOM — monitor logs |
| Basic | 1 GB | Recommended |

---

## API

### `POST /tts`
```json
{
  "text": "Hello from Kokoro!",
  "voice": "af_heart",
  "speed": 1.0,
  "lang_code": "a",
  "split_pattern": "\n+"
}
```
All fields except `text` are optional — fall back to env var defaults.

**Response:**
```json
{
  "url": "https://xxxx.supabase.co/storage/v1/object/public/audio/tts_uuid.wav",
  "filename": "tts_uuid.wav",
  "duration_seconds": 1.84,
  "voice": "af_heart",
  "lang_code": "a"
}
```

### `GET /health`
Returns loaded pipeline list, defaults, bucket name.

### `GET /voices`
Returns all voice IDs grouped by accent/gender.

### `GET /languages`
Returns all supported language codes.

---

## Voices

| ID | Gender | Accent |
|---|---|---|
| `af_heart` ⭐ | F | American |
| `af_bella` | F | American |
| `af_nicole` | F | American |
| `af_sarah` | F | American |
| `af_sky` | F | American |
| `am_adam` | M | American |
| `am_michael` | M | American |
| `bf_emma` | F | British |
| `bf_isabella` | F | British |
| `bm_george` | M | British |
| `bm_lewis` | M | British |

## Languages

| Code | Language | Extra deps |
|---|---|---|
| `a` | 🇺🇸 American English | — |
| `b` | 🇬🇧 British English | — |
| `e` | 🇪🇸 Spanish | — |
| `f` | 🇫🇷 French | — |
| `h` | 🇮🇳 Hindi | — |
| `i` | 🇮🇹 Italian | — |
| `j` | 🇯🇵 Japanese | `pip install misaki[ja]` |
| `p` | 🇧🇷 Brazilian Portuguese | — |
| `z` | 🇨🇳 Mandarin Chinese | `pip install misaki[zh]` |

---

## n8n Integration

```
Method: POST
URL: https://kokoro-tts.onrender.com/tts
Body:
{
  "text": "{{ $json.script }}",
  "voice": "af_heart",
  "speed": 1.0
}
```
Output `{{ $json.url }}` → CutEngine audio track URL.

---

## Phoneme Override Syntax

For custom pronunciation, Kokoro supports inline phoneme hints:
```
[Kokoro](/kˈOkəɹO/) is a TTS model.
```

---

## Local Dev
```bash
sudo apt-get install -y espeak-ng
pip install -r requirements.txt
cp .env.example .env   # fill in Supabase creds
uvicorn main:app --reload
```

Test:
```bash
curl -X POST http://localhost:8000/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world", "voice": "af_heart"}'
```
