import re
from collections import Counter
from datetime import datetime

from organizer.models import Extrato, Transacao, valor_br

LINHA = re.compile(r"^(\d{2}/\d{2}/\d{4}) (.+?) (-?[\d.]+,\d{2})$")
PERIODO = re.compile(r"período de visualização: (\d{2}/\d{2}/\d{4}) até (\d{2}/\d{2}/\d{4})")
CONTA = re.compile(r"agência: (\d+) conta: ([\d-]+)")


def eh_itau(linhas: list[str]) -> bool:
    texto = "\n".join(linhas).lower()
    return "extrato conta / lançamentos" in texto and "itau" in texto


def _data(valor: str):
    return datetime.strptime(valor, "%d/%m/%Y").date()


def parse_itau(linhas: list[str]) -> Extrato:
    texto = "\n".join(linhas)

    periodo = PERIODO.search(texto)
    if not periodo:
        raise ValueError("Extrato Itaú sem período de visualização")

    conta_match = CONTA.search(texto)
    conta = f"{conta_match.group(1)}/{conta_match.group(2)}" if conta_match else ""

    transacoes = []
    # O PDF não tem id de operação. Para reimportações não duplicarem (e lançamentos
    # idênticos no mesmo dia não se perderem) o id é a ocorrência dentro do arquivo.
    ocorrencias: Counter[str] = Counter()

    for linha in linhas:
        match = LINHA.match(linha)
        if not match:
            continue

        data, descricao, valor = match.groups()
        if descricao.startswith("SALDO DO DIA"):
            continue

        base = f"{data}|{descricao}|{valor}"
        ocorrencias[base] += 1

        transacoes.append(
            Transacao(
                banco="itau",
                conta=conta,
                data=_data(data),
                valor=valor_br(valor),
                descricao=descricao,
                id_externo=f"{base}|{ocorrencias[base]}",
            )
        )

    return Extrato(
        banco="itau",
        conta=conta,
        inicio=_data(periodo.group(1)),
        fim=_data(periodo.group(2)),
        transacoes=transacoes,
    )
