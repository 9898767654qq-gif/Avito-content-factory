"""Admin process: brief desk, approval, and the factory rework loop."""

from __future__ import annotations

import logging

import uvicorn

from app.factory_app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = create_app()


def main() -> None:
    settings = app.state.settings
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
