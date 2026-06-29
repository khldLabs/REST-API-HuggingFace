# THREATS.md — Security Review

> **Status:** This is a *write-up*, not a set of fixes. Nothing in the code has
> been patched. Each finding below describes the problem, where it lives, the
> impact, and a *direction* for remediation — the actual defensive code is left
> to be written deliberately, one OWASP risk per branch.
>
> Scope reviewed: `flask_api.py`, `create_key.py`, `client.js`, `examples/basic.js`.
> Mapped to the **OWASP Top 10 for LLM Applications**.

## Severity summary

| #  | Finding                                              | OWASP            | Severity  | Location |
|----|-----------------------------------------------------|------------------|-----------|----------|
| 1  | Path traversal via `Authorization` header           | LLM05 / general  | Critical  | `flask_api.py:42-44`, `19-25` |
| 2  | Hardcoded master key & model placeholder in source  | LLM02 / hygiene  | Critical  | `flask_api.py:11`, `17` |
| 3  | API keys are bearer secrets stored as plaintext filenames | LLM02      | High      | `create_key.py:39`, `flask_api.py:21` |
| 4  | No quota / rate-limit enforcement                    | LLM10            | High      | `flask_api.py:51-81` (absent) |
| 5  | No validation of `messages` structure/content        | LLM01 / LLM05    | High      | `flask_api.py:62-63` |
| 6  | No system-prompt-leak protection                     | LLM07            | Medium    | `flask_api.py:70-81` (absent) |
| 7  | No output handling / response sanitization           | LLM05            | Medium    | `flask_api.py:79-81` |
| 8  | Server binds `0.0.0.0` with no transport security    | general          | Medium    | `flask_api.py:118` |
| 9  | Auth uses non-constant-time existence check          | general          | Low       | `flask_api.py:44` |
| 10 | Usage tracking is racy (read-modify-write to file)   | general          | Low       | `flask_api.py:33-40` |

---

## 1. Path traversal via the `Authorization` header — **Critical**

**Where:** `flask_api.py:42-44` (`is_valid_key`) and `flask_api.py:19-25` (`load_key_data`).

```python
def is_valid_key(api_key):
    return os.path.exists(os.path.join(ALLOWED_KEYS_DIR, f"{api_key}.json"))
```

**Problem:** the `Authorization` header value is taken from the client and
spliced directly into a filesystem path. `os.path.join` does **not** sanitize —
if the header contains `../`, the path escapes `./allowed/`. A value like
`../../../../etc/passwd%00` (or any traversal sequence) lets an attacker probe
for the existence of arbitrary files on the host. Because `load_key_data` uses
the same pattern, `/key_info/<api_key>` can be used to *read* the contents of
any reachable `.json` file the process can open.

**Impact:** authentication bypass and arbitrary-file disclosure on the host.

**Remediation direction:** never let request-controlled input become a path
component. Validate the key against a strict allowlist charset (e.g.
`^[A-Za-z0-9_-]+$`) *before* touching the filesystem, reject anything else, and
additionally confirm the resolved absolute path stays inside `ALLOWED_KEYS_DIR`.
This is the `fix/auth-path-traversal` branch in your conventions.

---

## 2. Hardcoded master key & model placeholder in source — **Critical**

**Where:** `flask_api.py:17` (`MASTER_KEY = "KEY_HERE"`) and `flask_api.py:11`
(`MODEL_NAME = "INSERT_MODEL_HERE"`).

**Problem:** the master key — which authorizes `/update_params` to change global
generation behavior — is a literal in the source file. Any commit containing a
real value leaks it permanently into git history. The model name is also baked
in rather than configured.

**Impact:** full compromise of the privileged endpoint if a real key is ever
committed; no environment-specific configuration.

**Remediation direction:** read both from environment variables (e.g.
`os.environ["MASTER_KEY"]`), fail fast on startup if unset, and keep them in a
`.env` file that is git-ignored (now covered by `.gitignore`). Aligns with the
"no hardcoded secrets" rule.

---

## 3. API keys stored as plaintext filenames — **High**

**Where:** `create_key.py:39` writes `./allowed/<api_key>.json`; the key string
*is* the filename and also appears inside the file (`"API Key": api_key`).

**Problem:** the bearer secret is stored in cleartext and is enumerable by
directory listing. Anyone with read access to `./allowed/` obtains every valid
key. There is no hashing.

**Impact:** total credential disclosure from a single directory read or backup.

**Remediation direction:** store a hash of the key (e.g. salted SHA-256) as the
lookup, not the raw secret; keep the directory permissions tight; never log raw
keys.

