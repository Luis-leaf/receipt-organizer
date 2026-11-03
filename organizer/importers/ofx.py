import re
from datetime import date, timedelta
from decimal import Decimal

from organizer.models import TIPO_CARTAO, TIPO_CONTA, Extrato, Transacao

# BANKID/FID -> nome curto usado no resto do projeto
BANCOS = {
    "0260": "nubank",
    "260": "nubank",
    "0341": "itau",
    "341": "itau",
}


def ler_texto(conteudo: bytes) -> str:
    # OFX 1.x de banco brasileiro vem em UTF-8 ou cp1252 dependendo do banco
    try:
        return conteudo.decode("utf-8")
    except UnicodeDecodeError:
        return conteudo.decode("cp1252")


def _tag(bloco: str, nome: str) -> str | None:
    # SGML (OFX 1.x) nem sempre fecha as tags, então lê até o próximo '<' ou fim de linha
    match = re.search(rf"<{nome}>([^<\r\n]*)", bloco)
    return match.group(1).strip() if match else None


def _data(valor: str) -> date:
    return date(int(valor[:4]), int(valor[4:6]), int(valor[6:8]))


def parse_ofx(texto: str) -> Extrato:
    bank_id = _tag(texto, "BANKID") or _tag(texto, "FID") or ""
    banco = BANCOS.get(bank_id) or (_tag(texto, "ORG") or "desconhecido").lower()
    conta = _tag(texto, "ACCTID") or ""

    transacoes = []
    for bloco in texto.split("<STMTTRN>")[1:]:
        bloco = re.split(r"</STMTTRN>|</BANKTRANLIST>", bloco)[0]
        transacoes.append(
            Transacao(
                banco=banco,
                conta=conta,
                data=_data(_tag(bloco, "DTPOSTED")),
                valor=Decimal(_tag(bloco, "TRNAMT").replace(",", ".")),
                descricao=_tag(bloco, "MEMO") or _tag(bloco, "NAME") or "",
                id_externo=_tag(bloco, "FITID") or "",
            )
        )

    datas = [t.data for t in transacoes]
    inicio = _data(_tag(texto, "DTSTART")) if _tag(texto, "DTSTART") else min(datas)
    fim = _data(_tag(texto, "DTEND")) if _tag(texto, "DTEND") else max(datas)

    # Fatura de cartão: o OFX vem em <CCSTMTRS> e o DTEND é o dia do fechamento,
    # que já não entra na fatura (ex.: fatura de 01/10 a 01/11 tem compras até 31/10).
    tipo = TIPO_CARTAO if "<CCSTMTRS>" in texto else TIPO_CONTA
    if tipo == TIPO_CARTAO and _tag(texto, "DTEND"):
        fim = max([fim - timedelta(days=1), *datas])

    return Extrato(
        banco=banco,
        conta=conta,
        inicio=inicio,
        fim=fim,
        transacoes=transacoes,
        tipo=tipo,
    )
