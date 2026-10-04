"""Deterministic behaviour of the fake model, derived only from the request.

The OpenManus prompts are recognised by the tools they offer and by their markers:

* ``route`` tool (request router): ``team`` for "team"/"команда"; the ``browser`` agent
  for "browse"/"website"/"screenshot"/"сайт"; the ``manus`` agent for files, questions
  ("ask"/"спроси"), "python" or "slow" requests; else ``chat``.
* ``planning`` tool (team planner): a two-step plan for the preferred available agents.
* other tools (an agent step): ``ask_human`` first when the task says "ask"/"спроси";
  for browsing tasks ``browser_use`` opening the first URL of the task; otherwise
  ``python_execute`` writing the requested file (``hello.txt`` by default) into the
  working directory; then ``terminate``.
* no tools: a Markdown answer, either the final answer summarising a work record or a
  chat reply (a greeting for "hello"/"привет").

Agent replies for requests that contain "slow" are marked ``slow`` so that the server
delays them, which lets tests stop or reload a run while it is in progress. Requests
whose user messages contain "simulate a provider error" get an API error instead.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


ROUTE_TOOL = "route"
PLANNING_TOOL = "planning"
ASK_HUMAN_TOOL = "ask_human"
PYTHON_TOOL = "python_execute"
BROWSER_TOOL = "browser_use"
TERMINATE_TOOL = "terminate"

DEFAULT_FILE = "hello.txt"
DEFAULT_URL = "https://example.com"
SIMULATED_ERROR = "Simulated provider failure"
SUMMARY_FILE = "summary.md"
# Team agents in the order the fake planner prefers them.
PLAN_AGENT_PREFERENCE = (
    "coder",
    "writer",
    "manus",
    "data_analyst",
    "researcher",
    "browser",
)
QUOTE_MAX_CHARS = 120

_TEAM_RE = re.compile(r"\bteam\b|команд", re.IGNORECASE)
_ASK_RE = re.compile(r"\bask\b|спроси", re.IGNORECASE)
_AGENT_RE = re.compile(
    r"\bfiles?\b|файл|\bpython\b|\bslow\b|\bask\b|спроси", re.IGNORECASE
)
_BROWSE_RE = re.compile(r"\bbrowse\b|\bwebsite\b|\bscreenshot\b|сайт", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>\"')]+")
_GREETING_RE = re.compile(r"\b(?:hello|hi|hey)\b|привет|здравствуй", re.IGNORECASE)
_FAILURE_RE = re.compile(r"simulate a provider error", re.IGNORECASE)
_SLOW_RE = re.compile(r"\bslow\b|медленн", re.IGNORECASE)
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
_FILE_RE = re.compile(r"\b[\w\-]+\.(?:txt|md)\b")
_PLANNER_AGENT_RE = re.compile(r"^- \[([\w\-]+)\]", re.MULTILINE)
_TEAM_STEP_RE = re.compile(r"Your step \(\d+/\d+\): (.+)")
_RECORD_STEP_RE = re.compile(
    r"^Step (\d+) \[([\w\-]+)\]: (.+)\nStatus: (\w+)", re.MULTILINE
)
_OBSERVATION_PREFIX_RE = re.compile(r"^Observed output of cmd `[^`]+` executed:\n")
_ATTACHED_IMAGE_PREFIX = "Attached image:"
# Start of the list of uploaded files that the runner appends to a request.
_ATTACHMENTS_NOTE = "\n\nAttached files (paths relative to the workspace):"


@dataclass(frozen=True)
class ToolCall:
    """A function call made by the fake model."""

    name: str
    arguments: Dict[str, Any]


@dataclass(frozen=True)
class Reply:
    """The fake model's answer.

    Attributes:
        kind: What was recognised: route, plan, agent, answer, chat or error (logs).
        content: Text of the assistant message (may accompany tool calls).
        tool_calls: Function calls of the assistant message.
        slow: Whether the server should delay this reply.
        error: When set, the server answers with an API error with this message.
    """

    kind: str
    content: Optional[str] = None
    tool_calls: Tuple[ToolCall, ...] = ()
    slow: bool = False
    error: Optional[str] = None


# ------------------------------------------------------------------ helpers


def message_text(message: Dict[str, Any]) -> str:
    """Text of a chat message (text parts of multimodal content joined)."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def tool_names(tools: Optional[Sequence[Dict[str, Any]]]) -> List[str]:
    """Names of the functions offered in a request's ``tools``."""
    names = []
    for tool in tools or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(function, dict) and function.get("name"):
            names.append(str(function["name"]))
    return names


