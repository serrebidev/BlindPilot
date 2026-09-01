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
    openrouter: "OpenRouterFeatures" = field(default_factory=lambda: OpenRouterFeatures())


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


# ----- OpenRouter's server-side extras -----
#
# Everything below runs at OpenRouter rather than on this machine. A server
# tool the model calls is executed by OpenRouter and its result handed back to
# the model, so the chat window neither runs anything nor has to know how; and
# the thinking a reasoning model does comes back as its own stream, separate
# from the answer. Both are per-conversation choices, which is why they are
# kept on the profile beside temperature and the token limit.

# The thinking budget, as OpenRouter names it. "" means "say nothing about
# reasoning", which leaves each model at whatever it does by default; "none"
# is the separate request to turn thinking off on a model that would otherwise
# do it.
REASONING_DEFAULT = ""
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "max")
REASONING_EFFORT_LABELS = {
    REASONING_DEFAULT: "Model default",
    "none": "No thinking",
    "minimal": "Minimal",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "max": "Maximum",
}

# How much of each search result the web-search tool feeds back to the model.
SEARCH_CONTEXT_DEFAULT = ""
SEARCH_CONTEXT_SIZES = ("low", "medium", "high")
SEARCH_CONTEXT_LABELS = {
    SEARCH_CONTEXT_DEFAULT: "Tool default",
    "low": "Brief",
    "medium": "Standard",
    "high": "Thorough",
}

# Which reader turns an attached PDF into something a model can read. Without
# the file-parser plugin a PDF reaches only the models that read one natively.
PDF_ENGINE_OFF = ""
PDF_ENGINES = ("native", "mistral-ocr", "cloudflare-ai")
PDF_ENGINE_LABELS = {
    PDF_ENGINE_OFF: "Send PDFs unparsed",
    "native": "The model's own reader, where it has one",
    "mistral-ocr": "Mistral OCR, for scanned pages",
    "cloudflare-ai": "Cloudflare, text layer only",
}

# Every tool OpenRouter runs itself, as (type, label, what it does). A model
# that calls one has it executed at OpenRouter and the result returned to it
# mid-answer; nothing is run here and nothing needs a permission prompt, which
# is what makes them safe to offer in a chat window that has no agent.
OPENROUTER_SERVER_TOOLS: tuple[tuple[str, str, str], ...] = (
    ("openrouter:web_search", "Web search", "Search the web, as often as the answer needs"),
    ("openrouter:web_fetch", "Web fetch", "Read a page the model names"),
    ("openrouter:datetime", "Date and time", "Ask what the date and time are now"),
    ("openrouter:image_generation", "Image generation", "Draw an image"),
    ("openrouter:apply_patch", "Apply patch", "Edit a file the conversation carries"),
    ("openrouter:shell", "Shell", "Run a command in OpenRouter's sandbox, not on this machine"),
    ("openrouter:bash", "Bash", "Run bash in OpenRouter's sandbox, not on this machine"),
    ("openrouter:fusion", "Fusion", "Answer with several models at once and combine them"),
    ("openrouter:advisor", "Advisor", "Ask a stronger model for help part-way through"),
    ("openrouter:subagent", "Subagent", "Hand a self-contained job to a cheaper model"),
    ("openrouter:tool_search", "Tool search", "Find a tool for the job at hand"),
    (
        "openrouter:experimental__search_models",
        "Search models",
        "Look up which models OpenRouter offers",
    ),
)

SERVER_TOOL_LABELS = {name: label for name, label, _description in OPENROUTER_SERVER_TOOLS}
SERVER_TOOL_NAMES = tuple(name for name, _label, _description in OPENROUTER_SERVER_TOOLS)

# The two tools that take settings of their own.
SERVER_TOOL_WEB_SEARCH = "openrouter:web_search"
SERVER_TOOL_SUBAGENT = "openrouter:subagent"


