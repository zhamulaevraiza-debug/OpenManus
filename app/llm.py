import asyncio
import math
import os
import re
import sys
import weakref
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, Dict, List, Optional, Union

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncAzureOpenAI,
    AsyncOpenAI,
    AuthenticationError,
    InternalServerError,
    OpenAIError,
    RateLimitError,
)
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.config import LLMSettings, config
from app.context import add_usage, current_run
from app.exceptions import TokenLimitExceeded
from app.logger import logger
from app.schema import (
    ROLE_VALUES,
    TOOL_CHOICE_TYPE,
    TOOL_CHOICE_VALUES,
    Message,
    ToolChoice,
)
from app.utils.images import image_data_url
from app.utils.tokenizer import Tokenizer, get_tokenizer


# Models that take ``max_completion_tokens`` and reject ``temperature``.
REASONING_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5")

# Model-name patterns of vision-capable models (used when supports_images is unset).
_VISION_MODEL_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(?:^|[/.:])gpt-4o",
        r"(?:^|[/.:])gpt-4\.1",
        r"(?:^|[/.:])gpt-5",
        r"(?:^|[/.:])o[34](?:$|[-_.:])",
        r"(?:^|[/.:])claude-3",
        r"(?:^|[/.:])claude-(?:sonnet|opus|haiku)",
        r"(?:^|[/.:])claude-[\w.]*-4",
        r"(?:^|[/.:])gemini",
        r"vision",
        r"-vl",
        r"(?:^|[/.:])llava",
    )
)

# Errors worth retrying: network problems, timeouts, rate limits and 5xx responses.
TRANSIENT_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

DEFAULT_MAX_CONCURRENCY = 8

TOOL_IMAGES_NOTE = "Images returned by the tool calls above:"

DeltaCallback = Callable[[str], None]


def is_reasoning_model(model: str) -> bool:
    """Whether ``model`` is an OpenAI reasoning model (o-series, gpt-5)."""
    return model.strip().lower().rsplit("/", 1)[-1].startswith(REASONING_MODEL_PREFIXES)


def model_supports_images(model: str) -> bool:
    """Guess from the model name whether it accepts image inputs."""
    name = model.strip().lower()
    return any(pattern.search(name) for pattern in _VISION_MODEL_PATTERNS)


def _max_concurrency() -> int:
    try:
        return max(1, int(os.environ.get("OPENMANUS_LLM_MAX_CONCURRENCY", "")))
    except ValueError:
        return DEFAULT_MAX_CONCURRENCY


# One semaphore per (event loop, provider): asyncio primitives are loop-bound.
_provider_semaphores: (
    "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Dict[str, asyncio.Semaphore]]"
) = weakref.WeakKeyDictionary()


def _provider_semaphore(provider: str) -> asyncio.Semaphore:
    per_loop = _provider_semaphores.setdefault(asyncio.get_running_loop(), {})
    semaphore = per_loop.get(provider)
    if semaphore is None:
        semaphore = per_loop[provider] = asyncio.Semaphore(_max_concurrency())
    return semaphore


def _log_retry(retry_state: RetryCallState) -> None:
    error = retry_state.outcome.exception() if retry_state.outcome else None
    wait = retry_state.next_action.sleep if retry_state.next_action else 0
    logger.warning(
        f"LLM request failed ({type(error).__name__}: {error}); "
        f"retrying in {wait:.1f}s (attempt {retry_state.attempt_number})"
    )


def _retry_transient(func):
    """Retry a coroutine on transient provider errors, re-raising the last error."""
    return retry(
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        wait=wait_random_exponential(min=1, max=30),
        stop=stop_after_attempt(4),
        before_sleep=_log_retry,
        reraise=True,
    )(func)


def _print_delta(text: str) -> None:
    """CLI streaming output (used only when no web run is active)."""
    sys.stdout.write(text)
    sys.stdout.flush()


