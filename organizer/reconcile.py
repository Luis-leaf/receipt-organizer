import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from organizer.contas import Conta
from organizer.models import Transacao

# Quanto antes/depois do vencimento um pagamento ainda conta para aquela competência.
# Cobre o caso comum de pagar no fim do mês uma conta que vence no dia 1, sem ficar
# tão larga a ponto de um pagamento cair no mês vizinho.
JANELA_ANTES = timedelta(days=15)
JANELA_DEPOIS = timedelta(days=15)

# Períodos cobertos pelos extratos importados, por banco
Intervalos = dict[str, list[tuple[date, date]]]


class Status(Enum):
    PAGA = "paga"
    ATRASADA = "atrasada"
    FALTA_EXTRATO = "falta_extrato"
    VENCE_HOJE = "vence_hoje"
    A_VENCER = "a_vencer"


@dataclass(frozen=True, order=True)
class Competencia:
    """Mês de referência de uma conta, ex.: 2026-10."""

    ano: int
    mes: int

    @classmethod
    def de_texto(cls, texto: str) -> "Competencia":
        match = re.fullmatch(r"(\d{4})-(\d{1,2})", texto.strip())
        if not match or not 1 <= int(match.group(2)) <= 12:
            raise ValueError(f"competência inválida: {texto!r} (use AAAA-MM)")
        return cls(int(match.group(1)), int(match.group(2)))

    @classmethod
    def de_data(cls, dia: date) -> "Competencia":
        return cls(dia.year, dia.month)

    def __str__(self) -> str:
        return f"{self.ano:04d}-{self.mes:02d}"

    def somar(self, meses: int) -> "Competencia":
        mes = self.mes + meses
        ano = self.ano
        while mes > 12:
            mes -= 12
            ano += 1
        while mes < 1:
            mes += 12
            ano -= 1
        return Competencia(ano, mes)

    def vencimento(self, dia: int) -> date:
        # conta que vence dia 31 vence no último dia em meses mais curtos
        ultimo_dia = monthrange(self.ano, self.mes)[1]
        return date(self.ano, self.mes, min(dia, ultimo_dia))


@dataclass
class Situacao:
    conta: Conta
    competencia: Competencia
    vencimento: date
    transacao: Transacao | None = None
    pago_manual_em: date | None = None
    # bancos cujo extrato não cobre a janela de pagamento: sem eles não dá para
    # afirmar que a conta não foi paga
    bancos_sem_extrato: tuple[str, ...] = ()

    def status(self, hoje: date) -> Status:
        if self.transacao or self.pago_manual_em:
            return Status.PAGA

        if hoje > self.vencimento:
            if self.bancos_sem_extrato:
                return Status.FALTA_EXTRATO
            return Status.ATRASADA

        if hoje == self.vencimento:
            return Status.VENCE_HOJE

        return Status.A_VENCER

    def dias_para_vencer(self, hoje: date) -> int:
        return (self.vencimento - hoje).days


def periodo_coberto(intervalos: list[tuple[date, date]], inicio: date, fim: date) -> bool:
    """True se os intervalos, juntos e sem buracos, cobrem de `inicio` até `fim`."""
    cursor = inicio

    for comeco, final in sorted(intervalos):
        if comeco > cursor:
            break
        if final >= cursor:
            cursor = final + timedelta(days=1)
        if cursor > fim:
            return True

    return False


