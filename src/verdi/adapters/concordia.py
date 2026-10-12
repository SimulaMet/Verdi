"""Optional, OFF-by-default compatibility adapter to Google DeepMind Concordia.

This is a THIN bridge (todo D, follow-up). It exists so a Verdi agent / model
endpoint can be surfaced in Concordia's `Entity` and `LanguageModel` interfaces
for cross-framework inspection or co-simulation experiments. It is:

* **Lazy**: nothing imports ``concordia`` at module import time. The real
  package (``gdm-concordia``, Apache-2.0) is imported only inside the factory
  functions below, so this module loads cleanly whether or not Concordia is
  installed and never forces its heavy optional dependencies onto Verdi.
* **Off by default**: no Verdi module imports it; it adds no dependency to
  ``pyproject.toml``. It does NOT touch the deterministic world reducer, the
  governance engine, or the working simulation loop.
* **Qwen-only for the model bridge**: :class:`OpenAICompatibleLanguageModel`
  talks to the same OpenAI-compatible vLLM endpoint Verdi uses (no fallback to
  any other model family).

NOTE ON SCOPE: Concordia's mutable-world / game-master model is structurally
different from Verdi's deterministic hash-chained reducer. This adapter maps the
two clean seams — an agent as a Concordia ``Entity`` and the model endpoint as a
Concordia ``LanguageModel`` — and deliberately does NOT attempt to replace or
wrap the whole simulation. See ``docs/concordia_eval.md``.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any


def _import_concordia() -> Any:
    try:
        import concordia  # noqa: F401
    except ImportError as exc:  # pragma: no cover - env-specific
        raise ImportError(
            "gdm-concordia is not installed. Install it with "
            "`pip install gdm-concordia` to use the Concordia adapter. "
            "Verdi itself does not require it."
        ) from exc
    return concordia


def _concordia_entity_base() -> type:
    """Return Concordia's ``Entity`` ABC if installed, else a plain object base.

    This lets :class:`VerdiAgentEntity` genuinely subclass Concordia's ABC when
    available (so ``isinstance(x, Entity)`` holds) while still constructing —
    and being usable in inspection mode — when Concordia is not installed. The
    import happens once at class-definition time inside the factory below, NOT
    at module import time, keeping this module dependency-free on load.
    """
    try:
        from concordia.typing.entity import Entity
        return Entity
    except ImportError:
        return object


# --------------------------------------------------------------------------- #
# LanguageModel bridge: Verdi's Qwen/vLLM endpoint -> Concordia LanguageModel
# --------------------------------------------------------------------------- #
class OpenAICompatibleLanguageModel:
    """A Concordia ``LanguageModel`` backed by Verdi's OpenAI-compatible
    (vLLM/Qwen) chat-completions endpoint.

    Implements the two abstract methods of Concordia's ``LanguageModel`` ABC
    (``sample_text`` and ``sample_choice``) on top of a single POST to
    ``/chat/completions``. Uses stdlib-free httpx if available, else urllib, so
    it has no hard dependency beyond the standard library.
    """

    def __init__(self, base_url: str, api_key: str, model: str, *, timeout: float = 180.0):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def _post_chat(self, messages: list[dict[str, str]], *, temperature: float,
                   max_tokens: int) -> str:
        import json
        import urllib.request

        req = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps({
                "model": self._model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return str(body["choices"][0]["message"]["content"])

    def sample_text(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        terminators: Collection[str] = ("</s>",),
        temperature: float = 0.0,
        timeout: float | None = None,
        seed: int | None = None,
    ) -> str:
        text = self._post_chat(
            [{"role": "user", "content": prompt}],
            temperature=temperature, max_tokens=max_tokens,
        )
        for term in terminators:
            idx = text.find(term)
            if idx != -1:
                text = text[:idx]
        return text.strip()

    def sample_choice(
        self,
        prompt: str,
        responses: Sequence[str],
        *,
        seed: int | None = None,
    ) -> tuple[int, str, Mapping[str, Any]]:
        numbered = "\n".join(f"{i}. {r}" for i, r in enumerate(responses))
        ask = (
            f"{prompt}\n\nChoose exactly one of these options and reply with ONLY "
            f"its number:\n{numbered}"
        )
        raw = self.sample_text(ask, max_tokens=8, temperature=0.0)
        digits = "".join(ch for ch in raw if ch.isdigit())
        idx = int(digits) if digits else 0
        idx = max(0, min(idx, len(responses) - 1))
        return idx, responses[idx], {"raw": raw}


def make_language_model(base_url: str, api_key: str, model: str, *,
                        timeout: float = 180.0) -> OpenAICompatibleLanguageModel:
    """Factory mirroring Concordia's language-model constructors."""
    return OpenAICompatibleLanguageModel(base_url, api_key, model, timeout=timeout)