class TokenCounter:
    # Token constants
    BASE_MESSAGE_TOKENS = 4
    FORMAT_TOKENS = 2
    LOW_DETAIL_IMAGE_TOKENS = 85
    HIGH_DETAIL_TILE_TOKENS = 170

    # Image processing constants
    MAX_SIZE = 2048
    HIGH_DETAIL_TARGET_SHORT_SIDE = 768
    TILE_SIZE = 512

    def __init__(self, tokenizer: Tokenizer):
        self.tokenizer = tokenizer

    def count_text(self, text: str) -> int:
        """Calculate tokens for a text string"""
        return self.tokenizer.count(text) if text else 0

    def count_image(self, image_item: dict) -> int:
        """
        Calculate tokens for an image based on detail level and dimensions

        For "low" detail: fixed 85 tokens
        For "high" detail:
        1. Scale to fit in 2048x2048 square
        2. Scale shortest side to 768px
        3. Count 512px tiles (170 tokens each)
        4. Add 85 tokens
        """
        image_url = image_item.get("image_url")
        detail = (
            image_url.get("detail", "medium") if isinstance(image_url, dict) else None
        ) or "medium"

        # For low detail, always return fixed token count
        if detail == "low":
            return self.LOW_DETAIL_IMAGE_TOKENS

        # For high/medium detail, calculate based on dimensions if available
        if "dimensions" in image_item:
            width, height = image_item["dimensions"]
            return self._calculate_high_detail_tokens(width, height)

        return (
            self._calculate_high_detail_tokens(1024, 1024) if detail == "high" else 1024
        )

    def _calculate_high_detail_tokens(self, width: int, height: int) -> int:
        """Calculate tokens for high detail images based on dimensions"""
        # Step 1: Scale to fit in MAX_SIZE x MAX_SIZE square
        if width > self.MAX_SIZE or height > self.MAX_SIZE:
            scale = self.MAX_SIZE / max(width, height)
            width = int(width * scale)
            height = int(height * scale)

        # Step 2: Scale so shortest side is HIGH_DETAIL_TARGET_SHORT_SIDE
        scale = self.HIGH_DETAIL_TARGET_SHORT_SIDE / min(width, height)
        scaled_width = int(width * scale)
        scaled_height = int(height * scale)

        # Step 3: Count number of 512px tiles
        tiles_x = math.ceil(scaled_width / self.TILE_SIZE)
        tiles_y = math.ceil(scaled_height / self.TILE_SIZE)
        total_tiles = tiles_x * tiles_y

        # Step 4: Calculate final token count
        return (
            total_tiles * self.HIGH_DETAIL_TILE_TOKENS
        ) + self.LOW_DETAIL_IMAGE_TOKENS

    def count_content(self, content: Union[str, List[Union[str, dict]]]) -> int:
        """Calculate tokens for message content"""
        if not content:
            return 0

        if isinstance(content, str):
            return self.count_text(content)

        token_count = 0
        for item in content:
            if isinstance(item, str):
                token_count += self.count_text(item)
            elif isinstance(item, dict):
                if "text" in item:
                    token_count += self.count_text(item["text"])
                elif "image_url" in item:
                    token_count += self.count_image(item)
        return token_count

    def count_tool_calls(self, tool_calls: List[dict]) -> int:
        """Calculate tokens for tool calls"""
        token_count = 0
        for tool_call in tool_calls:
            if "function" in tool_call:
                function = tool_call["function"]
                token_count += self.count_text(function.get("name", ""))
                token_count += self.count_text(function.get("arguments", ""))
        return token_count

    def count_message_tokens(self, messages: List[dict]) -> int:
        """Calculate the total number of tokens in a message list"""
        total_tokens = self.FORMAT_TOKENS  # Base format tokens

        for message in messages:
            tokens = self.BASE_MESSAGE_TOKENS  # Base tokens per message

            # Add role tokens
            tokens += self.count_text(message.get("role", ""))

            # Add content tokens
            if "content" in message:
                tokens += self.count_content(message["content"])

            # Add tool calls tokens
            if "tool_calls" in message:
                tokens += self.count_tool_calls(message["tool_calls"])

            # Add name and tool_call_id tokens
            tokens += self.count_text(message.get("name", ""))
            tokens += self.count_text(message.get("tool_call_id", ""))

            total_tokens += tokens

        return total_tokens


