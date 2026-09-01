from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


PROVIDER_OPENROUTER = "openrouter"
PROVIDER_OPENAI = "openai"
PROVIDER_CLAUDE = "claude"
PROVIDER_GEMINI = "gemini"
PROVIDER_Z_AI = "z_ai"
PROVIDER_MOONSHOT = "moonshot"
PROVIDER_KIMI = "kimi"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_OPENCODE_GO = "opencode_go"
PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"

PROVIDER_LABELS = {
    PROVIDER_OPENROUTER: "OpenRouter",
    PROVIDER_OPENAI: "OpenAI",
    PROVIDER_CLAUDE: "Claude",
    PROVIDER_GEMINI: "Gemini",
    PROVIDER_Z_AI: "Z.AI",
    PROVIDER_MOONSHOT: "Moonshot AI",
    PROVIDER_KIMI: "Kimi",
    PROVIDER_DEEPSEEK: "DeepSeek",
    PROVIDER_OPENCODE_GO: "OpenCode Go",
    PROVIDER_OPENAI_COMPATIBLE: "OpenAI-compatible",
}

API_MODE_AUTO = "auto"
API_MODE_CHAT = "chat_completions"
API_MODE_RESPONSES = "responses"
API_MODE_MESSAGES = "messages"

API_MODE_LABELS = {
    API_MODE_AUTO: "Automatic",
    API_MODE_CHAT: "Chat Completions",
    API_MODE_RESPONSES: "Responses",
    API_MODE_MESSAGES: "Messages",
}


@dataclass(slots=True)
class Account:
    id: int | None = None
    name: str = ""
    provider: str = PROVIDER_OPENROUTER
    base_url: str = ""
    models_endpoint: str = "/models"
    chat_endpoint: str = "/chat/completions"
    responses_endpoint: str = "/responses"
    messages_endpoint: str = "/messages"
    api_mode: str = API_MODE_AUTO
    default_model: str = ""
    timeout_seconds: float = 120.0
    streaming: bool = True
    custom_headers: dict[str, str] = field(default_factory=dict)
    custom_body: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Profile:
    id: int | None = None
    name: str = ""
    system_prompt: str = ""
    default_account_id: int | None = None
    default_model: str = ""
    temperature: float | None = None
    max_output_tokens: int | None = None
    streaming: bool | None = None


@dataclass(slots=True)
class Conversation:
    id: int | None = None
    title: str = "New conversation"
    profile_id: int | None = None
    account_id: int | None = None
    model: str = ""
    system_prompt_snapshot: str = ""


@dataclass(slots=True)
class MessageAttachment:
    id: int | None = None
    message_id: int | None = None
    filename: str = ""
    mime_type: str = "application/octet-stream"
    data: bytes = b""
    source_path: str = ""


@dataclass(slots=True)
class Message:
    id: int | None = None
    conversation_id: int | None = None
    role: str = "user"
    content: str = ""
    attachments: list[MessageAttachment] = field(default_factory=list)


@dataclass(slots=True)
class GenerationSettings:
    model: str
    messages: list[dict[str, Any]]
    temperature: float | None = None
    max_output_tokens: int | None = None
    streaming: bool = True


@dataclass(slots=True)
class StreamEvent:
    kind: str
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
