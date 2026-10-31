from pathlib import Path

import pdfplumber

from organizer.importers.itau_pdf import eh_itau, parse_itau
from organizer.importers.mercadopago_pdf import eh_mercadopago, parse_mercadopago
from organizer.importers.ofx import ler_texto, parse_ofx
from organizer.models import Extrato

EXTENSOES = {".ofx", ".pdf"}


def extrair_linhas_pdf(caminho: Path) -> list[str]:
    texto = ""
    with pdfplumber.open(caminho) as pdf:
        for page in pdf.pages:
            extraido = page.extract_text()
            if extraido:
                texto += extraido + "\n"
    return [linha.strip() for linha in texto.splitlines() if linha.strip()]


def ler_extrato(caminho: Path) -> Extrato:
    """Detecta o banco pelo conteúdo (o Mercado Pago gera nome de arquivo aleatório)."""
    sufixo = caminho.suffix.lower()

    if sufixo == ".ofx":
        return parse_ofx(ler_texto(caminho.read_bytes()))

    if sufixo == ".pdf":
        linhas = extrair_linhas_pdf(caminho)
        if eh_itau(linhas):
            return parse_itau(linhas)
        if eh_mercadopago(linhas):
            return parse_mercadopago(linhas)
        raise ValueError(f"PDF de banco não reconhecido: {caminho.name}")

    raise ValueError(f"Formato não suportado: {caminho.name}")
