"""Import sound packs: folders of files named after cue sounds.

A pack is a zip holding files named after the four cues (``send``,
``received``, ``in_progress``, ``error``; any supported audio extension),
optionally inside one folder. Importing extracts to ``<packs_dir>/<name>/``
atomically and reports cue names it does not know instead of installing
them. Traversal, links and oversize entries are rejected.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

CUE_NAMES = ("send", "received", "in_progress", "error")
AUDIO_SUFFIXES = (".wav", ".ogg", ".aiff", ".aif", ".mp3")
_MAX_PACK_BYTES = 64 * 1024 * 1024


@dataclass
class ImportPackResult:
    folder: Path
    unknown: tuple[str, ...] = ()


def _cue_name(filename: str) -> str:
    stem = Path(filename).stem.lower()
    for cue in CUE_NAMES:
        if stem in (cue, cue.replace("_", "-"), cue.replace("_", "_")):
            return cue
    return ""


def import_sound_pack(zip_path: Path, packs_dir: Path) -> ImportPackResult:
    """Install a sound pack zip. Raises ValueError with a spoken reason."""
    zip_path = Path(zip_path)
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("This file is not a sound-pack zip.")
    packs_dir = Path(packs_dir)
    packs_dir.mkdir(parents=True, exist_ok=True)
    name = zip_path.stem or "pack"
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip() or "pack"
    destination = packs_dir / safe
    if destination.is_dir() and any(destination.iterdir()):
        raise ValueError(f"A sound pack named {safe!r} already exists.")
    with tempfile.TemporaryDirectory(prefix="blindpilot-pack-") as tmp:
        staged = Path(tmp) / "pack"
        staged.mkdir()
        unknown = []
        total = 0
        with zipfile.ZipFile(zip_path) as bundle:
            for info in bundle.infolist():
                raw = info.filename.replace("\\", "/")
                parts = [p for p in raw.split("/") if p not in ("", ".")]
                if not parts or any(p == ".." for p in parts):
                    raise ValueError(f"Pack entry {info.filename!r} is not a safe name.")
                if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ValueError(f"Pack entry {info.filename!r} is a link.")
                if info.is_dir():
                    continue
                data = bundle.read(info.filename)
                total += len(data)
                if total > _MAX_PACK_BYTES:
                    raise ValueError("The pack is too large.")
                target_name = parts[-1]
                cue = _cue_name(target_name)
                if not cue or Path(target_name).suffix.lower() not in AUDIO_SUFFIXES:
                    unknown.append(target_name)
                    continue
                staged.joinpath(f"{cue}{Path(target_name).suffix.lower()}").write_bytes(data)
        if not any(staged.iterdir()):
            raise ValueError("The pack has no recognizable cue sounds.")
        if destination.is_dir():
            shutil.rmtree(destination)
        # The temp folder may be on another filesystem (tmpfs /tmp on Linux),
        # where a rename fails with EXDEV; move falls back to copying.
        shutil.move(str(staged), str(destination))
    return ImportPackResult(folder=destination, unknown=tuple(sorted(set(unknown))))
