from __future__ import annotations

import json
import os
from typing import Any, Callable

from openai import OpenAI

from weld_traceability_agent.config import OpenAISection
from weld_traceability_agent.runtime_models import ConversationState, DraftRecord, IncomingMessage
from weld_traceability_agent.tools import ToolDefinition


class OpenAIResponsesLLMAdapter:
    def __init__(self, config: OpenAISection):
        api_key = os.getenv(config.api_key_env)
        if not api_key:
            raise RuntimeError(f"Missing OpenAI API key in env var {config.api_key_env}")
        self.config = config
        self.client = OpenAI(api_key=api_key)

    def run_turn(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        incoming: IncomingMessage,
        conversation: ConversationState,
        active_draft: DraftRecord | None,
        tools: list[ToolDefinition],
        tool_executor: Callable[[str, dict], dict],
    ) -> str:
        tool_payload = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "strict": True,
            }
            for tool in tools
        ]
        context: list[dict[str, Any]] = [
            {"role": "developer", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        for _ in range(0, 8):
            response = self.client.responses.create(
                model=self.config.model,
                input=context,
                tools=tool_payload,
                parallel_tool_calls=False,
                reasoning={"effort": self.config.reasoning_effort},
                temperature=self.config.temperature,
                max_output_tokens=self.config.max_output_tokens,
                store=False,
                timeout=self.config.timeout_sec,
            )
            function_calls = [item for item in response.output if getattr(item, "type", "") == "function_call"]
            if not function_calls:
                text = getattr(response, "output_text", "") or _extract_message_text(response.output)
                return text.strip()

            context.extend(_serialize_response_item(item) for item in response.output)
            for call in function_calls:
                arguments = json.loads(call.arguments or "{}")
                result = tool_executor(call.name, arguments)
                context.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )
        raise RuntimeError("OpenAI tool loop exceeded the maximum number of rounds.")


def _serialize_response_item(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json")
    if isinstance(item, dict):
        return item
    raise TypeError(f"Unsupported response item type: {type(item)!r}")


def _extract_message_text(output_items: list[Any]) -> str:
    parts: list[str] = []
    for item in output_items:
        if getattr(item, "type", "") != "message":
            continue
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", "") == "output_text":
                parts.append(getattr(content, "text", ""))
    return "\n".join(part for part in parts if part)
