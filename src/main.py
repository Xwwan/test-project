"""Command-line entry point for the chat service.

Run with::

    conda activate toy
    python -m src.main --host 127.0.0.1 --port 8000

The server uses only the Python standard library and the local Person 1
SQLite database under ``data/app.db``. Person 2's dialogue agents are
required for live LLM calls; if they are not on this branch yet, point
``model_client`` to a mock by adjusting :func:`build_dependencies`.
"""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import suppress

from src.api.routes import build_app
from src.services import DialogueDependencies


logger = logging.getLogger("chat-service")


def build_dependencies() -> DialogueDependencies:
    """Hook for production wiring.

    Override this in deployment if you need a custom ``model_client`` or
    want to swap in alternative Memory / Persona implementations.
    """

    return DialogueDependencies()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the chat service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    server = build_app(args.host, args.port, dependencies=build_dependencies())
    logger.info("listening on http://%s:%d", args.host, args.port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutdown requested")
    finally:
        with suppress(Exception):
            server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
