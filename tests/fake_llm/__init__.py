"""A deterministic, OpenAI-compatible fake model for end-to-end tests.

Run it with ``python -m tests.fake_llm.server --port 8765`` and point
``OPENMANUS_LLM_BASE_URL`` at ``http://127.0.0.1:8765/v1``.
"""
