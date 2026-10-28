"""Fake model endpoint that records what each harness sends.

Point an agent at http://<host>:<port>/<label>/v1 and every request body is
saved to <out>/<label>/NNNN.json. Each call is answered with a short final
reply ("Done.") so the harness ends quickly. Speaks OpenAI Chat Completions,
OpenAI Responses and Anthropic Messages, streaming or not.

Usage:
    python3 scripts/capture_proxy.py --port 8765 --out captures
"""

import argparse
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPLY = "Done."
REDACT = {"authorization", "x-api-key", "api-key", "cookie"}

_lock = threading.Lock()
_counters: dict[str, int] = {}


def _usage_openai() -> dict:
    return {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}


def chat_completion(model: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": REPLY},
                "finish_reason": "stop",
            }
        ],
        "usage": _usage_openai(),
    }


def chat_stream(model: str) -> list[dict | str]:
    base = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }
    return [
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": REPLY}}]},
        {
            **base,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": _usage_openai(),
        },
        "[DONE]",
    ]


def responses_body(model: str) -> dict:
    return {
        "id": f"resp_{uuid.uuid4().hex[:12]}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": [
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex[:12]}",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": REPLY, "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


def responses_stream(model: str) -> list[tuple[str, dict]]:
    body = responses_body(model)
    item = body["output"][0]
    created = {**body, "status": "in_progress", "output": []}
    return [
        ("response.created", {"type": "response.created", "response": created}),
        ("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {**item, "content": []}}),
        ("response.output_text.delta", {"type": "response.output_text.delta", "item_id": item["id"], "output_index": 0, "content_index": 0, "delta": REPLY}),
        ("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item}),
        ("response.completed", {"type": "response.completed", "response": body}),
    ]


def anthropic_body(model: str) -> dict:
    return {
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": REPLY}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def anthropic_stream(model: str) -> list[tuple[str, dict]]:
    body = anthropic_body(model)
    start = {**body, "content": [], "stop_reason": None}
    return [
        ("message_start", {"type": "message_start", "message": start}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": REPLY}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 1}}),
        ("message_stop", {"type": "message_stop"}),
    ]


class Handler(BaseHTTPRequestHandler):
    out_dir: Path

    def log_message(self, *_):
        pass

    def _label(self) -> str:
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        return parts[0] if parts else "_root"

    def _record(self, body: dict | None, raw: bytes) -> None:
        label = self._label()
        with _lock:
            _counters[label] = _counters.get(label, 0) + 1
            index = _counters[label]
        directory = self.out_dir / label
        directory.mkdir(parents=True, exist_ok=True)
        headers = {
            k: ("<redacted>" if k.lower() in REDACT else v) for k, v in self.headers.items()
        }
        record = {
            "time": time.time(),
            "method": self.command,
            "path": self.path,
            "headers": headers,
            "body": body if body is not None else raw.decode("utf-8", "replace"),
        }
        (directory / f"{index:04d}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2)
        )

    def _send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_sse(self, events: list) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for event in events:
            if isinstance(event, str):
                chunk = f"data: {event}\n\n"
            elif isinstance(event, tuple):
                chunk = f"event: {event[0]}\ndata: {json.dumps(event[1])}\n\n"
            else:
                chunk = f"data: {json.dumps(event)}\n\n"
            self.wfile.write(chunk.encode())
            self.wfile.flush()

    def do_GET(self):
        self._record(None, b"")
        self._send_json(
            {"object": "list", "data": [{"id": "capture-model", "object": "model", "owned_by": "capture"}]}
        )

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = None
        self._record(body, raw)
        body = body if isinstance(body, dict) else {}
        model = body.get("model") or "capture-model"
        stream = bool(body.get("stream"))
        path = self.path.split("?")[0]
        if path.endswith("/messages"):
            self._send_sse(anthropic_stream(model)) if stream else self._send_json(anthropic_body(model))
        elif path.endswith("/responses"):
            self._send_sse(responses_stream(model)) if stream else self._send_json(responses_body(model))
        else:
            self._send_sse(chat_stream(model)) if stream else self._send_json(chat_completion(model))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out", default="captures")
    args = parser.parse_args()
    Handler.out_dir = Path(args.out)
    Handler.out_dir.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"capture proxy on {args.host}:{args.port} -> {args.out}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
