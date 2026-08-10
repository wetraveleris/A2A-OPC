#!/usr/bin/env python3
"""Minimal A2A 1.0 JSON-RPC client using only the Python standard library."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


def request_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    encoded_body = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        url,
        data=encoded_body,
        method=method,
        headers={"Accept": "application/json", **(headers or {})},
    )
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def find_jsonrpc_interface(agent_card: dict[str, Any]) -> dict[str, Any]:
    for interface in agent_card.get("supportedInterfaces", []):
        if interface.get("protocolBinding") == "JSONRPC":
            return interface
    raise ValueError("The Agent Card does not advertise a JSONRPC interface")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="?", default="Hello from the raw A2A client")
    parser.add_argument("--agent", default="http://127.0.0.1:9999")
    args = parser.parse_args()

    base_url = args.agent.rstrip("/") + "/"
    try:
        card = request_json(urljoin(base_url, ".well-known/agent-card.json"))
        interface = find_jsonrpc_interface(card)
        protocol_version = interface.get("protocolVersion", "1.0")

        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "SendMessage",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "role": "ROLE_USER",
                    "parts": [{"text": args.message}],
                }
            },
        }
        result = request_json(
            interface["url"],
            method="POST",
            body=payload,
            headers={
                "A2A-Version": protocol_version,
                "Content-Type": "application/json",
            },
        )
    except (HTTPError, URLError, ValueError, KeyError) as error:
        print(f"A2A request failed: {error}", file=sys.stderr)
        return 1

    print("Agent Card:")
    print(json.dumps(card, ensure_ascii=False, indent=2))
    print("\nSendMessage response:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