---

## 4. No quota / rate-limit enforcement — **High (LLM10)**

**Where:** `flask_api.py:51-81`. `Usage Limit` is *recorded* by `create_key.py`
and `Usage Times` is incremented, but nothing ever compares them.

**Problem:** unbounded use of an expensive resource (GPU inference). A single
key — or an unauthenticated flood hitting the auth check — can exhaust compute.

**Impact:** denial of wallet / denial of service; the documented quota feature
is non-functional (the README even admits this).

**Remediation direction:** enforce `Usage Times < Usage Limit` before
generating; add per-key and per-IP rate limiting (token bucket or a library
such as Flask-Limiter). This is the `feat/llm10-rate-limiting` branch.

---

## 5. No validation of `messages` structure or content — **High (LLM01/LLM05)**

**Where:** `flask_api.py:62-63` — `messages = data["messages"]` is passed
straight to `tokenizer.apply_chat_template`.

**Problem:** the only check is that a `messages` key exists. There is no
verification that it's a list of `{role, content}` objects, no size/length cap,
and no prompt-injection inspection. Malformed input can crash the tokenizer;
hostile input is forwarded to the model unfiltered.

**Impact:** crashes (availability) and a wide-open prompt-injection surface.

**Remediation direction:** schema-validate each message (allowed roles,
string content, max count, max length) and run injection detection before
generation. This is the `feat/llm01-prompt-injection` work.

---

## 6. No system-prompt-leak protection — **Medium (LLM07)**

**Where:** generation path `flask_api.py:70-81`. The client can supply its own
`system` message and the model output is returned verbatim.

**Problem:** there is no guard preventing the model from echoing a privileged
system prompt, nor any separation between caller-supplied and server-enforced
instructions.

**Remediation direction:** keep server-side system instructions out of
client-controllable content and scan responses for leak patterns before
returning.

---

## 7. No output handling / response sanitization — **Medium (LLM05)**

**Where:** `flask_api.py:79-81` returns the decoded model text directly.

**Problem:** improper output handling — raw model text is handed to clients with
no encoding, filtering, or PII/secret redaction. Downstream consumers that render
it (HTML, shell, SQL) inherit injection risk.

**Remediation direction:** treat model output as untrusted; apply contextual
encoding and PII/secret redaction (your LLM02 redaction work) before returning.

---

## 8. Binds `0.0.0.0` with no transport security — **Medium**

**Where:** `flask_api.py:118` — `app.run(debug=False, port=5000, host='0.0.0.0')`.

**Problem:** the dev server listens on all interfaces over plain HTTP. Bearer
keys and prompts travel unencrypted; Flask's built-in server isn't meant for
production exposure.

**Remediation direction:** terminate TLS at a reverse proxy (nginx/caddy) or a
production WSGI server, and bind to `127.0.0.1` when only local access is needed.

---

## 9. Non-constant-time auth check — **Low**

**Where:** `flask_api.py:44`. Validity is "does this file exist," and the master
key compare at `flask_api.py:88` uses `!=`.

**Problem:** existence checks and `==`/`!=` on secrets can leak timing
information. Minor here, but worth noting for the master-key compare.

**Remediation direction:** compare secrets with `hmac.compare_digest`.

---

## 10. Racy usage tracking — **Low**

**Where:** `flask_api.py:33-40`. `update_key_usage` does read-JSON →
modify-in-memory → write-JSON with no locking.

**Problem:** concurrent requests with the same key can interleave and lose
counter increments or IP entries — and this becomes correctness-critical the
moment quota enforcement (#4) depends on the counter.

**Remediation direction:** serialize updates (file lock) or move usage state to
a store with atomic increments.

---

## Non-security correctness bugs (context, not vulnerabilities)

These aren't attacks but they mean the client and server don't actually
interoperate as written — relevant when testing any fix:

- **Response key mismatch:** server returns `{"response": ...}`
  (`flask_api.py:81`); client reads `response.data.content` (`client.js:118`)
  and `examples/basic.js:21` reads `response.content` → both get `undefined`.
- **`Bearer ` prefix mismatch:** client sends `Authorization: Bearer <key>`
  (`client.js:18`); server matches the raw header against `<key>.json`
  (`flask_api.py:44`) → it would look for a file literally named
  `Bearer <key>.json`.
- **`/key_info` URL mismatch:** client calls `/key_info` with no key
  (`client.js:133`); server route requires `/key_info/<api_key>`
  (`flask_api.py:103`) → 404.
