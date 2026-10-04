from types import SimpleNamespace

from loguru import logger as loguru_logger

from app.schema import IMAGE_OMITTED, Memory, Message


def _tool_calls(*ids):
    return [
        SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(
                model_dump=lambda: {"name": "t", "arguments": "{}"}
            ),
        )
        for call_id in ids
    ]


def test_trimming_never_leaves_orphan_tool_messages():
    memory = Memory(max_messages=4)
    memory.add_message(Message.user_message("task"))
    memory.add_message(Message.from_tool_calls(tool_calls=_tool_calls("a", "b")))
    memory.add_message(Message.tool_message("ra", name="t", tool_call_id="a"))
    memory.add_message(Message.tool_message("rb", name="t", tool_call_id="b"))
    memory.add_message(Message.assistant_message("thinking"))
    assert [m.role for m in memory.messages] == [
        "assistant",
        "tool",
        "tool",
        "assistant",
    ]

    memory.add_message(Message.user_message("next"))
    # Plain slicing would start with the tool result "ra" whose call was dropped
    assert [m.role for m in memory.messages] == ["assistant", "user"]

    memory.add_messages(
        [
            Message.from_tool_calls(tool_calls=_tool_calls("c")),
            Message.tool_message("rc", name="t", tool_call_id="c"),
        ]
    )
    assert memory.messages[0].role != "tool"
    assert len(memory.messages) <= 4


def test_only_latest_images_are_kept():
    memory = Memory(max_images=2)
    for i in range(4):
        memory.add_message(Message.user_message(f"shot {i}", base64_image=f"img{i}"))
    memory.add_message(
        Message.tool_message("", name="t", tool_call_id="x", base64_image="img4")
    )

    images = [m.base64_image for m in memory.messages]
    assert images == [None, None, None, "img3", "img4"]
    assert memory.messages[0].content == f"shot 0\n{IMAGE_OMITTED}"
    assert memory.messages[2].content == f"shot 2\n{IMAGE_OMITTED}"


def test_message_to_dict_omits_none_fields():
    message = Message.from_tool_calls(tool_calls=_tool_calls("a"), content=["x", "y"])
    data = message.to_dict()
    assert data == {
        "role": "assistant",
        "content": "x\ny",
        "tool_calls": [
            {
                "id": "a",
                "type": "function",
                "function": {"name": "t", "arguments": "{}"},
            }
        ],
    }


def test_structlog_events_are_forwarded_to_loguru():
    from app.utils.logger import logger as structlog_logger

    records = []
    handler_id = loguru_logger.add(
        records.append, level="DEBUG", format="{level}|{message}"
    )
    try:
        structlog_logger.warning("sandbox ready", sandbox_id="sb-1")
    finally:
        loguru_logger.remove(handler_id)
    assert len(records) == 1
    assert records[0].startswith("WARNING|")
    assert "sandbox ready" in records[0] and "sandbox_id='sb-1'" in records[0]


def test_clean_path_stays_inside_the_workspace():
    from app.utils.files_utils import clean_path

    assert clean_path("/workspace/src/app.py") == "src/app.py"
    assert clean_path("workspace/a.txt") == "a.txt"
    assert clean_path("docs/../a.txt") == "a.txt"
    assert clean_path("../../etc/passwd") == "etc/passwd"
    assert clean_path("/workspace") == ""
    assert clean_path("workspace_old/x") == "workspace_old/x"
    assert clean_path("/data/x", workspace_path="/data") == "x"
