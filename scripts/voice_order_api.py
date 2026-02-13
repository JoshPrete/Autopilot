"""Run the Voice Order sidecar API."""

import os

import uvicorn


if __name__ == "__main__":
    host = os.environ.get("VOICE_API_HOST", "0.0.0.0")
    port = int(os.environ.get("VOICE_API_PORT", "8000"))
    uvicorn.run("voice_order.app:app", host=host, port=port, reload=True)
