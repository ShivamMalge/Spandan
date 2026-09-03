"""The single LLM egress point (`agents.md` §5). Nothing else in this package
— or this repository — may open a connection to a model provider.

Two modes, `SPANDAN_LLM_MODE`:

- **replay** (default): answers come from committed cassettes, keyed by a hash
  of the rendered prompt plus the model id. No network, no key, no sockets, and
  no SDK import — the test conftest enforces the socket claim at the socket
  layer rather than trusting this docstring, and `test_replay_needs_no_sdk`
  enforces the import claim. A missing cassette raises loudly; it never falls
  through to the network, because a "replay" that quietly records is how an
  offline test suite starts costing money and leaking prompts.
- **record**: one call per cache miss to the Anthropic Messages API through the
  official `anthropic` Python SDK, installed as the optional `record` extra
  (`pip install -e .[record]`) and imported only inside `_record`, after the
  key check. Requires `ANTHROPIC_API_KEY` read straight from the environment —
  no `.env`, no dotenv loader — and the key is passed to the client explicitly,
  so a credential the SDK might otherwise find on disk is never used by
  accident. Writes the cassette beside the others so the diff shows exactly
  what was recorded.

The provider has changed three times (Anthropic → Gemini → Groq → Anthropic
again, this time with a key); the cassette key includes the model id, so
recordings from different providers coexist and never replay as one another.
The two Gemini cassettes are the fabrication finding and stay exactly as
recorded.

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

#: The model id, exactly as the Anthropic API names it (no date suffix). Haiku
#: 4.5 by the user's choice: the question the next recording answers is whether
#: a different model family fabricates evidence the way gemini-3.1-flash-lite
#: did, and a small fast model is the one an explanation layer would actually
#: run on.
MODEL_ID = "claude-haiku-4-5"
API_KEY_ENV = "ANTHROPIC_API_KEY"
CASSETTE_DIR = Path(__file__).with_name("cassettes")
#: The note is a few lines and competes with a five-line template; this is a
#: deliberate short-output cap, not a default. A response that hits it is not
#: recorded (see `_record`).
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
            f"{API_KEY_ENV} set."
        )

    return _record(prompt, model, key, path)


def record_request(prompt: str, model: str) -> dict:
    """The exact keyword arguments the record path hands to the SDK. Kept as
    data so a test can check them against the installed SDK signature: the
    1.x SDK dropped `temperature` from `messages.create`, and the first
    recording attempt found that out at the terminal."""
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }


def _record(prompt: str, model: str, key: str, path: Path) -> str:
    # The key check comes first, before the SDK is even imported: record mode
    # without a key must die before anything could open a socket.
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        raise RuntimeError(f"SPANDAN_LLM_MODE=record requires {API_KEY_ENV}")

    try:
        import anthropic
    except ImportError as err:
        raise RuntimeError(
            "SPANDAN_LLM_MODE=record needs the anthropic SDK, which is an optional "
            "extra so that replay stays dependency-free: pip install -e .[record]"
        ) from err

    client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=60.0)
    try:
        response = client.messages.create(**record_request(prompt, model))
    except anthropic.AuthenticationError as err:
        raise RuntimeError(f"record call refused: {API_KEY_ENV} was not accepted ({err.message})") from err
    except anthropic.RateLimitError as err:
        retry_after = err.response.headers.get("retry-after", "unknown")
        raise RuntimeError(f"record call rate-limited (model {model}); retry-after {retry_after}s") from err
    except anthropic.APIStatusError as err:
        # The status line alone once cost real debugging time; the API's error
        # message is the actual explanation, so it travels with the exception.
        raise RuntimeError(
            f"record call failed: HTTP {err.status_code} from the Anthropic API (model {model}): {err.message}"
        ) from err
    except anthropic.APIConnectionError as err:
        raise RuntimeError(f"record call could not reach the Anthropic API: {err}") from err

    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        category = f", category {details.category}" if details is not None else ""
        raise RuntimeError(f"the model declined this prompt (stop_reason=refusal{category}); nothing recorded")
    if response.stop_reason == "max_tokens":
        raise RuntimeError(
            f"the note was cut off at MAX_TOKENS={MAX_TOKENS}; nothing recorded. "
            "A truncated note is not what the model said."
        )

    text = "".join(block.text for block in response.content if block.type == "text")
    if not text:
        raise RuntimeError(f"the Anthropic API returned no text for model {model}: {response.to_json()}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "key": key,
                "model": model,
                "recorded_via": (
                    f"anthropic messages api via the anthropic python sdk {anthropic.__version__}, "
                    f"model {model} (served as {response.model}), default sampling, "
                    f"max_tokens {MAX_TOKENS}, SPANDAN_LLM_MODE=record"
                ),
                "stop_reason": response.stop_reason,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
                "prompt": prompt,
                "response_text": text,
            },
            indent=1,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return text
