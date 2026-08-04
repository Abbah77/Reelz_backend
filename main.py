"""
Entry point — run with:
  python main.py
or directly:
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --loop uvloop --http httptools
"""
import uvicorn
from app.config import get_settings

if __name__ == "__main__":
    s = get_settings()
    uvicorn.run(
        "app.main:app",
        host=s.host,
        port=s.port,
        workers=s.workers,
        loop="uvloop",
        http="httptools",
        log_level="debug" if s.debug else "info",
        access_log=s.debug,
        reload=s.debug,
    )
