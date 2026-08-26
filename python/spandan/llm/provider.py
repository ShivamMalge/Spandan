"""The single LLM egress point (`agents.md` §5). Nothing else in this package
— or this repository — may open a connection to a model provider.

Two modes, `SPANDAN_LLM_MODE`:

- **replay** (default): answers come from committed cassettes, keyed by a hash
  of the rendered prompt plus the model id. No network, no key, no sockets —
  the test conftest enforces that at the socket layer rather than trusting this
  docstring. A missing cassette raises loudly; it never falls through to the
  network, because a "replay" that quietly records is how an offline test suite
  starts costing money and leaking prompts.
- **record**: one HTTPS call per cache miss to Gemini's OpenAI-compatible
  chat-completions endpoint, via `urllib` — deliberately no SDK, so this stays
  the only egress point and adds no dependency. Requires `GEMINI_API_KEY` (read
  straight from the environment; no .env file, no dotenv loader). Writes the
  cassette beside the others so the diff shows exactly what was recorded.

The provider returns text. It has no access to the detector, the evaluation, or
the stream — the import-graph test asserts `spandan.detect` and `spandan.eval`
cannot even *import* this package, and the poisoned-import test proves
`make eval`'s numbers survive this package being unimportable.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

#: The Gemini model id, verified against ai.google.dev/gemini-api/docs/models
#: (stable, free tier). Flash-Lite is sized to the task — six structured fields
#: into a bounded paragraph is extraction and formatting — and the newest Flash
#: 503'd on free-tier capacity. Whether Lite also clears the judgement bar
#: (TARGET.md's dismissal test) is answered by the comparison, not assumed.
#: Fallback if this id 503s too: "gemini-2.5-flash-lite" (also free tier).
MODEL_ID = "gemini-3.1-flash-lite"
CASSETTE_DIR = Path(__file__).with_name("cassettes")
API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
MAX_TOKENS = 700


class CassetteMiss(RuntimeError):
    """Raised in replay mode when no cassette matches the prompt.

    Loud on purpose: the alternative — silently returning something, or
    silently going to the network — is exactly the kind of quiet fallback this
    project keeps finding in its own measurements.
    """


def _cassette_key(prompt: str, model: str) -> str:
    return hashlib.sha256(f"{model}\n{prompt}".encode("utf-8")).hexdigest()[:32]


def _cassette_path(key: str) -> Path:
    return CASSETTE_DIR / f"{key}.json"


def complete(prompt: str, model: str = MODEL_ID) -> str:
    """The one function that may talk to a model. Prompt in, text out."""
    mode = os.environ.get("SPANDAN_LLM_MODE", "replay")
    key = _cassette_key(prompt, model)
    path = _cassette_path(key)

    if path.exists():
        cassette = json.loads(path.read_text(encoding="utf-8"))
        return cassette["response_text"]

    if mode != "record":
        raise CassetteMiss(
            f"no cassette {key} for this prompt (model {model}) and "
            f"SPANDAN_LLM_MODE={mode!r}. Replay mode never touches the network. "
            "Record one deliberately: SPANDAN_LLM_MODE=record with "
            "GEMINI_API_KEY set."
        )

    return _record(prompt, model, key, path)


def _record(prompt: str, model: str, key: str, path: Path) -> str:
    import urllib.error
    import urllib.request

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("SPANDAN_LLM_MODE=record requires GEMINI_API_KEY")

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(
            {
                "model": model,
                "max_tokens": MAX_TOKENS,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        # The status line alone ("402 Payment Required") once cost real
        # debugging time; the API's JSON error body is the actual explanation.
        detail = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"record call failed: HTTP {err.code} {err.reason} from {API_URL} "
            f"(model {model}): {detail}"
        ) from err

    text = body["choices"][0]["message"]["content"]
    if not isinstance(text, str) or not text:
        raise RuntimeError(f"Gemini returned no text for model {model}: {body!r}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "key": key,
                "model": model,
                "recorded_via": (
                    f"gemini openai-compatible chat-completions api, model {model}, "
                    "urllib, SPANDAN_LLM_MODE=record"
                ),
                "prompt": prompt,
                "response_text": text,
            },
            indent=1,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return text
