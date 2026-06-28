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