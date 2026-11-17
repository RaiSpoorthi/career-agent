"""Gemini client (free tier) that returns parsed JSON.

Models are tried in the order given by GEMINI_MODELS in .env. If a model hits its
free limit (HTTP 429) or no longer exists (404), the next one is used.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Protocol

from career.profile import ROOT

DEFAULT_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]


class LLMError(RuntimeError):
    """The model could not produce a usable answer."""


class JSONModel(Protocol):
    def generate_json(self, system: str, prompt: str) -> dict[str, Any]: ...


def parse_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start : end + 1])


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    load_dotenv(ROOT / ".env")


class GeminiLLM:
    def __init__(self, api_key: str | None = None, models: list[str] | None = None, sleep=time.sleep):
        load_env()
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        if not self.api_key or self.api_key.startswith("paste-"):
            raise LLMError("GEMINI_API_KEY is missing. Open the .env file and paste your key after GEMINI_API_KEY=")
        env_models = [m.strip() for m in os.getenv("GEMINI_MODELS", "").split(",") if m.strip()]
        self.models = models or env_models or DEFAULT_MODELS
        self._sleep = sleep
        from google import genai  # imported lazily so offline commands work without it

        self._client = genai.Client(api_key=self.api_key)
        self.last_model: str | None = None

    def generate_json(self, system: str, prompt: str) -> dict[str, Any]:
        from google.genai import types

        config = types.GenerateContentConfig(system_instruction=system, temperature=0.2,
                                             response_mime_type="application/json")
        return self._generate(prompt, config, lambda response: parse_json(response.text))

    def generate_json_searched(self, system: str, prompt: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
        """Like generate_json, but the model may run Google searches first (grounding).

        Returns (answer, sources) where sources are the web pages it used: [{"title", "url"}].
        """
        from google.genai import types

        config = types.GenerateContentConfig(system_instruction=system, temperature=0.2,
                                             tools=[types.Tool(google_search=types.GoogleSearch())])

        def read(response) -> tuple[dict[str, Any], list[dict[str, str]]]:
            sources = []
            for candidate in getattr(response, "candidates", None) or []:
                meta = getattr(candidate, "grounding_metadata", None)
                for chunk in getattr(meta, "grounding_chunks", None) or []:
                    web = getattr(chunk, "web", None)
                    if web and getattr(web, "uri", None):
                        sources.append({"title": getattr(web, "title", "") or "", "url": web.uri})
            return parse_json(response.text), sources[:6]
        # Search quota is often not part of the free tier: fail fast so the caller can fall back.
        return self._generate(prompt, config, read, patient=False)

    def _generate(self, prompt: str, config, read, patient: bool = True):
        from google.genai import errors

        problems: list[str] = []
        for model in self.models:
            for attempt in range(3 if patient else 1):
                try:
                    response = self._client.models.generate_content(model=model, contents=prompt, config=config)
                    data = read(response)
                    self.last_model = model
                    return data
                except errors.ClientError as exc:
                    code = getattr(exc, "code", None)
                    if code in (401, 403) or "API key not valid" in str(exc):
                        raise LLMError("Gemini rejected the API key. Check GEMINI_API_KEY in .env "
                                       "(create a new key at aistudio.google.com).") from exc
                    if code == 429:
                        problems.append(f"{model}: free-tier limit reached")
                        if attempt == 0 and patient:
                            self._sleep(30)
                            continue
                        break
                    problems.append(f"{model}: {code} {str(exc)[:160]}")
                    break  # 404 (model retired) / 400: try the next model
                except errors.ServerError as exc:
                    problems.append(f"{model}: server error {getattr(exc, 'code', '')}")
                    if patient:
                        self._sleep(5 * (attempt + 1))
                except ValueError as exc:  # bad JSON
                    problems.append(f"{model}: unreadable output ({exc})")
                except Exception as exc:  # network down, proxy, DNS, timeout
                    raise LLMError(f"Could not reach Gemini ({type(exc).__name__}: {str(exc)[:120]}). "
                                   "Check your internet connection and try again.") from exc
        raise LLMError("Gemini could not complete the request:\n  " + "\n  ".join(problems[-6:]))
