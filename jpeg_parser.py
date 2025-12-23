from pathlib import Path
import shutil
import re
import os
import logging
from logging.handlers import RotatingFileHandler
from PIL import Image
import pytesseract
from dotenv import load_dotenv


load_dotenv()


class JpegParser:
    def __init__(self):
        
        self.project_root = Path.cwd()
        self.logs_dir = self.project_root / "logs"
        self.logs_dir.mkdir(exist_ok=True)

        self.logger = self._setup_logger()

        self.base_dir = Path(os.getenv("PATH_BASE"))
        self.final_dir = Path(os.getenv("PATH_FINAL"))

        self.info: dict[str, str] | None = None
        self.ano: str | None = None
        self.mes: str | None = None


    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger("jpeg_parser")
        logger.setLevel(logging.DEBUG)

        if logger.handlers:
            return logger

        logs_dir = Path.cwd() / "logs"
        logs_dir.mkdir(exist_ok=True)

        formatter = logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        file_handler = RotatingFileHandler(
            logs_dir / "jpeg_parser.log",
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

        return logger


    @staticmethod
    def clean_name(name: str) -> str:
        name = re.sub(r"[^A-Za-zÀ-ÿ ]+", "", name)
        name = re.sub(r"\s+", " ", name).strip()
        return name.replace(" ", "-")

    @staticmethod
    def get_year_month(date: str) -> tuple[str, str]:
        _, mes, ano = date.split("_")

        meses = {
            "01": "jan", "02": "fev", "03": "mar",
            "04": "abr", "05": "mai", "06": "jun",
            "07": "jul", "08": "ago", "09": "set",
            "10": "out", "11": "nov", "12": "dez",
        }

        return ano, meses[mes]

    @staticmethod
    def convert_ocr_date(date: str) -> str:
        meses = {
            "JAN": "01", "FEV": "02", "MAR": "03",
            "ABR": "04", "MAI": "05", "JUN": "06",
            "JUL": "07", "AGO": "08", "SET": "09",
            "OUT": "10", "NOV": "11", "DEZ": "12",
        }

        match = re.search(
            r"(\d{2})\s+(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+(\d{4})",
            date,
        )

        if not match:
            raise ValueError("Data OCR inválida")

        dia, mes, ano = match.groups()
        return f"{dia}_{meses[mes]}_{ano}"


    def has_files(self) -> bool:
        for path in self.base_dir.iterdir():
            if not path.is_file():
                continue

            if path.suffix.lower() in (".jpg", ".jpeg"):
                return True

        return False


    def extract_text(self, image_path: Path) -> list[str]:
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img, lang="por")
        return [line.strip() for line in text.splitlines() if line.strip()]


    def parse_receipt(self, lines: list[str]) -> dict[str, str]:
        info: dict[str, str] = {}

        date_pattern = re.compile(
            r"\b\d{2}\s+(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+\d{4}\s*-\s*\d{2}:\d{2}:\d{2}\b"
        )

        for i, line in enumerate(lines):
            match = date_pattern.search(line)
            if match:
                info["data_pagamento"] = self.convert_ocr_date(match.group())

            if "destino" in line.lower():
                target = lines[i + 2]

                if "nome" in target.lower():
                    info["beneficiario"] = self.clean_name(target.split("Nome")[-1])

                elif "favorec" in target.lower():
                    info["beneficiario"] = self.clean_name(target.split("Favorec")[-1])

        if "beneficiario" in info and "data_pagamento" in info:
            self.ano, self.mes = self.get_year_month(info["data_pagamento"])

        self.logger.info(f"Dados extraídos OCR: {info}")
        return info

    

    def move_file(self, image_path: Path):
        if not self.info or not self.ano or not self.mes:
            self.logger.warning(f"JPEG ignorado: {image_path.name}")
            return

        destino_dir = self.final_dir / self.ano / self.mes
        destino_dir.mkdir(parents=True, exist_ok=True)

        new_name = f"{self.info['beneficiario']}_{self.info['data_pagamento']}.jpeg"
        destino_final = destino_dir / new_name

        shutil.move(image_path, destino_final)
        self.logger.info(f"JPEG movido para {destino_final}")


    def run(self):
        images = [
        p for p in self.base_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg"}
        ]

        self.logger.info(f"Encontrados {len(images)} JPEGs")

        for image in images:
            self.logger.info(f"Processando OCR: {image.name}")
            lines = self.extract_text(image)
            self.info = self.parse_receipt(lines)
            self.move_file(image)
