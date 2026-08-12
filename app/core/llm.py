"""
Thin LLM provider abstraction so the app can run entirely on Google
Gemini's free tier (no billing/card required) instead of the paid
Anthropic API. If GEMINI_API_KEY is set, it's used by default; if only
ANTHROPIC_API_KEY is set, that's used instead. If neither is set, LLM
features degrade gracefully with a clear message rather than crashing.

Get a free Gemini key (no card needed): https://aistudio.google.com/apikey
"""
import os
import json
import logging

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

ACTIVE_PROVIDER = "gemini" if GEMINI_API_KEY else ("anthropic" if ANTHROPIC_API_KEY else None)


def is_configured() -> bool:
    return ACTIVE_PROVIDER is not None


def simple_completion(prompt: str, max_tokens: int = 800) -> str:
    """One-shot text completion, no tools. Used for qualitative resume
    feedback in ats_scorer.py."""
    if ACTIVE_PROVIDER == "gemini":
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(GEMINI_MODEL)
            response = model.generate_content(
                prompt, generation_config={"max_output_tokens": max_tokens}
            )
            return response.text
        except Exception as e:
            return f"LLM feedback failed (Gemini): {e}"

    if ACTIVE_PROVIDER == "anthropic":
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            response = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(b.text for b in response.content if hasattr(b, "text"))
        except Exception as e:
            return f"LLM feedback failed (Anthropic): {e}"

    return (
        "LLM feedback unavailable - no GEMINI_API_KEY or ANTHROPIC_API_KEY "
        "configured. Get a free Gemini key at https://aistudio.google.com/apikey "
        "and set it in your .env file."
    )


def agent_loop(system_prompt: str, user_message: str, tool_defs: list[dict], tool_impl: dict, max_turns: int = 5) -> str:
    """Runs a tool-calling agent loop against whichever provider is
    configured. tool_defs use Anthropic's schema shape (name/description/
    input_schema) - translated internally for Gemini."""
    if ACTIVE_PROVIDER == "gemini":
        return _gemini_agent_loop(system_prompt, user_message, tool_defs, tool_impl, max_turns)
    if ACTIVE_PROVIDER == "anthropic":
        return _anthropic_agent_loop(system_prompt, user_message, tool_defs, tool_impl, max_turns)
    return (
        "No LLM configured - the agent chat feature needs GEMINI_API_KEY "
        "(free, no card: https://aistudio.google.com/apikey) or "
        "ANTHROPIC_API_KEY set in your .env file."
    )


def _gemini_agent_loop(system_prompt, user_message, tool_defs, tool_impl, max_turns):
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)

    gemini_tools = [{
        "function_declarations": [
            {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}
            for t in tool_defs
        ]
    }]

    model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=system_prompt, tools=gemini_tools)
    chat = model.start_chat()

    response = chat.send_message(user_message)
    for _ in range(max_turns):
        function_calls = [
            part.function_call for part in response.candidates[0].content.parts
            if hasattr(part, "function_call") and part.function_call and part.function_call.name
        ]
        if not function_calls:
            return response.text

        function_responses = []
        for fc in function_calls:
            tool_name = fc.name
            tool_input = dict(fc.args)
            logger.info(f"[agent/gemini] calling tool={tool_name} input={tool_input}")
            try:
                result = tool_impl[tool_name](**tool_input)
            except Exception as e:
                result = {"error": str(e)}
            function_responses.append(
                genai.protos.Part(
                    function_response=genai.protos.FunctionResponse(
                        name=tool_name, response={"result": json.dumps(result)[:8000]}
                    )
                )
            )
        response = chat.send_message(function_responses)

    return "I wasn't able to complete this within the allotted reasoning steps - please try a more specific request."


def _anthropic_agent_loop(system_prompt, user_message, tool_defs, tool_impl, max_turns):
    from anthropic import Anthropic
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    messages = [{"role": "user", "content": user_message}]

    for _ in range(max_turns):
        response = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=1500,
            system=system_prompt, tools=tool_defs, messages=messages,
        )
        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if hasattr(b, "text"))

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_name = block.name
            tool_input = dict(block.input)
            logger.info(f"[agent/anthropic] calling tool={tool_name} input={tool_input}")
            try:
                result = tool_impl[tool_name](**tool_input)
            except Exception as e:
                result = {"error": str(e)}
            tool_results.append({
                "type": "tool_result", "tool_use_id": block.id,
                "content": json.dumps(result)[:8000],
            })
        messages.append({"role": "user", "content": tool_results})

    return "I wasn't able to complete this within the allotted reasoning steps - please try a more specific request."
