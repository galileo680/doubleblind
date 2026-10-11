import asyncio
import json
import os
import time
from collections.abc import Callable
from typing import Any

import openai
from openai import AsyncOpenAI
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from doubleblind_llm.budget import (
    BudgetGuard,
    Ledger,
    LedgerEntry,
    ledger_path_from_env,
    spend_limit_from_env,
)
from doubleblind_llm.capabilities import CapabilityReport, ModelCapabilities, load_capabilities
from doubleblind_llm.cassette import Cassette, cassette_path_from_env, fingerprint
from doubleblind_llm.domain import (
    LLMCallEvent,
    LLMRequest,
    LLMResponse,
    RawCompletion,
    Role,
    ToolMode,
    Usage,
)
from doubleblind_llm.errors import (
    MissingApiKeyError,
    ReplayMissError,
    StructuredOutputError,
)
from doubleblind_llm.parsing import (
    fallback_tool_instructions,
    parse_fallback_tool_calls,
    parse_native_tool_calls,
    parse_structured,
    schema_instructions,
    split_reasoning,
)
from doubleblind_llm.pricing import cost_usd, estimate_prompt_tokens
from doubleblind_llm.settings import (
    LLMSettings,
    Mode,
    ModelSpec,
    RoleSettings,
    load_dotenv,
    load_settings,
    mode_from_env,
)

API_KEY_ENV = "NEBIUS_API_KEY"
REPLAY_DELAY_ENV = "DOUBLEBLIND_REPLAY_DELAY_MS"
MAX_REPAIRS = 2
MAX_ATTEMPTS = 5
REQUEST_TIMEOUT_SECONDS = 180.0
REPAIR_SYSTEM_PROMPT = (
    "You repair JSON documents. Return ONLY a corrected JSON object that satisfies the schema, "
    "keeping as much of the original content as possible."
)

CallListener = Callable[[LLMCallEvent], None]


def is_retryable(error: BaseException) -> bool:
    if isinstance(error, openai.RateLimitError | openai.APIConnectionError):
        return True
    return isinstance(error, openai.APIStatusError) and error.status_code >= 500


def with_system_additions(
    messages: list[dict[str, Any]], additions: list[str]
) -> list[dict[str, Any]]:
    copied = [dict(message) for message in messages]
    if not additions:
        return copied
    extra = "\n\n".join(additions)
    if copied and copied[0].get("role") == "system":
        existing = copied[0].get("content") or ""
        copied[0]["content"] = f"{existing}\n\n{extra}" if existing else extra
        return copied
    return [{"role": "system", "content": extra}, *copied]


