import asyncio
import websockets


async def test():

    uri = "ws://127.0.0.1:8000/ws/risk"

    async with websockets.connect(uri) as websocket:

        print("✅ Connected to websocket")

        while True:
            msg = await websocket.recv()
            print(msg)


asyncio.run(test())