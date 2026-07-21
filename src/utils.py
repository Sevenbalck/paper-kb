"""Funzioni condivise dalla pipeline: config, manifest di stato, hashing file, chiamata a Claude."""
from __future__ import annotations

import os

# Su Windows, huggingface_hub prova per default a creare symlink nella cache dei modelli
# (usati da Docling), il che richiede "Developer Mode" attivo o
# privilegi da amministratore e altrimenti fallisce con WinError 1314. Disabilitandoli si
# usano copie normali dei file: un po' più spazio su disco, ma funziona ovunque senza
# configurazione speciale. Va impostato PRIMA di importare docling,
# quindi qui in cima a utils.py, che tutti gli altri moduli importano per primo.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

import hashlib
import json
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict[str, Any]:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Rendi tutti i path assoluti rispetto alla root del progetto
    for key, val in cfg["paths"].items():
        cfg["paths"][key] = ROOT / val
    return cfg


def ensure_dirs(cfg: dict[str, Any]) -> None:
    for key, path in cfg["paths"].items():
        if key == "manifest":
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir(parents=True, exist_ok=True)


def slugify(name: str) -> str:
    """Genera un paper_id stabile e leggibile da un nome file o titolo."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^\w\s-]", "", name).strip().lower()
    name = re.sub(r"[\s_-]+", "-", name)
    return name[:80] or "paper"


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


class Manifest:
    """Traccia lo stato di avanzamento di ogni paper (hash + stadi completati),
    così la pipeline è idempotente: puoi rilanciarla su un altro computer e
    salterà automaticamente ciò che è già stato fatto (soprattutto le
    chiamate a Claude, che sono quelle che costano)."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get(self, paper_id: str) -> dict[str, Any]:
        return self.data.setdefault(paper_id, {})

    def stage_done(self, paper_id: str, stage: str, current_hash: str) -> bool:
        entry = self.data.get(paper_id, {})
        return entry.get(stage) == current_hash

    def mark_done(self, paper_id: str, stage: str, current_hash: str) -> None:
        entry = self.get(paper_id)
        entry[stage] = current_hash
        self.save()


def _resolve_claude_cmd() -> list[str]:
    """Risolve il comando 'claude' in una lista eseguibile da subprocess.

    Su Windows, npm installa 'claude' come script .cmd/.bat: subprocess.run() non può
    eseguirlo direttamente (fallisce con WinError 2, "Impossibile trovare il file
    specificato") perché quei file vanno interpretati da cmd.exe, non lanciati come
    eseguibili nativi. Qui rileviamo il caso e anteponiamo "cmd /c".
    """
    path = shutil.which("claude")
    if path is None:
        raise RuntimeError(
            "Comando 'claude' non trovato nel PATH. Installa Claude Code:\n"
            "  npm install -g @anthropic-ai/claude-code\n"
            "poi autenticati con la tua sottoscrizione Pro/Max:\n"
            "  claude login\n"
            "Se lo hai appena installato, apri un nuovo terminale (il PATH va ricaricato)."
        )
    if os.name == "nt" and path.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", path]
    return [path]


def check_claude_code() -> None:
    _resolve_claude_cmd()
    if os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ATTENZIONE: la variabile d'ambiente ANTHROPIC_API_KEY è impostata. Se presente, "
            "Claude Code la usa al posto della tua sottoscrizione Pro e le chiamate vengono "
            "fatturate a consumo sulla API invece che sull'abbonamento. Rimuovila dall'ambiente "
            "(unset ANTHROPIC_API_KEY) per usare l'abbonamento."
        )


def call_claude_code(
    instruction: str,
    input_text: str = "",
    system_prompt: str | None = None,
    model: str | None = None,
    timeout: int = 600,
) -> str:
    """Invoca Claude tramite Claude Code in modalità headless (`claude -p`), che si autentica
    con il login della sottoscrizione Pro/Max invece che con una API key.

    `instruction` è il comando/domanda breve passato come argomento; `input_text` (il testo
    del paper o i chunk recuperati, che può essere lungo) viene passato via stdin, come nel
    pattern documentato `cat file | claude -p "istruzione"` — evita i limiti di lunghezza
    degli argomenti da riga di comando.

    Nessun tool è abilitato (--allowedTools vuoto): Claude deve solo leggere il testo fornito
    e rispondere, non gli serve accedere a file o eseguire comandi sulla tua macchina.
    """
    check_claude_code()

    cmd = _resolve_claude_cmd() + ["-p", instruction, "--output-format", "json", "--allowedTools", ""]
    if model:
        cmd += ["--model", model]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]

    result = subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Claude Code ha restituito un errore (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout.strip()  # fallback nel caso l'output non sia JSON strutturato

    if payload.get("is_error"):
        raise RuntimeError(f"Claude Code ha segnalato un errore: {payload.get('result')}")
    return payload.get("result", "")


def extract_json(text: str) -> Any:
    """Estrae un blocco JSON dalla risposta di Claude, tollerando fence ```json ... ```."""
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


def condense_markdown(md: str, threshold: int) -> str:
    """Se il documento è più lungo di `threshold` caratteri, tiene solo:
    tutte le intestazioni (per la struttura), il primo 25% (abstract/intro)
    e l'ultimo 15% (risultati/conclusioni). Riduce i token senza perdere
    i punti che contano di più per riassunto/estrazione entità."""
    if len(md) <= threshold:
        return md
    headings = "\n".join(l for l in md.splitlines() if l.strip().startswith("#"))
    head = md[: int(threshold * 0.6)]
    tail = md[-int(threshold * 0.4):]
    return (
        f"[Documento condensato per risparmiare token — struttura completa sotto]\n\n"
        f"## Indice delle sezioni rilevate\n{headings}\n\n"
        f"## Inizio documento (abstract/introduzione)\n{head}\n\n"
        f"## Parte finale documento (risultati/conclusioni)\n{tail}"
    )