class LLMClient:
    def __init__(
        self,
        settings: LLMSettings,
        *,
        mode: Mode = "live",
        api_key: str | None = None,
        capabilities: CapabilityReport | None = None,
        cassette: Cassette | None = None,
        ledger: Ledger | None = None,
        spend_limit_usd: float | None = None,
        on_call: CallListener | None = None,
        http_client: Any = None,
        retry_wait_seconds: float = 1.0,
        replay_delay_ms: int = 0,
    ) -> None:
        if mode in ("record", "replay") and cassette is None:
            raise ValueError(f"Mode '{mode}' requires a cassette")
        self.settings = settings
        self.mode = mode
        self.capabilities = capabilities
        self.cassette = cassette
        self.ledger = ledger
        self.guard = (
            BudgetGuard(ledger, spend_limit_usd)
            if ledger is not None and spend_limit_usd is not None
            else None
        )
        self.on_call = on_call
        self.retry_wait_seconds = retry_wait_seconds
        self.replay_delay_ms = replay_delay_ms
        self._api_key = api_key
        self._http_client = http_client
        self._openai: AsyncOpenAI | None = None
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._budget_lock = asyncio.Lock()
        self._reserved_usd = 0.0

    @classmethod
    def from_env(
        cls,
        *,
        on_call: CallListener | None = None,
        mode: Mode | None = None,
    ) -> "LLMClient":
        settings = load_settings()
        load_dotenv(settings.repo_root / ".env")
        resolved_mode = mode or mode_from_env()
        cassette = (
            Cassette(cassette_path_from_env(settings.repo_root))
            if resolved_mode in ("record", "replay")
            else None
        )
        return cls(
            settings,
            mode=resolved_mode,
            api_key=os.environ.get(API_KEY_ENV),
            capabilities=load_capabilities(settings.config_dir / "capabilities.json"),
            cassette=cassette,
            ledger=Ledger(ledger_path_from_env(settings.repo_root)),
            spend_limit_usd=spend_limit_from_env(),
            on_call=on_call,
            replay_delay_ms=int(os.environ.get(REPLAY_DELAY_ENV, "0")),
        )

    def openai_client(self) -> AsyncOpenAI:
        if self._openai is None:
            if not self._api_key:
                raise MissingApiKeyError(f"{API_KEY_ENV} is not set")
            self._openai = AsyncOpenAI(
                base_url=self.settings.base_url,
                api_key=self._api_key,
                max_retries=0,
                timeout=REQUEST_TIMEOUT_SECONDS,
                http_client=self._http_client,
            )
        return self._openai

    async def aclose(self) -> None:
        if self._openai is not None:
            await self._openai.close()

    def build_payload(
        self, request: LLMRequest, role: RoleSettings, model: ModelSpec
    ) -> tuple[dict[str, Any], ToolMode | None]:
        known = self.capabilities.models.get(model.alias) if self.capabilities else None
        capabilities = known or ModelCapabilities(alias=model.alias, id=model.id)
        thinking = request.thinking or role.thinking
        additions: list[str] = []
        payload: dict[str, Any] = {
            "model": model.id,
            "max_tokens": request.max_tokens or role.max_tokens,
        }
        temperature = request.temperature if request.temperature is not None else role.temperature
        if temperature is not None:
            payload["temperature"] = temperature
        if role.top_p is not None:
            payload["top_p"] = role.top_p
        extra_body: dict[str, Any] = {}
        toggle = capabilities.thinking.toggle
        if toggle == "chat_template_kwargs":
            extra_body["chat_template_kwargs"] = {"enable_thinking": thinking == "on"}
        elif toggle == "system_prompt":
            additions.append("/think" if thinking == "on" else "/no_think")
        budget = request.reasoning_budget or role.reasoning_budget
        if thinking == "on" and budget:
            extra_body["reasoning_budget"] = budget
        tool_mode: ToolMode | None = None
        if request.tools:
            if capabilities.native_tools:
                tool_mode = ToolMode.NATIVE
                payload["tools"] = request.tools
                payload["tool_choice"] = "auto"
            else:
                tool_mode = ToolMode.JSON_FALLBACK
                additions.append(fallback_tool_instructions(request.tools))
        if request.response_schema is not None:
            additions.append(schema_instructions(request.response_schema))
            payload["response_format"] = (
                {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "response",
                        "schema": request.response_schema,
                        "strict": True,
                    },
                }
                if capabilities.json_schema
                else {"type": "json_object"}
            )
        payload["messages"] = with_system_additions(request.messages, additions)
        if extra_body:
            payload["extra_body"] = extra_body
        return payload, tool_mode

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return await self._complete(request, allow_repair=True)

    async def _complete(self, request: LLMRequest, *, allow_repair: bool) -> LLMResponse:
        role_name = request.role.value
        role = self.settings.role(role_name)
        model = self.settings.model(role.model)
        payload, tool_mode = self.build_payload(request, role, model)
        key = fingerprint(payload)
        raw = await self._obtain(key, role_name, model, payload, request)
        content, reasoning = split_reasoning(raw.message)
        tool_calls = []
        if tool_mode == ToolMode.NATIVE:
            tool_calls = parse_native_tool_calls(raw.message.get("tool_calls") or [])
        elif tool_mode == ToolMode.JSON_FALLBACK and request.tools:
            names = {tool["function"]["name"] for tool in request.tools}
            tool_calls = parse_fallback_tool_calls(content, names)
        response = LLMResponse(
            content=content,
            reasoning=reasoning,
            tool_calls=tool_calls,
            parsed=None,
            usage=raw.usage,
            cost_usd=raw.cost_usd,
            model=raw.model,
            cache_key=key,
            finish_reason=raw.finish_reason,
            latency_ms=raw.latency_ms,
            from_cache=self.mode == "replay",
        )
        if request.response_schema is None or not allow_repair:
            return response
        return await self._enforce_schema(request, response)

    async def _enforce_schema(self, request: LLMRequest, response: LLMResponse) -> LLMResponse:
        schema = request.response_schema or {}
        parsed, errors = parse_structured(response.content, schema)
        content = response.content
        repairs = 0
        while errors and repairs < MAX_REPAIRS:
            repairs += 1
            repair_request = LLMRequest(
                role=Role.REPAIR,
                messages=[
                    {"role": "system", "content": REPAIR_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
                            f"Validation errors:\n{json.dumps(errors, ensure_ascii=False)}\n\n"
                            f"Document to repair:\n{content or ''}"
                        ),
                    },
                ],
                response_schema=schema,
                session_id=request.session_id,
                party=request.party,
            )
            repaired = await self._complete(repair_request, allow_repair=False)
            content = repaired.content
            parsed, errors = parse_structured(content, schema)
        if errors:
            raise StructuredOutputError(errors, content)
        return response.model_copy(
            update={"parsed": parsed, "content": content, "repairs": repairs}
        )

    async def _obtain(
        self,
        key: str,
        role_name: str,
        model: ModelSpec,
        payload: dict[str, Any],
        request: LLMRequest,
    ) -> RawCompletion:
        if self.mode == "replay":
            assert self.cassette is not None
            recorded = self.cassette.next(key)
            if recorded is None:
                raise ReplayMissError(key, role_name)
            if self.replay_delay_ms:
                await asyncio.sleep(self.replay_delay_ms / 1000)
            self._emit(role_name, recorded, request.session_id, request.party, from_cache=True)
            return recorded
        if self.mode == "record":
            assert self.cassette is not None
            reused = self.cassette.next_unused(key)
            if reused is not None:
                self._emit(role_name, reused, request.session_id, request.party, from_cache=True)
                return reused
        raw = await self.send(
            model,
            payload,
            role_name,
            session_id=request.session_id,
            party=request.party,
            cache_key=key,
        )
        if self.mode == "record":
            assert self.cassette is not None
            self.cassette.record(key, role_name, raw)
        return raw

    def _semaphore(self, model: ModelSpec) -> asyncio.Semaphore:
        if model.alias not in self._semaphores:
            self._semaphores[model.alias] = asyncio.Semaphore(model.max_concurrency)
        return self._semaphores[model.alias]

    async def send(
        self,
        model: ModelSpec,
        payload: dict[str, Any],
        role_name: str,
        *,
        session_id: str | None = None,
        party: str | None = None,
        cache_key: str | None = None,
        note: str | None = None,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> RawCompletion:
        price = self.settings.price_for(model.id)
        prompt_estimate = estimate_prompt_tokens(payload)
        estimate = cost_usd(
            price,
            Usage(
                prompt_tokens=prompt_estimate,
                completion_tokens=int(payload.get("max_tokens", 0)),
            ),
        )
        client = self.openai_client()
        retrying = AsyncRetrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(
                multiplier=self.retry_wait_seconds, min=0, max=8 * self.retry_wait_seconds
            ),
            retry=retry_if_exception(is_retryable),
            reraise=True,
        )
        async with self._semaphore(model):
            async with self._budget_lock:
                if self.guard is not None:
                    self.guard.check(estimate, self._reserved_usd)
                self._reserved_usd += estimate
            try:
                started = time.perf_counter()
                async for attempt in retrying:
                    with attempt:
                        completion = await client.chat.completions.create(**payload)
                latency_ms = int((time.perf_counter() - started) * 1000)
            finally:
                async with self._budget_lock:
                    self._reserved_usd -= estimate
        choice = completion.choices[0]
        message = choice.message.model_dump(exclude_none=True)
        usage = (
            Usage(
                prompt_tokens=completion.usage.prompt_tokens,
                completion_tokens=completion.usage.completion_tokens,
            )
            if completion.usage is not None
            else Usage(
                prompt_tokens=prompt_estimate,
                completion_tokens=0,
                estimated=True,
            )
        )
        raw = RawCompletion(
            message={
                key: message[key]
                for key in ("content", "reasoning", "reasoning_content", "tool_calls")
                if key in message
            },
            finish_reason=choice.finish_reason,
            usage=usage,
            model=completion.model or model.id,
            latency_ms=latency_ms,
            cost_usd=cost_usd(price, usage),
        )
        if self.ledger is not None:
            self.ledger.append(
                LedgerEntry.now(
                    role=role_name,
                    model=model.id,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    cost_usd=raw.cost_usd,
                    session_id=session_id,
                    party=party,
                    cache_key=cache_key,
                    note=note,
                )
            )
        self._emit(role_name, raw, session_id, party, from_cache=False)
        return raw

    async def list_model_ids(self) -> set[str]:
        page = await self.openai_client().models.list()
        return {model.id for model in page.data}

    def _emit(
        self,
        role_name: str,
        raw: RawCompletion,
        session_id: str | None,
        party: str | None,
        *,
        from_cache: bool,
    ) -> None:
        if self.on_call is None:
            return
        self.on_call(
            LLMCallEvent(
                role=role_name,
                model=raw.model,
                mode=self.mode,
                from_cache=from_cache,
                prompt_tokens=raw.usage.prompt_tokens,
                completion_tokens=raw.usage.completion_tokens,
                cost_usd=raw.cost_usd,
                latency_ms=raw.latency_ms,
                session_id=session_id,
                party=party,
            )
        )
