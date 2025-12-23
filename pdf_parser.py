from pathlib import Path
import re
import shutil
import pdfplumber
import os
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv


load_dotenv()


class PdfParser:
    def __init__(self):
        self.project_root = Path.cwd()
        self.logs_dir = self.project_root / "logs"
        self.logs_dir.mkdir(exist_ok=True)

        self.logger = self._setup_logger()

        self.base_dir = Path(os.getenv("PATH_BASE"))
        self.final_dir = Path(os.getenv("PATH_FINAL"))

        if not self.base_dir.exists():
            self.logger.error("PATH_BASE não existe: %s", self.base_dir)
            raise FileNotFoundError("PATH_BASE não existe")

        self.final_dir.mkdir(exist_ok=True, parents=True)

        self.info: dict[str, str] | None = None
        self.ano: str | None = None
        self.mes: str | None = None

        self.logger.info("PdfParser inicializado com sucesso")


    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger("pdf_parser")
        logger.setLevel(logging.DEBUG)

        if logger.handlers:
            return logger  

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # log em arquivo
        file_handler = RotatingFileHandler(
            self.logs_dir / "pdf_parser.log",
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)

        # log no terminal
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

        meses_abrev = {
            "01": "jan", "02": "fev", "03": "mar",
            "04": "abr", "05": "mai", "06": "jun",
            "07": "jul", "08": "ago", "09": "set",
            "10": "out", "11": "nov", "12": "dez",
        }

        return ano, meses_abrev[mes]


    @staticmethod
    def extract_text(receipt_path: Path) -> list[str]:
        text = ""

        with pdfplumber.open(receipt_path) as pdf:
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"

        return [line.strip() for line in text.splitlines() if line.strip()]


    def has_files(self) -> bool:
        for path in self.base_dir.iterdir():
            if not path.is_file():
                continue

            if path.suffix.lower() == ".pdf":
                return True

        return False


    def parse_receipt(self, lines: list[str]) -> dict[str, str]:
        info: dict[str, str] = {}

        first_line = lines[0].lower()
        self.logger.info("Iniciando parsing do comprovante")

        if "comprovante de transação" in first_line:
            self.logger.info("Tipo detectado: comprovante de transação")
            self._parse_mercado_pago_transacao(lines, info)

        elif "comprovante de pagamento" in first_line:
            self.logger.info("Tipo detectado: comprovante de pagamento")
            self._parse_comprovante_pagamento(lines, info)

        else:
            self.logger.warning("Tipo de comprovante não reconhecido")
            #colocar uma exception aqui

        if "beneficiario" in info:
            date = info.get("data_pagamento") or info.get("data_vencimento")
            if date:
                self.ano, self.mes = self.get_year_month(date)

        self.logger.info(f"Dados extraídos: {info}")
        return info

    def _parse_mercado_pago_transacao(self, lines: list[str], info: dict[str, str]):
        for i, line in enumerate(lines):
            lower = line.lower()

            if "beneficiário" in lower:
                beneficiario = lines[i + 1].split("DO")[0]
                info["beneficiario"] = self.clean_name(beneficiario)

            elif "você pagou a" in lower:
                info["beneficiario"] = self.clean_name(lines[i + 1])

            elif "vencimento do boleto" in lower:
                date = lines[i + 1].split()[0]
                info["data_vencimento"] = date.replace("/", "_")

    def _parse_comprovante_pagamento(self, lines: list[str], info: dict[str, str]):
        meses = {
            "janeiro": "01", "fevereiro": "02", "março": "03",
            "abril": "04", "maio": "05", "junho": "06",
            "julho": "07", "agosto": "08", "setembro": "09",
            "outubro": "10", "novembro": "11", "dezembro": "12",
        }

        for i, line in enumerate(lines):
            lower = line.lower()

            if "comprovante" in lower:
                match = re.search(
                    r"(\d{1,2}) de (\w+) de (\d{4})",
                    lines[i + 1].lower()
                )
                if match:
                    dia, mes, ano = match.groups()
                    info["data_pagamento"] = f"{dia.zfill(2)}_{meses[mes]}_{ano}"

            elif lower == "para":
                info["beneficiario"] = self.clean_name(lines[i + 1])

    

    def move_file(self, receipt: Path):
        if not self.info or not self.ano or not self.mes:
            self.logger.warning(f"Arquivo ignorado por falta de dados:{receipt.name}")
            return

        destino_dir = self.final_dir / self.ano / self.mes
        destino_dir.mkdir(parents=True, exist_ok=True)

        data = self.info.get("data_pagamento") or self.info.get("data_vencimento")
        new_name = f"{self.info['beneficiario']}_{data}.pdf"

        destino_final = destino_dir / new_name
        shutil.move(receipt, destino_final)

        self.logger.info(f"Arquivo movido para: {destino_final}")


    def run(self):
        receipts = list(self.base_dir.glob("*.pdf"))
        self.logger.info(f"Encontrados {len(receipts)} PDFs para processar")

        for receipt in receipts:
            self.logger.info(f"Processando: {receipt.name}")
            lines = self.extract_text(receipt)
            self.info = self.parse_receipt(lines)
            self.move_file(receipt)
