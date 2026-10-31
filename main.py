import argparse
import fcntl
import os
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

from organizer import cadastro
from organizer.acoes import buscar_conta, criar_conciliador, desmarcar_paga, importar_arquivos, marcar_paga
from organizer.contas import MEIOS, Conta, carregar_contas
from organizer.importers import EXTENSOES
from organizer.log import get_logger
from organizer.models import NOMES_BANCOS
from organizer.reconcile import Competencia
from organizer.report import montar_relatorio, titulo_mes
from organizer.storage import Storage
from organizer.telegram import ContasBot, TelegramApi

load_dotenv()


LOCK_FILE = Path.cwd() / ".process.lock"

logger = get_logger()


@contextmanager
def trava_da_entrada():
    """Impede dois processos de importar (e mover) os mesmos arquivos ao mesmo tempo.

    Usa flock: se o processo morrer, o sistema libera a trava sozinho.
    Entrega True se conseguiu a trava, False se outro processo já está importando.
    """
    with open(LOCK_FILE, "w") as arquivo:
        try:
            fcntl.flock(arquivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return

        try:
            yield True
        finally:
            fcntl.flock(arquivo, fcntl.LOCK_UN)


def env_path(nome: str, padrao: str | None = None) -> Path:
    valor = os.getenv(nome, padrao)
    if not valor:
        raise SystemExit(f"Variável {nome} não definida no .env")
    return Path(valor).expanduser()


def arquivos_da_entrada() -> list[Path]:
    entrada = env_path("PATH_ENTRADA")
    if not entrada.exists():
        raise SystemExit(f"PATH_ENTRADA não existe: {entrada}")

    arquivos = []
    for path in sorted(entrada.iterdir()):
        if path.is_file() and path.suffix.lower() in EXTENSOES:
            arquivos.append(path)
    return arquivos


def importar_entrada(storage: Storage) -> list[str]:
    with trava_da_entrada() as consegui:
        if not consegui:
            logger.info("Outro processo está importando; pulando")
            return []
        return importar_arquivos(storage, arquivos_da_entrada(), destino=env_path("PATH_EXTRATOS"))


def carregar() -> tuple[list[Conta], Storage]:
    contas = carregar_contas(env_path("CONTAS_PATH", "contas.yaml"))
    storage = Storage(env_path("DB_PATH", "data/organizer.db"))
    return contas, storage


def comando_importar(storage: Storage, arquivos: list[Path]):
    # Arquivos passados na linha de comando ficam onde estão;
    # os da pasta de entrada são arquivados depois de importados.
    if arquivos:
        mensagens = importar_arquivos(storage, arquivos, destino=None)
    else:
        mensagens = importar_entrada(storage)

    if not mensagens:
        print("Nenhum extrato novo para importar.")
    for msg in mensagens:
        print(msg)


def comando_status(storage: Storage, contas: list[Conta], mes: str | None):
    conciliador = criar_conciliador(storage, contas)
    hoje = date.today()

    if mes:
        competencia = Competencia.de_texto(mes)
        situacoes = conciliador.mes(competencia)
        titulo = titulo_mes(competencia)
    else:
        situacoes = conciliador.painel(hoje)
        titulo = f"Contas em {hoje:%d/%m/%Y}"

    print()
    print(montar_relatorio(titulo, situacoes, storage.cobertura(), hoje))


def comando_paguei(storage: Storage, contas: list[Conta], conta_id: str, mes: str | None, desfazer: bool):
    conta = buscar_conta(contas, conta_id)
    if not conta:
        raise SystemExit(f"Conta '{conta_id}' não existe. Veja os ids com: main.py contas")

    if desfazer:
        desmarcada = desmarcar_paga(storage, conta, mes)
        if desmarcada:
            print(f"{conta.nome} {desmarcada}: marcação removida")
        else:
            print(f"{conta.nome}: não havia marcação manual")
        return

    competencia = marcar_paga(storage, contas, conta, mes, date.today())
    vencimento = competencia.vencimento(conta.vencimento)
    print(f"{conta.nome} {competencia} (vence {vencimento:%d/%m}) marcada como paga")


def comando_contas(caminho: Path, storage: Storage, args):
    if args.acao == "adicionar":
        cadastro.adicionar(caminho, storage, args)
    elif args.acao == "editar":
        cadastro.editar(caminho, storage, args)
    elif args.acao == "remover":
        cadastro.remover(caminho, args)
    elif args.acao == "testar":
        cadastro.testar(caminho, storage, args)
    else:
        cadastro.listar(caminho)


def comando_bot():
    """Fica rodando: responde no Telegram, importa a pasta de entrada e manda o alerta diário."""
    token = os.getenv("TELEGRAM_TOKEN")
    chat_ids = {int(c) for c in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()}
    if not token or not chat_ids:
        raise SystemExit("Defina TELEGRAM_TOKEN e TELEGRAM_CHAT_IDS no .env")

    hora_alerta = int(os.getenv("HORA_ALERTA", "9"))
    contas, storage = carregar()
    bot = ContasBot(TelegramApi(token), chat_ids, storage, contas)
    bot.registrar_comandos()

    logger.info(f"Bot iniciado para {len(chat_ids)} chat(s), alerta às {hora_alerta}h")
    print("Bot rodando. Ctrl+C para parar.")

    while True:
        try:
            # relê o cadastro a cada volta: editar o contas.yaml não exige reiniciar
            bot.contas = carregar_contas(env_path("CONTAS_PATH", "contas.yaml"))

            for msg in importar_entrada(storage):
                bot.avisar_todos(f"📥 <b>Extrato importado</b>\n\n{msg}")

            bot.enviar_alerta_se_preciso(datetime.now(), hora_alerta)
            bot.processar_atualizacoes(espera=50)

        except KeyboardInterrupt:
            print("\nBot parado.")
            return
        except Exception:
            # sem internet, Telegram fora do ar etc.: espera e tenta de novo
            logger.exception("Erro no ciclo do bot; tentando de novo em 30s")
            time.sleep(30)


def adicionar_argumentos_de_conta(parser: argparse.ArgumentParser):
    parser.add_argument("--nome", help='nome para mostrar, ex.: "Energia"')
    parser.add_argument("--vencimento", type=int, help="dia do mês, de 1 a 31")
    parser.add_argument(
        "--padrao", action="append", help="trecho da descrição no extrato (repita para mais de um), ex.: COPEL"
    )
    parser.add_argument("--banco", help=f"só procura nos extratos desse banco: {', '.join(NOMES_BANCOS)}")
    parser.add_argument("--meio", choices=MEIOS, help="meio de pagamento (padrão: boleto)")


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Concilia extratos com as contas do mês. Sem comando: importa a pasta de entrada e mostra o painel."
    )
    sub = parser.add_subparsers(dest="comando")

    importar = sub.add_parser("importar", help="importa extratos (padrão: os da PATH_ENTRADA, que são arquivados)")
    importar.add_argument("arquivos", nargs="*", type=Path, help="arquivos específicos (não são movidos)")

    status = sub.add_parser("status", help="mostra as contas (padrão: painel de hoje)")
    status.add_argument("--mes", help="competência AAAA-MM para ver o mês inteiro")

    paguei = sub.add_parser("paguei", help="marca uma conta como paga sem esperar o extrato")
    paguei.add_argument("conta", help="id da conta no contas.yaml")
    paguei.add_argument("--mes", help="competência AAAA-MM (padrão: a mais provável)")
    paguei.add_argument("--desfazer", action="store_true", help="remove a marcação manual (padrão: a mais recente)")

    contas = sub.add_parser("contas", help="lista e edita as contas do contas.yaml (padrão: listar)")
    contas_sub = contas.add_subparsers(dest="acao")
    contas_sub.add_parser("listar", help="lista as contas cadastradas")

    adicionar = contas_sub.add_parser("adicionar", help="cadastra uma conta (pergunta o que faltar)")
    adicionar.add_argument("--id", help="identificador curto (padrão: gerado a partir do nome)")
    adicionar_argumentos_de_conta(adicionar)
    adicionar.add_argument("--sim", action="store_true", help="salva sem pedir confirmação")

    editar = contas_sub.add_parser("editar", help="altera uma conta (sem opções, pergunta campo a campo)")
    editar.add_argument("id")
    adicionar_argumentos_de_conta(editar)

    remover = contas_sub.add_parser("remover", help="remove uma conta")
    remover.add_argument("id")
    remover.add_argument("--sim", action="store_true", help="remove sem pedir confirmação")

    testar = contas_sub.add_parser("testar", help="mostra quais pagamentos dos extratos batem com a conta")
    testar.add_argument("id")

    buscar = sub.add_parser("buscar", help="procura um trecho nos extratos (para descobrir o padrão de uma conta)")
    buscar.add_argument("trecho")

    sub.add_parser("bot", help="roda o bot do Telegram (alertas, comandos e importação automática)")

    return parser


def main():
    args = criar_parser().parse_args()

    if args.comando == "bot":
        comando_bot()
        return

    caminho_contas = env_path("CONTAS_PATH", "contas.yaml")
    contas, storage = carregar()

    if args.comando == "importar":
        comando_importar(storage, args.arquivos)

    elif args.comando == "status":
        comando_status(storage, contas, args.mes)

    elif args.comando == "paguei":
        comando_paguei(storage, contas, args.conta, args.mes, args.desfazer)

    elif args.comando == "contas":
        comando_contas(caminho_contas, storage, args)

    elif args.comando == "buscar":
        cadastro.buscar(storage, args.trecho)

    else:
        comando_importar(storage, [])
        comando_status(storage, contas, mes=None)


if __name__ == "__main__":
    main()