def is_russian(text: str) -> bool:
    return bool(_CYRILLIC_RE.search(text))


def _last_text(messages: Sequence[Dict[str, Any]], role: str) -> str:
    for message in reversed(messages):
        if message.get("role") == role:
            return message_text(message)
    return ""


def _after(text: str, marker: str) -> str:
    """Text after the last ``marker`` (the whole text when it is missing)."""
    index = text.rfind(marker)
    return (text[index + len(marker) :] if index >= 0 else text).strip()


def _between(text: str, start: str, end: str) -> str:
    begin = text.find(start)
    begin = begin + len(start) if begin >= 0 else 0
    finish = text.find(end, begin)
    return text[begin : finish if finish >= 0 else len(text)].strip()


def _quote(text: str) -> str:
    line = " ".join(text.split())
    if len(line) > QUOTE_MAX_CHARS:
        line = line[: QUOTE_MAX_CHARS - 1].rstrip() + "…"
    return line


def _unique(items: Sequence[str]) -> List[str]:
    return list(dict.fromkeys(items))


# --------------------------------------------------------------- dispatch


def respond(
    messages: Sequence[Dict[str, Any]],
    tools: Optional[Sequence[Dict[str, Any]]] = None,
) -> Reply:
    """The fake model's reply to a chat completion request."""
    if any(
        _FAILURE_RE.search(message_text(message))
        for message in messages
        if message.get("role") == "user"
    ):
        return Reply("error", error=SIMULATED_ERROR)
    names = tool_names(tools)
    if ROUTE_TOOL in names:
        return _route(messages)
    if PLANNING_TOOL in names:
        return _plan(messages)
    if names:
        return _agent_step(messages, names)
    return _answer(messages)


# ----------------------------------------------------------------- router


def _route(messages: Sequence[Dict[str, Any]]) -> Reply:
    request = _after(_last_text(messages, "user"), "Latest user request:")
    arguments: Dict[str, Any]
    if _TEAM_RE.search(request):
        arguments = {"mode": "team", "reason": "The task needs several specialists"}
    elif _BROWSE_RE.search(request):
        arguments = {
            "mode": "agent",
            "agent": "browser",
            "reason": "The task needs a web browser",
        }
    elif _AGENT_RE.search(request):
        arguments = {
            "mode": "agent",
            "agent": "manus",
            "reason": "The task needs tools to work with files and code",
        }
    else:
        arguments = {"mode": "chat", "reason": "A conversational message"}
    return Reply("route", tool_calls=(ToolCall(ROUTE_TOOL, arguments),))


# ---------------------------------------------------------------- planner


def _plan(messages: Sequence[Dict[str, Any]]) -> Reply:
    system = "\n".join(
        message_text(message) for message in messages if message.get("role") == "system"
    )
    available = _PLANNER_AGENT_RE.findall(system)
    preferred = [key for key in PLAN_AGENT_PREFERENCE if key in available]
    agents = preferred + [key for key in available if key not in preferred]
    first = agents[0] if agents else "manus"
    second = agents[1] if len(agents) > 1 else first

    request = _after(_last_text(messages, "user"), "User request:")
    if is_russian(request):
        title = "Файл приветствия и сводка"
        steps = [
            f"[{first}] Создать файл {DEFAULT_FILE} с приветствием",
            f"[{second}] Написать {SUMMARY_FILE} с описанием файла {DEFAULT_FILE}",
        ]
    else:
        title = "Greeting file and summary"
        steps = [
            f"[{first}] Create {DEFAULT_FILE} with a friendly greeting",
            f"[{second}] Write {SUMMARY_FILE} describing {DEFAULT_FILE}",
        ]
    arguments = {"command": "create", "title": title, "steps": steps}
    return Reply("plan", tool_calls=(ToolCall(PLANNING_TOOL, arguments),))


# ------------------------------------------------------------------ agents