class Conciliador:
    def __init__(
        self,
        contas: list[Conta],
        transacoes: list[Transacao],
        manuais: dict[tuple[str, str], date],
        intervalos: Intervalos,
    ):
        self.contas = contas
        self.transacoes = transacoes
        self.manuais = manuais
        self.intervalos = intervalos

    def conciliar(self, competencias: list[Competencia], contas: list[Conta] | None = None) -> list[Situacao]:
        """Para cada conta e competência, acha o pagamento mais próximo do vencimento.

        As competências são percorridas em ordem e cada transação só é usada uma vez,
        para que o pagamento de um mês não seja contado também no mês vizinho.
        """
        situacoes = []

        for conta in contas or self.contas:
            candidatas = [t for t in self.transacoes if conta.casa(t)]
            usadas: set[str] = set()

            for competencia in sorted(competencias):
                vencimento = competencia.vencimento(conta.vencimento)

                pagamento = self._pagamento_mais_proximo(candidatas, usadas, vencimento)
                if pagamento:
                    usadas.add(pagamento.chave)

                situacoes.append(
                    Situacao(
                        conta=conta,
                        competencia=competencia,
                        vencimento=vencimento,
                        transacao=pagamento,
                        pago_manual_em=self.manuais.get((conta.id, str(competencia))),
                        bancos_sem_extrato=self._bancos_sem_extrato(conta, vencimento),
                    )
                )

        return sorted(situacoes, key=lambda s: (s.vencimento, s.conta.nome))

    def mes(self, competencia: Competencia) -> list[Situacao]:
        # Concilia também os meses vizinhos para cada pagamento cair no mês certo
        vizinhas = [competencia.somar(-1), competencia, competencia.somar(1)]
        return [s for s in self.conciliar(vizinhas) if s.competencia == competencia]

    def painel(self, hoje: date, dias_aviso: int = 7) -> list[Situacao]:
        """O que importa hoje: o mês corrente, pendências do mês anterior e o que vence logo."""
        atual = Competencia.de_data(hoje)
        competencias = [atual.somar(-2), atual.somar(-1), atual, atual.somar(1)]

        selecionadas = []
        for s in self.conciliar(competencias):
            if self._entra_no_painel(s, hoje, dias_aviso):
                selecionadas.append(s)

        return selecionadas

    @staticmethod
    def _entra_no_painel(s: Situacao, hoje: date, dias_aviso: int) -> bool:
        atual = Competencia.de_data(hoje)
        status = s.status(hoje)

        # mês corrente: tudo, pago ou não
        if s.competencia == atual:
            return True

        # mês anterior: só o que ficou pendente
        if s.competencia == atual.somar(-1):
            return status in (Status.ATRASADA, Status.FALTA_EXTRATO)

        # mês seguinte: só o que vence nos próximos dias
        if s.competencia == atual.somar(1):
            return status != Status.PAGA and s.dias_para_vencer(hoje) <= dias_aviso

        return False

    def proxima_em_aberto(self, conta: Conta, hoje: date) -> Competencia:
        """Competência que um `paguei <conta>` sem --mes deve marcar.

        A mais antiga em aberto que ainda está dentro da janela de pagamento (vencida
        há no máximo JANELA_DEPOIS). Pendências mais velhas precisam de --mes.
        """
        atual = Competencia.de_data(hoje)
        competencias = [atual.somar(-1), atual, atual.somar(1), atual.somar(2)]

        for s in self.conciliar(competencias, contas=[conta]):
            if s.status(hoje) != Status.PAGA and s.vencimento >= hoje - JANELA_DEPOIS:
                return s.competencia

        return atual.somar(1)

    @staticmethod
    def _pagamento_mais_proximo(candidatas: list[Transacao], usadas: set[str], vencimento: date) -> Transacao | None:
        inicio = vencimento - JANELA_ANTES
        fim = vencimento + JANELA_DEPOIS

        melhor = None
        for transacao in candidatas:
            if transacao.chave in usadas or not inicio <= transacao.data <= fim:
                continue

            distancia = abs((transacao.data - vencimento).days)
            if melhor is None or distancia < abs((melhor.data - vencimento).days):
                melhor = transacao

        return melhor

    def _bancos_sem_extrato(self, conta: Conta, vencimento: date) -> tuple[str, ...]:
        if not self.intervalos:
            return ("nenhum extrato importado",)

        # conta com banco fixo (fatura) só depende do extrato daquele banco
        bancos = [conta.banco] if conta.banco else sorted(self.intervalos)

        # o pagamento pode ter saído em qualquer dia da janela até o vencimento
        inicio = vencimento - JANELA_ANTES
        return tuple(
            banco for banco in bancos if not periodo_coberto(self.intervalos.get(banco, []), inicio, vencimento)
        )
