"""One local command: admin site plus the publisher worker."""

from __future__ import annotations

import logging
import signal
import subprocess
import sys

import uvicorn

from app.config import get_settings

log = logging.getLogger("parnaya.dev")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    worker = subprocess.Popen([sys.executable, "-m", "app.worker"])
    log.info("publisher worker pid=%s", worker.pid)
    try:
        uvicorn.run("app.main:app", host=settings.host, port=settings.port)
    finally:
        worker.send_signal(signal.SIGTERM)
        try:
            worker.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker.kill()


if __name__ == "__main__":
    main()
