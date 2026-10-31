from datetime import date
from decimal import Decimal

from organizer.importers.itau_pdf import eh_itau, parse_itau
from organizer.importers.mercadopago_pdf import eh_mercadopago, parse_mercadopago
from organizer.importers.ofx import ler_texto, parse_ofx

OFX_NUBANK = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
ENCODING:UTF-8
<OFX>
<SIGNONMSGSRSV1><SONRS><FI>
<ORG>NU PAGAMENTOS S.A.</ORG>
<FID>260</FID>
</FI></SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<BANKACCTFROM>
<BANKID>0260</BANKID>
<ACCTID>11111111-1</ACCTID>
</BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>20261001000000[-3:BRT]</DTSTART>
<DTEND>20261030000000[-3:BRT]</DTEND>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<DTPOSTED>20261006000000[-3:BRT]</DTPOSTED>
<TRNAMT>-437.36</TRNAMT>
<FITID>aaa</FITID>
<MEMO>Pagamento de boleto efetuado - FACULDADE EXEMPLO</MEMO>
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<DTPOSTED>20261026000000[-3:BRT]</DTPOSTED>
<TRNAMT>-211.42</TRNAMT>
<FITID>repetido</FITID>
<MEMO>Compra de Ações - XPTO4</MEMO>
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT</TRNTYPE>
<DTPOSTED>20261027000000[-3:BRT]</DTPOSTED>
<TRNAMT>211.42</TRNAMT>
<FITID>repetido</FITID>
<MEMO>Compra de Ações - XPTO4</MEMO>
</STMTTRN>
</BANKTRANLIST>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""

ITAU = """FULANA DE TAL 000.000.000-00 agência: 1234 conta: 012345-6
extrato conta / lançamentos
período de visualização: 01/09/2026 até 31/10/2026 emitido em: 31/10/2026 09:03:10
data lançamentos valor (R$) saldo (R$)
31/10/2026 SALDO DO DIA 0,01
30/10/2026 PIX TRANSF FULANO 30/10 -6.229,33
06/10/2026 REMUNERACAO/SALARIO 8.544,93
06/10/2026 FATURA PAGA Itau Uniclas -1.438,27
04/09/2026 PIX TRANSF FULANO04/09 -100,00
04/09/2026 PIX TRANSF FULANO04/09 -100,00
Consultas, informações e serviços transacionais, acesse itau.com.br""".splitlines()

MERCADO_PAGO = """EXTRATO DE CONTA
Fulano de Tal
CPF/CNPJ: 00000000000 Agência: 1 Conta: 12345678901
Periodo: De 01-09-2026 al 30-09-2026
DETALHE DOS MOVIMENTOS
Data Descrição ID da operação Valor Saldo
01-09-2026 Rendimentos 1749239974879 R$ 0,01 R$ 8,49
Pagamento com QR Pix 99
02-09-2026 176939676854 R$ -5,00 R$ 3,49
TECNOLOGIA LTDA
Pagamento de fatura Cartão
04-09-2026 177214264486 R$ -709,91 R$ 3,58
de crédito
04-09-2026 Dinheiro reservado Aluguel 176356820745 R$ -2.100,00 R$ 3,58
1/2
Data Descrição ID da operação Valor Saldo
Pagamento com Pix PADARIA
28-09-2026 PAO QUENTE DO 180295363591 R$ -10,00 R$ 14,13
BAIRRO
Data de geração: 31-10-2026
Mercado Pago Instituição de Pagamento Ltda.
2/2""".splitlines()


def test_ofx_nubank():
    extrato = parse_ofx(OFX_NUBANK)

    assert extrato.banco == "nubank"
    assert extrato.conta == "11111111-1"
    assert (extrato.inicio, extrato.fim) == (date(2026, 10, 1), date(2026, 10, 30))
    assert len(extrato.transacoes) == 3

    boleto = extrato.transacoes[0]
    assert boleto.data == date(2026, 10, 6)
    assert boleto.valor == Decimal("-437.36")
    assert "FACULDADE EXEMPLO" in boleto.descricao


def test_ofx_fitid_repetido_nao_colide():
    extrato = parse_ofx(OFX_NUBANK)
    chaves = {t.chave for t in extrato.transacoes}
    assert len(chaves) == 3


def test_ofx_cp1252():
    texto = ler_texto("<MEMO>Transferência</MEMO>".encode("cp1252"))
    assert "Transferência" in texto


def test_itau():
    assert eh_itau(ITAU)
    extrato = parse_itau(ITAU)

    assert extrato.banco == "itau"
    assert extrato.conta == "1234/012345-6"
    assert (extrato.inicio, extrato.fim) == (date(2026, 9, 1), date(2026, 10, 31))
    # SALDO DO DIA não é lançamento
    assert len(extrato.transacoes) == 5

    fatura = extrato.transacoes[2]
    assert fatura.descricao == "FATURA PAGA Itau Uniclas"
    assert fatura.valor == Decimal("-1438.27")


def test_itau_lancamentos_identicos_nao_colidem():
    extrato = parse_itau(ITAU)
    assert len({t.chave for t in extrato.transacoes}) == len(extrato.transacoes)


def test_mercadopago_descricao_quebrada():
    assert eh_mercadopago(MERCADO_PAGO)
    assert not eh_itau(MERCADO_PAGO)
    extrato = parse_mercadopago(MERCADO_PAGO)

    assert extrato.banco == "mercadopago"
    assert extrato.conta == "1/12345678901"
    assert [t.descricao for t in extrato.transacoes] == [
        "Rendimentos",
        "Pagamento com QR Pix 99 TECNOLOGIA LTDA",
        "Pagamento de fatura Cartão de crédito",
        "Dinheiro reservado Aluguel",
        "Pagamento com Pix PADARIA PAO QUENTE DO BAIRRO",
    ]
    assert extrato.transacoes[2].valor == Decimal("-709.91")
    assert extrato.transacoes[3].valor == Decimal("-2100.00")
    assert extrato.transacoes[2].id_externo == "177214264486"
