import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from organizer.importers.ofx import parse_ofx
from organizer.models import TIPO_CARTAO, Extrato, Transacao
from organizer.recorrentes import compras, encontrar_recorrentes, prever, somar_meses
from organizer.storage import Storage
from organizer.telegram import ContasBot

CARTAO = "cartao-teste"
# três faturas fechadas: agosto, setembro e outubro
FATURAS = {
    ("nubank", CARTAO): [
        (date(2026, 8, 1), date(2026, 8, 31)),
        (date(2026, 9, 1), date(2026, 9, 30)),
        (date(2026, 10, 1), date(2026, 10, 31)),
    ]
}


def compra(dia: date, valor: str, descricao: str) -> Transacao:
    return Transacao("nubank", CARTAO, dia, Decimal(valor), descricao, f"{dia}{valor}{descricao}")


def mensal(dias: list[tuple[int, int]], valor: str, descricao: str) -> list[Transacao]:
    return [compra(date(2026, mes, dia), valor, descricao) for mes, dia in dias]


def nomes(recorrentes) -> list[str]:
    return [r.nome for r in recorrentes]


# --- importação --------------------------------------------------------------

OFX_CARTAO = """OFXHEADER:100
DATA:OFXSGML
<OFX>
<SIGNONMSGSRSV1><SONRS><FI>
<ORG>NU PAGAMENTOS S.A.</ORG>
<FID>260</FID>
</FI></SONRS></SIGNONMSGSRSV1>
<CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS>
<CCACCTFROM>
<ACCTID>cartao-teste</ACCTID>
</CCACCTFROM>
<BANKTRANLIST>
<DTSTART>20261001000000[-3:BRT]</DTSTART>
<DTEND>20261101000000[-3:BRT]</DTEND>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<DTPOSTED>20261031000000[-3:BRT]</DTPOSTED>
<TRNAMT>-40.90</TRNAMT>
<FITID>a1</FITID>
<MEMO>Ebn *Streaming</MEMO>
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT</TRNTYPE>
<DTPOSTED>20261006000000[-3:BRT]</DTPOSTED>
<TRNAMT>100.00</TRNAMT>
<FITID>a2</FITID>
<MEMO>Pagamento recebido</MEMO>
</STMTTRN>
</BANKTRANLIST>
</CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1>
</OFX>
"""


def test_ofx_de_fatura_vira_extrato_de_cartao():
    extrato = parse_ofx(OFX_CARTAO)

    assert extrato.tipo == TIPO_CARTAO
    assert extrato.banco == "nubank"
    assert extrato.rotulo == "Cartão Nubank"
    # o DTEND é o dia do fechamento, que já não entra na fatura
    assert (extrato.inicio, extrato.fim) == (date(2026, 10, 1), date(2026, 10, 31))
    assert len(extrato.transacoes) == 2


def test_fatura_nao_entra_na_conciliacao_das_contas(tmp_path: Path):
    storage = Storage(tmp_path / "db.sqlite")
    storage.salvar_extrato(parse_ofx(OFX_CARTAO), "fatura.ofx")

    # nem as compras nem o período da fatura podem mexer no painel de contas
    assert storage.transacoes() == []
    assert storage.cobertura() == {}
    assert storage.intervalos() == {}

    assert [t.descricao for t in storage.transacoes(TIPO_CARTAO)] == ["Pagamento recebido", "Ebn *Streaming"]
    assert storage.faturas() == {("nubank", CARTAO): [(date(2026, 10, 1), date(2026, 10, 31))]}


def test_banco_antigo_ganha_a_coluna_tipo(tmp_path: Path):
    caminho = tmp_path / "antigo.sqlite"
    conn = sqlite3.connect(caminho)
    conn.executescript(
        """
        CREATE TABLE transacoes (chave TEXT PRIMARY KEY, banco TEXT NOT NULL, conta TEXT NOT NULL,
            data TEXT NOT NULL, valor TEXT NOT NULL, descricao TEXT NOT NULL, id_externo TEXT NOT NULL);
        CREATE TABLE importacoes (id INTEGER PRIMARY KEY AUTOINCREMENT, banco TEXT NOT NULL, conta TEXT NOT NULL,
            inicio TEXT NOT NULL, fim TEXT NOT NULL, arquivo TEXT NOT NULL, importado_em TEXT NOT NULL);
        INSERT INTO transacoes VALUES ('x', 'itau', '1', '2026-10-06', '-10.00', 'COPEL', 'x');
        INSERT INTO importacoes VALUES (1, 'itau', '1', '2026-10-01', '2026-10-31', 'a.pdf', '2026-10-31');
        """
    )
    conn.commit()
    conn.close()

    storage = Storage(caminho)

    assert [t.descricao for t in storage.transacoes()] == ["COPEL"]
    assert storage.cobertura() == {"itau": date(2026, 10, 31)}
    assert storage.faturas() == {}


