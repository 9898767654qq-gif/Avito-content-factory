"""Publisher process. Talks to Avito (or the dry-run client) and never to chats."""

from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.database import init_db, make_engine, make_session_factory
from app.services.avito_client import build_avito_client
from app.services.publisher import process_publisher_queue

log = logging.getLogger("parnaya.publisher")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    session_factory = make_session_factory(engine)
    client = build_avito_client(settings)
    log.info("publisher worker started, mode=%s", client.mode)
    while True:
        processed = False
        try:
            with session_factory() as session:
                processed = process_publisher_queue(session, client)
        except Exception:
            log.exception("publisher tick failed")
        if not processed:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
