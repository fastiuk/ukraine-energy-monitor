"""The Home Assistant integration ships its own copy of ua_outages; keep it identical, same version."""

import json
import re
import tomllib
from pathlib import Path

from ua_outages.http import USER_AGENT

ROOT = Path(__file__).parent.parent
SOURCE = ROOT / "src" / "ua_outages"
VENDORED = ROOT / "custom_components" / "ukraine_outages" / "ua_outages"


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


def test_vendored_core_matches_source():
    assert _files(SOURCE) == _files(VENDORED), (
        "custom_components/ukraine_outages/ua_outages is out of date; refresh it with:\n"
        "  rm -rf custom_components/ukraine_outages/ua_outages && "
        "cp -R src/ua_outages custom_components/ukraine_outages/ua_outages"
    )


def test_versions_agree():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    manifest = json.loads((ROOT / "custom_components" / "ukraine_outages" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == version
    assert USER_AGENT.startswith(f"ukraine-energy-monitor/{version} ")


def test_the_name_is_found_in_ukrainian():
    """Home Assistant's "Add integration" search (Fuse.js, threshold 0.2, distance 100) finds a word
    only within the first ~20 characters of the name; "ukraine" and "outages" are found through
    the domain. So the name starts with the words people type in Ukrainian."""
    manifest = json.loads((ROOT / "custom_components" / "ukraine_outages" / "manifest.json").read_text(encoding="utf-8"))
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert manifest["name"] == hacs["name"]
    start = manifest["name"][:20].lower()
    assert "графіки" in start and "відключень" in start
    assert manifest["domain"] == "ukraine_outages"


def _strings(tree, path=()):
    if isinstance(tree, dict):
        for key, value in tree.items():
            yield from _strings(value, (*path, key))
    elif isinstance(tree, str):
        yield path, tree


def test_translations_have_every_string_with_the_same_placeholders():
    """Home Assistant drops a translation whose placeholders differ from strings.json (and logs an error)."""
    component = ROOT / "custom_components" / "ukraine_outages"
    source = dict(_strings(json.loads((component / "strings.json").read_text(encoding="utf-8"))))
    for lang in ("en", "uk"):
        translated = dict(_strings(json.loads((component / "translations" / f"{lang}.json").read_text(encoding="utf-8"))))
        assert translated.keys() == source.keys(), lang
        for path, text in source.items():
            assert set(re.findall(r"\{(\w+)\}", translated[path])) == set(re.findall(r"\{(\w+)\}", text)), (lang, path)
