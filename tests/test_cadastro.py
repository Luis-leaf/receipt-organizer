from argparse import Namespace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from organizer import cadastro
from organizer.contas import carregar_contas, gerar_id, ler_cadastro
from organizer.models import Extrato, Transacao
from organizer.storage import Storage


def argumentos(**valores) -> Namespace:
    padrao = {"id": None, "nome": None, "vencimento": None, "padrao": None, "banco": None, "meio": None, "sim": True}
    return Namespace(**{**padrao, **valores})


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    storage = Storage(tmp_path / "db.sqlite")
    pagamento = Transacao("nubank", "1", date(2026, 10, 6), Decimal("-212.22"), "Pagamento de boleto - COPEL DIS", "x")
    storage.salvar_extrato(Extrato("nubank", "1", date(2026, 10, 1), date(2026, 10, 31), [pagamento]), "a.ofx")
    return storage


@pytest.fixture
def sem_terminal(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)


def test_gerar_id():
    assert gerar_id("Fatura Itaú Uniclass") == "fatura-itau-uniclass"
    assert gerar_id("Água & Esgoto") == "agua-esgoto"


def test_adicionar_por_argumentos_cria_o_arquivo(tmp_path, storage, sem_terminal, capsys):
    caminho = tmp_path / "contas.yaml"
    cadastro.adicionar(caminho, storage, argumentos(nome="Energia", vencimento=10, padrao=["COPEL"]))

    [conta] = carregar_contas(caminho)
    assert (conta.id, conta.nome, conta.vencimento, conta.meio) == ("energia", "Energia", 10, "boleto")
    # mostra o pagamento que bate, para a pessoa conferir o padrão
    assert "COPEL DIS" in capsys.readouterr().out


def test_adicionar_recusa_id_repetido(tmp_path, storage, sem_terminal):
    caminho = tmp_path / "contas.yaml"
    cadastro.adicionar(caminho, storage, argumentos(nome="Energia", vencimento=10, padrao=["COPEL"]))

    with pytest.raises(SystemExit):
        cadastro.adicionar(caminho, storage, argumentos(nome="Energia", vencimento=12, padrao=["ENEL"]))


def test_conta_invalida_nao_e_gravada(tmp_path, storage, sem_terminal):
    caminho = tmp_path / "contas.yaml"

    with pytest.raises(SystemExit):
        cadastro.adicionar(caminho, storage, argumentos(nome="Energia", vencimento=40, padrao=["COPEL"]))
    assert not caminho.exists()


def test_editar_interativo_mantem_o_que_ficar_vazio(tmp_path, storage, sem_terminal, monkeypatch):
    caminho = tmp_path / "contas.yaml"
    cadastro.adicionar(caminho, storage, argumentos(nome="Energia", vencimento=10, padrao=["COPEL"]))

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # nome, vencimento, padrões, banco, meio: só muda o vencimento
    respostas = iter(["", "12", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda _texto: next(respostas))
    cadastro.editar(caminho, storage, argumentos(id="energia", sim=False))

    [item] = ler_cadastro(caminho)
    assert item["vencimento"] == 12
    assert item["nome"] == "Energia"
    assert item["padroes"] == ["COPEL"]
    assert "banco" not in item


def test_remover(tmp_path, storage, sem_terminal):
    caminho = tmp_path / "contas.yaml"
    cadastro.adicionar(caminho, storage, argumentos(nome="Energia", vencimento=10, padrao=["COPEL"]))
    cadastro.remover(caminho, argumentos(id="energia"))
    assert ler_cadastro(caminho) == []


def test_buscar_ignora_acento_e_caixa(storage, capsys):
    cadastro.buscar(storage, "copél")
    assert "- R$ 212,22" in capsys.readouterr().out
