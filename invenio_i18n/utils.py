# -*- coding: utf-8 -*-
#
# This file is part of Invenio.
# Copyright (C) 2015-2018 CERN.
# Copyright (C) 2025 TUBITAK ULAKBIM.
# Copyright (C) 2025 University of Münster.
# Copyright (C) 2025 Graz University of Technology.
#
# Invenio is free software; you can redistribute it and/or modify it
# under the terms of the MIT License; see LICENSE file for more details.

"""I18N utils."""


import traceback
from json import JSONDecodeError, dump, load
from pathlib import Path
from subprocess import run

from click import secho
from flask import current_app
from invenio_base.utils import entry_points
from jinja2 import BaseLoader, Environment
from polib import POEntry, pofile

TRANSIFEX_CONFIG_TEMPLATE = """
[main]
host = https://www.transifex.com

[o:inveniosoftware:p:invenio:r:{{- resource }}]
file_filter = {{- temporary_cache }}/{{- package }}/<lang>/messages.po
source_file = {{- package }}/assets/semantic-ui/translations/{{- package }}/translations.pot
source_lang = en
type = PO
"""


def source_translation_files(input_directory):
    """Map source translation file contents to their languages."""
    for source_file in input_directory.iterdir():
        if not source_file.is_file() or source_file.suffix != ".json":
            msg = f"source file: {source_file} is not meant to be distributed."
            secho(msg)
            continue

        language = source_file.stem

        with source_file.open("r") as file_handle:
            try:
                obj = load(file_handle)
            except JSONDecodeError as error:
                tb = traceback.format_exc()
                msg = f"ERROR: source file: {source_file.name} couldn't be loaded because of error: {str(error)}\n{tb}"
                secho(msg)
            else:
                yield language, obj


def calculate_target_packages(
    exceptional_package_names,
    entrypoint_group,
    language,
):
    """Calculate target package translation paths.

    Maps each package to its target translation file path by inspecting entrypoint and handling exceptional package names.
    """
    package_translations_paths = {}

    for entry_point in entry_points(group=entrypoint_group):
        package_name = entry_point.name
        package_path = Path(entry_point.load().path)

        # Some webpack entry points use names that differ from their package names.
        # Map these exceptional webpack entry‑point names to their correct package names.
        package_name = exceptional_package_names.get(package_name, package_name)

        target_translations_path = (
            package_path / "translations" / package_name / "messages" / language
        )

        package_translations_paths[package_name] = (
            target_translations_path / "translations.json"
        )

    return package_translations_paths


def create_transifex_configuration(temporary_cache, js_resources):
    """Create a transifex fetch configuration.

    This configuration is built dynamically because the targeted packages are
    customizable over the configuration variable I18N_TRANSIFEX_JS_RESOURCES_MAP.
    """
    environment = Environment(loader=BaseLoader())
    config_template = environment.from_string(TRANSIFEX_CONFIG_TEMPLATE)

    with Path(temporary_cache / "collected_config").open("w") as fp:
        for resource, package in js_resources.items():
            config = config_template.render(
                temporary_cache=temporary_cache,
                resource=resource,
                package=package,
            )
            fp.write(config)
            fp.write("\n\n")


