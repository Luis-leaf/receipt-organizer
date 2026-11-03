import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


def normalizar(texto: str) -> str:
    """Maiúsculas, sem acento e com espaços colapsados, para comparar descrições."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().upper()


# Nome curto usado internamente -> nome para mostrar ao usuário
NOMES_BANCOS = {
    "nubank": "Nubank",
    "itau": "Itaú",
    "mercadopago": "Mercado Pago",
}


def nome_banco(banco: str) -> str:
    return NOMES_BANCOS.get(banco, banco.title())


# Tipos de extrato: o da conta corrente entra na conciliação das contas;
# a fatura do cartão só é usada para achar compras recorrentes.
TIPO_CONTA = "conta"
TIPO_CARTAO = "cartao"


def valor_br(texto: str) -> Decimal:
    """Converte '-6.229,33' em Decimal('-6229.33')."""
    return Decimal(texto.replace(".", "").replace(",", "."))


@dataclass(frozen=True)
class Transacao:
    banco: str
    conta: str
    data: date
    valor: Decimal
    descricao: str
    id_externo: str

    @property
    def chave(self) -> str:
        # O Nubank repete FITID entre lançamentos diferentes (ex.: compra e estorno),
        # então a chave combina o id com data, valor e descrição.
        partes = [self.banco, self.conta, self.id_externo, self.data.isoformat(), str(self.valor), self.descricao]
        bruto = "|".join(partes)
        return hashlib.sha1(bruto.encode()).hexdigest()


@dataclass
class Extrato:
    banco: str
    conta: str
    inicio: date
    fim: date
    transacoes: list[Transacao] = field(default_factory=list)
    tipo: str = TIPO_CONTA

    @property
    def rotulo(self) -> str:
        """'Nubank' ou 'Cartão Nubank', para mensagens."""
        if self.tipo == TIPO_CARTAO:
            return f"Cartão {nome_banco(self.banco)}"
        return nome_banco(self.banco)