class LLM:
    """Client for one configured model.

    Instances are cached per ``config_name`` (a ``[llm.<name>]`` table, falling back
    to ``[llm]``); :meth:`reset_instances` clears the cache after a config reload.
    Passing an :class:`LLMSettings` creates an uncached instance for those settings.
    """

    _instances: Dict[str, "LLM"] = {}

    def __new__(
        cls,
        config_name: str = "default",
        llm_config: Optional[Union[LLMSettings, Dict[str, LLMSettings]]] = None,
    ):
        if isinstance(llm_config, LLMSettings):
            instance = super().__new__(cls)
            instance._setup(llm_config)
            return instance
        if config_name not in cls._instances:
            settings_map = llm_config or config.llm
            instance = super().__new__(cls)
            instance._setup(settings_map.get(config_name, settings_map["default"]))
            cls._instances[config_name] = instance
        return cls._instances[config_name]

    @classmethod
    def reset_instances(cls) -> None:
        """Forget cached instances so that new ones use the current configuration."""
        cls._instances.clear()

    def _setup(self, settings: LLMSettings) -> None:
        self.settings = settings
        self.model = settings.model
        self.max_tokens = settings.max_tokens
        self.temperature = settings.temperature
        self.api_type = settings.api_type
        self.api_key = settings.api_key
        self.api_version = settings.api_version
        self.base_url = settings.base_url
        self.supports_images = (
            settings.supports_images
            if settings.supports_images is not None
            else model_supports_images(self.model)
        )

        # Cumulative usage of this instance (per-run usage lives in the RunContext)
        self.total_input_tokens = 0
        self.total_completion_tokens = 0
        self.max_input_tokens = settings.max_input_tokens

        self.tokenizer = get_tokenizer(self.model)
        self.token_counter = TokenCounter(self.tokenizer)

        if self.api_type == "azure":
            self.client = AsyncAzureOpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                api_version=self.api_version,
            )
        elif self.api_type == "aws":
            from app.bedrock import BedrockClient

            self.client = BedrockClient(
                region_name=BedrockClient.region_from_endpoint(self.base_url)
            )
        else:
            # The OpenAI SDK refuses an empty key; local servers (e.g. Ollama) ignore it.
            self.client = AsyncOpenAI(
                api_key=self.api_key or "not-set", base_url=self.base_url or None
            )

    @property
    def _provider_key(self) -> str:
        return f"{self.api_type}|{self.base_url}"

    @asynccontextmanager
    async def _provider_slot(self) -> AsyncIterator[None]:
        """Limit concurrent requests per provider (``OPENMANUS_LLM_MAX_CONCURRENCY``)."""
        async with _provider_semaphore(self._provider_key):
            yield

    def count_tokens(self, text: str) -> int:
        """Calculate the number of tokens in a text"""
        return self.tokenizer.count(text) if text else 0

    def count_message_tokens(self, messages: List[dict]) -> int:
        return self.token_counter.count_message_tokens(messages)

    def update_token_count(self, input_tokens: int, completion_tokens: int = 0) -> None:
        """Update token counts of this instance and of the active run."""
        self.total_input_tokens += input_tokens
        self.total_completion_tokens += completion_tokens
        add_usage(input_tokens, completion_tokens)
        logger.debug(
            f"Token usage: Input={input_tokens}, Completion={completion_tokens}, "
            f"Cumulative Input={self.total_input_tokens}, "
            f"Cumulative Completion={self.total_completion_tokens}"
        )

    def _consumed_input_tokens(self) -> int:
        """Input tokens counted against ``max_input_tokens`` (per run when active)."""
        run = current_run()
        if run is not None:
            return run.usage.get("input_tokens", 0)
        return self.total_input_tokens

    def check_token_limit(self, input_tokens: int) -> bool:
        """Check if token limits are exceeded"""
        if self.max_input_tokens is not None:
            return (
                self._consumed_input_tokens() + input_tokens
            ) <= self.max_input_tokens
        # If max_input_tokens is not set, always return True
        return True

    def get_limit_error_message(self, input_tokens: int) -> str:
        """Generate error message for token limit exceeded"""
        consumed = self._consumed_input_tokens()
        if (
            self.max_input_tokens is not None
            and (consumed + input_tokens) > self.max_input_tokens
        ):
            return f"Request may exceed input token limit (Current: {consumed}, Needed: {input_tokens}, Max: {self.max_input_tokens})"

        return "Token limit exceeded"

    def _ensure_within_limit(self, input_tokens: int) -> None:
        if not self.check_token_limit(input_tokens):
            raise TokenLimitExceeded(self.get_limit_error_message(input_tokens))

    @staticmethod
    def format_messages(
        messages: List[Union[dict, Message]], supports_images: bool = False
    ) -> List[dict]:
        """
        Format messages for LLM by converting them to OpenAI message format.

        Images (``base64_image``) are sent as ``image_url`` parts when the model
        supports images and dropped otherwise. Providers only accept images in user
        messages, so images of tool results are moved into one user message placed
        right after the run of tool messages; images on other roles are dropped.
        Input dicts are not modified.

        Args:
            messages: List of messages that can be either dict or Message objects
            supports_images: Flag indicating if the target model supports image inputs

        Returns:
            List[dict]: List of formatted messages in OpenAI format

        Raises:
            ValueError: If messages are invalid or missing required fields
            TypeError: If unsupported message types are provided

        Examples:
            >>> msgs = [
            ...     Message.system_message("You are a helpful assistant"),
            ...     {"role": "user", "content": "Hello"},
            ...     Message.user_message("How are you?")
            ... ]
            >>> formatted = LLM.format_messages(msgs)
        """
        formatted_messages: List[dict] = []
        tool_images: List[str] = []

        def flush_tool_images() -> None:
            if tool_images:
                formatted_messages.append(
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": TOOL_IMAGES_NOTE}]
                        + [
                            {
                                "type": "image_url",
                                "image_url": {"url": image_data_url(i)},
                            }
                            for i in tool_images
                        ],
                    }
                )
                tool_images.clear()

        for message in messages:
            if isinstance(message, Message):
                message = message.to_dict()
            elif isinstance(message, dict):
                message = dict(message)
            else:
                raise TypeError(f"Unsupported message type: {type(message)}")

            role = message.get("role")
            if role is None:
                raise ValueError("Message dict must contain 'role' field")
            if role not in ROLE_VALUES:
                raise ValueError(f"Invalid role: {role}")
            if role != "tool":
                flush_tool_images()

            image = message.pop("base64_image", None)
            if image and supports_images:
                if role == "user":
                    content = message.get("content")
                    if not content:
                        parts = []
                    elif isinstance(content, str):
                        parts = [{"type": "text", "text": content}]
                    else:
                        parts = [
                            (
                                {"type": "text", "text": item}
                                if isinstance(item, str)
                                else item
                            )
                            for item in content
                        ]
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data_url(image)},
                        }
                    )
                    message["content"] = parts
                elif role == "tool":
                    tool_images.append(image)

            if "content" in message or "tool_calls" in message:
                formatted_messages.append(message)

        flush_tool_images()
        return formatted_messages

    def _prepare_messages(
        self,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]],
        supports_images: bool,
    ) -> List[dict]:
        formatted = self.format_messages(messages, supports_images)
        if system_msgs:
            return self.format_messages(system_msgs, supports_images) + formatted
        return formatted

    def _completion_params(
        self, messages: List[dict], temperature: Optional[float]
    ) -> dict:
        params = {"model": self.model, "messages": messages}
        if is_reasoning_model(self.model):
            params["max_completion_tokens"] = self.max_tokens
        else:
            params["max_tokens"] = self.max_tokens
            params["temperature"] = (
                temperature if temperature is not None else self.temperature
            )
        return params

    def _delta_sink(
        self, stream: bool, on_delta: Optional[DeltaCallback]
    ) -> Optional[DeltaCallback]:
        """Where streamed chunks go: the callback, stdout for the CLI, or nowhere."""
        if on_delta is not None:
            return on_delta
        if stream and current_run() is None:
            return _print_delta
        return None

    @staticmethod
    def _log_api_error(method: str, error: Exception) -> None:
        if isinstance(error, TRANSIENT_ERRORS):
            return  # logged by the retry policy
        if isinstance(error, AuthenticationError):
            logger.error(f"{method}: authentication failed, check the API key")
        elif isinstance(error, OpenAIError):
            logger.error(f"{method}: OpenAI API error: {error}")
        elif not isinstance(error, (TokenLimitExceeded, ValueError)):
            logger.error(f"{method}: unexpected error: {type(error).__name__}: {error}")

    async def _complete_text(
        self,
        messages: List[dict],
        temperature: Optional[float],
        sink: Optional[DeltaCallback],
    ) -> str:
        """Run a text completion, streaming chunks to ``sink`` when given."""
        input_tokens = self.count_message_tokens(messages)
        self._ensure_within_limit(input_tokens)
        params = self._completion_params(messages, temperature)

        # Bedrock is always called without streaming; the text is delivered at once.
        if sink is None or self.api_type == "aws":
            async with self._provider_slot():
                response = await self.client.chat.completions.create(
                    **params, stream=False
                )
            content = response.choices[0].message.content if response.choices else None
            if not content:
                raise ValueError("Empty or invalid response from LLM")
            usage = response.usage
            self.update_token_count(
                usage.prompt_tokens if usage else input_tokens,
                usage.completion_tokens if usage else self.count_tokens(content),
            )
            if sink is not None:
                sink(content)
            return content

        parts: List[str] = []
        usage = None
        async with self._provider_slot():
            response = await self.client.chat.completions.create(**params, stream=True)
            async for chunk in response:
                usage = getattr(chunk, "usage", None) or usage
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                text = delta.content if delta is not None else None
                if text:
                    parts.append(text)
                    sink(text)
        if sink is _print_delta:
            _print_delta("\n")

        full_response = "".join(parts).strip()
        if not full_response:
            raise ValueError("Empty response from streaming LLM")
        self.update_token_count(
            usage.prompt_tokens if usage else input_tokens,
            usage.completion_tokens if usage else self.count_tokens(full_response),
        )
        return full_response

    @_retry_transient
    async def ask(
        self,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        stream: bool = True,
        temperature: Optional[float] = None,
        on_delta: Optional[DeltaCallback] = None,
    ) -> str:
        """
        Send a prompt to the LLM and get the response.

        Args:
            messages: List of conversation messages
            system_msgs: Optional system messages to prepend
            stream (bool): Stream the response to stdout (CLI only: ignored while a
                web run is active unless ``on_delta`` is given)
            temperature (float): Sampling temperature for the response
            on_delta: Called with every streamed text chunk (enables streaming)

        Returns:
            str: The generated response

        Raises:
            TokenLimitExceeded: If token limits are exceeded
            ValueError: If messages are invalid or response is empty
            OpenAIError: If the API call fails (transient errors are retried first)
        """
        try:
            formatted = self._prepare_messages(
                messages, system_msgs, self.supports_images
            )
            return await self._complete_text(
                formatted, temperature, self._delta_sink(stream, on_delta)
            )
        except Exception as e:
            self._log_api_error("ask", e)
            raise

    @_retry_transient
    async def ask_with_images(
        self,
        messages: List[Union[dict, Message]],
        images: List[Union[str, dict]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        stream: bool = False,
        temperature: Optional[float] = None,
        on_delta: Optional[DeltaCallback] = None,
    ) -> str:
        """
        Send a prompt with images to the LLM and get the response.

        Args:
            messages: List of conversation messages
            images: List of image URLs or image data dictionaries
            system_msgs: Optional system messages to prepend
            stream (bool): Stream the response to stdout (CLI only)
            temperature (float): Sampling temperature for the response
            on_delta: Called with every streamed text chunk (enables streaming)

        Returns:
            str: The generated response

        Raises:
            TokenLimitExceeded: If token limits are exceeded
            ValueError: If the model has no image support, messages are invalid or
                the response is empty
            OpenAIError: If the API call fails (transient errors are retried first)
        """
        try:
            if not self.supports_images:
                raise ValueError(
                    f"Model {self.model} does not support images "
                    "(set supports_images = true in its [llm] config to override)"
                )

            formatted_messages = self.format_messages(messages, supports_images=True)

            # Ensure the last message is from the user to attach images
            if not formatted_messages or formatted_messages[-1]["role"] != "user":
                raise ValueError(
                    "The last message must be from the user to attach images"
                )

            # Convert the last user message to multimodal content
            last_message = formatted_messages[-1]
            content = last_message.get("content")
            multimodal_content = (
                [{"type": "text", "text": content}]
                if isinstance(content, str)
                else list(content)
                if isinstance(content, list)
                else []
            )

            for image in images:
                if isinstance(image, str):
                    multimodal_content.append(
                        {"type": "image_url", "image_url": {"url": image}}
                    )
                elif isinstance(image, dict) and "url" in image:
                    multimodal_content.append({"type": "image_url", "image_url": image})
                elif isinstance(image, dict) and "image_url" in image:
                    multimodal_content.append(image)
                else:
                    raise ValueError(f"Unsupported image format: {image}")
            last_message["content"] = multimodal_content

            if system_msgs:
                formatted_messages = (
                    self.format_messages(system_msgs, supports_images=True)
                    + formatted_messages
                )
            return await self._complete_text(
                formatted_messages, temperature, self._delta_sink(stream, on_delta)
            )
        except Exception as e:
            self._log_api_error("ask_with_images", e)
            raise

    @_retry_transient
    async def ask_tool(
        self,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        timeout: int = 300,
        tools: Optional[List[dict]] = None,
        tool_choice: TOOL_CHOICE_TYPE = ToolChoice.AUTO,  # type: ignore
        temperature: Optional[float] = None,
        **kwargs,
    ) -> ChatCompletionMessage | None:
        """
        Ask LLM using functions/tools and return the response.

        Args:
            messages: List of conversation messages
            system_msgs: Optional system messages to prepend
            timeout: Request timeout in seconds
            tools: List of tools to use
            tool_choice: Tool choice strategy
            temperature: Sampling temperature for the response
            **kwargs: Additional completion arguments

        Returns:
            The model's message, or None when the provider returned no choices

        Raises:
            TokenLimitExceeded: If token limits are exceeded
            ValueError: If tools, tool_choice, or messages are invalid
            OpenAIError: If the API call fails (transient errors are retried first)
        """
        try:
            if tool_choice not in TOOL_CHOICE_VALUES:
                raise ValueError(f"Invalid tool_choice: {tool_choice}")
            if tools:
                for tool in tools:
                    if not isinstance(tool, dict) or "type" not in tool:
                        raise ValueError("Each tool must be a dict with 'type' field")

            formatted = self._prepare_messages(
                messages, system_msgs, self.supports_images
            )
            input_tokens = self.count_message_tokens(formatted) + sum(
                self.count_tokens(str(tool)) for tool in tools or []
            )
            self._ensure_within_limit(input_tokens)

            params = {
                **self._completion_params(formatted, temperature),
                "timeout": timeout,
                **kwargs,
            }
            if tools:
                params["tools"] = tools
                params["tool_choice"] = tool_choice

            async with self._provider_slot():
                response: ChatCompletion = await self.client.chat.completions.create(
                    **params, stream=False
                )

            if not response.choices or not response.choices[0].message:
                logger.warning("LLM returned a response without choices")
                return None

            usage = response.usage
            self.update_token_count(
                usage.prompt_tokens if usage else input_tokens,
                usage.completion_tokens if usage else 0,
            )
            return response.choices[0].message
        except Exception as e:
            self._log_api_error("ask_tool", e)
            raise


config.add_reload_hook(LLM.reset_instances)
