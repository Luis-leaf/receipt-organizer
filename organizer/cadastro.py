"""Comandos de terminal para cuidar do contas.yaml sem precisar editá-lo à mão."""

import sys
from pathlib import Path

from organizer.contas import MEIOS, conta_de_item, gerar_id, ler_cadastro, salvar_cadastro
from organizer.models import NOMES_BANCOS, Transacao, nome_banco, normalizar
from organizer.report import reais
from organizer.storage import Storage


def perguntar(texto: str, padrao: str | None = None) -> str:
    """Lê uma resposta no terminal. Enter vazio devolve o padrão."""
    sugestao = f" [{padrao}]" if padrao else ""
    resposta = input(f"{texto}{sugestao}: ").strip()
    return resposta or (padrao or "")


def usar_ou_perguntar(valor, texto: str, interativo: bool, padrao: str | None = None):
    """Usa o valor passado na linha de comando; se não veio, pergunta (quando há terminal)."""
    if valor:
        return valor
    if interativo:
        return perguntar(texto, padrao)
    return padrao


def confirmar(texto: str) -> bool:
    return input(f"{texto} [s/N]: ").strip().lower() in ("s", "sim")


def separar_padroes(texto: str) -> list[str]:
    return [p.strip() for p in texto.split(",") if p.strip()]


def formatar_transacao(t: Transacao) -> str:
    # saída com "-", entrada com "+"
    sinal = "-" if t.valor < 0 else "+"
    valor = f"{sinal} {reais(t.valor)}"
    return f"  {t.data:%d/%m/%Y}  {valor:>15}  {nome_banco(t.banco):<12}  {t.descricao}"


def mostrar_conta(item: dict):
    banco = nome_banco(item["banco"]) if item.get("banco") else "qualquer um"
    print(f"  id:         {item['id']}")
    print(f"  nome:       {item['nome']}")
    print(f"  vencimento: dia {item['vencimento']}")
    print(f"  meio:       {item.get('meio', 'boleto')}")
    print(f"  banco:      {banco}")
    print(f"  padrões:    {', '.join(item['padroes'])}")


def mostrar_pagamentos_encontrados(item: dict, storage: Storage):
    """Mostra o que a conta encontraria nos extratos já importados, para conferir os padrões."""
    conta = conta_de_item(item)
    encontradas = [t for t in storage.transacoes() if conta.casa(t)]

    print()
    if not encontradas:
        print("Nenhum pagamento nos extratos importados bate com esses padrões.")
        print("Dica: use 'main.py buscar <trecho>' para ver como o pagamento aparece no extrato.")
        return

    print(f"Pagamentos nos extratos importados que batem com essa conta ({len(encontradas)}):")
    for t in encontradas[-6:]:
        print(formatar_transacao(t))


def listar(caminho: Path):
    itens = ler_cadastro(caminho)
    if not itens:
        print("Nenhuma conta cadastrada. Comece com: uv run python main.py contas adicionar")
        return

    for item in sorted(itens, key=lambda i: int(i["vencimento"])):
        banco = f" · só {nome_banco(item['banco'])}" if item.get("banco") else ""
        print()
        print(f"  {item['nome']}")
        print(f"    id: {item['id']} · dia {item['vencimento']} · {item.get('meio', 'boleto')}{banco}")
        print(f"    procura por: {', '.join(item['padroes'])}")


def adicionar(caminho: Path, storage: Storage, args):
    itens = ler_cadastro(caminho)
    interativo = sys.stdin.isatty()

    nome = usar_ou_perguntar(args.nome, "Nome da conta (ex.: Energia)", interativo)
    vencimento = usar_ou_perguntar(args.vencimento, "Dia do vencimento (1 a 31)", interativo)

    if args.padrao:
        padroes = args.padrao
    else:
        texto = usar_ou_perguntar(
            None, "Como aparece no extrato? Trechos separados por vírgula (ex.: COPEL)", interativo
        )
        padroes = separar_padroes(texto or "")

    if not nome or not vencimento or not padroes:
        raise SystemExit("Informe --nome, --vencimento e pelo menos um --padrao")

    bancos = ", ".join(NOMES_BANCOS)
    banco = usar_ou_perguntar(args.banco, f"Só procurar em um banco? ({bancos} ou Enter para qualquer um)", interativo)
    meio = usar_ou_perguntar(args.meio, f"Meio de pagamento ({', '.join(MEIOS)})", interativo, padrao="boleto")

    item = {
        "id": args.id or gerar_id(nome),
        "nome": nome,
        "vencimento": int(vencimento),
        "meio": meio,
        "banco": banco or None,
        "padroes": padroes,
    }

    if any(i["id"] == item["id"] for i in itens):
        raise SystemExit(f"Já existe uma conta com id '{item['id']}'. Use --id para escolher outro.")

    try:
        conta_de_item(item)
    except ValueError as erro:
        raise SystemExit(f"Conta inválida: {erro}") from None

    print()
    print("Nova conta:")
    mostrar_conta(item)
    mostrar_pagamentos_encontrados(item, storage)

    if interativo and not args.sim and not confirmar("\nSalvar?"):
        print("Nada foi salvo.")
        return

    salvar_cadastro(caminho, itens + [item])
    print(f"\nConta '{item['id']}' salva em {caminho}.")


