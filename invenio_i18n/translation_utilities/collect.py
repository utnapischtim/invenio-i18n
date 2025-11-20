# -*- coding: utf-8 -*-
#
# This file is part of Invenio.
# Copyright (C) 2025 Graz University of Technology.
#
# Invenio is free software; you can redistribute it and it
# under the terms of the MIT License; see LICENSE file for more details.
"""Collect PO translations, convert to JSON, and validate."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from polib import POFile, pofile

from .discovery import (
    find_package_path,
    find_po_files,
)
from .io import write_json_file


@dataclass
class Message:
    msgid: str
    text: str


@dataclass
class TranslationBundle:
    locale: str
    messages: list[Message]


@dataclass
class PackageTranslation:
    package_name: str
    translation_bundles: list[TranslationBundle]

    def add(self, locale: str, po_file: POFile) -> None:
        """TODO."""
        # convert po_file to internal representation

    def get_translation_bundle(self, locale: str) -> TranslationBundle:
        """."""
        for k in self.translation_bundles:
            if k.locale == locale:
                return k
        return None


def build_global_translation_bundle(
    locale: str,
    package_translations: list[PackageTranslation],
) -> TranslationBundle:
    """build."""
    # TODO: combine multiple package translations to one translation bundle


def get_package_translations(package_name: str) -> PackageTranslation:
    """Get all translations from one package.

    :param package_name: Name of the package like 'invenio-app-rdm'
    :return: Translations organized by language like {"de": {...}, "fr": {...}}
    """
    package_root = find_package_path(package_name)
    package_translation = PackageTranslation(package_name)

    if not package_root:
        return package_translation

    for locale, po_path in find_po_files(package_root, package_name):
        po_file = pofile(str(po_path))
        package_translation.add(locale, po_file)

    return package_translation


def collect_translations(packages: list[str]) -> list[PackageTranslation]:
    """Collect translations from packages.

    :param packages: List of package names like ['invenio-app-rdm', 'invenio-rdm-records']
    :return: Dictionary with collected translations and summary info
    """
    return [get_package_translations(p) for p in packages]


def write_translations_to_json(
    collected_data: list[PackageTranslation],
    output_file: Path,
    locales: list[str],
    write_package_wise_too: bool = False,
) -> None:
    """Write collected translations to JSON files.

    :param collected_data: Output from collect_translations()
    :param output_dir: Where to save the translation files
    """
    for locale in locales:
        write_json_file(
            output_file, build_global_translation_bundle(locale, collected_data)
        )

    if write_package_wise_too:
        output_dir = output_file.parent

        for package_translation in collected_data.translation_bundles:
            package_output = (
                output_dir / package_translation.package_name / "translations.json"
            )
            write_json_file(package_output, package_translation.to_json())
