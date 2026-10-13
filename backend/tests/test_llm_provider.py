"""LLM provider switch — offline unit tests (no network). Run: python3 backend/tests/test_llm_provider.py"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import llm_provider as lp  # noqa: E402


def test_provider_selection():
    os.environ.pop("LLM_PROVIDER", None); os.environ.pop("OLLAMA_URL", None)
    assert lp.provider() == "groq"
    os.environ["OLLAMA_URL"] = "http://10.42.235.232:11434/api/generate"
    assert lp.provider() == "ollama"
    assert lp.ollama_base() == "http://10.42.235.232:11434"          # full endpoint accepted
    os.environ["LLM_PROVIDER"] = "groq"
    assert lp.provider() == "groq"                                    # explicit wins
    os.environ.pop("LLM_PROVIDER"); os.environ.pop("OLLAMA_URL")


def test_generate_rendering_keeps_history():
    system, prompt = lp._render([{"role": "system", "content": "S1"}, {"role": "user", "content": "hi"},
                                 {"role": "assistant", "content": "hello"}, {"role": "user", "content": "fine?"}])
    assert system == "S1" and "User: hi" in prompt and "Assistant: hello" in prompt and prompt.endswith("Assistant:")
    assert lp._render([{"role": "user", "content": "only"}]) == ("", "only")


def test_circuit_breaker_opens_and_resets():
    lp._fail.update(count=0, open_until=0.0)
    for _ in range(3):
        lp._record(False)
    try:
        lp._breaker_check()
        raise AssertionError("breaker should be open")
    except lp.LLMOfflineError:
        pass
    lp._fail.update(count=0, open_until=0.0)
    lp._record(True)
    lp._breaker_check()


def test_unreachable_server_fails_fast():
    os.environ["OLLAMA_URL"] = "http://127.0.0.1:9"                   # nothing listens here
    os.environ["OLLAMA_MODEL"] = "any"
    lp._fail.update(count=0, open_until=0.0)
    try:
        lp.ollama_chat([{"role": "user", "content": "x"}], timeout=3)
        raise AssertionError("expected LLMOfflineError")
    except lp.LLMOfflineError:
        pass
    assert lp.ollama_ok() is False
    for k in ("OLLAMA_URL", "OLLAMA_MODEL"):
        os.environ.pop(k)
    lp._fail.update(count=0, open_until=0.0)


def test_translation_gate():
    import llm_chatbot
    src = "Rider not wearing helmet: penalty ₹1,000 (Section 194D)."
    llm_chatbot._check_translation(src, "ஹெல்மெட் இல்லாமல் சவாரி: அபராதம் ₹1,000 (Section 194D).", "ta-IN")
    for bad in ("Rider not wearing helmet: penalty ₹1,000 (Section 194D).", "ஹெல்மெட் அபராதம் ₹100 (Section 194D)."):
        try:
            llm_chatbot._check_translation(src, bad, "ta-IN")
            raise AssertionError("gate should reject: " + bad)
        except llm_chatbot.GroqOfflineError:
            pass


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as e:  # noqa: BLE001
                fails += 1; print("FAIL", name, "->", repr(e)[:300])
    sys.exit(1 if fails else 0)
