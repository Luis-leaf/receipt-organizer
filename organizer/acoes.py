"""Ações usadas tanto pela linha de comando quanto pelo bot do Telegram."""

import shutil
from datetime import date
from pathlib import Path

from organizer.contas import Conta
from organizer.importers import ler_extrato
from organizer.log import get_logger
from organizer.models import Extrato, nome_banco
from organizer.reconcile import Competencia, Conciliador
from organizer.storage import Storage

logger = get_logger()


def criar_conciliador(storage: Storage, contas: list[Conta]) -> Conciliador:
    return Conciliador(
        contas=contas,
        transacoes=storage.transacoes(),
        manuais=storage.pagamentos_manuais(),
        intervalos=storage.intervalos(),
    )


def buscar_conta(contas: list[Conta], conta_id: str) -> Conta | None:
    for conta in contas:
        if conta.id == conta_id.lower():
            return conta
    return None


def arquivar(arquivo: Path, extrato: Extrato, destino_base: Path) -> Path:
    destino_dir = destino_base / extrato.banco / str(extrato.fim.year)
    destino_dir.mkdir(parents=True, exist_ok=True)

    extensao = arquivo.suffix.lower()
    base_name = f"{extrato.banco}_{extrato.inicio:%Y-%m-%d}_{extrato.fim:%Y-%m-%d}"
    destino = destino_dir / f"{base_name}{extensao}"

    contador = 1
    while destino.exists():
        destino = destino_dir / f"{base_name}_{contador}{extensao}"
        contador += 1

    shutil.move(arquivo, destino)
    logger.info(f"Extrato arquivado em: {destino}")
    return destino


def importar_arquivos(storage: Storage, arquivos: list[Path], destino: Path | None) -> list[str]:
    """Importa os extratos e, se `destino` for dado, arquiva cada um lá.

    Retorna uma mensagem por arquivo, para mostrar no terminal ou no Telegram.
    """
    mensagens = []

    for arquivo in arquivos:
        try:
            extrato = ler_extrato(arquivo)
        except Exception:
            logger.exception(f"Falha ao ler {arquivo.name} (arquivo mantido onde está)")
            mensagens.append(f"{arquivo.name}: não consegui ler esse arquivo (veja o log)")
            continue

        novas = storage.salvar_extrato(extrato, arquivo.name)

        msg = (
            f"{nome_banco(extrato.banco)} de {extrato.inicio:%d/%m/%Y} a {extrato.fim:%d/%m/%Y}: "
            f"{len(extrato.transacoes)} lançamentos ({novas} novos) · {arquivo.name}"
        )
        logger.info(msg)
        mensagens.append(msg)

        if destino:
            arquivar(arquivo, extrato, destino)

    return mensagens


def marcar_paga(storage: Storage, contas: list[Conta], conta: Conta, mes: str | None, hoje: date) -> Competencia:
    """Marca a conta como paga. Sem `mes`, usa a competência em aberto mais provável."""
    if mes:
        competencia = Competencia.de_texto(mes)
    else:
        competencia = criar_conciliador(storage, contas).proxima_em_aberto(conta, hoje)

    storage.marcar_pago(conta.id, str(competencia), hoje)
    logger.info(f"{conta.id} {competencia} marcada como paga")
    return competencia


def desmarcar_paga(storage: Storage, conta: Conta, mes: str | None) -> str | None:
    """Remove a marcação manual. Sem `mes`, desfaz a mais recente daquela conta.

    Retorna a competência desmarcada, ou None se não havia marcação.
    """
    if not mes:
        marcadas = [comp for (conta_id, comp) in storage.pagamentos_manuais() if conta_id == conta.id]
        if not marcadas:
            return None
        mes = max(marcadas)

    if not storage.desmarcar_pago(conta.id, mes):
        return None

    logger.info(f"{conta.id} {mes} desmarcada")
    return mes
