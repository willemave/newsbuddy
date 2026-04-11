#!/usr/bin/env python3
"""Probe Google Gemini routing paths for News App workers.

This script tests whether calls succeed via:
1) Gemini Developer API (google-gla) using API key.
2) Vertex AI in a pinned region (us-central1) using google-genai SDK.
3) Vertex AI in a pinned region via pydantic-ai GoogleProvider.
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

PROMPT = "Reply with exactly OK"
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"

# Keep script runnable without PYTHONPATH by reading credentials from .env directly.
load_dotenv(dotenv_path=ENV_PATH, override=True)


@dataclass
class ProbeResult:
    name: str
    ok: bool
    duration_ms: int
    detail: str


def _mask_secret(value: str | None) -> str:
    if not value:
        return "<unset>"
    if len(value) <= 8:
        return "<set>"
    return f"<set:{value[:4]}...{value[-4:]}>"


def _classify_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "user location is not supported" in message:
        return "LOCATION_UNSUPPORTED"
    if "default credentials" in message or (
        "could not automatically determine credentials" in message
    ):
        return "MISSING_ADC"
    if "permission" in message and ("denied" in message or "insufficient" in message):
        return "PERMISSION_DENIED"
    if "project" in message and ("required" in message or "not set" in message):
        return "MISSING_PROJECT"
    if "api key" in message and "missing" in message:
        return "MISSING_API_KEY"
    return "ERROR"


def _run_probe(name: str, probe_fn: Callable[[], str]) -> ProbeResult:
    started = time.monotonic()
    try:
        output = probe_fn().strip()
        elapsed = int((time.monotonic() - started) * 1000)
        return ProbeResult(name=name, ok=True, duration_ms=elapsed, detail=output)
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.monotonic() - started) * 1000)
        code = _classify_error(exc)
        return ProbeResult(name=name, ok=False, duration_ms=elapsed, detail=f"{code}: {exc}")


def _extract_text(response: object) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    return "<no text field in response>"


def _probe_genai_google_gla(api_key: str, model: str) -> str:
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(model=model, contents=PROMPT)
    return _extract_text(response)


def _probe_genai_vertex(project: str, location: str, model: str) -> str:
    client = genai.Client(vertexai=True, project=project, location=location)
    response = client.models.generate_content(model=model, contents=PROMPT)
    return _extract_text(response)


def _probe_pydantic_google_gla(api_key: str, model: str) -> str:
    provider = GoogleProvider(api_key=api_key)
    agent = Agent(
        GoogleModel(model, provider=provider),
        output_type=str,
        system_prompt="Return OK only.",
    )
    result = agent.run_sync("Ping")
    return str(result.output)


def _probe_pydantic_vertex(project: str, location: str, model: str) -> str:
    provider = GoogleProvider(project=project, location=location)
    agent = Agent(
        GoogleModel(model, provider=provider),
        output_type=str,
        system_prompt="Return OK only.",
    )
    result = agent.run_sync("Ping")
    return str(result.output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Gemini google-gla vs Vertex us-central1")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Model name to probe")
    parser.add_argument("--project", default=None, help="GCP project for Vertex calls")
    parser.add_argument("--location", default="us-central1", help="Vertex location")
    parser.add_argument(
        "--credentials",
        default=None,
        help="Optional path to service-account JSON for ADC (sets GOOGLE_APPLICATION_CREDENTIALS)",
    )
    args = parser.parse_args()

    if args.credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = args.credentials

    api_key = os.getenv("GOOGLE_API_KEY")
    project = (
        args.project
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or os.getenv("VERTEXAI_PROJECT")
    )
    location = args.location or os.getenv("GOOGLE_CLOUD_LOCATION") or "us-central1"

    print("== Probe Context ==")
    print(f"python-genai={version('google-genai')}")
    print(f"pydantic-ai={version('pydantic-ai')}")
    print(f"model={args.model}")
    print(f"GOOGLE_API_KEY={_mask_secret(api_key)}")
    print(f"project={project or '<unset>'}")
    print(f"location={location}")
    creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "<unset>"
    print(f"GOOGLE_APPLICATION_CREDENTIALS={creds}")
    print()

    results: list[ProbeResult] = []

    if api_key:
        results.append(
            _run_probe(
                "google-genai google-gla",
                lambda: _probe_genai_google_gla(api_key=api_key, model=args.model),
            )
        )
        results.append(
            _run_probe(
                "pydantic-ai google-gla",
                lambda: _probe_pydantic_google_gla(api_key=api_key, model=args.model),
            )
        )
    else:
        results.append(
            ProbeResult(
                name="google-gla checks",
                ok=False,
                duration_ms=0,
                detail="MISSING_API_KEY: GOOGLE_API_KEY unavailable",
            )
        )

    if project:
        results.append(
            _run_probe(
                "google-genai vertex(us-central1)",
                lambda: _probe_genai_vertex(project=project, location=location, model=args.model),
            )
        )
        results.append(
            _run_probe(
                "pydantic-ai vertex(us-central1)",
                lambda: _probe_pydantic_vertex(
                    project=project,
                    location=location,
                    model=args.model,
                ),
            )
        )
    else:
        results.append(
            ProbeResult(
                name="vertex checks",
                ok=False,
                duration_ms=0,
                detail="MISSING_PROJECT: pass --project or set GOOGLE_CLOUD_PROJECT",
            )
        )

    print("== Probe Results ==")
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.name} ({result.duration_ms}ms)")
        print(f"  {result.detail}")

    all_ok = all(result.ok for result in results)
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
