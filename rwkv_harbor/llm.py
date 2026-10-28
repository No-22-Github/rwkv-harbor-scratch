"""RWKV continuation backend for Harbor's Terminus agents.

Talks to rwkv_lightning_cuda's raw continuation endpoint (/v1/batch/completions),
which applies no chat template and prefills nothing. The transcript is rendered
here with the g1 chat template used by RWKV-Agent (internal/inference/prompt.go):

    User: <text>\n\nAssistant: <text>\n\n ... User: <text>\n\nAssistant:

Rules carried over from RWKV-Agent:
- Blocks are separated by exactly one blank line, so blank lines inside
  non-assistant text are collapsed (CleanChatText). Terminal output is full of
  them and would otherwise read as turn boundaries.
- thinking=off: the assistant opening is a bare "Assistant:" with no <think>.
- The checkpoint often writes the next "User:" turn instead of EOS, so role
  labels are stop strings and the matched label is cut from the output.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from harbor.llms.base import (
    BaseLLM,
    ContextLengthExceededError,
    LLMResponse,
    OutputLengthExceededError,
)
from harbor.models.metric import UsageInfo

ROLE_LABELS = {"system": "System", "user": "User", "assistant": "Assistant"}

# Endpoint settings come from the environment so configs carry no addresses.
API_BASE_ENV = "RWKV_API_BASE"
API_KEY_ENV = "RWKV_API_KEY"

RETRY_ATTEMPTS = 4
RETRY_BACKOFF_SEC = 2.0

# RWKV-Agent's G1Protocol.Stops ("\nUser:" also covers "\n\nUser:"), plus
# "<EOD>": once string stops are sent the server no longer stops on the EOS
# token, which then decodes to "<EOD>" and generation runs on.
DEFAULT_STOPS = ["<EOD>", "\nUser:", "\nSystem:", "\nTool:"]

# Named sampling presets, values pinned to RWKV-Agent internal/samplingpreset.
# The server's penalties only count tokens generated in the current request,
# so they curb in-turn loops but not a turn that copies an earlier one.
SAMPLING_PRESETS = {
    "greedy": {
        "temperature": 1.0,
        "top_k": 1,
        "top_p": 1.0,
        "alpha_presence": 0.0,
        "alpha_frequency": 0.0,
        "alpha_decay": 1.0,
    },
    "agent-default": {
        "temperature": 0.3,
        "top_k": 65536,
        "top_p": 0.5,
        "alpha_presence": 0.0,
        "alpha_frequency": 0.0,
        "alpha_decay": 1.0,
    },
    # Recommended by RWKV-Agent's benchmark protocol for long multi-step tasks.
    "agent-fast": {
        "temperature": 0.3,
        "top_k": 65536,
        "top_p": 0.5,
        "alpha_presence": 0.5,
        "alpha_frequency": 0.1,
        "alpha_decay": 0.996,
    },
    "agent-stable": {
        "temperature": 1.0,
        "top_k": 20,
        "top_p": 0.3,
        "alpha_presence": 0.0,
        "alpha_frequency": 0.0,
        "alpha_decay": 1.0,
    },
    "backend": {
        "temperature": 1.0,
        "top_k": 20,
        "top_p": 0.3,
        "alpha_presence": 2.0,
        "alpha_frequency": 0.2,
        "alpha_decay": 0.996,
    },
}
DEFAULT_SAMPLING_PRESET = "agent-default"


def clean_chat_text(role: str, text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if role == "assistant":
        # Assistant text is protocol payload and stays verbatim, except that a
        # blank line inside it would end the block, so it is collapsed too.
        while "\n\n" in text:
            text = text.replace("\n\n", "\n")
        return text.strip()
    while "\n\n" in text:
        text = text.replace("\n\n", "\n")
    return text.strip()


def render_prompt(messages: list[dict[str, Any]]) -> str:
    parts = []
    for message in messages:
        role = message["role"]
        label = ROLE_LABELS.get(role)
        if label is None:
            raise ValueError(f"Unsupported role for RWKV transcript: {role!r}")
        content = message.get("content") or ""
        if not isinstance(content, str):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        parts.append(f"{label}: {clean_chat_text(role, content)}\n\n")
    parts.append("Assistant:")
    return "".join(parts)


class RwkvContinuationLLM(BaseLLM):
    def __init__(
        self,
        model_name: str,
        api_base: str | None = None,
        context_limit: int = 25600,
        max_output_tokens: int = 2048,
        stops: list[str] | None = None,
        sampling_preset: str = DEFAULT_SAMPLING_PRESET,
        sampling: dict[str, Any] | None = None,
        api_key: str | None = None,
        request_timeout_sec: float = 600.0,
        call_log_dir: Path | None = None,
        **kwargs,
    ):
        # Terminus forwards LiteLLM-specific kwargs (credentials, rollout
        # flags); none of them apply to this endpoint.
        super().__init__()
        self._model_name = model_name
        api_base = api_base or os.environ.get(API_BASE_ENV)
        if not api_base:
            raise ValueError(
                f"Set {API_BASE_ENV} (e.g. http://host:8000/v1) or llm_kwargs.api_base"
            )
        self._api_base = api_base.rstrip("/")
        api_key = api_key or os.environ.get(API_KEY_ENV)
        self._context_limit = context_limit
        self._max_output_tokens = max_output_tokens
        self._stops = list(stops or DEFAULT_STOPS)
        if sampling_preset not in SAMPLING_PRESETS:
            raise ValueError(
                f"Unknown sampling_preset {sampling_preset!r}; "
                f"choose from {sorted(SAMPLING_PRESETS)}"
            )
        # sampling overrides single fields of the preset.
        self._sampling = {**SAMPLING_PRESETS[sampling_preset], **(sampling or {})}
        if kwargs.get("temperature") is not None:
            self._sampling["temperature"] = kwargs["temperature"]
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._timeout = request_timeout_sec
        self._call_log_dir = call_log_dir
        self._call_index = 0

    def get_model_context_limit(self) -> int:
        return self._context_limit

    def get_model_output_limit(self) -> int | None:
        return self._max_output_tokens

    async def _post(
        self, client: httpx.AsyncClient, path: str, body: dict[str, Any]
    ) -> httpx.Response:
        # The shared endpoint sits behind a gateway that occasionally answers
        # 502; one such reply used to end a whole trial. Retry server and
        # transport errors with backoff, fail fast on client errors.
        for attempt in range(RETRY_ATTEMPTS):
            try:
                response = await client.post(
                    f"{self._api_base}{path}", json=body, headers=self._headers
                )
                if response.status_code < 500:
                    response.raise_for_status()
                    return response
                error: Exception = httpx.HTTPStatusError(
                    f"Server error {response.status_code} for {path}",
                    request=response.request,
                    response=response,
                )
            except httpx.TransportError as exc:
                error = exc
            if attempt == RETRY_ATTEMPTS - 1:
                raise error
            await asyncio.sleep(RETRY_BACKOFF_SEC * 2**attempt)
        raise AssertionError("unreachable")

    async def _stream_completion(
        self, client: httpx.AsyncClient, body: dict[str, Any]
    ) -> tuple[str, str | None, str | None]:
        # The gateway drops non-streaming requests that run past ~90 s, which a
        # 2048-token turn on a long prompt does under concurrent trials. A
        # stream keeps the connection busy. Retried like _post; a stream cut
        # midway is restarted from scratch.
        for attempt in range(RETRY_ATTEMPTS):
            try:
                async with client.stream(
                    "POST",
                    f"{self._api_base}/batch/completions",
                    json={**body, "stream": True},
                    headers=self._headers,
                ) as response:
                    if response.status_code < 500:
                        response.raise_for_status()
                        pieces: list[str] = []
                        finish_reason = matched_stop = None
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[len("data:") :].strip()
                            if data == "[DONE]":
                                return "".join(pieces), finish_reason, matched_stop
                            choice = json.loads(data)["choices"][0]
                            pieces.append(choice.get("delta", {}).get("content") or "")
                            finish_reason = choice.get("finish_reason") or finish_reason
                            matched_stop = choice.get("matched_stop") or matched_stop
                        error: Exception = httpx.RemoteProtocolError(
                            "Stream ended without [DONE]"
                        )
                    else:
                        error = httpx.HTTPStatusError(
                            f"Server error {response.status_code} for /batch/completions",
                            request=response.request,
                            response=response,
                        )
            except httpx.TransportError as exc:
                error = exc
            if attempt == RETRY_ATTEMPTS - 1:
                raise error
            await asyncio.sleep(RETRY_BACKOFF_SEC * 2**attempt)
        raise AssertionError("unreachable")

    async def _count_tokens(self, client: httpx.AsyncClient, text: str) -> int:
        response = await self._post(client, "/tokens/count", {"text": text})
        return int(response.json()["tokens"])

    async def call(
        self,
        prompt: str,
        message_history: list[dict[str, Any]] = [],
        **kwargs,
    ) -> LLMResponse:
        messages = [
            m if isinstance(m, dict) else m.model_dump() for m in message_history
        ] + [{"role": "user", "content": prompt}]
        text_prompt = render_prompt(messages)
        max_tokens = int(kwargs.get("max_tokens") or self._max_output_tokens)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            prompt_tokens = await self._count_tokens(client, text_prompt)
            if prompt_tokens + max_tokens > self._context_limit:
                raise ContextLengthExceededError(
                    f"Prompt has {prompt_tokens} tokens; with max_tokens={max_tokens} "
                    f"it exceeds the {self._context_limit}-token context."
                )

            body = {
                "model": self._model_name,
                "contents": [text_prompt],
                "max_tokens": max_tokens,
                "stop": self._stops,
                **self._sampling,
            }
            started = time.time()
            raw, finish_reason, matched_stop = await self._stream_completion(
                client, body
            )
            elapsed = time.time() - started

            content = raw
            if matched_stop and content.endswith(matched_stop):
                content = content[: -len(matched_stop)]
            content = content.strip()
            completion_tokens = await self._count_tokens(client, raw) if raw else 0

        self._log_call(
            text_prompt, raw, matched_stop, prompt_tokens, completion_tokens, elapsed
        )

        # Older builds report "stop" even at max_tokens, so a missing stop
        # match with a full token budget also counts as truncation.
        if finish_reason == "length" or (
            not matched_stop and completion_tokens >= max_tokens
        ):
            raise OutputLengthExceededError(
                f"Response hit max_tokens={max_tokens}", truncated_response=content
            )

        return LLMResponse(
            content=content,
            model_name=self._model_name,
            usage=UsageInfo(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cache_tokens=0,
                cost_usd=0.0,
            ),
        )

    def _log_call(
        self,
        prompt: str,
        raw: str,
        matched_stop: str | None,
        prompt_tokens: int,
        completion_tokens: int,
        elapsed: float,
    ) -> None:
        if self._call_log_dir is None:
            return
        self._call_log_dir.mkdir(parents=True, exist_ok=True)
        self._call_index += 1
        record = {
            "prompt": prompt,
            "raw_output": raw,
            "matched_stop": matched_stop,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "elapsed_sec": round(elapsed, 2),
        }
        path = self._call_log_dir / f"call-{self._call_index:04d}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
