"""Compras recorrentes no cartão de crédito (assinaturas, mensalidades, planos...).

Não precisa de cadastro: as recorrentes são descobertas nas últimas faturas importadas.
Uma compra é recorrente quando o mesmo estabelecimento cobra o mesmo valor (com pequena
variação) mais ou menos no mesmo dia, de mês em mês. Parcelas ficam de fora.
"""

import re
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from organizer.models import Transacao, normalizar

# Quantas faturas (as mais recentes) entram na análise
FATURAS_ANALISADAS = 3

# Reajuste e IOF mudam um pouco o valor; acima disso é outra compra
VARIACAO_VALOR = Decimal("0.05")

# Distância entre duas cobranças seguidas da mesma assinatura
INTERVALO_MINIMO = 25
INTERVALO_MAXIMO = 35
# ...e quanto o dia do mês pode mudar (dia 31 e dia 1 contam como vizinhos)
DIFERENCA_DE_DIA = 3

# Se a próxima cobrança já deveria estar na última fatura importada e não está,
# a assinatura parou. A folga cobre cobranças que escorregam alguns dias.
FOLGA_PARA_PARAR = timedelta(days=4)

PARCELA = re.compile(r"\bPARCELA \d+/\d+|\bPARC\b")
# Linhas da fatura que não são compras
NAO_E_COMPRA = re.compile(r"^IOF\b|^PAGAMENTO RECEBIDO|^AJUSTE A CREDITO")
# Intermediador de pagamento antes do nome da loja: "Ebn *Spotify", "Pg *Nio Fibra", "Mp *Melimais"
INTERMEDIADOR = re.compile(r"^[A-Za-z]{1,4} ?\*\s*")


def somar_meses(dia: date, meses: int) -> date:
    """Mesmo dia `meses` depois; dia 31 vira o último dia em meses mais curtos."""
    total = dia.year * 12 + dia.month - 1 + meses
    ano, mes = divmod(total, 12)
    mes += 1
    return date(ano, mes, min(dia.day, monthrange(ano, mes)[1]))


def estabelecimento(descricao: str) -> str:
    """Chave para juntar as cobranças da mesma loja.

    Usa a primeira palavra depois do intermediador, porque a descrição muda entre um
    mês e outro: "Dm *Spotify" / "Ebn *Spotify", "Google One" / "Google G1sk003m".
    Lojas diferentes com a mesma primeira palavra são separadas depois, pelo valor e pelo dia.
    """
    texto = INTERMEDIADOR.sub("", normalizar(descricao))
    palavra = re.match(r"[A-Z0-9]+", texto)
    return palavra.group(0) if palavra else texto


def nome_para_mostrar(descricao: str) -> str:
    """'Ebn *Spotify' -> 'Spotify'; 'Anthropic* Claude Sub' -> 'Anthropic Claude Sub'."""
    nome = INTERMEDIADOR.sub("", descricao.strip()) or descricao
    return re.sub(r"\s*\*\s*", " ", nome).strip()


def compras(transacoes: list[Transacao]) -> list[Transacao]:
    """Só as compras à vista: sem parcelas, IOF, pagamentos de fatura, ajustes e estornos."""
    debitos: list[Transacao] = []
    creditos: list[Transacao] = []

    for t in transacoes:
        descricao = normalizar(t.descricao)
        if NAO_E_COMPRA.search(descricao) or PARCELA.search(descricao):
            continue
        if t.valor < 0:
            debitos.append(t)
        else:
            creditos.append(t)

    # estorno: um crédito com a mesma descrição e valor cancela a compra
    for credito in creditos:
        for i, debito in enumerate(debitos):
            if debito.valor == -credito.valor and normalizar(debito.descricao) == normalizar(credito.descricao):
                del debitos[i]
                break

    return debitos


@dataclass
class Recorrente:
    banco: str
    cartao: str
    nome: str
    cobrancas: list[Transacao]
    ativa: bool = True

    @property
    def ultima(self) -> Transacao:
        return self.cobrancas[-1]

    @property
    def valor(self) -> Decimal:
        """Valor esperado (o da última cobrança), positivo."""
        return -self.ultima.valor

    def datas_previstas(self, ate: date) -> list[date]:
        """Próximas cobranças, a partir da última conhecida, antes de `ate`."""
        datas = []
        meses = 1
        while (proxima := somar_meses(self.ultima.data, meses)) < ate:
            datas.append(proxima)
            meses += 1
        return datas


def _agrupar_por_valor(cobrancas: list[Transacao]) -> list[list[Transacao]]:
    grupos: list[list[Transacao]] = []
    for t in sorted(cobrancas, key=lambda t: -t.valor):
        if grupos and -t.valor <= -grupos[-1][0].valor * (1 + VARIACAO_VALOR):
            grupos[-1].append(t)
        else:
            grupos.append([t])
    return grupos


