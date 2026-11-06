"""LLM client: secrets never appear in logs or errors."""
from adagent.llm.client import scrub


def test_key_scrubbing():
    s = scrub("error with key sk-abcdefghijklmnopqrstuvwxyz0123456789 in it")
    assert "sk-abcdefghijklmnop" not in s
