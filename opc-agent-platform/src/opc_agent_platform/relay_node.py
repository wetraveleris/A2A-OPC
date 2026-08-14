from __future__ import annotations

import argparse
import asyncio
import json
import os

from typing import Any

from dotenv import load_dotenv
from websockets.asyncio.client import connect

from .conversation import A2ACommunicator


async def execute_local_task(
    communicator: A2ACommunicator,
    local_agent_url: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    request_id = str(message.get("requestId", ""))
    payload = message.get("payload")
    if not request_id or not isinstance(payload, dict):
        return {
            "type": "result",
            "requestId": request_id,
            "error": "Relay task did not include a valid request ID and payload",
        }
    try:
        task_id, task_state, response = await communicator.send_json_to_url(
            local_agent_url,
            payload,
        )
        return {
            "type": "result",
            "requestId": request_id,
            "taskId": task_id,
            "taskState": task_state,
            "response": response,
        }
    except Exception as exc:
        return {
            "type": "result",
            "requestId": request_id,
            "error": str(exc),
        }


async def run_node(
    relay_url: str,
    relay_token: str,
    agent_id: str,
    local_agent_url: str,
) -> None:
    communicator = A2ACommunicator(base_url=local_agent_url)
    headers = {"Authorization": f"Bearer {relay_token}"} if relay_token else None
    delay = 1.0
    while True:
        try:
            url = f"{relay_url.rstrip('/')}/{agent_id}"
            async with connect(
                url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=20,
            ) as websocket:
                await websocket.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "metadata": {
                                "provider": os.getenv("LLM_PROVIDER", "unknown"),
                                "model": os.getenv("OLLAMA_MODEL", "unknown"),
                            },
                        }
                    )
                )
                delay = 1.0
                async for raw_message in websocket:
                    message = json.loads(raw_message)
                    if message.get("type") != "task":
                        continue
                    result = await execute_local_task(
                        communicator,
                        local_agent_url,
                        message,
                    )
                    await websocket.send(json.dumps(result, ensure_ascii=False))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Relay connection failed: {exc}; retrying in {delay:.0f}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Connect a local OPC Agent to Relay")
    parser.add_argument("--agent-id", default=os.getenv("OPC_NODE_AGENT_ID"))
    parser.add_argument("--relay-url", default=os.getenv("OPC_RELAY_URL"))
    parser.add_argument("--relay-token", default=os.getenv("OPC_RELAY_TOKEN", ""))
    parser.add_argument("--local-agent-url", default=os.getenv("OPC_LOCAL_AGENT_URL"))
    args = parser.parse_args()
    if not args.agent_id or not args.relay_url or not args.local_agent_url:
        parser.error("agent-id, relay-url and local-agent-url are required")
    try:
        asyncio.run(
            run_node(
                relay_url=args.relay_url,
                relay_token=args.relay_token,
                agent_id=args.agent_id,
                local_agent_url=args.local_agent_url.rstrip("/"),
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
