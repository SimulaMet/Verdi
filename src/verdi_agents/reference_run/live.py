"""L1 live-model pilot (ADR-010): run the pilot_v1 slice against the live Qwen endpoint.

- Deterministic scaffolding (scheduler, reducer, governance, law) is
  identical to the recorded pilot; the live model drives PROPOSALS only.
- temperature 0, JSON-mode structured proposals via AgentPolicy.
- Every model call is captured verbatim by PilotDriver into live_tape.jsonl.
- Endpoint configuration comes exclusively from environment variables
  (VERDI_VLLM_*); no model name, URL, or key is hardcoded here.

The real live run is deliberately NOT a pytest test: it is a script-style
invocation (see docs or state/STATUS.md) so CI never performs network
calls. Budget: <=120 model calls, concurrency 1 in the run loop.

Reproducibility contract (ADR-010): "reproducible from recorded tape"
means ``run_pilot_from_live_tape(tape)`` reproduces the run's ledger
byte-for-byte; the live endpoint itself is NOT bit-reproducible.
"""
from __future__ import annotations

import json
from pathlib import Path

from verdi_agents.agents.client import ModelClient, ModelResult
from verdi_agents.agents.policy import ModelCaller
from verdi_agents.config import model_endpoint_from_env
from verdi_agents.reference_run.driver import PilotDriver, PilotRun


class _ClientFactory:
    """Marker + zero-arg builder for fresh :class:`ModelClient` instances.

    The pilot dispatch loop runs one ``asyncio.run(...)`` per dispatch, so
    each dispatch gets its own event loop. An ``httpx.AsyncClient`` binds to
    the loop it is used in, therefore a single long-lived client would be
    reused across (closed) loops and fail. A factory lets the adapter build
    one client per dispatch inside that dispatch's loop and close it in the
    same loop (retries remain per-request per docs/ARCHITECTURE.md).
    """

    def __init__(self, config) -> None:
        self._config = config

    def __call__(self) -> ModelClient:
        return ModelClient(self._config)


