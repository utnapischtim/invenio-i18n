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

"""CLI for Invenio internationalization module."""
import subprocess
import traceback
from json import JSONDecodeError, dump, load
from pathlib import Path

import polib
from click import Path as ClickPath
from click import group, option, secho
from flask import current_app
from flask.cli import with_appcontext
from invenio_base.utils import entry_points
from jinja2 import BaseLoader, Environment

from .translation_utilities import (
    collect_translations,
    write_translations_to_json,
    write_validation_report,
)
from .translation_utilities.collect import (
    validate_translations as validate_translations_from_packages,
)
from .translation_utilities.convert import po_to_i18next_json
from .translation_utilities.discovery import (
    find_all_packages_with_translations,
    find_bundle_path,
    find_bundle_po_file,
    find_js_po_files,
    find_package_path,
    find_po_files,
    normalize_package_to_module_name,
)

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
    result = subprocess.run(transifex_pull_cmd, capture_output=True, text=True)
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


@group(chain=True)
@with_appcontext
def i18n():
    """i18n commands."""


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


@i18n.command()
@option(
    "-i",
    "--input-directory",
    required=True,
    type=ClickPath(
        exists=True, file_okay=False, dir_okay=True, writable=False, path_type=Path
    ),
    help="Input directory for translations in JSON format.",
)
def distribute_js_translations(input_directory: Path):
    """
    Distribute package‑specific JavaScript translations to installed packages.

    It modifies the installed packages directly,
    so it must be run in the env where packages are installed.

    Usage
    -----
    .. code-block::
       $ invenio i18n distribute-js-translations -i js_translations/

    Translation Bundle Structure
    ---------------------------
    The command expects an input directory the translation bundle that contains one
    unified JSON file per language.

    For example, a German translation bundle should contain:
    - ``de.json`` - Unified JSON file with all German translations

    Other examples:
    - ``en.json`` - English translations
    - ``tr.json`` - Turkish translations

    Each JSON file should contain a dictionary where keys are package module names
    (e.g., ``invenio_app_rdm``) and values are dictionaries of translation key-value pairs.

    Example structure of ``de.json``:
    .. code-block:: json
       {
         "invenio_app_rdm": {
           "Preview": "Vorschau",
           "Save": "Speichern"
         },
         "invenio_communities": {
           "Create": "Erstellen"
         }
       }

    The ``invenio i18n fetch-from-transifex`` command can be used to retrieve
    translations from Transifex and unify them into this format.

    Configuration
    -------------
    In order for the command to work properly, add the following
    config to the ``invenio.cfg``:

    .. code-block:: python
       I18N_JS_DISTR_EXCEPTIONAL_PACKAGE_MAP = {
         "jobs": "invenio_jobs",
         "invenio_previewer_theme": "invenio_previewer",
         "invenio_app_rdm_theme": "invenio_app_rdm",
       }

    Distribution Process
    --------------------
    Reads JSON files from the translation bundle, finds package asset directories
    via webpack entrypoints, and writes translations.json files to each package.

    For example, for locale ``de`` the extracted fragment for
    ``invenio_communities`` is written to:

    ``<site‑packages>/invenio-communities/assets/semantic-ui/translations/invenio_communities/messages/de/translations.json``

    Note: This modifies installed packages in the active virtual environment.
    Missing directories and files will be created automatically if not exist.
    """
    try:
        distribute_js_translations_from_directory(input_directory)
    except Exception as e:
        secho(f"Error during distribution: {e}")
        secho(traceback.format_exc())
        raise


def _convert_to_list(ctx, param, value):
    """Convert Click's tuple from multiple=True to a list."""
    if value is None:
        return []
    return list(value)


@i18n.command()
@option(
    "--packages",
    "-p",
    multiple=True,
    callback=_convert_to_list,
    help="Packages to include. Can be specified multiple times.",
)
@option(
    "--all-packages",
    "--global",
    is_flag=True,
    help="Collect from all invenio_* packages",
)
def create_global_pot(packages: list[str] | None, all_packages: bool):
    """Collect translations and write JSON files for testing.

    Collects PO translations from packages and converts them to JSON format.
    Output files are written to the i18n-collected/ directory.

    Examples:
        invenio i18n create-global-pot -p invenio-app-rdm -p invenio-rdm-records
        invenio i18n create-global-pot --all-packages
    """
    if all_packages:
        if packages:
            secho("Warning: --all-packages ignores --packages")
        packages = [
            name for name, _ in find_all_packages_with_translations(prefix="invenio_")
        ]
    elif not packages:
        secho("Error: Provide --packages or --all-packages")
        return

    output_dir = Path.cwd() / "i18n-collected"
    output_dir.mkdir(exist_ok=True)
    collected_data = collect_translations(packages)
    write_translations_to_json(collected_data, output_dir)
    secho(
        f"Collected translations for {collected_data['packagesProcessed']} packages into {output_dir}",
    )
    secho(f"Wrote merged JSON: {output_dir / 'translations.json'}")
    secho(
        "Per‑package JSON under: i18n-collected/<package>/translations.json",
    )


