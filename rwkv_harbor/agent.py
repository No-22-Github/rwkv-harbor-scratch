"""Terminus 2 driven by the RWKV continuation backend.

Use with --agent rwkv_harbor.agent:RwkvTerminus (or import_path in a job
config). Backend settings go in kwargs.llm_kwargs and are passed straight to
RwkvContinuationLLM.
"""

from __future__ import annotations

from typing import Any, override

from harbor.agents.agent_auth_resolution import ResolvedModelConnection
from harbor.agents.terminus_2.terminus_2 import Terminus2
from harbor.llms.base import BaseLLM, LLMBackend

from rwkv_harbor.llm import RwkvContinuationLLM


class RwkvTerminus(Terminus2):
    @staticmethod
    @override
    def name() -> str:
        return "rwkv-terminus"

    @classmethod
    @override
    def check_model_credentials(cls, connection, *, kwargs, env, explicit_env=None):
        pass

    @classmethod
    @override
    def resolve_model_connection_config(
        cls, model_name, resolve_env, **connection_kwargs
    ):
        # The endpoint is configured through llm_kwargs.api_base, not LiteLLM.
        return ResolvedModelConnection()

    @override
    def _init_llm(
        self,
        llm_backend: LLMBackend | str,
        model_name: str,
        temperature: float | None,
        collect_rollout_details: bool,
        llm_kwargs: dict[str, Any] | None,
        **_litellm_kwargs: Any,
    ) -> BaseLLM:
        kwargs = dict(llm_kwargs or {})
        kwargs.setdefault("call_log_dir", self.logs_dir / "rwkv-calls")
        if temperature is not None:
            kwargs["temperature"] = temperature
        return RwkvContinuationLLM(model_name=model_name, **kwargs)
