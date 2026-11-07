#!/usr/bin/env python
"""Tiny CDP client for the NapCatQQ Desktop WebView2 (Tauri) window.

Usage:
  python scripts/cdp.py js "<expression>"      # evaluate JS, print JSON result
  python scripts/cdp.py shot <out.png>         # screenshot to file
  python scripts/cdp.py click "<css selector>" # click first matching element
  python scripts/cdp.py text                   # dump visible text
"""
import asyncio
import base64
import json
import sys

import websockets

PORT = 9222


async def get_ws_url() -> str:
    import urllib.request

    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=5) as r:
        targets = json.load(r)
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise SystemExit("no page target")
    return pages[0]["webSocketDebuggerUrl"]


class Client:
    def __init__(self, ws):
        self.ws = ws
        self.id = 0

    async def call(self, method, **params):
        self.id += 1
        mid = self.id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=30))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise SystemExit(f"CDP error: {msg['error']}")
                return msg.get("result", {})

    async def eval(self, expr, await_promise=True):
        res = await self.call(
            "Runtime.evaluate",
            expression=expr,
            returnByValue=True,
            awaitPromise=await_promise,
            userGesture=True,
        )
        if res.get("exceptionDetails"):
            return {"exception": res["exceptionDetails"].get("text"),
                    "detail": str(res["exceptionDetails"].get("exception", {}).get("description", ""))[:500]}
        return res.get("result", {}).get("value")


async def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd = sys.argv[1]
    async with websockets.connect(await get_ws_url(), max_size=64 * 1024 * 1024) as ws:
        c = Client(ws)
        if cmd == "js":
            out = await c.eval(sys.argv[2])
            print(json.dumps(out, ensure_ascii=False, indent=1) if not isinstance(out, str) else out)
        elif cmd == "text":
            out = await c.eval("document.body.innerText")
            print(out)
        elif cmd == "click":
            sel = sys.argv[2]
            out = await c.eval(
                "(function(){const el=document.querySelector(%s);if(!el)return 'NOT FOUND';"
                "el.scrollIntoView({block:'center'});el.click();return 'clicked: '+(el.innerText||el.outerHTML).slice(0,60);})()"
                % json.dumps(sel)
            )
            print(out)
        elif cmd == "tap":
            x, y = float(sys.argv[2]), float(sys.argv[3])
            for kind in ("mousePressed", "mouseReleased"):
                await c.call(
                    "Input.dispatchMouseEvent",
                    type=kind,
                    x=x,
                    y=y,
                    button="left",
                    clickCount=1,
                    buttons=1 if kind == "mousePressed" else 0,
                )
                await asyncio.sleep(0.05)
            print(f"tapped {x},{y}")
        elif cmd == "shot":
            res = await c.call("Page.captureScreenshot", format="png")
            data = base64.b64decode(res["data"])
            with open(sys.argv[2], "wb") as f:
                f.write(data)
            print(f"saved {sys.argv[2]} {len(data)}B")
        else:
            raise SystemExit(__doc__)


asyncio.run(main())
