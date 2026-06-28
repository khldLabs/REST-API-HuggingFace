# Self-Hosted LLM REST API

A small self-hosted REST API that wraps a HuggingFace causal language model
behind a Flask backend, with a file-based API-key system and a JavaScript
client library for managing conversations.

> ⚠️ **Status: learning / work-in-progress, not production-ready.** This project
> has known security issues that are intentionally *unpatched* and documented in
> [THREATS.md](THREATS.md). Read that before exposing this anywhere.

---

## Architecture

```
client.js  ──HTTP──►  flask_api.py  ──►  HuggingFace model (transformers)
(conversation                │
 management)                 ▼
                       ./allowed/<key>.json   ◄── created by create_key.py
                       (file-based key store:
                        validity + usage stats)
```

- **`flask_api.py`** — Flask backend. Loads a transformers model once at
  startup and exposes the HTTP endpoints below.
- **`create_key.py`** — interactive CLI that writes one API-key file into
  `./allowed/`. Validity is simply "a file named `<key>.json` exists."
- **`client.js`** — JavaScript (`axios`) client class for building
  conversations and calling the API.
- **`examples/basic.js`** — minimal usage example for the client.

---

## Requirements

- Python 3.9+
- Python deps (pinned in [requirements.txt](requirements.txt)): `flask`,
  `transformers`, `torch`, `accelerate`
- Node.js (for the JS client) with `axios`
- A HuggingFace causal model that supports chat templates

---

## Setup

1. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure the backend.** In `flask_api.py`, the following are currently
   hardcoded placeholders and must be set before running:
   - `MODEL_NAME` (line 11) — the HuggingFace model id to load.
   - `MASTER_KEY` (line 17) — the privileged key for `/update_params`.

   > 🔐 These are secrets/config and should not be committed. See
   > [THREATS.md](THREATS.md) findings #2 — moving them to environment variables
   > is recommended.

3. **Create at least one API key**
   ```bash
   python create_key.py
   ```
   This prompts for the key string, owner, and optional usage limit, then writes
   `./allowed/<key>.json`. The `allowed/` directory is git-ignored.

4. **Run the backend**
   ```bash
   python flask_api.py
   ```
   The API listens on `http://0.0.0.0:5000`.

   > Note: the README previously said `python app.py`; the actual entry file is
   > `flask_api.py`.

---

## API endpoints

| Method | Path                 | Auth            | Purpose |
|--------|----------------------|-----------------|---------|
| POST   | `/generate`          | API key         | Generate a model response from a `messages` array. |
| POST   | `/update_params`     | **Master key**  | Update global generation parameters (e.g. `max_new_tokens`). |
| GET    | `/key_info/<api_key>`| (key in URL)    | Return the stored metadata for a key. |

### `POST /generate`

- **Header:** `Authorization: <api_key>`
- **Body:**
  ```json
  { "messages": [ { "role": "user", "content": "Hello" } ] }
  ```
- **Response:**
  ```json
  { "response": "...model output..." }
  ```
- **Errors:** `403` invalid/missing key, `400` missing `messages`.

### `POST /update_params`

- **Header:** `Authorization: <master_key>`
- **Body:** JSON object merged into the generation params, e.g.
  `{ "max_new_tokens": 256 }`.

### `GET /key_info/<api_key>`

- Returns the stored JSON for that key, or `404` if not found.

---

## Key file format

Each file in `./allowed/` looks like:

```json
{
    "API Key": "the-key",
    "Owner": "owner name",
    "Usage Limit": null,
    "Usage Times": 0,
    "IPs": []
}
```

`Usage Times` and `IPs` are updated on each `/generate` call. `Usage Limit` is
recorded but **not currently enforced** (see [THREATS.md](THREATS.md) #4).

---

## Known issues

This codebase has documented security findings and a few client/server contract
mismatches (e.g. the client reads a `content` field the server doesn't send, and
sends a `Bearer ` prefix the server doesn't strip). All of these — and their
remediation direction — are catalogued in **[THREATS.md](THREATS.md)**. They are
intentionally left unpatched for now.

---

## Security

Do not deploy this as-is. Start with [THREATS.md](THREATS.md), then address
findings one OWASP risk at a time. Never commit the `allowed/` directory or any
real keys (enforced by `.gitignore`).