@i18n.command()
@option(
    "--packages",
    "-p",
    multiple=True,
    callback=_convert_to_list,
    help="Packages to validate. Can be specified multiple times.",
)
@option(
    "--all-packages",
    "--global",
    is_flag=True,
    help="Validate all invenio_* packages",
)
def validate_translations(packages: list[str] | None, all_packages: bool):
    """Validate translation quality.

    Checks PO files for missing, fuzzy, and obsolete translations.
    Generates a validation report in i18n-collected/validation-report.json.

    Examples:
        invenio i18n validate-translations -p invenio-app-rdm -p invenio-rdm-records
        invenio i18n validate-translations --all-packages
    """
    if all_packages:
        if packages:
            secho("Warning: --all-packages ignores --packages")
        packages = [
            name for name, _ in find_all_packages_with_translations(prefix="invenio_")
        ]
    elif not packages:
        secho("Error: Provide --packages or --all-packages")
        return

    output_dir = Path.cwd() / "i18n-collected"
    output_dir.mkdir(exist_ok=True)

    summary = validate_translations_from_packages(packages)
    write_validation_report(summary, output_dir)
    report_path = output_dir / "validation-report.json"
    secho(f"Validation report written: {report_path}")

    summary_data = summary.get("summary", {})
    secho(
        f"Summary: packages={summary_data.get('totalPackages', 0)}, "
        f"locales={summary_data.get('totalLocales', 0)}, "
        f"issues={summary_data.get('totalIssues', 0)}",
    )


