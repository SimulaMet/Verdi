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
"""
from __future__ import annotations

from pathlib import Path

from verdi.agents.client import ModelClient, ModelResult
from verdi.agents.policy import ModelCaller
from verdi.config import model_endpoint_from_env
from verdi.pilot.driver import PilotDriver, PilotRun


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