# --- o que conta como compra ---------------------------------------------------


def test_compras_ignora_parcelas_iof_pagamentos_e_estornos():
    transacoes = [
        compra(date(2026, 10, 1), "-92.45", "Loja X - Parcela 5/10"),
        compra(date(2026, 10, 10), "-3.97", 'IOF de "Servico Gringo"'),
        compra(date(2026, 10, 10), "3.97", "IOF de volta de Servico Gringo"),
        compra(date(2026, 10, 6), "4602.02", "Pagamento recebido"),
        compra(date(2026, 10, 24), "137.38", "Ajuste a crédito"),
        compra(date(2026, 10, 7), "-78.60", "Farmacia - NuPay"),
        compra(date(2026, 10, 7), "78.60", "Farmacia - NuPay"),
        compra(date(2026, 10, 10), "-113.55", "Servico Gringo"),
    ]

    assert [t.descricao for t in compras(transacoes)] == ["Servico Gringo"]


# --- detecção -----------------------------------------------------------------


def test_assinatura_mensal_com_descricao_variando():
    transacoes = [
        compra(date(2026, 8, 13), "-40.90", "Dm *Streaming"),
        compra(date(2026, 9, 13), "-40.90", "Ebn *Streaming"),
        compra(date(2026, 10, 13), "-40.90", "Ebn *Streaming"),
    ]

    [r] = encontrar_recorrentes(transacoes, FATURAS)

    assert r.nome == "Streaming"
    assert r.valor == Decimal("40.90")
    assert r.ativa
    assert len(r.cobrancas) == 3


def test_pequena_variacao_de_valor_e_dia_continua_recorrente():
    transacoes = [
        compra(date(2026, 8, 7), "-264.58", "Faculdade"),
        compra(date(2026, 9, 5), "-264.58", "Faculdade"),
        compra(date(2026, 10, 6), "-270.00", "Faculdade"),
    ]

    [r] = encontrar_recorrentes(transacoes, FATURAS)

    assert r.valor == Decimal("270.00")


def test_duas_cobrancas_no_mes_com_mesmo_valor_viram_duas_recorrentes():
    # duas linhas de celular: uma no dia 6 e outra no dia 29
    transacoes = mensal([(8, 6), (9, 6), (10, 6), (8, 29), (9, 29), (10, 29)], "-38.00", "Operadora")

    recorrentes = encontrar_recorrentes(transacoes, FATURAS)

    assert [r.ultima.data.day for r in recorrentes] == [6, 29]


def test_mesma_loja_com_valores_diferentes_separa_as_assinaturas():
    # dois planos na mesma loja, cobrados no fim do mês (dia 31 e dia 1 são vizinhos)
    dias = [(8, 2), (8, 31), (10, 1), (10, 31)]
    transacoes = mensal(dias, "-17.91", "Pet Plano") + mensal(dias, "-19.90", "Pet Plano")

    recorrentes = encontrar_recorrentes(transacoes, FATURAS)

    assert sorted(r.valor for r in recorrentes) == [Decimal("17.91"), Decimal("19.90")]


def test_compra_frequente_com_mesmo_valor_nao_e_assinatura():
    # o almoço de sempre: mesmo valor, mas várias vezes no mês
    dias = [(8, 22), (8, 25), (9, 18), (9, 19), (9, 26), (10, 10)]
    transacoes = mensal(dias, "-31.90", "Restaurante")

    assert encontrar_recorrentes(transacoes, FATURAS) == []


def test_compra_que_aparece_uma_vez_nao_e_recorrente():
    transacoes = [compra(date(2026, 10, 3), "-129.90", "Loja de Jogos")]

    assert encontrar_recorrentes(transacoes, FATURAS) == []


