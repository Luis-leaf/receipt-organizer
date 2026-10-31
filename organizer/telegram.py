import html
import json
import urllib.error
import urllib.request
from datetime import date, datetime

from organizer.acoes import buscar_conta, criar_conciliador, desmarcar_paga, marcar_paga
from organizer.contas import Conta
from organizer.log import get_logger
from organizer.reconcile import Competencia, Situacao, Status
from organizer.report import agrupar, descrever, titulo_mes
from organizer.storage import Storage

logger = get_logger()

AJUDA = """📋 <b>Comandos</b>

/status
Contas que importam agora

/mes 2026-10
Todas as contas de um mês

/contas
Lista as contas e seus ids

/paguei copel
Marca como paga (o extrato confirma depois)

/paguei copel 2026-10
Marca um mês específico

/desfazer copel
Desfaz a última marcação

🔔 Todo dia de manhã eu aviso o que vence nos próximos dias ou ficou para trás."""

# Avisa quando faltam até esses dias para vencer
DIAS_DE_AVISO = 3


class TelegramErro(Exception):
    pass


class TelegramApi:
    """Chamadas HTTP para a Bot API, sem dependências externas."""

    def __init__(self, token: str):
        self.url_base = f"https://api.telegram.org/bot{token}"

    def chamar(self, metodo: str, espera_http: int = 20, **params):
        requisicao = urllib.request.Request(
            f"{self.url_base}/{metodo}",
            data=json.dumps(params).encode(),
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(requisicao, timeout=espera_http) as resposta:
                dados = json.load(resposta)
        except urllib.error.HTTPError as erro:
            # a mensagem de erro não leva a URL, porque ela contém o token
            detalhe = erro.read().decode(errors="replace")
            raise TelegramErro(f"{metodo}: HTTP {erro.code} {detalhe}") from None

        if not dados.get("ok"):
            raise TelegramErro(f"{metodo}: {dados}")
        return dados["result"]

    def enviar(self, chat_id: int, texto: str, botoes: list[list[dict]] | None = None):
        params = {"chat_id": chat_id, "text": texto, "parse_mode": "HTML"}
        if botoes:
            params["reply_markup"] = {"inline_keyboard": botoes}
        self.chamar("sendMessage", **params)

    def atualizacoes(self, offset: int, espera: int) -> list[dict]:
        # Long polling: o Telegram segura a conexão até chegar mensagem ou passar
        # `espera` segundos. Por isso a espera do HTTP precisa ser um pouco maior.
        return self.chamar("getUpdates", espera_http=espera + 10, offset=offset, timeout=espera)

    def responder_botao(self, callback_id: str, texto: str):
        self.chamar("answerCallbackQuery", callback_query_id=callback_id, text=texto)


class ContasBot:
    def __init__(self, api: TelegramApi, chat_ids: set[int], storage: Storage, contas: list[Conta]):
        self.api = api
        self.chat_ids = chat_ids
        self.storage = storage
        self.contas = contas

    def registrar_comandos(self):
        """Faz os comandos aparecerem no menu "/" do Telegram."""
        comandos = [
            {"command": "status", "description": "contas que importam agora"},
            {"command": "mes", "description": "todas as contas de um mês (ex.: /mes 2026-10)"},
            {"command": "contas", "description": "lista as contas e seus ids"},
            {"command": "paguei", "description": "marca uma conta como paga (ex.: /paguei copel)"},
            {"command": "desfazer", "description": "desfaz a última marcação (ex.: /desfazer copel)"},
        ]
        self.api.chamar("setMyCommands", commands=comandos)

    # --- envio -------------------------------------------------------------

    def avisar_todos(self, texto: str, botoes: list[list[dict]] | None = None, exceto: int | None = None):
        for chat_id in self.chat_ids:
            if chat_id == exceto:
                continue
            try:
                self.api.enviar(chat_id, texto, botoes)
            except Exception:
                logger.exception(f"Falha ao enviar mensagem para o chat {chat_id}")

    def enviar_alerta_se_preciso(self, agora: datetime, hora_alerta: int):
        """Manda o alerta do dia uma única vez, na primeira volta depois de `hora_alerta`.

        Se o computador estava desligado no horário, o alerta sai quando ele ligar.
        """
        hoje = agora.date()
        if agora.hour < hora_alerta:
            return
        if self.storage.ler_estado("ultimo_alerta") == hoje.isoformat():
            return

        alerta = self.montar_alerta(hoje)
        if alerta:
            texto, botoes = alerta
            self.avisar_todos(texto, botoes)
            logger.info("Alerta diário enviado")
        else:
            logger.info("Nada para alertar hoje")

        self.storage.gravar_estado("ultimo_alerta", hoje.isoformat())

    def montar_alerta(self, hoje: date) -> tuple[str, list[list[dict]]] | None:
        situacoes = [s for s in self._conciliador().painel(hoje) if precisa_de_atencao(s, hoje)]
        if not situacoes:
            return None

        texto = formatar_secoes(f"🔔 Contas para hoje, {hoje:%d/%m}", situacoes, hoje)
        texto += "\n\n💡 Já pagou? Toque no botão. O extrato confirma depois."

        botoes = []
        for s in situacoes:
            rotulo = f"✅ Paguei: {s.conta.nome} ({s.vencimento:%d/%m})"
            botoes.append([{"text": rotulo, "callback_data": f"paguei|{s.conta.id}|{s.competencia}"}])

        return texto, botoes

    # --- recebimento -------------------------------------------------------

    def processar_atualizacoes(self, espera: int = 50):
        offset = int(self.storage.ler_estado("telegram_offset", "0"))

        for atualizacao in self.api.atualizacoes(offset, espera):
            # grava antes de tratar: se a mensagem quebrar o bot, ela não volta em loop
            self.storage.gravar_estado("telegram_offset", str(atualizacao["update_id"] + 1))

            try:
                if "message" in atualizacao:
                    self._tratar_mensagem(atualizacao["message"])
                elif "callback_query" in atualizacao:
                    self._tratar_botao(atualizacao["callback_query"])
            except Exception:
                logger.exception(f"Erro ao tratar a atualização {atualizacao['update_id']}")

    def _autorizado(self, chat: dict) -> bool:
        if chat.get("id") in self.chat_ids:
            return True
        logger.warning(f"Mensagem ignorada de chat não autorizado: {chat.get('id')} ({chat.get('first_name')})")
        return False

    def _tratar_mensagem(self, mensagem: dict):
        chat = mensagem.get("chat", {})
        if not self._autorizado(chat):
            return

        texto = (mensagem.get("text") or "").strip()
        resposta = self.responder(texto, quem=chat.get("first_name", "alguém"), chat_id=chat["id"])
        self.api.enviar(chat["id"], resposta)

    def _tratar_botao(self, callback: dict):
        chat = callback.get("message", {}).get("chat", {})
        if not self._autorizado(chat):
            return

        acao, conta_id, mes = callback.get("data", "").split("|")
        if acao != "paguei":
            return

        resposta = self.comando_paguei(
            [conta_id, mes], quem=callback["from"].get("first_name", "alguém"), chat_id=chat["id"]
        )
        self.api.responder_botao(callback["id"], "Marcado!")
        self.api.enviar(chat["id"], resposta)

    # --- comandos ----------------------------------------------------------

    def responder(self, texto: str, quem: str, chat_id: int) -> str:
        partes = texto.split()
        if not partes:
            return AJUDA

        # "/paguei@meu_bot copel" -> "/paguei"
        comando = partes[0].split("@")[0].lower()
        argumentos = partes[1:]

        if comando == "/status":
            return self.comando_status()
        if comando == "/mes":
            return self.comando_mes(argumentos)
        if comando == "/contas":
            return self.comando_contas()
        if comando == "/paguei":
            return self.comando_paguei(argumentos, quem, chat_id)
        if comando == "/desfazer":
            return self.comando_desfazer(argumentos, quem, chat_id)
        return AJUDA

    def comando_status(self) -> str:
        hoje = date.today()
        situacoes = self._conciliador().painel(hoje)
        return formatar_secoes(f"📊 Contas em {hoje:%d/%m}", situacoes, hoje)

    def comando_mes(self, argumentos: list[str]) -> str:
        hoje = date.today()
        try:
            competencia = Competencia.de_texto(argumentos[0]) if argumentos else Competencia.de_data(hoje)
        except ValueError:
            return "Use o formato AAAA-MM, ex.: /mes 2026-10"

        situacoes = self._conciliador().mes(competencia)
        return formatar_secoes(f"📅 {titulo_mes(competencia)}", situacoes, hoje)

    def comando_contas(self) -> str:
        linhas = ["📋 <b>Contas cadastradas</b>"]
        for c in sorted(self.contas, key=lambda c: c.vencimento):
            linhas.append("")
            linhas.append(f"<b>{html.escape(c.nome)}</b>")
            linhas.append(f"Vence dia {c.vencimento} · id: <code>{c.id}</code>")
        return "\n".join(linhas)

    def comando_paguei(self, argumentos: list[str], quem: str, chat_id: int) -> str:
        if not argumentos:
            return "Qual conta? Ex.: /paguei copel  (veja os ids em /contas)"

        conta = buscar_conta(self.contas, argumentos[0])
        if not conta:
            return f"Não conheço a conta <code>{html.escape(argumentos[0])}</code>. Veja os ids em /contas"

        mes = argumentos[1] if len(argumentos) > 1 else None
        try:
            competencia = marcar_paga(self.storage, self.contas, conta, mes, date.today())
        except ValueError:
            return "Use o mês no formato AAAA-MM, ex.: /paguei copel 2026-10"
        vencimento = competencia.vencimento(conta.vencimento)

        # avisa a outra pessoa para ninguém pagar a mesma conta duas vezes
        nome = html.escape(conta.nome)
        self.avisar_todos(
            f"☑️ {html.escape(quem)} marcou <b>{nome}</b> (vence {vencimento:%d/%m}) como paga",
            exceto=chat_id,
        )
        return f"☑️ <b>{nome}</b> (vence {vencimento:%d/%m}) marcada como paga.\n\nQuando o extrato chegar, eu confirmo."

    def comando_desfazer(self, argumentos: list[str], quem: str, chat_id: int) -> str:
        if not argumentos:
            return "Qual conta? Ex.: /desfazer copel"

        conta = buscar_conta(self.contas, argumentos[0])
        if not conta:
            return f"Não conheço a conta <code>{html.escape(argumentos[0])}</code>. Veja os ids em /contas"

        mes = argumentos[1] if len(argumentos) > 1 else None
        desmarcada = desmarcar_paga(self.storage, conta, mes)
        if not desmarcada:
            return f"{html.escape(conta.nome)} não tinha nenhuma marcação manual."

        nome = html.escape(conta.nome)
        self.avisar_todos(f"↩️ {html.escape(quem)} desfez a marcação de <b>{nome}</b> ({desmarcada})", exceto=chat_id)
        return f"↩️ Marcação de <b>{nome}</b> ({desmarcada}) desfeita."

    def _conciliador(self):
        # recria a cada uso para enxergar extratos e marcações novos
        return criar_conciliador(self.storage, self.contas)


def precisa_de_atencao(s: Situacao, hoje: date) -> bool:
    status = s.status(hoje)
    if status in (Status.ATRASADA, Status.FALTA_EXTRATO, Status.VENCE_HOJE):
        return True
    return status == Status.A_VENCER and s.dias_para_vencer(hoje) <= DIAS_DE_AVISO


def formatar_secoes(titulo: str, situacoes: list[Situacao], hoje: date) -> str:
    """Contas separadas por seção, com uma linha em branco entre cada uma.

    Duas linhas por conta: no celular o alinhamento em colunas do terminal quebra.
    """
    linhas = [f"<b>{html.escape(titulo)}</b>"]

    if not situacoes:
        linhas.append("")
        linhas.append("Nenhuma conta para mostrar.")
        return "\n".join(linhas)

    for nome_secao, do_grupo in agrupar(situacoes, hoje):
        linhas.append("")
        linhas.append(f"<u>{nome_secao} ({len(do_grupo)})</u>")
        for s in do_grupo:
            icone, detalhe = descrever(s, hoje)
            linhas.append("")
            linhas.append(f"{icone} <b>{s.vencimento:%d/%m} · {html.escape(s.conta.nome)}</b>")
            linhas.append(html.escape(detalhe))

    return "\n".join(linhas)
