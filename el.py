#!/usr/bin/env python3
"""ElevenLabs Studio Kit v0.1 — local CLI for speech, sound effects, and music.

Free-plan credits only. The self-cap is 9,500 of the 10,000 credits that
reset each calendar month (95%). The API key is read from the
ELEVENLABS_API_KEY environment variable and is never printed, logged, or
written to a file.

Commands: tts, sfx, music, ledger, models.

Credit estimates are local (the live balance endpoints may hang on some
keys), so treat the ledger as a conservative meter, not a vendor invoice.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

VERSION = "v0.1"
TZ = ZoneInfo("America/Chicago")
API_BASE = "https://api.elevenlabs.io"
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"
DEFAULT_TTS_MODEL = "eleven_multilingual_v2"
DEFAULT_SFX_MODEL = "eleven_text_to_sound_v2"
DEFAULT_MUSIC_MODEL = "music_v1"
PLAN_CREDITS = 10_000
MONTHLY_CAP = 9_500  # 95% self-cap; never spend past this on purpose
TTS_CREDITS_PER_CHAR = 1
SFX_CREDITS_PER_GEN = 200
MUSIC_CREDITS_PER_MINUTE = 900
TIMEOUT_META = 20
TIMEOUT_GEN = 120
SFX_DURATION_MIN = 0.5
SFX_DURATION_MAX = 30.0
MUSIC_LENGTH_MIN = 3
MUSIC_LENGTH_MAX = 600
LEDGER_VERSION = 1
HANGING_ENDPOINTS = ("/v1/user", "/v1/user/subscription", "/v1/voices")

SEED_ENTRY = {
    "date": "2026-11-18",
    "kind": "tts",
    "estimated_credits": 30,
    "output_file": "first-words.mp3",
    "note": "First Studio Hours TTS clip, about 30 characters.",
}


class StudioError(Exception):
    """User-facing error for bad input, a cap hit, or an API failure."""


class CapError(StudioError):
    """This call would push the month over the 9,500-credit self-cap."""


def home() -> Path:
    raw = os.environ.get("ELEVENLABS_HOME", "").strip()
    return Path(raw).resolve() if raw else Path(__file__).resolve().parent


def out_dir() -> Path:
    path = home() / "out"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ledger_path() -> Path:
    raw = os.environ.get("ELEVENLABS_LEDGER", "").strip()
    return Path(raw).resolve() if raw else home() / "ledger.json"


def today(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=TZ)
    return now.astimezone(TZ)


def api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise StudioError(
            "No API key in the environment. Set ELEVENLABS_API_KEY "
            "(through the masked secret box, never by pasting it in chat)."
        )
    return key


def estimate_tts(text: str) -> int:
    if not text or not text.strip():
        raise StudioError("Need some text to speak.")
    return len(text) * TTS_CREDITS_PER_CHAR


def estimate_sfx() -> int:
    return SFX_CREDITS_PER_GEN


def estimate_music(length_seconds: int) -> int:
    if length_seconds < MUSIC_LENGTH_MIN or length_seconds > MUSIC_LENGTH_MAX:
        raise StudioError(
            f"Music length must be {MUSIC_LENGTH_MIN} to {MUSIC_LENGTH_MAX} seconds."
        )
    return math.ceil(length_seconds * MUSIC_CREDITS_PER_MINUTE / 60)


def parse_sfx_duration(raw: float | None) -> float | None:
    if raw is None:
        return None
    if raw < SFX_DURATION_MIN or raw > SFX_DURATION_MAX:
        raise StudioError(
            f"Sound-effect duration must be {SFX_DURATION_MIN} to "
            f"{SFX_DURATION_MAX} seconds, or omitted so the model picks."
        )
    return raw


def empty_ledger() -> dict[str, Any]:
    return {
        "version": LEDGER_VERSION,
        "monthly_cap": MONTHLY_CAP,
        "plan_credits": PLAN_CREDITS,
        "entries": [],
    }


def seeded_ledger() -> dict[str, Any]:
    data = empty_ledger()
    data["entries"] = [dict(SEED_ENTRY)]
    return data


def load_ledger() -> dict[str, Any]:
    path = ledger_path()
    if not path.is_file():
        data = seeded_ledger() if _should_seed(path) else empty_ledger()
        if _should_seed(path):
            save_ledger(data)
        return data
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StudioError(f"Could not read the ledger: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise StudioError("The ledger file is not in the expected format.")
    data.setdefault("version", LEDGER_VERSION)
    data.setdefault("monthly_cap", MONTHLY_CAP)
    data.setdefault("plan_credits", PLAN_CREDITS)
    return data


def _should_seed(path: Path) -> bool:
    """Seed only the kit's own ledger, never a test or override path."""
    if os.environ.get("ELEVENLABS_HOME", "").strip():
        return False
    if os.environ.get("ELEVENLABS_LEDGER", "").strip():
        return False
    return path == (Path(__file__).resolve().parent / "ledger.json")


