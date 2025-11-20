# -*- coding: utf-8 -*-
#
# This file is part of Invenio.
# Copyright (C) 2025 Graz University of Technology.
#
# Invenio is free software; you can redistribute it and/or modify it
# under the terms of the MIT License; see LICENSE file for more details.
"""Validation helpers for i18n service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from polib import POFile, pofile

from .discovery import (
    find_package_path,
    find_po_files,
    normalize_package_to_module_name,
)
from .io import write_json_file


@dataclass
class Issues:
    """Issues."""

    untranslated: list[str] = []
    fuzzy: list[str] = []
    obsolete: list[str] = []


@dataclass
class Counts:
    """Counts."""

    untranslated: int
    fuzzyTranslations: int
    obsoleteTranslations: int

    def __init__(self, issues: Issues) -> None:
        """Construct."""
        self.untranslated = len(issues.untranslated)
        self.fuzzyTranslations = len(issues.fuzzy)
        self.obsoleteTranslations = len(issues.obsolete)


@dataclass
class ValidationReport:
    """Validation report."""

    package: str
    locale: str
    filename: Path
    issues: Issues
    counts: Counts


@dataclass
class PackageValidation:
    """Package validation."""

    package_name: str
    reports: list[ValidationReport]


@dataclass
class ValidationSummary:
    """Validation summary."""


def validate_translations(packages: list[str]) -> ValidationSummary:
    """Validate translations from packages.

    :param packages: List of package names to check like ['invenio-app-rdm']
    :return: Summary of all issues found
    """
    reports = [get_package_validation_report(package) for package in packages]

    return calculate_validation_summary(reports, packages)


def get_package_validation_report(package_name: str) -> PackageValidation:
    """Get validation reports for one package.

    :param package_name: Name of the package to check like 'invenio-app-rdm'
    :return: List of reports showing what needs to be fixed
    """
    package_root = find_package_path(package_name)
    package_validation = PackageValidation(package_name)

    if not package_root:
        return package_validation

    for locale, po_path in find_po_files(package_root, package_name):
        po_file = pofile(str(po_path))
        package_validation.reports.append(
            validate_po(po_file, package_name, locale, po_path)
        )

    return package_validation


def validate_po(
    po_file: POFile,
    package_name: str,
    locale: str,
    po_path: Path,
) -> ValidationReport:
    """Check one translation file for problems.

    :param po_file: The translation file to check
    :param package_name: Name of the package
    :param locale: Language code like 'de' or 'fr'
    :param po_path: Path to the file being checked
    :return: Report with all issues found (untranslated, fuzzy, obsolete)
    """
    issues = Issues()

    for entry in po_file:
        if entry.obsolete:
            issues.obsolete.append(entry.msgid)
        elif "fuzzy" in entry.flags:
            issues.fuzzy.append(entry.msgid)
        elif not entry.msgstr and not entry.msgstr_plural:
            issues.untranslated.append(entry.msgid)

    counts = Counts(issues)

    return ValidationReport(
        package=normalize_package_to_module_name(package_name),
        locale=locale,
        filename=po_path,
        issues=issues,
        counts=counts,
    )


def write_validation_report(validation_summary: dict, output_dir: Path) -> None:
    """Write validation report to JSON file.

    :param validation_summary: Output from validate_translations()
    :param output_dir: Where to save the validation report
    """
    report_path = output_dir / "validation-report.json"
    write_json_file(report_path, validation_summary)


def calculate_validation_summary(reports: list[PackageValidation]) -> ValidationSummary:
    """Create a summary of all validation issues.

    :param reports: Individual reports from each package
    :param packages: Names of packages that were checked
    :return: Combined summary with totals and details
    """
    package_breakdown: dict[str, dict] = {}
    language_breakdown: dict[str, dict] = {}

    for report in reports:
        pkg = report["package"]
        locale = report["locale"]
        counts = report["counts"]

        if pkg not in package_breakdown:
            package_breakdown[pkg] = {
                "locales": 0,
                "totalIssues": 0,
                "untranslatedStrings": 0,
                "fuzzyTranslations": 0,
                "problematicLanguages": [],
            }
        package_breakdown[pkg]["locales"] += 1
        package_breakdown[pkg]["totalIssues"] += sum(counts.values())
        package_breakdown[pkg]["untranslatedStrings"] += counts["untranslated"]
        package_breakdown[pkg]["fuzzyTranslations"] += counts["fuzzyTranslations"]
        if sum(counts.values()) > 0:
            package_breakdown[pkg]["problematicLanguages"].append(
                {"locale": locale, "issues": counts}
            )

        if locale not in language_breakdown:
            language_breakdown[locale] = {
                "packages": 0,
                "totalIssues": 0,
                "untranslatedStrings": 0,
                "fuzzyTranslations": 0,
                "isComplete": True,
            }
        language_breakdown[locale]["packages"] += 1
        language_breakdown[locale]["totalIssues"] += sum(counts.values())
        language_breakdown[locale]["untranslatedStrings"] += counts["untranslated"]
        language_breakdown[locale]["fuzzyTranslations"] += counts["fuzzyTranslations"]
        if sum(counts.values()) > 0:
            language_breakdown[locale]["isComplete"] = False

    all_locales = {report["locale"] for report in reports}

    summary_data = {
        "totalPackages": len(packages),
        "totalLocales": len(all_locales),
        "totalIssues": sum(sum(r["counts"].values()) for r in reports),
        "untranslatedStrings": sum(r["counts"]["untranslated"] for r in reports),
        "fuzzyTranslations": sum(r["counts"]["fuzzyTranslations"] for r in reports),
    }

    return {
        "summary": summary_data,
        "packageBreakdown": package_breakdown,
        "languageBreakdown": language_breakdown,
        "reports": reports,
    }
