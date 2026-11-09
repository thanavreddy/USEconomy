"""Thin wrapper around the official Groq Python SDK."""

from __future__ import annotations

from typing import Sequence

from groq import Groq


class GroqClientError(Exception):
    """Raised when the Groq API is unavailable or returns an invalid response."""


class GroqClient:
    """Minimal Groq chat-completions client."""

    # Reasonable defaults for SQL generation and analytical reasoning. If
    # GROQ_MODEL is empty, the first available model from this list is chosen.
    PREFERRED_MODELS = (
        "llama-3.3-70b-versatile",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3-32b",
        "moonshotai/kimi-k2-instruct",
        "llama-3.1-8b-instant",
    )

    def __init__(self, api_key: str, model: str = "") -> None:
        if not api_key or not api_key.strip():
            raise GroqClientError("GROQ_API_KEY is not set.")
        self._client = Groq(api_key=api_key.strip())
        self._configured_model = (model or "").strip()
        self._resolved_model: str | None = None
        self._available_models: list[str] | None = None

    def _model_ids(self) -> list[str]:
        if self._available_models is not None:
            return self._available_models
        try:
            response = self._client.models.list()
        except Exception as exc:
            raise GroqClientError(f"Could not list Groq models: {exc}") from exc

        data = getattr(response, "data", None)
        if data is None and isinstance(response, (list, tuple)):
            data = response
        model_ids: list[str] = []
        for item in data or []:
            model_id = getattr(item, "id", None)
            if model_id is None and isinstance(item, dict):
                model_id = item.get("id")
            if model_id:
                model_ids.append(str(model_id))
        self._available_models = model_ids
        return model_ids

    def resolve_model(self) -> str:
        """Return a valid model id, preferring GROQ_MODEL when configured."""
        if self._resolved_model:
            return self._resolved_model

        model_ids: list[str] | None = None
        try:
            model_ids = self._model_ids()
        except GroqClientError:
            if not self._configured_model:
                raise

        if self._configured_model:
            if model_ids and self._configured_model not in model_ids:
                raise GroqClientError(
                    f"GROQ_MODEL '{self._configured_model}' is not available. "
                    f"Available models: {', '.join(model_ids)}"
                )
            self._resolved_model = self._configured_model
            return self._resolved_model

        model_ids = model_ids or []
        for preferred in self.PREFERRED_MODELS:
            if preferred in model_ids:
                self._resolved_model = preferred
                return self._resolved_model
        if model_ids:
            self._resolved_model = model_ids[0]
            return self._resolved_model
        raise GroqClientError("No Groq models are available for this API key.")

    @property
    def model(self) -> str:
        return self._resolved_model or self._configured_model or "auto"

    def chat(
        self,
        messages: Sequence[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 900,
    ) -> str:
        """Call Groq chat completions and return the assistant text."""
        model = self.resolve_model()
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=list(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            raise GroqClientError(
                f"Groq request failed for model '{model}': {exc}"
            ) from exc

        choices = getattr(response, "choices", None) or []
        if not choices:
            raise GroqClientError("Groq returned an empty response.")
        content = getattr(choices[0].message, "content", None)
        if not content or not str(content).strip():
            raise GroqClientError("Groq returned an empty answer.")
        return str(content).strip()

    def test_connection(self) -> str:
        """Return the resolved model id, raising a clear error if unavailable."""
        return self.resolve_model()
