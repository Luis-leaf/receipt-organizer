from pathlib import Path
import os
import sys
from dotenv import load_dotenv

from pdf_parser import PdfParser
from jpeg_parser import JpegParser


load_dotenv()


LOCK_FILE = Path.cwd() / ".process.lock"


def acquire_lock():
    if LOCK_FILE.exists():
        print("Processo já em execução. Abortando.")
        sys.exit(1)

    LOCK_FILE.write_text(str(os.getpid()))


def release_lock():
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()


def main():
    base_dir = Path(os.getenv("PATH_BASE"))

    if not base_dir.exists():
        raise FileNotFoundError("PATH_BASE inválido")

    pdf_parser = PdfParser()
    jpeg_parser = JpegParser()
    

    if pdf_parser.has_files():
        pdf_parser.run()

    if jpeg_parser.has_files():
        jpeg_parser.run()


if __name__ == "__main__":
    acquire_lock()

    try:
        main()
    finally:
        release_lock()
