import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from organizer.models import NOMES_BANCOS, Transacao, normalizar

MEIOS = ("boleto", "fatura", "debito", "pix")

CABECALHO = """\
# Contas recorrentes. Cada uma é procurada nos extratos pelos `padroes`
# (trechos da descrição, sem diferença de maiúscula/acento) perto do dia de `vencimento`.
# `banco` é opcional: restringe a busca aos extratos daquele banco.
#
# Dá para editar à mão ou pelo terminal:
#   uv run python main.py contas adicionar
#   uv run python main.py contas editar <id>
#   uv run python main.py contas remover <id>

"""


@dataclass(frozen=True)
class Conta:
    id: str
    nome: str
    vencimento: int
    meio: str
    padroes: tuple[re.Pattern, ...]
    banco: str | None = None

    def casa(self, transacao: Transacao) -> bool:
        if transacao.valor >= 0:
            return False
        if self.banco and transacao.banco != self.banco:
            return False
        descricao = normalizar(transacao.descricao)
        return any(p.search(descricao) for p in self.padroes)


def gerar_id(nome: str) -> str:
    """'Fatura Itaú Uniclass' -> 'fatura-itau-uniclass'"""
    return re.sub(r"[^a-z0-9]+", "-", normalizar(nome).lower()).strip("-")


def conta_de_item(item: dict) -> Conta:
    """Valida um item do contas.yaml e cria a Conta. Erros viram ValueError com mensagem clara."""
    conta_id = str(item.get("id") or "").strip()
    if not conta_id:
        raise ValueError("toda conta precisa de um id")

    try:
        vencimento = int(item.get("vencimento"))
    except (TypeError, ValueError):
        raise ValueError(f"{conta_id}: vencimento precisa ser um dia do mês (1 a 31)") from None
    if not 1 <= vencimento <= 31:
        raise ValueError(f"{conta_id}: vencimento precisa ser um dia do mês (1 a 31)")

    padroes = item.get("padroes") or []
    if not padroes:
        raise ValueError(f"{conta_id}: informe pelo menos um padrão de descrição")

    compilados = []
    for padrao in padroes:
        try:
            compilados.append(re.compile(normalizar(str(padrao))))
        except re.error as erro:
            raise ValueError(f"{conta_id}: padrão inválido {padrao!r} ({erro})") from None

    meio = item.get("meio", "boleto")
    if meio not in MEIOS:
        raise ValueError(f"{conta_id}: meio precisa ser um destes: {', '.join(MEIOS)}")

    banco = item.get("banco")
    if banco and banco not in NOMES_BANCOS:
        raise ValueError(f"{conta_id}: banco precisa ser um destes: {', '.join(NOMES_BANCOS)}")

    return Conta(
        id=conta_id,
        nome=item.get("nome") or conta_id,
        vencimento=vencimento,
        meio=meio,
        padroes=tuple(compilados),
        banco=banco,
    )


def ler_cadastro(caminho: Path) -> list[dict]:
    """Itens do contas.yaml como dicionários (lista vazia se o arquivo não existe)."""
    if not caminho.exists():
        return []
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    return dados.get("contas") or []


def salvar_cadastro(caminho: Path, itens: list[dict]):
    # valida tudo antes de gravar, para nunca deixar um contas.yaml quebrado
    validar_cadastro(itens)

    ordem = ["id", "nome", "vencimento", "meio", "banco", "padroes"]
    organizados = []
    for item in itens:
        organizados.append({chave: item[chave] for chave in ordem if item.get(chave)})

    corpo = yaml.safe_dump(
        {"contas": organizados},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=None,
    )
    # uma linha em branco entre as contas
    corpo = corpo.replace("\n- ", "\n\n- ")
    caminho.write_text(CABECALHO + corpo, encoding="utf-8")


def validar_cadastro(itens: list[dict]) -> list[Conta]:
    contas = []
    ids = set()
    for item in itens:
        conta = conta_de_item(item)
        if conta.id in ids:
            raise ValueError(f"id de conta repetido: {conta.id}")
        ids.add(conta.id)
        contas.append(conta)
    return contas


def carregar_contas(caminho: Path) -> list[Conta]:
    return validar_cadastro(ler_cadastro(caminho))