def test_assinatura_que_nao_cobrou_no_ultimo_mes_parou():
    transacoes = mensal([(8, 26), (9, 26)], "-193.87", "Seguradora")

    [r] = encontrar_recorrentes(transacoes, FATURAS)

    assert not r.ativa


def test_so_olha_as_tres_ultimas_faturas():
    faturas = {("nubank", CARTAO): [(date(2026, 7, 1), date(2026, 7, 31)), *FATURAS[("nubank", CARTAO)]]}
    # cobrada em julho e agosto, depois parou: julho não entra, então sobra só uma cobrança
    transacoes = mensal([(7, 15), (8, 15)], "-20.00", "Revista")

    assert encontrar_recorrentes(transacoes, faturas) == []


# --- previsão -----------------------------------------------------------------


def todas_as_recorrentes():
    return (
        mensal([(8, 2), (9, 2), (10, 2)], "-14.90", "Video")
        + mensal([(8, 13), (9, 13), (10, 13)], "-40.90", "Streaming")
        + mensal([(8, 28), (9, 28), (10, 28)], "-95.00", "Internet")
        + mensal([(8, 26), (9, 26)], "-193.87", "Seguradora")
    )


def test_previsao_separa_o_que_vai_cair_do_que_ja_deve_ter_caido():
    recorrentes = encontrar_recorrentes(todas_as_recorrentes(), FATURAS)

    [p] = prever(recorrentes, FATURAS, hoje=date(2026, 11, 3))

    assert (p.inicio, p.fechamento) == (date(2026, 11, 1), date(2026, 12, 1))
    assert [(d, r.nome) for d, r in p.ja_deve_ter_caido] == [(date(2026, 11, 2), "Video")]
    assert [(d, r.nome) for d, r in p.ainda_vai_cair] == [
        (date(2026, 11, 13), "Streaming"),
        (date(2026, 11, 28), "Internet"),
    ]
    # a seguradora parou de cobrar
    assert p.total == Decimal("135.90")
    assert p.faturas_faltando == []


def test_previsao_avisa_quando_falta_importar_fatura():
    recorrentes = encontrar_recorrentes(todas_as_recorrentes(), FATURAS)

    [p] = prever(recorrentes, FATURAS, hoje=date(2026, 12, 10))

    assert (p.inicio, p.fechamento) == (date(2026, 12, 1), date(2027, 1, 1))
    assert p.faturas_faltando == [date(2026, 12, 1)]
    assert [(d, r.nome) for d, r in p.ainda_vai_cair] == [
        (date(2026, 12, 13), "Streaming"),
        (date(2026, 12, 28), "Internet"),
    ]


def test_somar_meses_respeita_meses_curtos():
    assert somar_meses(date(2026, 10, 31), 1) == date(2026, 11, 30)
    assert somar_meses(date(2026, 12, 15), 1) == date(2027, 1, 15)


# --- telegram -----------------------------------------------------------------


class ApiMuda:
    def enviar(self, chat_id, texto, botoes=None):
        pass


@pytest.fixture
def bot(tmp_path: Path) -> ContasBot:
    return ContasBot(ApiMuda(), {111}, Storage(tmp_path / "db.sqlite"), contas=[])


def test_comando_recorrentes_no_telegram(bot: ContasBot):
    for inicio, fim in FATURAS[("nubank", CARTAO)]:
        do_mes = [t for t in todas_as_recorrentes() if inicio <= t.data <= fim]
        extrato = Extrato("nubank", CARTAO, inicio, fim, do_mes, tipo=TIPO_CARTAO)
        bot.storage.salvar_extrato(extrato, f"fatura-{fim}.ofx")

    previsao = bot.responder("/recorrentes", quem="Ana", chat_id=111)
    todas = bot.responder("/recorrentes todas", quem="Ana", chat_id=111)

    assert "Ainda vai cair no cartão" in previsao
    assert "Cartão Nubank" in previsao
    assert "Pararam de cobrar" in todas
    assert "Seguradora" in todas
    assert bot.responder("/cartao", quem="Ana", chat_id=111).startswith("<b>💳")


def test_comando_recorrentes_sem_fatura(bot: ContasBot):
    resposta = bot.responder("/recorrentes", quem="Ana", chat_id=111)

    assert "Nenhuma fatura de cartão importada" in resposta