def save_ledger(data: dict[str, Any]) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def entry_year_month(entry: dict[str, Any]) -> tuple[int, int] | None:
    raw = str(entry.get("date") or "")
    try:
        d = datetime.strptime(raw[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return d.year, d.month


def month_used(data: dict[str, Any], year: int, month: int) -> int:
    total = 0
    for entry in data.get("entries") or []:
        ym = entry_year_month(entry)
        if ym == (year, month):
            try:
                total += int(entry.get("estimated_credits") or 0)
            except (TypeError, ValueError):
                continue
    return total


def remaining_under_cap(data: dict[str, Any], now: datetime | None = None) -> int:
    stamp = today(now)
    used = month_used(data, stamp.year, stamp.month)
    return max(0, MONTHLY_CAP - used)


def assert_within_cap(estimate: int, data: dict[str, Any], now: datetime | None = None) -> None:
    if estimate < 0:
        raise StudioError("Credit estimate cannot be negative.")
    stamp = today(now)
    used = month_used(data, stamp.year, stamp.month)
    if used + estimate > MONTHLY_CAP:
        left = max(0, MONTHLY_CAP - used)
        raise CapError(
            f"Refusing this call: {estimate} estimated credits would take "
            f"this month from {used} to {used + estimate}, past the "
            f"{MONTHLY_CAP:,} self-cap (95% of the {PLAN_CREDITS:,} free "
            f"credits). Remaining under the cap: {left}."
        )


def relativize_output(path: Path | str) -> str:
    """Store kit-local paths relative to the kit home; leave others alone."""
    raw = Path(path)
    try:
        return raw.resolve().relative_to(home()).as_posix()
    except ValueError:
        return str(path)


def append_entry(
    kind: str,
    estimated_credits: int,
    output_file: str,
    note: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    stamp = today(now)
    entry = {
        "date": stamp.strftime("%Y-%m-%d"),
        "kind": kind,
        "estimated_credits": int(estimated_credits),
        "output_file": relativize_output(output_file),
        "note": (note or "").strip(),
    }
    data = load_ledger()
    data["entries"].append(entry)
    save_ledger(data)
    return entry


def default_output(kind: str, ext: str = ".mp3", now: datetime | None = None) -> Path:
    stamp = today(now).strftime("%Y%m%d-%H%M%S")
    return out_dir() / f"{kind}-{stamp}{ext}"


def _safe_error_body(raw: bytes) -> str:
    """Turn an error body into a short message. Never include headers."""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:400]
    detail = payload.get("detail", payload)
    if isinstance(detail, dict):
        status = detail.get("status") or ""
        message = detail.get("message") or detail.get("msg") or ""
        bits = [str(x) for x in (status, message) if x]
        return ": ".join(bits) if bits else str(detail)[:400]
    if isinstance(detail, list) and detail:
        first = detail[0]
        if isinstance(first, dict):
            return str(first.get("msg") or first.get("message") or first)[:400]
        return str(first)[:400]
    return str(detail)[:400]


def http_request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: int = TIMEOUT_META,
    expect_json: bool = False,
) -> bytes | Any:
    """Call the ElevenLabs API. The key goes only in the xi-api-key header."""
    if path in HANGING_ENDPOINTS:
        raise StudioError(
            f"Refusing to call {path}: that endpoint may hang with some "
            "key permission sets. The local ledger tracks credits instead."
        )
    url = API_BASE + path
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Accept": "application/json" if expect_json else "audio/mpeg",
        "xi-api-key": api_key(),
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        message = _safe_error_body(exc.read())
        label = f"HTTP {exc.code}"
        if message:
            label = f"{label}: {message}"
        raise StudioError(label) from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        # Do not stringify the whole exception; it can include the request.
        raise StudioError(f"Network error talking to ElevenLabs: {reason}") from None
    except TimeoutError as exc:
        raise StudioError(f"Timed out after {timeout}s calling {path}.") from exc
    if expect_json:
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StudioError("ElevenLabs returned something that is not JSON.") from exc
    return payload


def write_audio(path: Path, payload: bytes) -> Path:
    if not payload:
        raise StudioError("ElevenLabs returned an empty audio file.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def cmd_tts(
    text: str,
    voice_id: str = DEFAULT_VOICE,
    output: Path | None = None,
    note: str = "",
    model_id: str = DEFAULT_TTS_MODEL,
    now: datetime | None = None,
) -> dict[str, Any]:
    estimate = estimate_tts(text)
    data = load_ledger()
    assert_within_cap(estimate, data, now=now)
    dest = Path(output) if output else default_output("tts", now=now)
    body = {"text": text, "model_id": model_id}
    audio = http_request(
        "POST",
        f"/v1/text-to-speech/{voice_id}",
        body=body,
        timeout=TIMEOUT_GEN,
    )
    write_audio(dest, audio)  # type: ignore[arg-type]
    entry = append_entry("tts", estimate, str(dest), note or "TTS clip", now=now)
    return {"entry": entry, "output": str(dest), "estimated_credits": estimate}


def cmd_sfx(
    prompt: str,
    duration: float | None = None,
    output: Path | None = None,
    note: str = "",
    model_id: str = DEFAULT_SFX_MODEL,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not prompt or not prompt.strip():
        raise StudioError("Need a prompt describing the sound effect.")
    seconds = parse_sfx_duration(duration)
    estimate = estimate_sfx()
    data = load_ledger()
    assert_within_cap(estimate, data, now=now)
    dest = Path(output) if output else default_output("sfx", now=now)
    body: dict[str, Any] = {"text": prompt.strip(), "model_id": model_id}
    if seconds is not None:
        body["duration_seconds"] = seconds
    audio = http_request(
        "POST",
        "/v1/sound-generation",
        body=body,
        timeout=TIMEOUT_GEN,
    )
    write_audio(dest, audio)  # type: ignore[arg-type]
    entry = append_entry(
        "sfx", estimate, str(dest), note or prompt.strip()[:80], now=now
    )
    return {"entry": entry, "output": str(dest), "estimated_credits": estimate}


def cmd_music(
    prompt: str,
    length_seconds: int,
    output: Path | None = None,
    note: str = "",
    model_id: str = DEFAULT_MUSIC_MODEL,
    instrumental: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not prompt or not prompt.strip():
        raise StudioError("Need a prompt describing the music.")
    estimate = estimate_music(length_seconds)
    data = load_ledger()
    assert_within_cap(estimate, data, now=now)
    dest = Path(output) if output else default_output("music", now=now)
    body: dict[str, Any] = {
        "prompt": prompt.strip(),
        "music_length_ms": int(length_seconds * 1000),
        "model_id": model_id,
        "force_instrumental": bool(instrumental),
    }
    audio = http_request(
        "POST",
        "/v1/music",
        body=body,
        timeout=TIMEOUT_GEN,
    )
    write_audio(dest, audio)  # type: ignore[arg-type]
    entry = append_entry(
        "music", estimate, str(dest), note or prompt.strip()[:80], now=now
    )
    return {"entry": entry, "output": str(dest), "estimated_credits": estimate}


def cmd_ledger(now: datetime | None = None) -> str:
    stamp = today(now)
    data = load_ledger()
    used = month_used(data, stamp.year, stamp.month)
    left = remaining_under_cap(data, now=stamp)
    month_label = stamp.strftime("%Y-%m")
    lines = [
        f"ElevenLabs Studio Kit {VERSION} — local credit ledger",
        f"Month: {month_label} ({TZ.key})",
        f"Used this month (estimated): {used} credits",
        f"Self-cap: {MONTHLY_CAP:,} of {PLAN_CREDITS:,} free credits (95%)",
        f"Remaining under the cap: {left}",
        "This is a local estimate. Live balance endpoints are not used.",
        "Free plan: no rollover, no commercial license, never auto-upgrade.",
    ]
    month_entries = [
        e for e in data.get("entries") or [] if entry_year_month(e) == (stamp.year, stamp.month)
    ]
    if month_entries:
        lines.append("")
        lines.append("This month's entries:")
        for entry in month_entries:
            note = entry.get("note") or ""
            extra = f" — {note}" if note else ""
            lines.append(
                f"  {entry.get('date')}  {entry.get('kind'):5}  "
                f"{entry.get('estimated_credits')} cr  "
                f"{entry.get('output_file')}{extra}"
            )
    else:
        lines.append("No entries for this month yet.")
    return "\n".join(lines)


def cmd_models() -> str:
    payload = http_request("GET", "/v1/models", timeout=TIMEOUT_META, expect_json=True)
    if not isinstance(payload, list):
        raise StudioError("Unexpected models response.")
    lines = [f"{len(payload)} models:"]
    for model in payload:
        if not isinstance(model, dict):
            continue
        mid = model.get("model_id") or "?"
        name = model.get("name") or ""
        flags = []
        if model.get("can_do_text_to_speech"):
            flags.append("tts")
        desc = (model.get("description") or "").strip()
        flag = f" [{', '.join(flags)}]" if flags else ""
        line = f"  {mid}  {name}{flag}"
        if desc:
            line += f" — {desc[:80]}"
        lines.append(line)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="el.py",
        description=(
            "Local ElevenLabs studio wrapper. Free credits only; stops at "
            "the 95% self-cap. The API key is never printed."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    tts = sub.add_parser("tts", help="Turn text into speech and save an audio file.")
    tts.add_argument("text", help="The words to speak.")
    tts.add_argument("--voice", default=DEFAULT_VOICE, help="Voice id.")
    tts.add_argument("--output", "-o", default="", help="Output path (default: out/).")
    tts.add_argument("--model", default=DEFAULT_TTS_MODEL, help="TTS model id.")
    tts.add_argument("--note", default="", help="Short ledger note.")

    sfx = sub.add_parser("sfx", help="Generate a sound effect from a prompt.")
    sfx.add_argument("prompt", help="Describe the sound.")
    sfx.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Length in seconds (0.5 to 30). Omit to let the model pick.",
    )
    sfx.add_argument("--output", "-o", default="", help="Output path (default: out/).")
    sfx.add_argument("--model", default=DEFAULT_SFX_MODEL, help="Sound-generation model id.")
    sfx.add_argument("--note", default="", help="Short ledger note.")

    music = sub.add_parser("music", help="Generate a short music clip from a prompt.")
    music.add_argument("prompt", help="Describe the music.")
    music.add_argument(
        "--length",
        type=int,
        default=30,
        help="Length in seconds (3 to 600). Keep it short. Default: 30.",
    )
    music.add_argument("--output", "-o", default="", help="Output path (default: out/).")
    music.add_argument("--model", default=DEFAULT_MUSIC_MODEL, help="Music model id.")
    music.add_argument(
        "--vocals",
        action="store_true",
        help="Allow vocals (default is instrumental).",
    )
    music.add_argument("--note", default="", help="Short ledger note.")

    sub.add_parser("ledger", help="Show this month's estimated use and remaining cap.")
    sub.add_parser("models", help="List models from GET /v1/models.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "tts":
            result = cmd_tts(
                args.text,
                voice_id=args.voice,
                output=Path(args.output) if args.output else None,
                note=args.note,
                model_id=args.model,
            )
            print(
                f"Wrote {result['output']} (estimated {result['estimated_credits']} credits)."
            )
        elif args.command == "sfx":
            result = cmd_sfx(
                args.prompt,
                duration=args.duration,
                output=Path(args.output) if args.output else None,
                note=args.note,
                model_id=args.model,
            )
            print(
                f"Wrote {result['output']} (estimated {result['estimated_credits']} credits)."
            )
        elif args.command == "music":
            result = cmd_music(
                args.prompt,
                length_seconds=args.length,
                output=Path(args.output) if args.output else None,
                note=args.note,
                model_id=args.model,
                instrumental=not args.vocals,
            )
            print(
                f"Wrote {result['output']} (estimated {result['estimated_credits']} credits)."
            )
        elif args.command == "ledger":
            print(cmd_ledger())
        elif args.command == "models":
            print(cmd_models())
        else:
            parser.error(f"Unknown command: {args.command}")
    except CapError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except StudioError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
