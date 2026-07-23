"""One-off: translate Chinese comments/docstrings in app/*.py to pt (Google)."""
import re
from pathlib import Path

from deep_translator import GoogleTranslator

ROOT = Path(__file__).resolve().parents[1] / "app"
HAN = re.compile(r"[\u4e00-\u9fff]")
TR = GoogleTranslator(source="zh-CN", target="pt")


def translate_chunk(text: str) -> str:
    if not HAN.search(text):
        return text
    # Batch long text in chunks for API limits
    parts = []
    buf = text
    while buf:
        chunk = buf[:4500]
        if len(buf) > 4500:
            split = chunk.rfind("\n")
            if split > 500:
                chunk = buf[:split]
        parts.append(TR.translate(chunk))
        buf = buf[len(chunk) :]
    return "".join(parts)


def should_skip_line(path: Path, line: str) -> bool:
    if path.name == "const.py" and re.match(r'^\s*["\'][\u4e00-\u9fff]["\'],?\s*$', line):
        return True
    if path.name == "voice.py" and re.search(r'\(\s*["\'][\u4e00-\u9fff]+["\']', line):
        return True
    return False


def translate_docstrings(source: str) -> tuple[str, bool]:
    changed = False

    def repl(match: re.Match) -> str:
        nonlocal changed
        quote, body = match.group(1), match.group(2)
        if not HAN.search(body):
            return match.group(0)
        changed = True
        return quote + translate_chunk(body) + quote

    pattern = re.compile(r'("""|\'\'\')(.*?)\1', re.DOTALL)
    return pattern.sub(repl, source), changed


def translate_comments(source: str, path: Path) -> tuple[str, bool]:
    changed = False
    out_lines = []
    for line in source.splitlines(keepends=True):
        if should_skip_line(path, line):
            out_lines.append(line)
            continue
        stripped = line.lstrip()
        if stripped.startswith("#") and HAN.search(line):
            prefix = line[: len(line) - len(stripped)]
            body = stripped[1:].strip()
            tr = translate_chunk(body)
            nl = "\n" if line.endswith("\n") else ""
            out_lines.append(f"{prefix}# {tr}{nl}")
            changed = True
        else:
            out_lines.append(line)
    return "".join(out_lines), changed


def process_file(path: Path) -> bool:
    src = path.read_text(encoding="utf-8")
    if not HAN.search(src):
        return False
    text, c1 = translate_comments(src, path)
    text, c2 = translate_docstrings(text)
    if c1 or c2 or text != src:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> None:
    updated = []
    for p in sorted(ROOT.rglob("*.py")):
        try:
            if process_file(p):
                updated.append(str(p.relative_to(ROOT.parent)))
        except Exception as exc:
            print(f"ERR {p}: {exc}")
    for u in updated:
        print(u)


if __name__ == "__main__":
    main()
