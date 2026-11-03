from datetime import date, timedelta
from decimal import Decimal

from organizer.models import nome_banco
from organizer.reconcile import Competencia, Situacao, Status
from organizer.recorrentes import FATURAS_ANALISADAS, Previsao, Recorrente

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


# --- compras recorrentes no cartão ---------------------------------------

SEM_FATURA = (
    "Nenhuma fatura de cartão importada. Exporte a fatura do Nubank em OFX "
    "(app → Cartão → Faturas → Exportar) e coloque na pasta de entrada."
)


def nome_cartao(banco: str) -> str:
    return f"Cartão {nome_banco(banco)}"


def lista_de_dias(datas: list[date]) -> str:
    return ", ".join(f"{d:%d/%m}" for d in datas)


def montar_previsao(previsoes: list[Previsao], hoje: date) -> str:
    """Terminal: o que ainda vai cair de recorrente na fatura aberta."""
    titulo = f"Recorrentes no cartão em {hoje:%d/%m/%Y}"
    linhas = [titulo.upper(), "─" * len(titulo)]

    if not previsoes:
        linhas += ["", SEM_FATURA]

    for p in previsoes:
        ultimo_dia = p.fechamento - timedelta(days=1)
        cabecalho = f"{nome_cartao(p.banco)} · fatura aberta de {p.inicio:%d/%m} a {ultimo_dia:%d/%m}"
        linhas += ["", f"{cabecalho} · fecha {p.fechamento:%d/%m}"]
        if p.faturas_faltando:
            linhas.append(f"  ⚠️ Falta importar a fatura que fechou em {lista_de_dias(p.faturas_faltando)}")

        linhas += ["", f"Ainda vai cair ({len(p.ainda_vai_cair)}) · {reais(p.total)}"]
        if not p.ainda_vai_cair:
            linhas.append("  Nada mais até o fechamento.")
        for dia, r in p.ainda_vai_cair:
            linhas.append(f"  🔁 {dia:%d/%m}  {r.nome:<40} ~{reais(r.valor)}")

        if p.ja_deve_ter_caido:
            linhas += ["", f"Já devem ter caído ({len(p.ja_deve_ter_caido)})"]
            for dia, r in p.ja_deve_ter_caido:
                linhas.append(f"  ✔️ {dia:%d/%m}  {r.nome:<40} ~{reais(r.valor)}")

    return "\n".join(linhas)


def montar_relatorio_recorrentes(recorrentes: list[Recorrente], faturas: dict) -> str:
    """Terminal: todas as recorrentes encontradas, para conferir a detecção."""
    titulo = "Compras recorrentes no cartão"
    linhas = [titulo.upper(), "─" * len(titulo)]

    if not faturas:
        linhas += ["", SEM_FATURA]

    for (banco, cartao), periodos in faturas.items():
        ultimas = periodos[-FATURAS_ANALISADAS:]
        do_cartao = [r for r in recorrentes if (r.banco, r.cartao) == (banco, cartao)]
        ativas = [r for r in do_cartao if r.ativa]
        paradas = [r for r in do_cartao if not r.ativa]
        por_mes = sum((r.valor for r in ativas), Decimal(0))

        linhas += [
            "",
            f"{nome_cartao(banco)} · {len(ultimas)} fatura(s), compras de "
            f"{ultimas[0][0]:%d/%m/%Y} a {ultimas[-1][1]:%d/%m/%Y}",
            "",
            f"Ativas ({len(ativas)}) · {reais(por_mes)} por mês",
        ]
        if not ativas:
            linhas.append("  Nenhuma compra recorrente encontrada.")
        for r in ativas:
            linhas.append("")
            linhas.append(f"  🔁 dia {r.ultima.data.day:02d}  {r.nome}")
            datas = lista_de_dias([t.data for t in r.cobrancas])
            linhas.append(f"{RECUO}{reais(r.valor)} · cobrada em {datas}")

        if paradas:
            linhas += ["", f"Pararam de cobrar ({len(paradas)})"]
            for r in paradas:
                linhas.append("")
                linhas.append(f"  ⏹️ dia {r.ultima.data.day:02d}  {r.nome}")
                linhas.append(f"{RECUO}{reais(r.valor)} · última cobrança em {r.ultima.data:%d/%m}")

    return "\n".join(linhas)
