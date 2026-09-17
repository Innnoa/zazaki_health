#!/usr/bin/env python3
"""PC-side receiver for the Galaxy Watch 7 heart-rate stream.

Listens on ws://0.0.0.0:8765. Each JSON text frame is printed to stdout and
appended as one JSON line to data/hr_YYYYMMDD.jsonl (created on demand).

Only dependency: the `websockets` package (>=12, asyncio implementation).
"""
import argparse
import asyncio
import collections
import datetime
import json
from pathlib import Path

import websockets

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# T5-E: dedupe by frame timestamp t. On a WS reconnect the watch replays from its
# watermark, which can overlap frames the PC already wrote. t is a millisecond epoch
# from a single watch, and delivery is ordered (replay ascending, then live ascending),
# so a bounded ring of recently seen unique t values is enough to drop exact duplicates.
# The ring only ever holds unique t (duplicates are skipped before insertion), so deque
# eviction order == insertion order and the set is pruned in sync.
DEDUPE_WINDOW = 8192
_seen_t_deque = collections.deque()
_seen_t_set = set()


def _mark_seen(t: int) -> bool:
    """Return True if t was already seen (duplicate); otherwise record it and return False."""
    if t in _seen_t_set:
        return True
    if len(_seen_t_deque) >= DEDUPE_WINDOW:
        _seen_t_set.discard(_seen_t_deque.popleft())
    _seen_t_deque.append(t)
    _seen_t_set.add(t)
    return False


def now_str() -> str:
    return datetime.datetime.now().isoformat(timespec="milliseconds")


def log_line(json_text: str) -> None:
    """Append one raw JSON line to data/hr_YYYYMMDD.jsonl (serialized by the event loop)."""
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    out = DATA_DIR / f"hr_{stamp}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json_text + "\n")


async def handle_client(websocket) -> None:
    print(f"[{now_str()}] client connected: {websocket.remote_address}", flush=True)
    try:
        async for raw in websocket:
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"[{now_str()}] dropped non-JSON frame from "
                      f"{websocket.remote_address}: {exc}", flush=True)
                continue
            t = frame.get("t") if isinstance(frame, dict) else None
            if not isinstance(t, int):
                # Cannot dedupe or timestamp a frame without an integer t; log and skip (T5-E).
                print(f"[{now_str()}] dropped frame without integer 't': "
                      f"{json.dumps(frame, ensure_ascii=False)}", flush=True)
                continue
            if _mark_seen(t):
                continue  # replay-overlap duplicate, already written (T5-E)
            line = json.dumps(frame, ensure_ascii=False)
            log_line(line)
            print(f"[{now_str()}] {line}", flush=True)
    finally:
        print(f"[{now_str()}] client disconnected: {websocket.remote_address}", flush=True)


async def main(host: str, port: int) -> None:
    print(f"[{now_str()}] listening on ws://{host}:{port}", flush=True)
    async with websockets.serve(handle_client, host, port):
        await asyncio.Future()  # run forever; CancelledError on Ctrl-C stops the server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HR WebSocket receiver -> JSONL")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        asyncio.run(main(args.host, args.port))
    except KeyboardInterrupt:
        pass
