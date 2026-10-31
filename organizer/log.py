import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_logger(name: str = "organizer") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    logs_dir = Path(os.getenv("LOG_DIR", Path.cwd() / "logs"))
    logs_dir.mkdir(exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # log em arquivo
    file_handler = RotatingFileHandler(
        logs_dir / "organizer.log",
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    # log no terminal (só avisos, o relatório é a saída principal)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger
