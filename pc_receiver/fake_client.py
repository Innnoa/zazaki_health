#!/usr/bin/env python3
"""Sends 5 fake heart-rate frames to the receiver for end-to-end testing.

Usage:  python3 fake_client.py [ws://127.0.0.1:8765]
"""
import asyncio
import json
import sys
import time

import websockets

FRAMES = [
    {"t": int(time.time() * 1000), "hr": 79, "status": 1, "ibi": [812, 799, 824]},
    {"t": int(time.time() * 1000) + 1000, "hr": 81, "status": 1, "ibi": [741, 755, 768]},
    {"t": int(time.time() * 1000) + 2000, "hr": 80, "status": 1, "ibi": [750, 762]},
    {"t": int(time.time() * 1000) + 3000, "hr": 79, "status": 1, "ibi": [758, 771, 749]},
    {"t": int(time.time() * 1000) + 4000, "hr": 78, "status": 1, "ibi": [769, 780]},
]


async def main(url: str) -> None:
    async with websockets.connect(url) as ws:
        for frame in FRAMES:
            await ws.send(json.dumps(frame))
            print(f"sent frame hr={frame['hr']} status={frame['status']} "
                  f"ibi_count={len(frame['ibi'])}", flush=True)
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8765"))
