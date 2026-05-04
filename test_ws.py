import asyncio
import websockets

async def test():
    try:
        async with websockets.connect('ws://206.72.198.54:5002?token=super_secret_token_2026') as ws:
            print("Connected successfully!")
            msg = await ws.recv()
            print("Received:", msg[:100])
    except Exception as e:
        print("Error:", e)

asyncio.run(test())