@dataclass(slots=True)
class OpenRouterFeatures:
    """What a conversation asks OpenRouter to do beyond answering.

    Stored as one JSON column on the profile, so a release that adds a tool
    does not need a further database migration.
    """

    server_tools: list[str] = field(default_factory=list)
    search_max_results: int | None = None
    search_context: str = SEARCH_CONTEXT_DEFAULT
    reasoning_effort: str = REASONING_DEFAULT
    reasoning_max_tokens: int | None = None
    # Whether the thinking is asked for in the response at all. Off still lets
    # the model think; it just does not send the thinking back.
    show_reasoning: bool = True
    pdf_engine: str = PDF_ENGINE_OFF

    def as_dict(self) -> dict[str, Any]:
        return {
            "server_tools": list(self.server_tools),
            "search_max_results": self.search_max_results,
            "search_context": self.search_context,
            "reasoning_effort": self.reasoning_effort,
            "reasoning_max_tokens": self.reasoning_max_tokens,
            "show_reasoning": bool(self.show_reasoning),
            "pdf_engine": self.pdf_engine,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "OpenRouterFeatures":
        """Read back what was stored, ignoring anything unrecognisable.

        A profile written by a newer release, or edited by hand, must not stop
        a conversation from being opened, so every field falls back to its
        default rather than raising.
        """
        if not isinstance(payload, dict):
            return cls()
        raw_tools = payload.get("server_tools")
        tools = [
            str(name)
            for name in (raw_tools if isinstance(raw_tools, list) else [])
            if str(name) in SERVER_TOOL_NAMES
        ]
        effort = str(payload.get("reasoning_effort") or "")
        context = str(payload.get("search_context") or "")
        engine = str(payload.get("pdf_engine") or "")
        return cls(
            server_tools=tools,
            search_max_results=_positive_int(payload.get("search_max_results")),
            search_context=context if context in SEARCH_CONTEXT_SIZES else SEARCH_CONTEXT_DEFAULT,
            reasoning_effort=effort if effort in REASONING_EFFORTS else REASONING_DEFAULT,
            reasoning_max_tokens=_positive_int(payload.get("reasoning_max_tokens")),
            show_reasoning=payload.get("show_reasoning", True) is not False,
            pdf_engine=engine if engine in PDF_ENGINES else PDF_ENGINE_OFF,
        )

    def request_tools(self) -> list[dict[str, Any]]:
        """The `tools` array these choices come to, in the order offered."""
        tools: list[dict[str, Any]] = []
        for name in SERVER_TOOL_NAMES:
            if name not in self.server_tools:
                continue
            entry: dict[str, Any] = {"type": name}
            if name == SERVER_TOOL_WEB_SEARCH:
                parameters: dict[str, Any] = {}
                if self.search_max_results:
                    parameters["max_results"] = self.search_max_results
                if self.search_context:
                    parameters["search_context_size"] = self.search_context
                if parameters:
                    entry["parameters"] = parameters
            tools.append(entry)
        return tools

    def request_reasoning(self) -> dict[str, Any] | None:
        """The `reasoning` object, or None to say nothing about thinking."""
        reasoning: dict[str, Any] = {}
        if self.reasoning_effort == "none":
            # OpenRouter turns thinking off through `enabled`, not an effort of
            # "none" — that is this application's word for the same request.
            reasoning["enabled"] = False
            return reasoning
        if self.reasoning_effort:
            reasoning["effort"] = self.reasoning_effort
        if self.reasoning_max_tokens:
            reasoning["max_tokens"] = self.reasoning_max_tokens
        if reasoning and not self.show_reasoning:
            # The model still thinks; the thinking just does not come back.
            reasoning["exclude"] = True
        return reasoning or None

    def request_plugins(self) -> list[dict[str, Any]]:
        """The `plugins` array. Only the PDF reader lives here."""
        if not self.pdf_engine:
            return []
        return [{"id": "file-parser", "pdf": {"engine": self.pdf_engine}}]


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


@dataclass(slots=True)
class GenerationSettings:
    model: str
    messages: list[dict[str, Any]]
    temperature: float | None = None
    max_output_tokens: int | None = None
    streaming: bool = True
    # Sent only when the provider is asked for them, so a provider that would
    # reject an unknown key never sees one.
    reasoning: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    plugins: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class StreamEvent:
    kind: str
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