def editar(caminho: Path, storage: Storage, args):
    itens = ler_cadastro(caminho)
    item = next((i for i in itens if i["id"] == args.id), None)
    if not item:
        raise SystemExit(f"Conta '{args.id}' não existe. Veja os ids com: main.py contas")

    novo = dict(item)
    alterou_por_argumento = any([args.nome, args.vencimento, args.padrao, args.banco is not None, args.meio])

    if alterou_por_argumento:
        if args.nome:
            novo["nome"] = args.nome
        if args.vencimento:
            novo["vencimento"] = args.vencimento
        if args.padrao:
            novo["padroes"] = args.padrao
        if args.banco is not None:
            # --banco "" volta a procurar em qualquer banco
            novo["banco"] = args.banco or None
        if args.meio:
            novo["meio"] = args.meio

    elif sys.stdin.isatty():
        print("Enter mantém o valor atual.\n")
        novo["nome"] = perguntar("Nome", item["nome"])
        novo["vencimento"] = int(perguntar("Dia do vencimento", str(item["vencimento"])))
        novo["padroes"] = separar_padroes(perguntar("Padrões (separados por vírgula)", ", ".join(item["padroes"])))
        novo["banco"] = perguntar("Banco (- para qualquer um)", item.get("banco") or "-")
        novo["banco"] = None if novo["banco"] == "-" else novo["banco"]
        novo["meio"] = perguntar("Meio de pagamento", item.get("meio", "boleto"))

    else:
        raise SystemExit("Diga o que mudar: --nome, --vencimento, --padrao, --banco ou --meio")

    try:
        conta_de_item(novo)
    except ValueError as erro:
        raise SystemExit(f"Conta inválida: {erro}") from None

    print()
    mostrar_conta(novo)
    mostrar_pagamentos_encontrados(novo, storage)

    itens = [novo if i["id"] == args.id else i for i in itens]
    salvar_cadastro(caminho, itens)
    print(f"\nConta '{args.id}' atualizada.")


def remover(caminho: Path, args):
    itens = ler_cadastro(caminho)
    item = next((i for i in itens if i["id"] == args.id), None)
    if not item:
        raise SystemExit(f"Conta '{args.id}' não existe. Veja os ids com: main.py contas")

    if not args.sim and not confirmar(f"Remover '{item['nome']}'?"):
        print("Nada foi removido.")
        return

    salvar_cadastro(caminho, [i for i in itens if i["id"] != args.id])
    print(f"Conta '{args.id}' removida.")


def testar(caminho: Path, storage: Storage, args):
    item = next((i for i in ler_cadastro(caminho) if i["id"] == args.id), None)
    if not item:
        raise SystemExit(f"Conta '{args.id}' não existe. Veja os ids com: main.py contas")

    mostrar_conta(item)
    mostrar_pagamentos_encontrados(item, storage)


def buscar(storage: Storage, trecho: str):
    """Procura um trecho nas descrições dos extratos importados (para descobrir o padrão de uma conta)."""
    procurado = normalizar(trecho)
    encontradas = [t for t in storage.transacoes() if procurado in normalizar(t.descricao)]

    if not encontradas:
        print(f"Nada encontrado com '{trecho}' nos extratos importados.")
        return

    print(f"{len(encontradas)} lançamento(s) com '{trecho}':\n")
    for t in encontradas[-30:]:
        print(formatar_transacao(t))
