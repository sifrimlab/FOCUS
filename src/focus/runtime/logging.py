"""Configure the existing console/file handlers and dataset log path."""

import os
import logging


def setup_logging(
    dataset_path: str | None = None, debug: bool = False
) -> logging.Logger:
    """
    Configure the 'focus' logger with a console handler and optionally a file handler.

    Can be called in two phases: first with dataset_path=None to install a
    console-only handler (useful before the config is validated and dataset_path
    is known), then again with a real dataset_path to add the file handler.

    Parameters
    ----------
    dataset_path : str or None
            Directory where focus.log is written.  If None, only the console
            handler is installed (no file handler).
    debug : bool
            If True, the console handler emits DEBUG-level messages and werkzeug
            HTTP request logs are shown.  If False (default), the console shows
            INFO and above only, and werkzeug is silenced.

    Returns the configured logger.
    """
    logger = logging.getLogger("focus")
    logger.setLevel(logging.DEBUG)

    # Add console handler only if one is not already present
    has_console = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logger.handlers
    )
    if not has_console:
        console = logging.StreamHandler()
        console.setLevel(logging.DEBUG if debug else logging.INFO)
        console.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
            )
        )
        logger.addHandler(console)

    # Add file handler only when dataset_path is known and no file handler exists yet
    if dataset_path is not None:
        has_file = any(isinstance(h, logging.FileHandler) for h in logger.handlers)
        if not has_file:
            log_file = os.path.join(dataset_path, "focus.log")
            file_handler = logging.FileHandler(log_file, mode="a")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
                )
            )
            logger.addHandler(file_handler)

    # Silence werkzeug HTTP request logs unless in debug mode
    logging.getLogger("werkzeug").setLevel(logging.DEBUG if debug else logging.WARNING)

    return logger