def _request_index(messages: Sequence[Dict[str, Any]]) -> Optional[int]:
    """Index of the message holding the agent's task.

    Seeded history ends with a plain assistant message (the agent's own messages all
    carry tool calls); the task is the first user message after it, skipping extra
    image messages. Later user messages are next-step prompts.
    """
    history_end = max(
        (
            index
            for index, message in enumerate(messages)
            if message.get("role") == "assistant" and not message.get("tool_calls")
        ),
        default=-1,
    )
    for index in range(history_end + 1, len(messages)):
        message = messages[index]
        if message.get("role") == "user" and not message_text(message).startswith(
            _ATTACHED_IMAGE_PREFIX
        ):
            return index
    return None


def _tool_results(messages: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    """Latest result text per tool name (the "Observed output" prefix removed)."""
    names_by_id = {
        call.get("id"): call.get("function", {}).get("name")
        for message in messages
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
    }
    results: Dict[str, str] = {}
    for message in messages:
        if message.get("role") != "tool":
            continue
        name = message.get("name") or names_by_id.get(message.get("tool_call_id"))
        if name:
            results[name] = _OBSERVATION_PREFIX_RE.sub("", message_text(message), 1)
    return results


def _requested_file(task: str) -> str:
    match = _FILE_RE.search(task)
    return match.group(0) if match else DEFAULT_FILE


def _default_content(file_name: str, russian: bool) -> str:
    if file_name.endswith(".md"):
        if russian:
            return (
                "# Сводка\n\n"
                f"- `{DEFAULT_FILE}` — файл с дружеским приветствием.\n"
                "- Создано командой агентов OpenManus.\n"
            )
        return (
            "# Summary\n\n"
            f"- `{DEFAULT_FILE}` holds a friendly greeting.\n"
            "- Created by the OpenManus agent team.\n"
        )
    return "Привет от OpenManus!\n" if russian else "Hello from OpenManus!\n"


def _write_file_code(file_name: str, content: str) -> str:
    return (
        "from pathlib import Path\n\n"
        f"Path({file_name!r}).write_text({content!r}, encoding='utf-8')\n"
        f"print({f'Wrote {file_name}'!r})\n"
    )


def _terminate_calls(names: List[str], succeeded: bool) -> Tuple[ToolCall, ...]:
    if TERMINATE_TOOL not in names:
        return ()
    status = "success" if succeeded else "failure"
    return (ToolCall(TERMINATE_TOOL, {"status": status}),)


def _browse_step(
    task: str, names: List[str], results: Dict[str, str], russian: bool, slow: bool
) -> Reply:
    """Open the first URL of the task in the browser, then finish."""
    match = _URL_RE.search(task)
    url = match.group(0).rstrip(".,;:") if match else DEFAULT_URL
    if BROWSER_TOOL not in results:
        thought = (
            f"Открою {url} в браузере."
            if russian
            else f"I will open {url} in the browser."
        )
        arguments = {"action": "go_to_url", "url": url}
        return Reply(
            "agent",
            content=thought,
            tool_calls=(ToolCall(BROWSER_TOOL, arguments),),
            slow=slow,
        )
    succeeded = not results[BROWSER_TOOL].startswith("Error")
    if russian:
        summary = f"Открыл {url}." if succeeded else f"Не удалось открыть {url}."
    else:
        summary = f"Opened {url}." if succeeded else f"Could not open {url}."
    return Reply(
        "agent",
        content=summary,
        tool_calls=_terminate_calls(names, succeeded),
        slow=slow,
    )


def _agent_step(messages: Sequence[Dict[str, Any]], names: List[str]) -> Reply:
    index = _request_index(messages)
    request = message_text(messages[index]) if index is not None else ""
    team_step = _TEAM_STEP_RE.search(request)
    # The task is the user's own words: the team step, or the request without the
    # list of attachments (attached files are inputs, not the file to write).
    task = team_step.group(1) if team_step else request.split(_ATTACHMENTS_NOTE)[0]
    results = _tool_results(messages[index + 1 :] if index is not None else [])
    russian = is_russian(task)
    slow = bool(_SLOW_RE.search(request))
    file_name = _requested_file(task)

    if (
        _ASK_RE.search(task)
        and ASK_HUMAN_TOOL in names
        and ASK_HUMAN_TOOL not in results
    ):
        question = (
            f"Что написать в файл {file_name}?"
            if russian
            else f"What should I write into {file_name}?"
        )
        thought = (
            "Сначала уточню у вас содержимое файла."
            if russian
            else "I need to ask you what the file should contain."
        )
        return Reply(
            "agent",
            content=thought,
            tool_calls=(ToolCall(ASK_HUMAN_TOOL, {"inquire": question}),),
            slow=slow,
        )

    if BROWSER_TOOL in names and _BROWSE_RE.search(task):
        return _browse_step(task, names, results, russian, slow)

    if PYTHON_TOOL in names and PYTHON_TOOL not in results:
        answer = results.get(ASK_HUMAN_TOOL, "").strip()
        content = answer + "\n" if answer else _default_content(file_name, russian)
        thought = (
            f"Создам `{file_name}` в рабочей папке с помощью Python."
            if russian
            else f"I will create `{file_name}` in the workspace with Python."
        )
        return Reply(
            "agent",
            content=thought,
            tool_calls=(
                ToolCall(PYTHON_TOOL, {"code": _write_file_code(file_name, content)}),
            ),
            slow=slow,
        )

    succeeded = "'success': False" not in results.get(PYTHON_TOOL, "")
    if russian:
        summary = (
            f"Файл `{file_name}` создан в рабочей папке."
            if succeeded
            else f"Не удалось создать `{file_name}`."
        )
    else:
        summary = (
            f"Created `{file_name}` in the workspace."
            if succeeded
            else f"Could not create `{file_name}`."
        )
    return Reply(
        "agent",
        content=summary,
        tool_calls=_terminate_calls(names, succeeded),
        slow=slow,
    )


# ----------------------------------------------------------------- answers


def _answer(messages: Sequence[Dict[str, Any]]) -> Reply:
    prompt = _last_text(messages, "user")
    if "Work record:" in prompt:
        return Reply("answer", content=_final_answer(prompt))
    return Reply("chat", content=_chat_reply(prompt.strip()))


def _final_answer(prompt: str) -> str:
    request = _between(prompt, "User request:", "Work record:")
    record = _between(prompt, "Work record:", "Write the final answer now.")
    russian = is_russian(request)
    files = _unique(_FILE_RE.findall(record))
    steps = _RECORD_STEP_RE.findall(record)

    if russian:
        lines = ["**Готово!** Запрос выполнен.", "", f"> {_quote(request)}", ""]
        files_title, no_files = "**Файлы в рабочей папке:**", "Файлы не создавались."
        header = "| # | Шаг | Агент | Статус |"
    else:
        lines = ["**Done!** Your request is complete.", "", f"> {_quote(request)}", ""]
        files_title, no_files = "**Files in the workspace:**", "No files were created."
        header = "| # | Step | Agent | Status |"

    if files:
        lines += [files_title, ""] + [f"- `{name}`" for name in files]
    else:
        lines.append(no_files)
    if steps:
        lines += ["", header, "|---|---|---|---|"]
        for number, agent, text, status in steps:
            mark = "✅" if status == "completed" else "⚠️"
            cell = text.replace("|", "\\|")
            lines.append(f"| {number} | {cell} | {agent} | {mark} {status} |")
    return "\n".join(lines)


def _chat_reply(request: str) -> str:
    russian = is_russian(request)
    if _GREETING_RE.search(request):
        if russian:
            return (
                "Привет! 👋 Я **OpenManus** — команда ИИ-агентов.\n\n"
                "Я могу:\n"
                "- искать информацию в интернете;\n"
                "- писать и запускать код;\n"
                "- создавать документы и файлы.\n\n"
                "Чем займёмся?"
            )
        return (
            "Hello! 👋 I'm **OpenManus**, a team of AI agents.\n\n"
            "I can:\n"
            "- research the web,\n"
            "- write and run code,\n"
            "- create documents and files.\n\n"
            "What shall we work on?"
        )
    if russian:
        return (
            f"Вы написали: «{_quote(request)}».\n\n"
            "Дайте мне задачу — я найду информацию, напишу код или создам файлы."
        )
    return (
        f"You wrote: “{_quote(request)}”.\n\n"
        "Give me a task and I will research, write code or create files for you."
    )
