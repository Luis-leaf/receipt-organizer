import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from organizer.models import Extrato, Transacao

SCHEMA = """
CREATE TABLE IF NOT EXISTS transacoes (
    chave TEXT PRIMARY KEY,
    banco TEXT NOT NULL,
    conta TEXT NOT NULL,
    data TEXT NOT NULL,
    valor TEXT NOT NULL,
    descricao TEXT NOT NULL,
    id_externo TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS importacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    banco TEXT NOT NULL,
    conta TEXT NOT NULL,
    inicio TEXT NOT NULL,
    fim TEXT NOT NULL,
    arquivo TEXT NOT NULL,
    importado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pagamentos_manuais (
    conta_id TEXT NOT NULL,
    competencia TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (conta_id, competencia)
);

-- pequenos valores que precisam sobreviver entre execuções (ex.: offset do Telegram)
CREATE TABLE IF NOT EXISTS estado (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, caminho: Path):
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(caminho)
        self.conn.executescript(SCHEMA)

    def salvar_extrato(self, extrato: Extrato, arquivo: str) -> int:
        """Grava as transações ignorando as já importadas. Retorna quantas eram novas."""
        antes = self.conn.total_changes
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO transacoes VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (t.chave, t.banco, t.conta, t.data.isoformat(), str(t.valor), t.descricao, t.id_externo)
                    for t in extrato.transacoes
                ],
            )
            novas = self.conn.total_changes - antes
            self.conn.execute(
                "INSERT INTO importacoes (banco, conta, inicio, fim, arquivo, importado_em) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    extrato.banco,
                    extrato.conta,
                    extrato.inicio.isoformat(),
                    extrato.fim.isoformat(),
                    arquivo,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        return novas

    def transacoes(self) -> list[Transacao]:
        linhas = self.conn.execute(
            "SELECT banco, conta, data, valor, descricao, id_externo FROM transacoes ORDER BY data"
        )
        return [
            Transacao(banco, conta, date.fromisoformat(data), Decimal(valor), descricao, id_externo)
            for banco, conta, data, valor, descricao, id_externo in linhas
        ]

    def cobertura(self) -> dict[str, date]:
        """Até que dia cada banco tem extrato importado."""
        linhas = self.conn.execute("SELECT banco, MAX(fim) FROM importacoes GROUP BY banco ORDER BY banco")
        return {banco: date.fromisoformat(fim) for banco, fim in linhas}

    def intervalos(self) -> dict[str, list[tuple[date, date]]]:
        """Períodos cobertos pelos extratos importados, por banco."""
        intervalos: dict[str, list[tuple[date, date]]] = {}
        for banco, inicio, fim in self.conn.execute("SELECT DISTINCT banco, inicio, fim FROM importacoes"):
            intervalos.setdefault(banco, []).append((date.fromisoformat(inicio), date.fromisoformat(fim)))
        return intervalos

    def marcar_pago(self, conta_id: str, competencia: str, data: date):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO pagamentos_manuais VALUES (?, ?, ?)",
                (conta_id, competencia, data.isoformat()),
            )

    def desmarcar_pago(self, conta_id: str, competencia: str) -> bool:
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM pagamentos_manuais WHERE conta_id = ? AND competencia = ?",
                (conta_id, competencia),
            )
        return cursor.rowcount > 0

    def pagamentos_manuais(self) -> dict[tuple[str, str], date]:
        linhas = self.conn.execute("SELECT conta_id, competencia, data FROM pagamentos_manuais")
        return {(conta_id, comp): date.fromisoformat(data) for conta_id, comp, data in linhas}

    def ler_estado(self, chave: str, padrao: str | None = None) -> str | None:
        linha = self.conn.execute("SELECT valor FROM estado WHERE chave = ?", (chave,)).fetchone()
        return linha[0] if linha else padrao

    def gravar_estado(self, chave: str, valor: str):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO estado VALUES (?, ?)", (chave, valor))
