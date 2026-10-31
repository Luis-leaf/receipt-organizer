from datetime import date, datetime
from pathlib import Path

import pytest

from organizer.contas import carregar_contas
from organizer.storage import Storage
from organizer.telegram import ContasBot

CONTAS_YAML = """
contas:
  - id: copel
    nome: Copel
    vencimento: 10
    padroes: ["COPEL"]
  - id: aluguel
    nome: Aluguel
    vencimento: 1
    padroes: ["IMOBILIARIA"]
"""

EU = 111
ESPOSA = 222
ESTRANHO = 999


class ApiFalsa:
    def __init__(self, atualizacoes=None):
        self.enviadas: list[tuple[int, str, list | None]] = []
        self.botoes_respondidos: list[str] = []
        self.pendentes = atualizacoes or []

    def enviar(self, chat_id, texto, botoes=None):
        self.enviadas.append((chat_id, texto, botoes))

    def atualizacoes(self, offset, espera):
        novas = [a for a in self.pendentes if a["update_id"] >= offset]
        self.pendentes = []
        return novas

    def responder_botao(self, callback_id, texto):
        self.botoes_respondidos.append(callback_id)

    def para(self, chat_id) -> list[str]:
        return [texto for destino, texto, _ in self.enviadas if destino == chat_id]


def mensagem(update_id: int, chat_id: int, texto: str, nome: str = "Ana") -> dict:
    chat = {"id": chat_id, "first_name": nome, "type": "private"}
    return {"update_id": update_id, "message": {"chat": chat, "text": texto}}


@pytest.fixture
def bot(tmp_path: Path):
    caminho = tmp_path / "contas.yaml"
    caminho.write_text(CONTAS_YAML, encoding="utf-8")
    storage = Storage(tmp_path / "db.sqlite")
    return ContasBot(ApiFalsa(), {EU, ESPOSA}, storage, carregar_contas(caminho))


def test_ignora_chat_nao_autorizado(bot):
    bot.api.pendentes = [mensagem(1, ESTRANHO, "/paguei copel", nome="Estranho")]
    bot.processar_atualizacoes()

    assert bot.api.enviadas == []
    assert bot.storage.pagamentos_manuais() == {}


def test_paguei_marca_e_avisa_a_outra_pessoa(bot):
    bot.api.pendentes = [mensagem(1, EU, "/paguei copel 2026-11")]
    bot.processar_atualizacoes()

    assert ("copel", "2026-11") in bot.storage.pagamentos_manuais()
    assert "marcada como paga" in bot.api.para(EU)[0]
    assert "Ana marcou" in bot.api.para(ESPOSA)[0]


def test_paguei_com_mes_invalido_responde_sem_quebrar(bot):
    bot.api.pendentes = [mensagem(1, EU, "/paguei copel outubro")]
    bot.processar_atualizacoes()

    assert "AAAA-MM" in bot.api.para(EU)[0]
    assert bot.storage.pagamentos_manuais() == {}


def test_comando_com_nome_do_bot_e_conta_desconhecida(bot):
    bot.api.pendentes = [mensagem(1, EU, "/paguei@meu_bot internet")]
    bot.processar_atualizacoes()
    assert "Não conheço a conta" in bot.api.para(EU)[0]


def test_desfazer(bot):
    bot.storage.marcar_pago("copel", "2026-11", date(2026, 11, 5))
    bot.api.pendentes = [mensagem(1, ESPOSA, "/desfazer copel", nome="Esposa")]
    bot.processar_atualizacoes()

    assert bot.storage.pagamentos_manuais() == {}
    assert "desfez" in bot.api.para(EU)[0]


def test_botao_do_alerta_marca_como_paga(bot):
    callback = {
        "id": "cb1",
        "data": "paguei|aluguel|2026-11",
        "from": {"first_name": "Esposa"},
        "message": {"chat": {"id": ESPOSA}},
    }
    bot.api.pendentes = [{"update_id": 7, "callback_query": callback}]
    bot.processar_atualizacoes()

    assert ("aluguel", "2026-11") in bot.storage.pagamentos_manuais()
    assert bot.api.botoes_respondidos == ["cb1"]


def test_offset_nao_reprocessa_mensagens(bot):
    bot.api.pendentes = [mensagem(5, EU, "/contas")]
    bot.processar_atualizacoes()
    assert bot.storage.ler_estado("telegram_offset") == "6"


def test_alerta_sai_uma_vez_por_dia_e_so_depois_da_hora(bot):
    # 29/10: aluguel vence em 3 dias (01/11) e não tem extrato nenhum
    cedo = datetime(2026, 10, 29, 7, 0)
    bot.enviar_alerta_se_preciso(cedo, hora_alerta=9)
    assert bot.api.enviadas == []

    depois = datetime(2026, 10, 29, 9, 30)
    bot.enviar_alerta_se_preciso(depois, hora_alerta=9)
    bot.enviar_alerta_se_preciso(depois, hora_alerta=9)

    assert len(bot.api.para(EU)) == 1
    assert len(bot.api.para(ESPOSA)) == 1

    _, texto, botoes = bot.api.enviadas[0]
    assert "Aluguel" in texto
    assert any(linha[0]["callback_data"] == "paguei|aluguel|2026-11" for linha in botoes)


def test_sem_pendencia_nao_manda_alerta(bot):
    # o mês anterior também conta: pendência de setembro sem extrato ainda gera alerta
    for mes in ("2026-09", "2026-10"):
        bot.storage.marcar_pago("copel", mes, date(2026, 10, 5))
        bot.storage.marcar_pago("aluguel", mes, date(2026, 10, 5))

    # 15/10: tudo do mês pago e nada vencendo nos próximos 3 dias
    bot.enviar_alerta_se_preciso(datetime(2026, 10, 15, 10, 0), hora_alerta=9)
    assert bot.api.enviadas == []
