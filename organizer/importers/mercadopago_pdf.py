import re
from dataclasses import replace
from datetime import datetime

from organizer.models import Extrato, Transacao, valor_br

# 04-09-2026 Dinheiro reservado Aluguel 176356820745 R$ -2.100,00 R$ 3,58
LINHA = re.compile(r"^(\d{2}-\d{2}-\d{4})\s+(.*?)\s*(\d{9,})\s+R\$\s*(-?[\d.]+,\d{2})\s+R\$\s*(-?[\d.]+,\d{2})$")
PERIODO = re.compile(r"Periodo: De (\d{2}-\d{2}-\d{4}) al (\d{2}-\d{2}-\d{4})")
CONTA = re.compile(r"Agência: (\d+) Conta: (\d+)")
CABECALHO = "Data Descrição ID da operação Valor Saldo"
FIM_DA_PAGINA = re.compile(r"^(\d+/\d+|Data de geração:.*)$")


def eh_mercadopago(linhas: list[str]) -> bool:
    texto = "\n".join(linhas)
    return "EXTRATO DE CONTA" in texto and "Mercado Pago" in texto


def _data(valor: str):
    return datetime.strptime(valor, "%d-%m-%Y").date()


def _linhas_da_tabela(linhas: list[str]) -> list[str]:
    """Só as linhas entre o cabeçalho da tabela e o rodapé de cada página."""
    dentro = False
    tabela = []

    for linha in linhas:
        if linha == CABECALHO:
            dentro = True
        elif FIM_DA_PAGINA.match(linha):
            dentro = False
        elif dentro:
            tabela.append(linha)

    return tabela


def parse_mercadopago(linhas: list[str]) -> Extrato:
    texto = "\n".join(linhas)

    periodo = PERIODO.search(texto)
    if not periodo:
        raise ValueError("Extrato Mercado Pago sem período")

    conta_match = CONTA.search(texto)
    conta = f"{conta_match.group(1)}/{conta_match.group(2)}" if conta_match else ""

    # Descrições longas quebram em volta da linha com data e valor, centralizadas:
    # tantas linhas antes quanto depois. Ex.:
    #
    #   Pix recebido FULANA DE                           <- antes
    #   04-09-2026 176253885921 R$ 710,00 R$ 713,49      <- linha principal
    #   TAL DA SILVA                                     <- depois
    #
    # Então guardamos as linhas soltas até achar a linha principal, e depois dela
    # as próximas N linhas soltas (N = quantas vieram antes) completam a descrição.
    transacoes = []
    linhas_antes: list[str] = []
    faltam_depois = 0

    for linha in _linhas_da_tabela(linhas):
        match = LINHA.match(linha)

        if not match:
            if faltam_depois > 0:
                ultima = transacoes[-1]
                transacoes[-1] = replace(ultima, descricao=f"{ultima.descricao} {linha}")
                faltam_depois -= 1
            else:
                linhas_antes.append(linha)
            continue

        data, meio_da_descricao, id_operacao, valor, _saldo = match.groups()
        descricao = " ".join([*linhas_antes, meio_da_descricao]).strip()

        transacoes.append(
            Transacao(
                banco="mercadopago",
                conta=conta,
                data=_data(data),
                valor=valor_br(valor),
                descricao=descricao,
                id_externo=id_operacao,
            )
        )

        faltam_depois = len(linhas_antes)
        linhas_antes = []

    return Extrato(
        banco="mercadopago",
        conta=conta,
        inicio=_data(periodo.group(1)),
        fim=_data(periodo.group(2)),
        transacoes=transacoes,
    )
