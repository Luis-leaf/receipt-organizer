from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from organizer.contas import carregar_contas
from organizer.models import Transacao
from organizer.reconcile import Competencia, Conciliador, Status, periodo_coberto
from organizer.storage import Storage

CONTAS_YAML = """
contas:
  - id: aluguel
    nome: Aluguel
    vencimento: 1
    padroes: ["IMOBILIARIA"]
  - id: faculdade
    nome: Faculdade
    vencimento: 10
    padroes: ["FACULDADE"]
  - id: fatura-nubank
    nome: Fatura Nubank
    vencimento: 8
    banco: nubank
    padroes: ["^PAGAMENTO DE FATURA$"]
"""

COBRE_TUDO = {
    "nubank": [(date(2026, 1, 1), date(2026, 12, 31))],
    "mercadopago": [(date(2026, 1, 1), date(2026, 12, 31))],
}


def conciliar(contas, transacoes, competencias, intervalos=COBRE_TUDO, manuais=None):
    return Conciliador(list(contas), transacoes, manuais or {}, intervalos).conciliar(competencias)


def tx(dia: date, valor: str, descricao: str, banco: str = "nubank") -> Transacao:
    return Transacao(banco, "1", dia, Decimal(valor), descricao, f"{dia}{descricao}{valor}")


@pytest.fixture
def contas(tmp_path: Path):
    caminho = tmp_path / "contas.yaml"
    caminho.write_text(CONTAS_YAML, encoding="utf-8")
    return {c.id: c for c in carregar_contas(caminho)}


def test_padrao_ignora_acento_e_caixa(contas):
    assert contas["faculdade"].casa(tx(date(2026, 10, 6), "-437.36", "Pagamento de boleto - FACULDADE EXEMPLO"))
    assert contas["aluguel"].casa(tx(date(2026, 10, 1), "-2100", "pagto boleto imobiliária exemplo"))


def test_creditos_e_outro_banco_nao_casam(contas):
    assert not contas["faculdade"].casa(tx(date(2026, 10, 6), "437.36", "Estorno FACULDADE"))
    fatura_mp = tx(date(2026, 10, 6), "-700", "Pagamento de fatura", banco="mercadopago")
    assert not contas["fatura-nubank"].casa(fatura_mp)


def test_pagamento_antes_do_vencimento_conta_na_competencia(contas):
    transacoes = [tx(date(2026, 10, 6), "-437.36", "FACULDADE")]
    [s] = conciliar([contas["faculdade"]], transacoes, [Competencia(2026, 10)])

    assert s.vencimento == date(2026, 10, 10)
    assert s.transacao == transacoes[0]
    assert s.status(date(2026, 10, 31)) == Status.PAGA


def test_aluguel_pago_no_mes_anterior_nao_conta_duas_vezes(contas):
    # aluguel de 01/10 pago em 28/09 e o de 01/11 ainda não pago
    transacoes = [tx(date(2026, 9, 28), "-2100", "IMOBILIARIA EXEMPLO")]
    competencias = [Competencia(2026, 10), Competencia(2026, 11)]
    out, nov = conciliar([contas["aluguel"]], transacoes, competencias)

    assert out.transacao == transacoes[0]
    assert nov.transacao is None
    assert nov.status(date(2026, 10, 29)) == Status.A_VENCER


def test_atrasada_x_falta_extrato(contas):
    competencias = [Competencia(2026, 10)]
    hoje = date(2026, 10, 20)

    [coberta] = conciliar([contas["faculdade"]], [], competencias)
    assert coberta.status(hoje) == Status.ATRASADA

    so_ate_setembro = {**COBRE_TUDO, "mercadopago": [(date(2026, 9, 1), date(2026, 9, 30))]}
    [incompleta] = conciliar([contas["faculdade"]], [], competencias, so_ate_setembro)
    assert incompleta.status(hoje) == Status.FALTA_EXTRATO
    assert incompleta.bancos_sem_extrato == ("mercadopago",)

    # conta com banco fixo só depende do extrato daquele banco
    [fatura] = conciliar([contas["fatura-nubank"]], [], competencias, so_ate_setembro)
    assert fatura.status(hoje) == Status.ATRASADA


def test_falta_extrato_se_janela_antes_do_vencimento_nao_esta_coberta(contas):
    # aluguel de 01/10 pode ter sido pago no fim de setembro: extrato só de outubro não basta
    so_outubro = {"nubank": [(date(2026, 10, 1), date(2026, 10, 31))]}
    [s] = conciliar([contas["aluguel"]], [], [Competencia(2026, 10)], so_outubro)
    assert s.status(date(2026, 10, 20)) == Status.FALTA_EXTRATO


def test_periodo_coberto_junta_extratos_seguidos():
    setembro = (date(2026, 9, 1), date(2026, 9, 30))
    outubro = (date(2026, 10, 1), date(2026, 10, 31))
    inicio, fim = date(2026, 9, 11), date(2026, 10, 1)

    assert periodo_coberto([outubro, setembro], inicio, fim)
    assert not periodo_coberto([outubro], inicio, fim)
    assert not periodo_coberto([(date(2026, 9, 1), date(2026, 9, 20)), outubro], inicio, fim)


def test_pagamento_manual(contas):
    manuais = {("faculdade", "2026-10"): date(2026, 10, 9)}
    [s] = conciliar([contas["faculdade"]], [], [Competencia(2026, 10)], manuais=manuais)
    assert s.status(date(2026, 10, 20)) == Status.PAGA


def test_painel_mostra_o_que_vence_no_mes_seguinte(contas):
    hoje = date(2026, 10, 29)
    situacoes = Conciliador(list(contas.values()), [], {}, COBRE_TUDO).painel(hoje)
    assert any(s.conta.id == "aluguel" and s.vencimento == date(2026, 11, 1) for s in situacoes)
    assert not any(s.vencimento == date(2026, 11, 10) for s in situacoes)


def test_paguei_sem_mes_ignora_pendencia_antiga(contas):
    conciliador = Conciliador(list(contas.values()), [], {}, COBRE_TUDO)

    assert conciliador.proxima_em_aberto(contas["faculdade"], date(2026, 10, 31)) == Competencia(2026, 11)
    assert conciliador.proxima_em_aberto(contas["faculdade"], date(2026, 10, 12)) == Competencia(2026, 10)


def test_competencia_vencimento_em_mes_curto():
    assert Competencia(2026, 2).vencimento(31) == date(2026, 2, 28)
    assert Competencia(2026, 12).somar(1) == Competencia(2027, 1)
    assert Competencia(2026, 1).somar(-1) == Competencia(2025, 12)
    assert Competencia(2026, 9) < Competencia(2026, 10)


def test_storage_nao_duplica(tmp_path: Path):
    from organizer.models import Extrato

    storage = Storage(tmp_path / "db.sqlite")
    t = tx(date(2026, 10, 6), "-437.36", "FACULDADE")
    extrato = Extrato("nubank", "1", date(2026, 10, 1), date(2026, 10, 30), [t])

    assert storage.salvar_extrato(extrato, "a.ofx") == 1
    assert storage.salvar_extrato(extrato, "a.ofx") == 0
    assert storage.transacoes() == [t]
    assert storage.intervalos() == {"nubank": [(date(2026, 10, 1), date(2026, 10, 30))]}