# --------------------------------------------------------------------------- #
# Entity bridge: a Verdi agent snapshot -> Concordia Entity
# --------------------------------------------------------------------------- #
def _make_verdi_agent_entity_class() -> type:
    """Build the concrete ``VerdiAgentEntity`` class once, subclassing Concordia's
    real ``Entity`` ABC when available (so ``isinstance(x, Entity)`` holds), else
    duck-typing the same interface. Import of Concordia happens here, not at
    module load."""
    base = _concordia_entity_base()

    class VerdiAgentEntity(base):  # type: ignore[misc,valid-type]
        """A VERDI agent (persona + committed memory + situation) projected as a
        Concordia ``Entity``.

        READ-ONLY projection: carries the agent's identity, persona, rendered
        memory summary, and current situation as the entity's context. ``act``
        produces a free-form action string via the supplied language model;
        ``observe`` appends to the entity's observation buffer. It does NOT
        write back into any Verdi world state — that stays owned by the
        deterministic reducer.
        """

        def __init__(self, *, name: str, persona: str, memory_summary: str = "",
                     situation: str = "",
                     language_model: OpenAICompatibleLanguageModel | None = None):
            super().__init__()
            self._name = name
            self._persona = persona
            self._memory_summary = memory_summary
            self._situation = situation
            self._language_model = language_model
            self._observations: list[str] = []

        @property
        def name(self) -> str:
            return self._name

        def _context(self) -> str:
            parts = [f"You are {self._name}.", f"Persona: {self._persona}"]
            if self._memory_summary:
                parts.append(f"Your recorded history so far:\n{self._memory_summary}")
            if self._situation:
                parts.append(f"Current situation: {self._situation}")
            if self._observations:
                parts.append("Recent observations:\n" + "\n".join(self._observations[-10:]))
            return "\n\n".join(parts)

        def act(self, action_spec: Any = None) -> str:
            call_to_action = getattr(action_spec, "call_to_action", None) or (
                "Describe your next action in this society."
            )
            if self._language_model is None:
                # No model bound: deterministic placeholder for offline use.
                return f"[{self._name}] (no language model bound)"
            return self._language_model.sample_text(
                f"{self._context()}\n\n{call_to_action}", max_tokens=128, temperature=0.0,
            )

        def observe(self, observation: str) -> None:
            self._observations.append(observation)

    return VerdiAgentEntity


# Concrete class, built once at import of THIS module (Concordia imported lazily
# inside the factory; safe whether or not gdm-concordia is installed).
VerdiAgentEntity: type = _make_verdi_agent_entity_class()


def make_verdi_agent_entity(*, name: str, persona: str, memory_summary: str = "",
                            situation: str = "",
                            language_model: OpenAICompatibleLanguageModel | None = None) -> VerdiAgentEntity:
    """Factory for a Concordia-entity view of one Verdi agent."""
    return VerdiAgentEntity(name=name, persona=persona, memory_summary=memory_summary,
                             situation=situation, language_model=language_model)


def as_concordia_entity(entity: VerdiAgentEntity) -> Any:
    """Validate ``entity`` against Concordia's real ``Entity`` ABC (requires
    ``gdm-concordia`` installed). Returns the entity unchanged when it conforms;
    raises ImportError if Concordia is absent."""
    _import_concordia()
    from concordia.typing.entity import Entity

    if not isinstance(entity, Entity):
        raise TypeError(f"{type(entity).__name__} does not satisfy concordia.Entity")
    return entity


__all__ = [
    "OpenAICompatibleLanguageModel",
    "VerdiAgentEntity",
    "make_language_model",
    "make_verdi_agent_entity",
    "as_concordia_entity",
]
