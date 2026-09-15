"""Convenience entrypoint for running the FastAPI development server."""

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv


if __name__ == "__main__":
    backend_dir = Path(__file__).resolve().parent
    # Shell settings win; local settings override shared Compose defaults.
    load_dotenv(backend_dir / ".env")
    load_dotenv(backend_dir.parent / ".env")
    os.chdir(backend_dir)
    uvicorn.run(
        "apps.api.app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
