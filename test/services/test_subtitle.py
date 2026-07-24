import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Quando o arquivo de teste é executado diretamente, o pacote do aplicativo também pode ser importado do diretório raiz do warehouse.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services import subtitle


class TestSubtitleService(unittest.TestCase):
    def test_file_to_subtitles_returns_empty_for_missing_input(self):
        """Tanto os caminhos vazios quanto os arquivos inexistentes devem retornar com segurança uma lista vazia."""
        self.assertEqual(subtitle.file_to_subtitles(""), [])
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_file = Path(tmp_dir) / "missing.srt"
            self.assertEqual(subtitle.file_to_subtitles(str(missing_file)), [])

    def test_levenshtein_distance_and_similarity_cover_common_boundaries(self):
        """
        A correção de legenda depende da distância de edição para escolher se deseja continuar mesclando legendas adjacentes, de modo que cubra a string vazia,
        Existem quatro limites: troca de parâmetros, ignorar casos e dissimilaridade óbvia, para evitar fusões erradas após o ajuste do algoritmo.
        """
        self.assertEqual(subtitle.levenshtein_distance("kitten", "sitting"), 3)
        self.assertEqual(subtitle.levenshtein_distance("a", "longer"), 6)
        self.assertEqual(subtitle.levenshtein_distance("hello", ""), 5)
        self.assertEqual(subtitle.similarity("Hello", "hello"), 1.0)
        self.assertLess(subtitle.similarity("hello", "world"), 0.5)

    def test_create_returns_empty_when_whisper_is_unavailable(self):
        """As dependências opcionais do Whisper devem ser ignoradas se não estiverem instaladas, em vez de lançar uma exceção no thread da tarefa."""
        with patch.object(subtitle, "WhisperModel", None):
            self.assertEqual(subtitle.create("audio.mp3"), "")

    def test_create_returns_none_when_whisper_model_cannot_load(self):
        """Quando o download ou inicialização do modelo falha, um resultado de falha deve ser retornado e a camada de tarefa pode atualizar o status."""
        with patch.object(subtitle, "model", None), patch.object(
            subtitle,
            "WhisperModel",
            side_effect=RuntimeError("model unavailable"),
        ):
            self.assertIsNone(subtitle.create("audio.mp3"))

    def test_create_writes_punctuated_and_trailing_segments(self):
        """
        Usa um modelo Whisper falso para substituir o processamento de carimbo de data/hora palavra por palavra, sem acessar a rede ou carregar o modelo real.
        Um segmento contém quebras de pontuação e texto sem pontuação no final, o que pode verificar dois caminhos críticos de escrita.
        """

        class _FakeWhisperModel:
            def __init__(self, **kwargs):
                self.init_kwargs = kwargs

            def transcribe(self, audio_file, **kwargs):
                words = [
                    SimpleNamespace(start=0.0, end=0.4, word="Hello"),
                    SimpleNamespace(start=0.4, end=0.9, word=" world."),
                    SimpleNamespace(start=1.0, end=1.5, word="Again"),
                ]
                segment = SimpleNamespace(
                    start=0.0,
                    end=1.8,
                    words=words,
                )
                info = SimpleNamespace(language="en", language_probability=0.99)
                return [segment], info

        with tempfile.TemporaryDirectory() as tmp_dir:
            subtitle_file = Path(tmp_dir) / "generated.srt"
            with patch.object(subtitle, "model", None), patch.object(
                subtitle,
                "WhisperModel",
                _FakeWhisperModel,
            ):
                subtitle.create("audio.mp3", str(subtitle_file))

            items = subtitle.file_to_subtitles(str(subtitle_file))

        self.assertEqual([item[2] for item in items], ["Hello world", "Again"])

    def test_correct_ignores_markdown_separator_lines(self):
        """
        Whisper fallback A fase de correção também deve ser ignorada `---` Este tipo de linha de script insondável.

        Se você continuar mantendo o delimitador Markdown aqui,`correct()` vai pensar que o script tem mais linhas do que
        O número de linhas de legendas e preenchimento `00:00:00,000 --> 00:00:00,000`, o software de edição irá
        O SRT gerado foi determinado para não ser importado.
        """
        original_srt = (
            "1\n"
            "00:00:00,100 --> 00:00:01,000\n"
            "primeiro parágrafo\n\n"
            "2\n"
            "00:00:01,100 --> 00:00:02,000\n"
            "Segundo parágrafo\n\n"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            subtitle_file = Path(tmp_dir) / "subtitle.srt"
            subtitle_file.write_text(original_srt, encoding="utf-8")

            subtitle.correct(
                subtitle_file=str(subtitle_file),
                video_script="primeiro parágrafo\n---\nSegundo parágrafo",
            )

            corrected_srt = subtitle_file.read_text(encoding="utf-8")

        self.assertIn("primeiro parágrafo", corrected_srt)
        self.assertIn("Segundo parágrafo", corrected_srt)
        self.assertNotIn("---", corrected_srt)
        self.assertNotIn("00:00:00,000 --> 00:00:00,000", corrected_srt)

    def test_correct_merges_adjacent_subtitles_for_one_script_sentence(self):
        """
        Whisper Talvez divida uma frase de cópia em vários períodos de tempo. A lógica de correção deve mesclar intervalos de tempo e restaurar
        Texto original do roteiro para evitar fragmentação desnecessária das legendas finais.
        """
        original_srt = (
            "1\n00:00:00,100 --> 00:00:01,000\nHello\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\nworld\n\n"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            subtitle_file = Path(tmp_dir) / "subtitle.srt"
            subtitle_file.write_text(original_srt, encoding="utf-8")

            subtitle.correct(str(subtitle_file), "Hello world")
            items = subtitle.file_to_subtitles(str(subtitle_file))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][1], "00:00:00,100 --> 00:00:02,000")
        self.assertEqual(items[0][2], "Hello world")

    def test_correct_replaces_mismatch_and_appends_missing_script_line(self):
        """
        Se o resultado da transcrição for completamente inconsistente com o roteiro, o roteiro ainda deverá prevalecer; não há frases extras no script que possam ser reutilizadas.
        Use um espaço reservado de tempo zero explícito ao usar a linha do tempo para evitar a perda de texto e manter o comportamento compatível existente.
        """
        original_srt = "1\n00:00:00,100 --> 00:00:01,000\nWrong text\n\n"

        with tempfile.TemporaryDirectory() as tmp_dir:
            subtitle_file = Path(tmp_dir) / "subtitle.srt"
            subtitle_file.write_text(original_srt, encoding="utf-8")

            subtitle.correct(str(subtitle_file), "Expected sentence. Extra sentence.")
            items = subtitle.file_to_subtitles(str(subtitle_file))

        self.assertEqual(
            [item[2] for item in items],
            ["Expected sentence", "Extra sentence"],
        )
        self.assertEqual(items[1][1], "00:00:00,000 --> 00:00:00,000")

    def test_file_to_subtitles_keeps_last_block_without_trailing_newline(self):
        """
        The final subtitle must be parsed even when the SRT file does not end
        with a trailing blank line. Many tools omit it, and previously the last
        block was silently dropped because only a blank line flushed a block.
        """
        srt_without_trailing_blank = (
            "1\n"
            "00:00:00,000 --> 00:00:01,000\n"
            "Hello\n\n"
            "2\n"
            "00:00:01,000 --> 00:00:02,000\n"
            "World"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            subtitle_file = Path(tmp_dir) / "subtitle.srt"
            subtitle_file.write_text(srt_without_trailing_blank, encoding="utf-8")

            items = subtitle.file_to_subtitles(str(subtitle_file))

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0][2], "Hello")
        self.assertEqual(items[1][2], "World")

    def test_file_to_subtitles_parses_blocks_with_trailing_newline(self):
        """A normal SRT ending in a blank line still parses all blocks."""
        srt_with_trailing_blank = (
            "1\n"
            "00:00:00,000 --> 00:00:01,000\n"
            "Hello\n\n"
            "2\n"
            "00:00:01,000 --> 00:00:02,000\n"
            "World\n\n"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            subtitle_file = Path(tmp_dir) / "subtitle.srt"
            subtitle_file.write_text(srt_with_trailing_blank, encoding="utf-8")

            items = subtitle.file_to_subtitles(str(subtitle_file))

        self.assertEqual([item[2] for item in items], ["Hello", "World"])


if __name__ == "__main__":
    unittest.main()
