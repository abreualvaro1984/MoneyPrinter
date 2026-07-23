#!/usr/bin/env python3
"""Traduz trechos em chinês (Han) para português (pt-BR) em arquivos de texto do repo."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from deep_translator import GoogleTranslator

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / ".chinese_to_pt_cache.json"
HAN_RE = re.compile(r"[\u4e00-\u9fff]")
# Trecho contínuo com ao menos 1 Han; inclui pontuação CJK e espaços internos.
CHUNK_RE = re.compile(
    r"(?:"
    r"[\u4e00-\u9fff]"
    r"|[，。、；：！？｡＂＃＄％＆＇（）＊＋，－．／：；＜＝＞＠［＼］＾＿｀｛｜｝～"
    r"\u3000-\u303f\uff00-\uffef"
    r"“”‘’（）【】《》〈〉「」『』…—·・]"
    r")+"
    r"(?:[\s0-9A-Za-z_./:+\-%,()\[\]{}|]*"
    r"(?:"
    r"[\u4e00-\u9fff]"
    r"|[，。、；：！？｡＂＃＄％＆＇（）＊＋，－．／：；＜＝＞＠［＼］＾＿｀｛｜｝～"
    r"\u3000-\u303f\uff00-\uffef"
    r"“”‘’（）【】《》〈〉「」『』…—·・]"
    r")+)*"
)

SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "docs/sponsors",
}
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".mp3",
    ".mp4",
    ".ttf",
    ".ttc",
    ".lock",
    ".pyc",
    ".bin",
    ".woff",
    ".woff2",
}
# Locale chinês: manter como pacote de idioma (não virar PT).
SKIP_FILES = {
    "webui/i18n/zh.json",
    "README-zh.md",  # documentação chinesa preservada
}


def load_cache() -> dict[str, str]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict[str, str]) -> None:
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if "docs/sponsors" in rel:
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if rel in SKIP_FILES:
            continue
        # Não tocar no cache deste script
        if path.name == CACHE_PATH.name:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if HAN_RE.search(text):
            files.append(path)
    return files


def translate_chunk(text: str, translator: GoogleTranslator, cache: dict[str, str]) -> str:
    key = text
    if key in cache:
        return cache[key]
    # Evita enviar só pontuação
    if not HAN_RE.search(text):
        return text
    for attempt in range(5):
        try:
            # Google tem limite ~4500 chars
            if len(text) > 4000:
                mid = len(text) // 2
                # split near space
                split_at = text.rfind(" ", 0, mid) or mid
                left = translate_chunk(text[:split_at], translator, cache)
                right = translate_chunk(text[split_at:], translator, cache)
                out = left + right
            else:
                out = translator.translate(text)
            if not out:
                out = text
            cache[key] = out
            return out
        except Exception as exc:
            wait = 1.5 * (attempt + 1)
            print(f"  retry {attempt + 1}: {exc} (sleep {wait:.1f}s)", flush=True)
            time.sleep(wait)
    cache[key] = text
    return text


def transform_text(content: str, translator: GoogleTranslator, cache: dict[str, str]) -> str:
    parts: list[str] = []
    last = 0
    for match in CHUNK_RE.finditer(content):
        if not HAN_RE.search(match.group(0)):
            continue
        parts.append(content[last : match.start()])
        original = match.group(0)
        translated = translate_chunk(original, translator, cache)
        parts.append(translated)
        last = match.end()
    parts.append(content[last:])
    return "".join(parts)


def main() -> int:
    files = iter_files()
    print(f"Arquivos com chinês: {len(files)}", flush=True)
    cache = load_cache()
    translator = GoogleTranslator(source="zh-CN", target="pt")
    changed = 0
    for i, path in enumerate(files, 1):
        rel = path.relative_to(ROOT).as_posix()
        print(f"[{i}/{len(files)}] {rel}", flush=True)
        original = path.read_text(encoding="utf-8")
        updated = transform_text(original, translator, cache)
        if updated != original:
            path.write_text(updated, encoding="utf-8", newline="\n")
            changed += 1
            left = len(HAN_RE.findall(updated))
            print(f"  updated (han restantes: {left})", flush=True)
        else:
            print("  unchanged", flush=True)
        if i % 5 == 0:
            save_cache(cache)
    save_cache(cache)
    print(f"Pronto. Arquivos alterados: {changed}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