def _dias_vizinhos(a: date, b: date) -> bool:
    diferenca = abs(a.day - b.day)
    return min(diferenca, 31 - diferenca) <= DIFERENCA_DE_DIA


def _separar_em_series(cobrancas: list[Transacao]) -> list[list[Transacao]]:
    """Encadeia as cobranças de mês em mês.

    Mesmo valor pode ter mais de uma série: a Vivo Easy cobra duas linhas, uma no
    dia 6 e outra no dia 29.
    """
    series: list[list[Transacao]] = []
    for t in sorted(cobrancas, key=lambda t: t.data):
        candidatas = [
            s
            for s in series
            if INTERVALO_MINIMO <= (t.data - s[-1].data).days <= INTERVALO_MAXIMO and _dias_vizinhos(t.data, s[-1].data)
        ]
        if candidatas:
            min(candidatas, key=lambda s: abs((t.data - s[-1].data).days - 30)).append(t)
        else:
            series.append([t])
    return series


def encontrar_recorrentes(
    transacoes: list[Transacao], faturas: dict[tuple[str, str], list[tuple[date, date]]]
) -> list[Recorrente]:
    """Recorrentes de cada cartão nas últimas FATURAS_ANALISADAS faturas.

    `transacoes` são os lançamentos das faturas; `faturas` vem de Storage.faturas().
    """
    recorrentes = []

    for (banco, cartao), periodos in faturas.items():
        ultimas = periodos[-FATURAS_ANALISADAS:]
        inicio, fim = ultimas[0][0], ultimas[-1][1]
        do_cartao = [t for t in transacoes if t.banco == banco and t.conta == cartao and inicio <= t.data <= fim]

        por_loja: dict[str, list[Transacao]] = {}
        for t in compras(do_cartao):
            por_loja.setdefault(estabelecimento(t.descricao), []).append(t)

        for cobrancas in por_loja.values():
            for mesmo_valor in _agrupar_por_valor(cobrancas):
                series = _separar_em_series(mesmo_valor)
                # Uma compra avulsa com o mesmo valor (o almoço de R$ 31,90 de sempre)
                # mostra que é consumo frequente, não assinatura.
                if any(len(s) < 2 for s in series):
                    continue
                for serie in series:
                    proxima = somar_meses(serie[-1].data, 1)
                    recorrentes.append(
                        Recorrente(
                            banco=banco,
                            cartao=cartao,
                            nome=nome_para_mostrar(serie[-1].descricao),
                            cobrancas=serie,
                            ativa=proxima >= fim - FOLGA_PARA_PARAR,
                        )
                    )

    return sorted(recorrentes, key=lambda r: (not r.ativa, r.ultima.data.day, r.nome))


@dataclass
class Previsao:
    """O que ainda vai cair na fatura aberta de um cartão."""

    banco: str
    cartao: str
    inicio: date  # primeiro dia da fatura aberta
    fechamento: date  # dia em que ela fecha (já não entra nela)
    ainda_vai_cair: list[tuple[date, Recorrente]] = field(default_factory=list)
    ja_deve_ter_caido: list[tuple[date, Recorrente]] = field(default_factory=list)
    # fechamento de faturas que já fecharam mas não foram importadas
    faturas_faltando: list[date] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((r.valor for _, r in self.ainda_vai_cair), Decimal(0))


def prever(
    recorrentes: list[Recorrente], faturas: dict[tuple[str, str], list[tuple[date, date]]], hoje: date
) -> list[Previsao]:
    """Para cada cartão, as recorrentes que caem na fatura aberta hoje."""
    previsoes = []

    for (banco, cartao), periodos in faturas.items():
        # a fatura seguinte começa no dia do fechamento da última importada
        ultimo_fechamento = periodos[-1][1] + timedelta(days=1)
        meses = 0
        while somar_meses(ultimo_fechamento, meses + 1) <= hoje:
            meses += 1

        previsao = Previsao(
            banco=banco,
            cartao=cartao,
            inicio=somar_meses(ultimo_fechamento, meses),
            fechamento=somar_meses(ultimo_fechamento, meses + 1),
            faturas_faltando=[somar_meses(ultimo_fechamento, m) for m in range(1, meses + 1)],
        )

        for r in recorrentes:
            if not r.ativa or (r.banco, r.cartao) != (banco, cartao):
                continue
            for dia in r.datas_previstas(ate=previsao.fechamento):
                if dia < previsao.inicio:
                    continue
                if dia >= hoje:
                    previsao.ainda_vai_cair.append((dia, r))
                else:
                    previsao.ja_deve_ter_caido.append((dia, r))

        previsao.ainda_vai_cair.sort(key=lambda item: (item[0], item[1].nome))
        previsao.ja_deve_ter_caido.sort(key=lambda item: (item[0], item[1].nome))
        previsoes.append(previsao)

    return previsoes
