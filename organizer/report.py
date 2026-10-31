from datetime import date
from decimal import Decimal

from organizer.models import nome_banco
from organizer.reconcile import Competencia, Situacao, Status

MESES = [
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]

# Ordem e título das seções do relatório
SECOES = [
    ("Precisa de atenção", (Status.ATRASADA, Status.FALTA_EXTRATO, Status.VENCE_HOJE)),
    ("A vencer", (Status.A_VENCER,)),
    ("Pagas", (Status.PAGA,)),
]

# Recuo da segunda linha de cada conta, para alinhar com o nome
RECUO = " " * 12


def reais(valor: Decimal) -> str:
    # 6229.33 -> "6,229.33" -> "R$ 6.229,33"
    texto = f"{abs(valor):,.2f}"
    texto = texto.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {texto}"


def plural_dias(n: int) -> str:
    return f"{n} dia" if n == 1 else f"{n} dias"


def titulo_mes(competencia: Competencia) -> str:
    return f"Contas de {MESES[competencia.mes - 1]}/{competencia.ano}"


def descrever(s: Situacao, hoje: date) -> tuple[str, str]:
    """Ícone e texto do status de uma conta, usados no terminal e no Telegram."""
    status = s.status(hoje)

    if status == Status.PAGA and s.transacao:
        icone = "✅"
        t = s.transacao
        detalhe = f"Paga em {t.data:%d/%m} · {reais(t.valor)} · {nome_banco(t.banco)}"

    elif status == Status.PAGA:
        icone = "☑️"
        detalhe = f"Marcada como paga em {s.pago_manual_em:%d/%m} · aguardando o extrato"

    elif status == Status.ATRASADA:
        icone = "🔴"
        atraso = plural_dias(-s.dias_para_vencer(hoje))
        detalhe = f"Venceu há {atraso} · pagamento não encontrado"

    elif status == Status.FALTA_EXTRATO:
        icone = "❓"
        atraso = plural_dias(-s.dias_para_vencer(hoje))
        bancos = ", ".join(nome_banco(b) for b in s.bancos_sem_extrato)
        detalhe = f"Venceu há {atraso} · falta extrato: {bancos}"

    elif status == Status.VENCE_HOJE:
        icone = "🟠"
        detalhe = "Vence HOJE"

    else:
        icone = "⏳"
        detalhe = f"Vence em {plural_dias(s.dias_para_vencer(hoje))}"

    return icone, detalhe


def agrupar(situacoes: list[Situacao], hoje: date) -> list[tuple[str, list[Situacao]]]:
    """Separa as contas nas seções de SECOES, pulando as vazias."""
    grupos = []
    for titulo, status_da_secao in SECOES:
        do_grupo = [s for s in situacoes if s.status(hoje) in status_da_secao]
        if do_grupo:
            grupos.append((titulo, do_grupo))
    return grupos


def montar_relatorio(titulo: str, situacoes: list[Situacao], cobertura: dict[str, date], hoje: date) -> str:
    linhas = [titulo.upper(), "─" * len(titulo)]

    if not situacoes:
        linhas.append("")
        linhas.append("Nenhuma conta para mostrar.")

    for nome_secao, do_grupo in agrupar(situacoes, hoje):
        linhas.append("")
        linhas.append(f"{nome_secao} ({len(do_grupo)})")
        for s in do_grupo:
            icone, detalhe = descrever(s, hoje)
            linhas.append("")
            linhas.append(f"  {icone} {s.vencimento:%d/%m}  {s.conta.nome}")
            linhas.append(f"{RECUO}{detalhe}")

    linhas.append("")
    linhas.append("─" * len(titulo))
    if cobertura:
        partes = [f"{nome_banco(banco)} até {fim:%d/%m}" for banco, fim in cobertura.items()]
        linhas.append("Extratos importados: " + " · ".join(partes))
    else:
        linhas.append("Nenhum extrato importado ainda. Veja 'Como importar extratos' no README.")

    return "\n".join(linhas)
