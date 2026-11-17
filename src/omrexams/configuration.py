import os
import hashlib
import re
import tempfile
import unicodedata
from copy import deepcopy
from pathlib import Path

import yaml

from .profiles import validate_profiles

MAX_PART_SIZE = 1024 * 1024
TEMPLATE_FIELDS = ("header", "footer", "preamble")


def _read_text(path):
    path = Path(path)
    if path.stat().st_size > MAX_PART_SIZE:
        raise ValueError(f"Parte di configurazione troppo grande: {path.name}")
    return path.read_text(encoding="utf-8-sig")


def _read_yaml(path):
    try:
        result = yaml.safe_load(_read_text(path))
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML non valido: {Path(path).name}: {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"La configurazione deve essere un oggetto: {Path(path).name}")
    return result


def config_part_paths(config):
    for profile in (config.get("profiles") or {}).values():
        if isinstance(profile, str):
            yield profile
    for field in TEMPLATE_FIELDS:
        if field + "_file" in config:
            if not isinstance(config[field + "_file"], str):
                raise ValueError(f"{field}_file deve essere un percorso relativo")
            yield config[field + "_file"]


def resolve_config(config, directory):
    root = Path(directory).resolve()
    resolved = deepcopy(config)

    def part_path(reference):
        if not isinstance(reference, str) or not reference or "\\" in reference:
            raise ValueError("Percorso di configurazione non valido")
        relative = Path(reference)
        if relative.is_absolute():
            raise ValueError("Le parti di configurazione devono avere percorsi relativi")
        path = root / relative
        try:
            path.resolve().relative_to(root)
        except ValueError:
            raise ValueError(f"Parte di configurazione esterna alla directory config: {reference}") from None
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root):
            raise ValueError(f"Collegamento simbolico non ammesso: {reference}")
        if not path.is_file():
            raise ValueError(f"Parte di configurazione non trovata: {reference}")
        return path

    profiles = resolved.get("profiles")
    if isinstance(profiles, dict):
        for name, profile in profiles.items():
            if isinstance(profile, str):
                path = part_path(profile)
                if path.suffix.lower() not in (".yaml", ".yml"):
                    raise ValueError(f"La variante deve essere un file YAML: {profile}")
                profiles[name] = _read_yaml(path)
    for field in TEMPLATE_FIELDS:
        reference = resolved.pop(field + "_file", None)
        if reference is not None:
            if resolved.get(field):
                raise ValueError(f"Specifica solo {field} oppure {field}_file")
            resolved[field] = _read_text(part_path(reference))
    resolved.setdefault("seed", 42)
    seed = resolved["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("Il seed della configurazione deve essere un intero")
    validate_profiles(resolved)
    return resolved


def load_config(path):
    path = Path(path)
    return resolve_config(_read_yaml(path), path.parent)


def configuration_files(config):
    validate_profiles(config)
    main = deepcopy(config)
    main.pop("basedir", None)
    main.setdefault("seed", 42)
    if isinstance(main["seed"], bool) or not isinstance(main["seed"], int):
        raise ValueError("Il seed della configurazione deve essere un intero")
    files = {}
    stems = {
        name: re.sub(
            r"[^a-z0-9]+", "_",
            unicodedata.normalize("NFKD", name.casefold()).encode("ascii", "ignore").decode(),
        ).strip("_")[:100] or "variante"
        for name in (main.get("profiles") or {})
    }
    for name, profile in (main.get("profiles") or {}).items():
        stem = stems[name]
        if list(stems.values()).count(stem) > 1:
            stem += "_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
        reference = f"variants/{stem}.yaml"
        files[reference] = yaml.safe_dump(profile, allow_unicode=True, sort_keys=False)
        main["profiles"][name] = reference
    for field in TEMPLATE_FIELDS:
        text = main.pop(field, None)
        if text is not None and not isinstance(text, str):
            raise ValueError(f"Il template {field} deve essere testo")
        if text:
            reference = f"templates/{field}.md"
            files[reference] = text
            main[field + "_file"] = reference
    files["config.yaml"] = yaml.safe_dump(main, allow_unicode=True, sort_keys=False)
    for reference, text in files.items():
        if len(text.encode("utf-8")) > MAX_PART_SIZE:
            raise ValueError(f"Parte di configurazione troppo grande: {reference}")
    return files


def save_config_directory(config, directory):
    root = Path(directory)
    files = configuration_files(config)
    for reference in files:
        target = root / reference
        if root.is_symlink() or target.parent.is_symlink() or target.is_symlink():
            raise ValueError(f"Collegamento simbolico non ammesso: {reference}")
    for reference, contents in files.items():
        target = root / reference
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(contents)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