def fetch_translations_from_transifex(token, temporary_cache, languages, js_resources):
    """Fetch translations from transifex."""
    temporary_cache.mkdir(parents=True, exist_ok=True)

    create_transifex_configuration(temporary_cache, js_resources)

    transifex_pull_cmd = [
        "tx",
        f"--token={token}",
        f"--config={temporary_cache}/collected_config",
        "pull",
        f"--languages={languages}",
        "--force",
    ]
    result = run(transifex_pull_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        secho(f"Error fetching from Transifex: {result.stderr}")
        raise RuntimeError(
            f"Transifex pull failed with return code {result.returncode}"
        )


def map_to_i18next_style(po_file):
    """Map translations from po to i18next style.

    Plurals need a special format.
    """
    obj = {}
    for entry in po_file:
        obj[entry.msgid] = entry.msgstr
        if entry.msgstr_plural:
            obj[entry.msgid] = entry.msgstr_plural[0]
            obj[entry.msgid + "_plural"] = entry.msgstr_plural[1]
    return obj


def distribute_js_translations_from_directory(
    input_directory: Path, entrypoint_group: str = "invenio_assets.webpack"
):
    """Distribute JavaScript translations from JSON files to installed packages.

    This is a helper function which reads unified JSON files per language (e.g., de.json, en.json)
    from a translation bundle directory and distributes them to package asset directories.

    The translation bundle should contain JSON files named after locale codes:
    - ``de.json`` for German translations
    - ``en.json`` for English translations

    :param input_directory: containing JSON files - translation bundle
    :param entrypoint_group: Entrypoint group for discovering package paths
    :raises RuntimeError: If distribution fails
    """
    exceptional_package_names = current_app.config.get(
        "I18N_JS_DISTR_EXCEPTIONAL_PACKAGE_MAP", {}
    )

    for language, unified_translations in source_translation_files(input_directory):
        target_packages = calculate_target_packages(
            exceptional_package_names, entrypoint_group, language
        )

        for package_name, translations in unified_translations.items():
            if package_name not in target_packages:
                msg = (
                    f"Package {package_name} doesn't have webpack entrypoint. "
                    "Skipping..."
                )
                secho(msg)
                continue

            target_file = target_packages[package_name]
            target_file.parent.mkdir(parents=True, exist_ok=True)

            with target_file.open("w", encoding="utf-8") as file_pointer:
                dump(translations, file_pointer, indent=2, ensure_ascii=False)

            msg = (
                f"{package_name} translations for language {language} "
                "have been written."
            )
            secho(msg)


def has_translation_key(po_path: Path, msgid: str, match_prefix: bool) -> bool:
    """Check if PO file contains the translation key."""
    try:
        po_file = pofile(str(po_path))
        if match_prefix:
            return any(entry.msgid.startswith(msgid) for entry in po_file)
        return po_file.find(msgid) is not None
    except Exception:
        return False


def update_po_file(
    po_path: Path,
    msgid: str,
    msgstr: str,
    match_prefix: bool = False,
    target_name: str | None = None,
) -> tuple[bool, bool]:
    """Update PO file with translation."""
    try:
        po_file = pofile(str(po_path))
    except Exception as e:
        secho(f"Error opening {po_path}: {e}")
        return False, False

    updated = False
    created_new = False
    count = 0

    def remove_fuzzy_flag(entry):
        if isinstance(entry.flags, list):
            if "fuzzy" in entry.flags:
                entry.flags.remove("fuzzy")
        else:
            entry.flags.discard("fuzzy")

    for entry in po_file:
        if (match_prefix and entry.msgid.startswith(msgid)) or entry.msgid == msgid:
            entry.msgstr = msgstr
            remove_fuzzy_flag(entry)
            updated = True
            count += 1
            if not match_prefix:
                break

    if not updated or match_prefix:
        for entry in list(po_file.obsolete_entries()):
            if (match_prefix and entry.msgid.startswith(msgid)) or entry.msgid == msgid:
                entry.msgstr = msgstr
                remove_fuzzy_flag(entry)
                entry.obsolete = False
                po_file.append(entry)
                updated = True
                count += 1
                if not match_prefix:
                    break

    if not updated and not match_prefix:
        po_file.append(POEntry(msgid=msgid, msgstr=msgstr))
        updated = True
        created_new = True

    if not updated:
        return False, False

    try:
        po_file.save()
    except Exception as e:
        secho(f"Error saving {po_path}: {e}")
        return False, False

    is_js_po = (
        "assets" in str(po_path)
        or "messages.po" in str(po_path)
        and "LC_MESSAGES" not in str(po_path)
    )
    if not is_js_po:
        translations_dir = po_path.parent.parent.parent
        result = run(
            ["pybabel", "compile", "-d", str(translations_dir)],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            secho(f"Warning: Failed to compile: {result.stderr}")

    name = target_name or po_path
    if match_prefix:
        secho(f"Updated {count} translation(s) matching '{msgid}' in {name}")
    elif created_new:
        secho(f"Created '{msgid}' in {name}")
    else:
        secho(f"Updated '{msgid}' in {name}")

    return updated, created_new