def has_translation_key(po_path: Path, msgid: str, match_prefix: bool) -> bool:
    """Check if PO file contains the translation key."""
    try:
        po_file = polib.pofile(str(po_path))
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
        po_file = polib.pofile(str(po_path))
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
        po_file.append(polib.POEntry(msgid=msgid, msgstr=msgstr))
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
        result = subprocess.run(
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


@i18n.command()
@option("--package", "-p", help="Package name like 'invenio-app-rdm'")
@option("--bundle", "-b", help="Bundle name like 'invenio-translations-de'")
@option("--locale", "-l", required=True, help="Language code like 'de' or 'fr'")
@option(
    "--msgid",
    required=True,
    help="Original English text (or prefix if --prefix is used)",
)
@option("--msgstr", required=True, help="New translation")
@option("--prefix", is_flag=True, help="Match msgid by prefix instead of exact match")
def update_translation(package, bundle, locale, msgid, msgstr, prefix):
    """Update translation in PO file(s)."""
    if not package and not bundle:
        secho("Error: Provide --package or --bundle")
        return
    if package and bundle:
        secho("Error: Cannot specify both --package and --bundle")
        return

    if package:
        package_root = find_package_path(package)
        if not package_root:
            secho(f"Package {package} not found")
            return
        po_path = next(
            (
                path
                for loc, path in find_po_files(package_root, package)
                if loc == locale
            ),
            None,
        )
        if not po_path:
            secho(f"No PO file for {package} in {locale}")
            return
        update_po_file(po_path, msgid, msgstr, prefix, f"{package}/{locale}")

    elif bundle:
        bundle_root = find_bundle_path(bundle)
        if not bundle_root:
            secho(f"Bundle {bundle} not found")
            return
        po_path = find_bundle_po_file(bundle_root, locale)
        if not po_path:
            secho(f"No PO file for {bundle} in {locale}")
            return

        instance_po_path = (
            Path(current_app.root_path)
            / "translations"
            / locale
            / "LC_MESSAGES"
            / "messages.po"
        )
        if instance_po_path.exists():
            try:
                instance_po = polib.pofile(str(instance_po_path))
                entry = (
                    next((e for e in instance_po if e.msgid.startswith(msgid)), None)
                    if prefix
                    else instance_po.find(msgid)
                )
                if entry:
                    secho(
                        f"Warning: Instance translation '{entry.msgstr}' will override bundle",
                    )
            except Exception:
                # Ignore errors when checking instance translations
                pass

        update_po_file(po_path, msgid, msgstr, prefix, f"{bundle}/{locale}")


@i18n.command()
@option("--package", "-p", required=True, help="Package name like 'invenio-app-rdm'")
@option("--locale", "-l", required=True, help="Language code like 'de' or 'fr'")
@option(
    "--msgid",
    required=True,
    help="Original English text (or prefix if --prefix is used)",
)
@option("--msgstr", required=True, help="New translation")
@option("--prefix", is_flag=True, help="Match msgid by prefix instead of exact match")
@option(
    "--build",
    is_flag=True,
    help="Automatically convert PO to JSON and rebuild webpack after update",
)
def update_js_translation(package, locale, msgid, msgstr, prefix, build):
    """Update JavaScript translation in messages.po file and convert to translations.json.

    Updates a translation in a package's JavaScript PO file and automatically
    converts it to JSON format for webpack.

    Usage:
        # Update a German translation for invenio-app-rdm
        invenio i18n update-js-translation -p invenio-app-rdm -l de --msgid "Save" --msgstr "Speichern"

        # Update with automatic webpack rebuild
        invenio i18n update-js-translation -p invenio-communities -l de --msgid "Create" --msgstr "Erstellen" --build

        # Update multiple translations matching a prefix
        invenio i18n update-js-translation -p invenio-app-rdm -l de --msgid "Upload" --msgstr "Hochladen" --prefix
    """
    package_root = find_package_path(package)
    if not package_root:
        secho(f"Package {package} not found")
        return

    po_path = next(
        (
            path
            for loc, path in find_js_po_files(package_root, package)
            if loc == locale
        ),
        None,
    )
    if not po_path:
        secho(f"No JavaScript PO file for {package} in {locale}")
        secho(
            "Hint: JavaScript PO files are typically in assets/semantic-ui/translations/<package>/messages/<locale>/messages.po",
        )
        return

    updated, _created = update_po_file(
        po_path, msgid, msgstr, prefix, f"{package}/{locale} (JS)"
    )
    if not updated:
        return
    try:
        po_file = polib.pofile(str(po_path))
        json_data = po_to_i18next_json(po_file, package)

        json_path = po_path.parent / "translations.json"
        json_path.parent.mkdir(parents=True, exist_ok=True)

        with json_path.open("w", encoding="utf-8") as fp:
            dump(json_data, fp, indent=2, ensure_ascii=False)

        secho(f"Converted to JSON: {json_path}")
    except Exception as e:
        secho(f"Warning: Failed to convert to JSON: {e}")

    if build:
        secho("Rebuilding webpack assets...")
        result = subprocess.run(
            ["invenio", "webpack", "build"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            secho(f"Warning: Webpack build failed: {result.stderr}")
        else:
            secho("Webpack rebuild complete")


@i18n.command()
@option(
    "--packages",
    "-p",
    multiple=True,
    callback=_convert_to_list,
    help="Packages to build. Can be specified multiple times.",
)
@option(
    "--all-packages",
    "--global",
    is_flag=True,
    help="Build JavaScript translations for all invenio_* packages",
)
@option(
    "--output-directory",
    "-o",
    type=ClickPath(
        exists=False, file_okay=False, dir_okay=True, writable=True, path_type=Path
    ),
    default=Path.cwd() / "js-translations",
    help="Directory for temporary JSON files. Default: ./js-translations",
)
@option(
    "--rebuild-webpack",
    is_flag=True,
    help="Rebuild webpack assets after distributing translations",
)
@option(
    "--collect-assets",
    is_flag=True,
    help="Collect static assets after rebuilding webpack",
)
def build_js_translations(
    packages: list[str] | None,
    all_packages: bool,
    output_directory: Path,
    rebuild_webpack: bool,
    collect_assets: bool,
):
    """Build JavaScript translations: convert PO to JSON, distribute, and rebuild.

    Collect JavaScript PO files (messages.po) from installed packages
    Convert PO files to JSON format (translations.json)
    Distribute JSON files to package asset directories
    Optionally rebuild webpack and collect assets

    PO files come from Transifex, but webpack needs JSON files.
    This command converts and distributes them automatically.

    Usage:
        invenio i18n build-js-translations -p invenio-app-rdm
        invenio i18n build-js-translations --all-packages
        invenio i18n build-js-translations --all-packages --rebuild-webpack --collect-assets
    """
    if all_packages:
        if packages:
            secho("Warning: --all-packages ignores --packages")
        packages = [
            name
            for name, package_root in find_all_packages_with_translations(
                prefix="invenio_"
            )
            if any(find_js_po_files(package_root, name))
        ]
    elif not packages:
        secho("Error: Provide --packages or --all-packages")
        return

    if not packages:
        secho("No packages found with JavaScript translations")
        return

    output_directory.mkdir(parents=True, exist_ok=True)
    secho(
        f"Collecting JavaScript translations from {len(packages)} package(s)...",
    )

    translations_by_language: dict[str, dict[str, dict[str, str]]] = {}

    for package_name in packages:
        package_root = find_package_path(package_name)
        if not package_root:
            continue

        for locale, po_path in find_js_po_files(package_root, package_name):
            if locale not in translations_by_language:
                translations_by_language[locale] = {}

            try:
                po_file = polib.pofile(str(po_path))
                module_name = normalize_package_to_module_name(package_name)
                translations_by_language[locale][module_name] = po_to_i18next_json(
                    po_file, package_name
                )
                secho(f"  Collected {locale} from {package_name}")
            except Exception as e:
                secho(f"  Error reading {po_path}: {e}")

    if not translations_by_language:
        secho("No JavaScript translations found")
        return

    for locale, translations in translations_by_language.items():
        json_path = output_directory / f"{locale}.json"
        with json_path.open("w", encoding="utf-8") as fp:
            dump(translations, fp, indent=2, ensure_ascii=False)
        secho(f"Wrote {json_path}")

    secho("Distributing translations to package assets...")
    try:
        distribute_js_translations_from_directory(output_directory)
    except Exception as e:
        secho(f"Error during distribution: {e}")
        secho(traceback.format_exc())
        return

    if rebuild_webpack:
        secho("Rebuilding webpack assets...")
        result = subprocess.run(
            ["invenio", "webpack", "build"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            secho(f"Warning: Webpack build failed: {result.stderr}")
        else:
            secho("Webpack rebuild complete")

    if collect_assets:
        secho("Collecting static assets...")
        result = subprocess.run(
            ["invenio", "collect", "--verbose"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            secho(f"Warning: Asset collection failed: {result.stderr}")
        else:
            secho("Asset collection complete")

    secho("JavaScript translation build complete!")


@i18n.command()
@option("--token", "-t", required=True, help="API token for your Transifex account.")
@option(
    "--languages",
    "-l",
    required=True,
    help="Languages you want to download translations for. One or multiple comma separated values, e.g. 'de,en,fr'.",
)
@option(
    "--output-directory",
    "-o",
    required=True,
    type=ClickPath(
        exists=True, file_okay=False, dir_okay=True, writable=True, path_type=Path
    ),
    help="Directory to which collected translations in JSON format should be written.",
)
def fetch_from_transifex(token, languages, output_directory):
    """Retrieve package translations from Transifex and unify them to a single file using i18next format.

    Usage
    -----
    .. code-block:: console
       $ invenio i18n fetch-from-transifex -t <your transifex API token> -l 'de,en,fr' -o js_translations/

    The command expects an API token associated with a Transifex account to be able to pull translations.
    Such a token can be generated in the user settings on the Transifex website.

    The output directory will be used to store downloaded translations per package as well as the unified translation file.

    To supply the packages for which translations should be pulled, add the following config to your instance's ``invenio.cfg``:

    .. code-block:: python
        I18N_TRANSIFEX_JS_RESOURCES_MAP = {
            "invenio-administration-messages-ui": "invenio_administration",
            "invenio-app-rdm-messages-ui": "invenio_app_rdm",
            "invenio-communities-messages-ui": "invenio_communities",
            "invenio-rdm-records-messages-ui": "invenio_rdm_records",
            "invenio-requests-messages-ui": "invenio_requests",
            "invenio-search-ui-messages-js": "invenio_search_ui"
        }

    Fetching and unifying of translations
    ---------------------------
    This CLI command pulls translations in PO format from Transifex for all packages specified in the config.
    It will then unify all translations to a single JSON file in a format that can be used with the i18next library.
    The unified file will contain keys for package names on the top level and a nested dict with translation keys and values for each package, e.g.:

    .. code-block:: json
        {
            "invenio_administration": {
                "Error": "Fehler",
                "Save": "Speichern",
                ...
            },
            "invenio_app_rdm": {
                "Basic information": "Allgemeine Informationen",
                "New": "Neu",
                ...
            },
            ...
        }
    """
    js_resources = current_app.config["I18N_TRANSIFEX_JS_RESOURCES_MAP"]

    temporary_cache = output_directory / "tmp"

    fetch_translations_from_transifex(token, temporary_cache, languages, js_resources)

    collected_translations = {}

    for language in languages.split(","):
        collected_translations[language] = {}

        for package in js_resources.values():
            po_path = Path(temporary_cache) / package / language / "messages.po"
            if not po_path.exists():
                secho(f"Warning: PO file not found: {po_path}")
                continue
            try:
                po_file = polib.pofile(str(po_path))
                collected_translations[language][package] = map_to_i18next_style(
                    po_file
                )
            except Exception as e:
                secho(f"Error reading PO file {po_path}: {e}")
                continue

        output_file = Path(f"{output_directory}/{language}.json")
        with output_file.open("w", encoding="utf-8") as fp:
            dump(collected_translations[language], fp, indent=4, ensure_ascii=False)
