import google.generativeai as genai
import os
import logging
import asyncio

logger = logging.getLogger(__name__)

# Fix 2 — initialize once, reuse
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
_model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config={
        "temperature": 0.2,
        "max_output_tokens": 16384,
    }
)


_tool_model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config={
        "temperature": 0.2,
        "max_output_tokens": 16384,
    }
)


async def start_tool_chat(system_prompt: str, user_prompt: str, tool_schemas: list[dict]):
    """
    Start a multi-turn chat session with tools enabled and send the first message.
    Returns (chat_session, response) — caller drives the loop from here via
    extract_tool_calls() / send_tool_results().
    """
    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    logger.info("SYSTEM PROMPT:\n%s", system_prompt)
    logger.info("USER PROMPT (%d chars):\n%s", len(user_prompt), user_prompt)

    chat_session = _tool_model.start_chat()

    try:
        response = await asyncio.to_thread(
            chat_session.send_message,
            full_prompt,
            tools=[{"function_declarations": tool_schemas}],
        )
        _log_response(response)
        return chat_session, response
    except Exception as e:
        logger.error("LLM tool-call first turn failed: %s", e)
        raise


def _tool_result_parts(tool_results: list[dict]) -> list:
    """Build Gemini function_response parts from tool_runner-shaped results."""
    parts = []
    for tr in tool_results:
        response_payload = {"error": tr["error"]} if "error" in tr else {"result": tr.get("result")}
        parts.append(
            genai.protos.Part(
                function_response=genai.protos.FunctionResponse(
                    name=tr["name"],
                    response=response_payload,
                )
            )
        )
    return parts


async def _bridge_stream(make_response):
    """
    Drive a blocking Gemini streaming response from async code.
    """
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()
    result: dict = {}

    def worker():
        try:
            response = make_response()

            previous = ""

            for chunk in response:
                try:
                    current = chunk.text or ""
                except Exception:
                    current = ""

                # Gemini often streams the entire response-so-far.
                # Convert it into a true delta.
                if current.startswith(previous):
                    delta = current[len(previous):]
                else:
                    delta = current

                previous = current

                if delta:
                    loop.call_soon_threadsafe(
                        q.put_nowait,
                        ("text", delta),
                    )

            try:
                response.resolve()
            except Exception:
                pass

            result["calls"] = extract_tool_calls(response)
            result["text"] = extract_text(response)
            _log_response(response)

        except Exception as e:
            logger.error("Streaming turn failed: %s", e)
            result["error"] = e

        finally:
            loop.call_soon_threadsafe(q.put_nowait, (SENTINEL, None))

    fut = loop.run_in_executor(None, worker)

    while True:
        kind, val = await q.get()

        if kind is SENTINEL:
            break

        yield {
            "type": "text",
            "text": val,
        }

    await fut

    if "error" in result:
        raise result["error"]

    yield {
        "type": "done",
        "tool_calls": result.get("calls", []),
        "text": result.get("text", ""),
    }


async def start_tool_chat_stream(system_prompt: str, user_prompt: str, tool_schemas: list[dict]):
    """
    Streaming variant of start_tool_chat. Returns (chat_session, stream) where
    `stream` is the async generator of events for the first turn. Drive further
    turns with send_tool_results_stream(chat_session, results).
    """
    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    logger.info("SYSTEM PROMPT:\n%s", system_prompt)
    logger.info("USER PROMPT (%d chars):\n%s", len(user_prompt), user_prompt)

    chat_session = _tool_model.start_chat()

    def make_response():
        return chat_session.send_message(
            full_prompt,
            tools=[{"function_declarations": tool_schemas}],
            stream=False,
        )

    return chat_session, _bridge_stream(make_response)


def send_tool_results_stream(chat_session, tool_results: list[dict]):
    """Streaming variant of send_tool_results — returns an async event generator."""
    parts = _tool_result_parts(tool_results)

    def make_response():
        return chat_session.send_message(parts, stream=True)

    return _bridge_stream(make_response)


async def send_tool_results(chat_session, tool_results: list[dict]):
    """
    tool_results: list of {"name": str, "result": Any} or {"name": str, "error": str}
    (matches the shape returned by agent.tool_runner.execute_tool_calls).
    Sends them back as function_response parts and returns the model's next turn.
    """
    parts = _tool_result_parts(tool_results)

    try:
        response = await asyncio.to_thread(chat_session.send_message, parts)
        _log_response(response)
        return response
    except Exception as e:
        logger.error("LLM tool-result turn failed: %s", e)
        raise


def extract_tool_calls(response) -> list[dict]:
    """Pull function_call parts out of a Gemini response. Empty list = model is done calling tools."""
    calls = []
    try:
        for part in response.candidates[0].content.parts:
            if getattr(part, "function_call", None) and part.function_call.name:
                calls.append({
                    "name": part.function_call.name,
                    "args": dict(part.function_call.args) if part.function_call.args else {},
                })
    except (AttributeError, IndexError):
        pass
    return calls


def extract_text(response) -> str:
    """Pull plain text out of a Gemini response, if the model returned a final answer instead of tool calls."""
    try:
        return response.text
    except Exception:
        # response.text raises if the response contains only function_call parts (no text part)
        return ""


def _log_response(response):
    try:
        finish_reason = response.candidates[0].finish_reason
        logger.info("Finish reason: %s", finish_reason)
    except (AttributeError, IndexError):
        pass

    # ADD THIS — log full raw response content every turn
    try:
        for i, part in enumerate(response.candidates[0].content.parts):
            if getattr(part, "function_call", None) and part.function_call.name:
                logger.info(
                    "LLM turn part[%d] TOOL CALL: %s | args: %s",
                    i, part.function_call.name, dict(part.function_call.args or {})
                )
            else:
                try:
                    logger.info("LLM turn part[%d] TEXT:\n%s", i, part.text)
                except Exception:
                    pass
    except (AttributeError, IndexError):
        pass


async def chat(system_prompt: str, user_prompt: str) -> str:
    full_prompt = f"{system_prompt}\n\n{user_prompt}"

    logger.info("SYSTEM PROMPT:\n%s", system_prompt)
    logger.info("USER PROMPT (%d chars):\n%s", len(user_prompt), user_prompt)

    try:
        # Fix 1 — don't block the event loop
        response = await asyncio.to_thread(_model.generate_content, full_prompt)

        finish_reason = response.candidates[0].finish_reason
        logger.info("Finish reason: %s", finish_reason)

        result = response.text
        logger.info("LLM RESPONSE:\n%s", result)
        return result

    except Exception as e:
        logger.error("LLM call failed: %s", e)
        raise