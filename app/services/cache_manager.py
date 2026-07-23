"""Serviços de estatísticas, visualização e limpeza de cache de material de vídeo."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Iterator

from loguru import logger

from app.utils import utils


# O material online usa o MD5 do URL como um nome de arquivo estável. O gerenciamento de cache aceita apenas este formato de nomenclatura para evitar
# Vídeos, arquivos de descrição ou outros arquivos comerciais que os usuários colocam por engano no diretório são excluídos como cache.
_VIDEO_CACHE_FILE_PATTERN = re.compile(r"^vid-[0-9a-f]{32}\.mp4$")
_SECONDS_PER_DAY = 24 * 60 * 60


@dataclass(frozen=True)
class VideoCacheStats:
    """Resultados estatísticos leves para diretórios de cache, contendo apenas metadados do sistema de arquivos."""

    file_count: int = 0
    total_size: int = 0
    oldest_mtime: float | None = None
    newest_mtime: float | None = None


@dataclass(frozen=True)
class VideoCacheCleanupResult:
    """O resultado da execução de uma limpeza permite que a exclusão parcial do arquivo falhe."""

    deleted_count: int = 0
    deleted_size: int = 0
    failed_count: int = 0


@dataclass(frozen=True)
class _VideoCacheEntry:
    """As menores informações do arquivo salvas durante a fase de digitalização para evitar a abertura ou análise do vídeo durante a limpeza."""

    path: str
    name: str
    size: int
    mtime: float


def video_cache_dir() -> str:
    """Retorna o diretório de cache de vídeo padrão para gerenciamento de projetos."""

    return os.path.realpath(utils.storage_dir("cache_videos"))


def _iter_video_cache_entries() -> Iterator[_VideoCacheEntry]:
    """Verifique sequencialmente o primeiro nível do diretório de cache padrão.

    O propósito de usar ``os.scandir`` é reutilizar os metadados retornados pela passagem de diretório quando o cache atinge dezenas de milhares de arquivos.
    Evite consultar os tipos de arquivo novamente após ``Path.iterdir``. Não há recursão, nem abertura de vídeo, nem chamada
    FFmpeg, portanto, o consumo de tempo está principalmente relacionado linearmente ao número de arquivos, não à capacidade total de vídeo."""

    cache_dir = video_cache_dir()
    try:
        entries = os.scandir(cache_dir)
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning(
            f"failed to scan video cache directory: path={cache_dir}, error={exc}"
        )
        return

    with entries:
        for entry in entries:
            if not _VIDEO_CACHE_FILE_PATTERN.fullmatch(entry.name):
                continue

            try:
                # Links simbólicos não são seguidos para garantir que a lógica de limpeza não ultrapasse os limites padrão do diretório de cache.
                if not entry.is_file(follow_symlinks=False):
                    continue
                stat_result = entry.stat(follow_symlinks=False)
            except OSError as exc:
                logger.warning(
                    f"failed to inspect video cache file: file={entry.name}, error={exc}"
                )
                continue

            yield _VideoCacheEntry(
                path=entry.path,
                name=entry.name,
                size=stat_result.st_size,
                mtime=stat_result.st_mtime,
            )


def _is_cleanup_candidate(
    entry: _VideoCacheEntry,
    max_age_days: int | None,
    now: float,
) -> bool:
    if max_age_days is None:
        return True
    return entry.mtime < now - max_age_days * _SECONDS_PER_DAY


def _validate_max_age_days(max_age_days: int | None) -> None:
    """Parâmetros de limpeza inválidos devem ser rejeitados de forma confiável, mesmo se o diretório de cache estiver vazio."""
    if max_age_days is None:
        return
    if (
        isinstance(max_age_days, bool)
        or not isinstance(max_age_days, int)
        or max_age_days <= 0
    ):
        raise ValueError("max_age_days must be a positive integer or None")


def get_video_cache_stats(max_age_days: int | None = None) -> VideoCacheStats:
    """Conte todos os caches ou visualize caches purgáveis ​​cujo tempo de modificação seja anterior a um número especificado de dias.

    ``max_age_days=None`` significa armazenar tudo em cache. O processo estatístico lê apenas o tamanho e a hora de modificação da entrada do diretório.
    O conteúdo de vídeo não é lido, portanto, mesmo que a capacidade total do cache seja grande, não haverá E/S proporcional à capacidade."""

    _validate_max_age_days(max_age_days)
    now = time.time()
    file_count = 0
    total_size = 0
    oldest_mtime = None
    newest_mtime = None

    for entry in _iter_video_cache_entries():
        if not _is_cleanup_candidate(entry, max_age_days, now):
            continue
        file_count += 1
        total_size += entry.size
        oldest_mtime = (
            entry.mtime if oldest_mtime is None else min(oldest_mtime, entry.mtime)
        )
        newest_mtime = (
            entry.mtime if newest_mtime is None else max(newest_mtime, entry.mtime)
        )

    return VideoCacheStats(
        file_count=file_count,
        total_size=total_size,
        oldest_mtime=oldest_mtime,
        newest_mtime=newest_mtime,
    )


def clean_video_cache(max_age_days: int | None = None) -> VideoCacheCleanupResult:
    """Limpa o cache de vídeo padrão e retorna resultados agregados que podem ser exibidos ao usuário.

    Pode haver um longo intervalo entre a visualização da página e o clique real para limpar, portanto, você deve digitalizar novamente e avaliar ao executar.
    Listas de candidatos antigas não podem ser reutilizadas. A exclusão adota tolerância a falhas arquivo por arquivo: registra quando um único arquivo está ocupado ou tem permissões insuficientes
    Avise e continue evitando um arquivo anormal entre centenas de arquivos, causando falha na limpeza inteira."""

    _validate_max_age_days(max_age_days)
    now = time.time()
    logger.info(
        f"start cleaning video cache: max_age_days={max_age_days}"
    )

    candidate_count = 0
    candidate_size = 0
    deleted_count = 0
    deleted_size = 0
    failed_count = 0
    cache_dir = video_cache_dir()

    # Exclua durante a digitalização sem manter a lista completa de candidatos na memória. Mesmo que o diretório cresça para centenas de milhares de arquivos,
    # A memória adicional durante o processo de limpeza permanece constante; use o unified agora durante a execução para evitar longos processos de limpeza.
    # Os tempos limite continuam mudando, criando uma gama imprevisível de candidatos.
    for entry in _iter_video_cache_entries():
        if not _is_cleanup_candidate(entry, max_age_days, now):
            continue
        candidate_count += 1
        candidate_size += entry.size
        try:
            # entry.path vem do scandir de primeiro nível do diretório padrão; verifique o diretório pai e a soma novamente antes de excluir
            # Nome do arquivo para evitar a expansão acidental do intervalo excluível ao modificar a lógica de verificação no futuro.
            if (
                os.path.realpath(os.path.dirname(entry.path)) != cache_dir
                or not _VIDEO_CACHE_FILE_PATTERN.fullmatch(entry.name)
                or os.path.islink(entry.path)
            ):
                raise ValueError("cache file is outside the managed directory")
            os.unlink(entry.path)
            deleted_count += 1
            deleted_size += entry.size
        except (OSError, ValueError) as exc:
            failed_count += 1
            logger.warning(
                f"failed to delete video cache file: file={entry.name}, error={exc}"
            )

    logger.info(
        "finished cleaning video cache: "
        f"candidates={candidate_count}, candidate_bytes={candidate_size}, "
        f"deleted={deleted_count}, deleted_bytes={deleted_size}, failed={failed_count}"
    )
    return VideoCacheCleanupResult(
        deleted_count=deleted_count,
        deleted_size=deleted_size,
        failed_count=failed_count,
    )