class LiveModelAdapter:
    """Thin counting ModelCaller wrapper around a :class:`ModelClient`.

    The run loop is sequential (concurrency 1); ``call_count`` therefore
    equals the number of dispatches that reached the model, in order.
    ``last_result`` / ``last_prompt`` expose the most recent call so the
    driver can capture verbatim records (PolicyResult does not carry the
    raw ModelResult or the prompt).

    Accepts either a ready :class:`ModelCaller` (used by the scaffold
    tests) or a zero-arg factory returning one (the live path). The factory
    form builds one :class:`ModelClient` per dispatch inside that dispatch's
    event loop and closes it in the same loop, which is required because the
    pilot runs one ``asyncio.run`` per dispatch (see _ClientFactory).

    Engineering note: one pooled connection per dispatch, no cross-call
    pooling; acceptable at concurrency-1 / <=120 calls (ADR-010); would
    need pooling for high-throughput use.
    """

    def __init__(self, client: _ClientFactory | ModelCaller, *, model: str) -> None:
        self._factory: _ClientFactory | None = (
            client if isinstance(client, _ClientFactory) else None
        )
        self._client: ModelCaller | None = (
            None if isinstance(client, _ClientFactory) else client
        )
        self.model = model
        self.call_count = 0
        self.last_result: ModelResult | None = None
        self.last_prompt: str | None = None

    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_format_json: bool = False,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        self.call_count += 1
        self.last_prompt = messages[0].get("content") if messages else None
        if self._factory is not None:
            per_call_client = self._factory()
            try:
                result = await per_call_client.generate_structured(
                    messages,
                    response_format_json=response_format_json,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            finally:
                close = getattr(per_call_client, "aclose", None)
                if close is not None:
                    await close()
        else:
            assert self._client is not None
            result = await self._client.generate_structured(
                messages,
                response_format_json=response_format_json,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        self.last_result = result
        return result


class TapeReplayCaller:
    """Offline ModelCaller that replays a captured live tape (ADR-010).

    Wraps a :class:`~verdi_agents.agents.recorded.RecordedModel` over the
    ``response_raw`` of every captured call (in ``seq`` order) and exposes
    the ``last_result`` / ``last_prompt`` attributes the driver's live
    capture path reads after each call. The :class:`ModelResult` is
    reconstructed from the tape line — ``request_id``/``trace_id``/``model``/
    ``usage``/``reasoning_content`` are taken verbatim when present; when a
    field is absent, deterministic stand-ins (derived from ``seq``) are used
    so replays of the same tape are stable across runs. No network access.
    """

    def __init__(self, tape: list[dict] | str | Path) -> None:
        if isinstance(tape, (str, Path)):
            tape = [
                json.loads(line)
                for line in Path(tape).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        self._rows = sorted(tape, key=lambda row: row.get("seq", 0))
        self._tape: list[str] = [
            row.get("response_raw") or "" for row in self._rows
        ]
        first = self._rows[0] if self._rows else {}
        self._model = str(first.get("model") or "live-tape-replay")
        self.call_count = 0
        self.last_result: ModelResult | None = None
        self.last_prompt: str | None = None

    def __len__(self) -> int:
        return len(self._tape)

    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_format_json: bool = False,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        if not self._tape:
            raise RuntimeError(
                f"live tape exhausted after {self.call_count} replays"
            )
        self.call_count += 1
        row = self._rows[self.call_count - 1]
        seq = row.get("seq", self.call_count - 1)
        self.last_prompt = (
            messages[0].get("content") if messages else row.get("prompt")
        )
        self.last_result = ModelResult(
            content=self._tape.pop(0),
            request_id=str(row.get("request_id") or f"replay-req-{seq}"),
            model=str(row.get("model") or self._model),
            trace_id=str(row.get("trace_id") or f"replay-trace-{seq}"),
            usage=dict(row.get("usage") or {}),
            reasoning_content=str(row.get("reasoning_content") or ""),
        )
        return self.last_result


def run_pilot_from_live_tape(
    tape_path: str | Path,
    repo_root: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> PilotRun:
    """ADR-010 first-class replay: drive the pilot from a captured live tape.

    Loads ``tape_path`` (the ``live_tape.jsonl`` of a previous live run),
    replays its ``response_raw`` responses in ``seq`` order through
    :class:`TapeReplayCaller`, and drives the pilot in live mode with
    capture OFF (no new live_tape.jsonl is written; the run's ledger is
    written to ``out_dir`` when given). Fully offline: no network, no
    endpoint configuration required.
    """
    caller = TapeReplayCaller(Path(tape_path))
    model_id = caller._model
    # "" (not None) disables capture: PilotDriver only auto-creates the
    # default runs/live/<stamp>/ dir when live_capture_dir is None.
    driver = PilotDriver(
        repo_root,
        model=caller,
        model_id=model_id,
        live_capture_dir="",
    )
    return driver.run(out_dir)


def run_live_pilot(
    repo_root: str | Path | None = None,
    out_dir: str | Path | None = None,
    *,
    max_calls: int = 120,
    concurrency_1: bool = True,
) -> PilotRun:
    """Drive one live pilot_v1 run against the env-configured endpoint.

    Raises RuntimeError when the endpoint is not configured. Asserts the
    ADR-010 budget (``max_calls``) after the run completes.
    """
    if not concurrency_1:
        raise ValueError(
            "ADR-010 fixes run-loop concurrency at 1; refusing to run"
        )
    config = model_endpoint_from_env()
    if config is None:
        raise RuntimeError(
            "live endpoint not configured — set VERDI_VLLM_* env vars "
            "(VERDI_VLLM_BASE_URL, VERDI_VLLM_MODEL, VERDI_VLLM_API_KEY)"
        )
    adapter = LiveModelAdapter(_ClientFactory(config), model=config.model)
    driver = PilotDriver(
        repo_root,
        model=adapter,
        model_id=config.model,
        base_url=config.base_url,
    )
    pilot = driver.run(out_dir)
    if adapter.call_count > max_calls:
        raise AssertionError(
            f"budget exceeded: {adapter.call_count} model calls > {max_calls} (ADR-010)"
        )
    return pilot


__all__ = ["LiveModelAdapter", "run_live_pilot"]
