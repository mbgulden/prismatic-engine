import asyncio
import json
import websockets
import httpx
import time
import subprocess
import os

async def test_gateway():
    port = 8083
    token = "test-token-123"

    # Start the gateway server in the background with a specific token
    process = subprocess.Popen(
        ["python3", "-m", "prismatic.dispatcher", "gateway", "--port", str(port)],
        env={**os.environ, "PYTHONPATH": ".", "PRISMATIC_API_TOKEN": token},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # Wait for the server to start
    time.sleep(5)

    try:
        # 1. Connect to WebSocket with token
        ws_url = f"ws://127.0.0.1:{port}/api/v1/events/ws?token={token}"
        async with websockets.connect(ws_url) as websocket:
            print("Connected to WebSocket")

            # 2. Ingest an event via REST API
            ingest_url = f"http://127.0.0.1:{port}/api/v1/events/ingest"
            event_data = {
                "event": "launched",
                "agent_name": "jules",
                "issue_id": "GRO-1567",
                "title": "Test Event",
                "message": "This is a test event"
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    ingest_url,
                    json=event_data,
                    headers={"Authorization": f"Bearer {token}"}
                )
                print(f"Ingest Response: {response.status_code}")
                assert response.status_code == 202

            # 3. Receive event via WebSocket
            received_msg = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            received_event = json.loads(received_msg)
            print(f"Received Event: {received_event['event']}")

            assert received_event["event"] == "launched"
            assert received_event["agent_name"] == "jules"
            assert received_event["issue_id"] == "GRO-1567"
            assert "timestamp" in received_event

            # 4. Test unauthorized WebSocket
            unauth_ws_url = f"ws://127.0.0.1:{port}/api/v1/events/ws?token=wrong"
            try:
                async with websockets.connect(unauth_ws_url) as unauth_ws:
                    msg = await unauth_ws.recv()
                    err = json.loads(msg)
                    assert err["error"] == "Unauthorized"
                    print("Unauthorized WebSocket access blocked correctly")
            except websockets.exceptions.ConnectionClosedOK:
                pass

            print("Verification successful!")

    finally:
        process.terminate()
        process.wait()

if __name__ == "__main__":
    asyncio.run(test_gateway())
