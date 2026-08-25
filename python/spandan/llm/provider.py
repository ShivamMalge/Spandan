"""The single LLM egress point (`agents.md` §5). Nothing else in this package
— or this repository — may open a connection to a model provider.

Two modes, `SPANDAN_LLM_MODE`:

- **replay** (default): answers come from committed cassettes, keyed by a hash
  of the rendered prompt plus the model id. No network, no key, no sockets —
  the test conftest enforces that at the socket layer rather than trusting this
  docstring. A missing cassette raises loudly; it never falls through to the
  network, because a "replay" that quietly records is how an offline test suite
  starts costing money and leaking prompts.
- **record**: one HTTPS call to the Anthropic Messages API per cache miss, via
  `urllib` — deliberately not the SDK, so this stays the only egress point and
  adds no dependency. Requires `ANTHROPIC_API_KEY`. Writes the cassette beside
  the others so the diff shows exactly what was recorded.

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

MODEL_ID = "claude-sonnet-5"
CASSETTE_DIR = Path(__file__).with_name("cassettes")
API_URL = "https://api.anthropic.com/v1/messages"
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
            "ANTHROPIC_API_KEY set."
        )

    return _record(prompt, model, key, path)


def _record(prompt: str, model: str, key: str, path: Path) -> str:
    import urllib.request

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("SPANDAN_LLM_MODE=record requires ANTHROPIC_API_KEY")

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
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.loads(response.read().decode("utf-8"))

    text = "".join(
        block["text"] for block in body.get("content", []) if block.get("type") == "text"
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "key": key,
                "model": model,
                "recorded_via": "anthropic messages api, urllib, SPANDAN_LLM_MODE=record",
                "prompt": prompt,
                "response_text": text,
            },
            indent=1,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return text
