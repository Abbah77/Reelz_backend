"""
Entry point — run with:
  python main.py
or via uvicorn directly:
  uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1 --loop uvloop --http httptools
"""
import uvicorn
from app.config import get_settings

if __name__ == "__main__":
    s = get_settings()
    uvicorn.run(
        "app:app",
        host=s.host,
        port=s.port,
        workers=s.workers,
        loop="uvloop",          # libuv-backed event loop — same speed as Node's libuv
        http="httptools",       # C-level HTTP parser — same library Node uses internally
        log_level="debug" if s.debug else "info",
        access_log=s.debug,
        reload=s.debug,
        # h11 fallback for HTTP/1.1; httptools handles HTTP/1.x
        # HTTP/2 is handled at the client layer (httpx)
    )
