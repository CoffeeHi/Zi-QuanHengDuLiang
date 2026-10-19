#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 QHDL Poem Fix contributors
"""Build or verify the narrowly patched QHDL Poem Fix font.

The checked-in JSON contains the final glyph outlines and exact metrics.
The upstream v0.2 input is SHA-256 pinned; it is never modified in place.
Requires fontTools==4.67.0. No historical review files or local vendor are used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import fontTools
from fontTools.pens.recordingPen import DecomposingRecordingPen, replayRecording
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTFont

REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE_SHA256 = "0829f730d241003477521cd535282c01d66fc5a20cea912c538b885a750c5e61"
TARGET_CHARACTERS = "入送书士个凉屏料罥浥疏乌"
FAMILY = "QHDL Poem Fix"
POSTSCRIPT = "QHDLPoemFix-Regular"
FULL_NAME = FAMILY + " Regular"
VERSION = "0.204-poemfix"
REVISION = 0.204
MODIFIED_TIME = int(datetime(2026, 10, 19, tzinfo=timezone.utc).timestamp()) + 2082844800
COPYRIGHT_ADDITION = "Selected glyph revisions copyright 2026 Poem Subtitle Font Revision contributors."
MUTABLE_TABLES = {"CFF ", "hmtx", "vmtx", "name", "head", "DSIG"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def public_path(path: Path) -> str:
    """Keep validation artifacts portable and free of workstation paths."""
    try:
        return str(path.relative_to(REPOSITORY))
    except ValueError:
        return path.name


def normalized(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (tuple, list)):
        return [normalized(item) for item in value]
    return value


def outline_hash(glyph_set, glyph_name: str) -> str:
    pen = DecomposingRecordingPen(glyph_set)
    glyph_set[glyph_name].draw(pen)
    raw = json.dumps(normalized(pen.value), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def recording_hash(recording) -> str:
    raw = json.dumps(normalized(recording), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def name_values(font, name_id: int):
    return sorted({row.toUnicode() for row in font["name"].names if row.nameID == name_id})


def unicode_tables(font):
    return [(table.platformID, table.platEncID, table.language, table.format,
             getattr(table, "cmap", {}), getattr(table, "uvsDict", {}))
            for table in font["cmap"].tables if table.isUnicode()]


def load_patch(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != "recording-pen-glyph-patch-v1":
        raise ValueError("Unsupported glyph patch format")
    if payload.get("source_sha256") != SOURCE_SHA256:
        raise ValueError("Patch is not based on the pinned upstream v0.2 font")
    rows = payload.get("glyphs", [])
    if len(rows) != len(TARGET_CHARACTERS) or {row["character"] for row in rows} != set(TARGET_CHARACTERS):
        raise ValueError("Patch must contain each of the expected characters exactly once")
    if len({row["glyph_name"] for row in rows}) != len(TARGET_CHARACTERS):
        raise ValueError("Expected a distinct target glyph for each patched character")
    for row in rows:
        if recording_hash(row["recording"]) != row["final_outline_sha256"]:
            raise ValueError("Patch outline payload hash mismatch: " + row["character"])
        for key in ("horizontal_metrics", "vertical_metrics"):
            metric = row[key]
            if len(metric) != 2 or not all(isinstance(value, int) for value in metric):
                raise ValueError("Invalid integer metrics: " + row["character"])
    return payload, rows


def validate_base(font, rows):
    if font["head"].unitsPerEm != 1000 or len(font.getGlyphOrder()) != 65535:
        raise ValueError("Unexpected upstream UPM or glyph count")
    cmap, glyph_set = font.getBestCmap(), font.getGlyphSet()
    for row in rows:
        char, glyph = row["character"], row["glyph_name"]
        if cmap.get(ord(char)) != glyph:
            raise ValueError("Unexpected source Unicode mapping: " + char)
        if outline_hash(glyph_set, glyph) != row["source_outline_sha256"]:
            raise ValueError("Unexpected source outline: " + char)


def rename(font):
    values = {1: FAMILY, 2: "Regular", 3: POSTSCRIPT + ";" + VERSION,
              4: FULL_NAME, 5: "Version " + VERSION, 6: POSTSCRIPT,
              16: FAMILY, 17: "Regular"}
    for row in font["name"].names:
        if row.nameID in values:
            row.string = values[row.nameID].encode(row.getEncoding(), errors="replace")
        elif row.nameID == 0 and COPYRIGHT_ADDITION not in row.toUnicode():
            row.string = (row.toUnicode() + "; " + COPYRIGHT_ADDITION).encode(
                row.getEncoding(), errors="replace")
    for name_id, value in values.items():
        font["name"].setName(value, name_id, 3, 1, 0x409)
    cff = font["CFF "].cff
    cff.fontNames = [POSTSCRIPT]
    top = cff.topDictIndex[0]
    top.FamilyName, top.FullName, top.version = FAMILY, FULL_NAME, VERSION
    # All internal font names are independent, including each CID font dict.
    if hasattr(top, "FontName"):
        top.FontName = POSTSCRIPT
    if hasattr(top, "BaseFontName"):
        top.BaseFontName = POSTSCRIPT
    if hasattr(top, "CIDFontVersion"):
        top.CIDFontVersion = REVISION
    if hasattr(top, "FDArray"):
        for index, font_dict in enumerate(top.FDArray):
            font_dict.FontName = f"QHDLPoemFix-FD{index}"
    font["head"].fontRevision = REVISION
    font["head"].modified = MODIFIED_TIME
    if "DSIG" in font:
        del font["DSIG"]


def build(source: Path, output: Path, rows):
    font = TTFont(source, recalcTimestamp=False)
    try:
        validate_base(font, rows)
        cff = font["CFF "].cff
        top = cff.topDictIndex[0]
        for row in rows:
            glyph = row["glyph_name"]
            old = top.CharStrings[glyph]
            pen = T2CharStringPen(row["horizontal_metrics"][0], None)
            replayRecording(row["recording"], pen)
            top.CharStrings[glyph] = pen.getCharString(private=old.private,
                                                       globalSubrs=cff.GlobalSubrs)
            # These are reviewed final values, not estimates from new bounds.
            font["hmtx"][glyph] = tuple(row["horizontal_metrics"])
            font["vmtx"][glyph] = tuple(row["vertical_metrics"])
        rename(font)
        output.parent.mkdir(parents=True, exist_ok=True)
        font.save(output, reorderTables=True)
    finally:
        font.close()


def verify(source: Path, output: Path, rows):
    original = TTFont(source, recalcTimestamp=False)
    derived = TTFont(output, recalcTimestamp=False)
    checks, errors = {}, []
    try:
        old_order, new_order = original.getGlyphOrder(), derived.getGlyphOrder()
        allowed = {row["glyph_name"] for row in rows}
        by_name = {row["glyph_name"]: row for row in rows}
        old_set, new_set = original.getGlyphSet(), derived.getGlyphSet()
        modified, unexpected_outline, unexpected_metric, patch_errors = [], [], [], []
        metric_changes = {"hmtx": [], "vmtx": []}
        # Iterate every glyph, including unencoded glyphs; do not sample.
        for glyph in old_order:
            if glyph not in new_set:
                errors.append("Missing glyph: " + glyph)
                continue
            before, after = outline_hash(old_set, glyph), outline_hash(new_set, glyph)
            if before != after:
                modified.append(glyph)
                if glyph not in allowed:
                    unexpected_outline.append(glyph)
            if glyph in allowed and after != by_name[glyph]["final_outline_sha256"]:
                patch_errors.append({"glyph": glyph, "kind": "outline_hash"})
            for table, patch_key in (("hmtx", "horizontal_metrics"),
                                     ("vmtx", "vertical_metrics")):
                old_metric, new_metric = original[table][glyph], derived[table][glyph]
                if old_metric != new_metric:
                    metric_changes[table].append(glyph)
                    if glyph not in allowed:
                        unexpected_metric.append({"glyph": glyph, "table": table})
                if glyph in allowed and tuple(by_name[glyph][patch_key]) != new_metric:
                    patch_errors.append({"glyph": glyph, "kind": table})
        old_tables, new_tables = set(original.reader.tables), set(derived.reader.tables)
        frozen_tables = sorted((old_tables | new_tables) - MUTABLE_TABLES)
        frozen_identical = {tag: (tag in old_tables and tag in new_tables
                                 and original.reader[tag] == derived.reader[tag])
                            for tag in frozen_tables}
        cmap = original.getBestCmap()
        affected_codepoints = [f"U+{cp:04X}" for cp, glyph in sorted(cmap.items())
                               if glyph in allowed]
        top = derived["CFF "].cff.topDictIndex[0]
        cff_names = list(derived["CFF "].cff.fontNames)
        fd_names = [fd.FontName for fd in getattr(top, "FDArray", [])]
        copyright_text = "\n".join(name_values(derived, 0))
        metadata = {
            "family": name_values(derived, 1), "full_name": name_values(derived, 4),
            "postscript_name": name_values(derived, 6), "version": name_values(derived, 5),
            "cff_font_names": cff_names, "cff_family": top.FamilyName,
            "cff_full_name": top.FullName, "cff_version": top.version,
            "cff_fd_names": fd_names, "head_modified": derived["head"].modified,
        }
        expected_metric_changes = {
            table: {row["glyph_name"] for row in rows
                    if original[table][row["glyph_name"]] != tuple(row[patch_key])}
            for table, patch_key in (("hmtx", "horizontal_metrics"),
                                     ("vmtx", "vertical_metrics"))
        }
        checks.update({
            "glyph_count": {"original": len(old_order), "derived": len(new_order)},
            "glyph_order_unchanged": old_order == new_order,
            "best_cmap_unchanged": original.getBestCmap() == derived.getBestCmap(),
            "all_unicode_cmap_tables_unchanged": unicode_tables(original) == unicode_tables(derived),
            "units_per_em_unchanged": original["head"].unitsPerEm == derived["head"].unitsPerEm == 1000,
            "actual_modified_outlines": modified,
            "actual_modified_outline_count": len(modified),
            "expected_modified_outline_count": len(allowed),
            "exactly_expected_glyphs_changed": set(modified) == allowed,
            "unexpected_outline_changes": unexpected_outline,
            "metric_changes": metric_changes,
            "exactly_expected_metric_changes": all(set(metric_changes[table]) == expected_metric_changes[table]
                                                   for table in metric_changes),
            "unexpected_metric_changes": unexpected_metric,
            "final_patch_mismatches": patch_errors,
            "affected_unicode_codepoints": affected_codepoints,
            "cmap_alias_note": "The patched shared glyphs also cover U+2F0A, U+2F20, U+F979, U+F9BE; mappings are unchanged.",
            "frozen_tables_byte_identical": frozen_identical,
            "table_set_expected": new_tables == old_tables - {"DSIG"},
            "dsig_removed": "DSIG" not in derived,
            "metadata": metadata,
            "independent_names_expected": (name_values(derived, 1) == [FAMILY]
                                           and name_values(derived, 4) == [FULL_NAME]
                                           and name_values(derived, 6) == [POSTSCRIPT]
                                           and name_values(derived, 5) == ["Version " + VERSION]
                                           and cff_names == [POSTSCRIPT]
                                           and top.FamilyName == FAMILY and top.FullName == FULL_NAME
                                           and top.version == VERSION
                                           and all(name.startswith("QHDLPoemFix-") for name in fd_names)),
            "embedded_original_copyright_retained": all(value in copyright_text for value in name_values(original, 0)),
            "fixed_head_modified": derived["head"].modified == MODIFIED_TIME,
            "head_revision_expected": abs(derived["head"].fontRevision - REVISION) <= 1 / 65536,
        })
        for key in ("glyph_order_unchanged", "best_cmap_unchanged", "all_unicode_cmap_tables_unchanged",
                    "units_per_em_unchanged", "exactly_expected_glyphs_changed",
                    "exactly_expected_metric_changes", "table_set_expected", "dsig_removed",
                    "independent_names_expected", "embedded_original_copyright_retained",
                    "fixed_head_modified", "head_revision_expected"):
            if not checks[key]:
                errors.append("Failed check: " + key)
        if not all(frozen_identical.values()):
            errors.append("A table outside the permitted build changes differs")
        if unexpected_outline or unexpected_metric or patch_errors:
            errors.append("Unexpected glyph/metric change or final patch mismatch")
    finally:
        original.close()
        derived.close()
    return checks, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=REPOSITORY / "data/QuanHengDuLiang-v0.2.otf")
    parser.add_argument("--patch", type=Path, default=REPOSITORY / "patches/poem-glyphs.json")
    parser.add_argument("--output", type=Path, default=REPOSITORY / "data/QuanHengDuLiang-PoemFix-v0.2.4.otf")
    parser.add_argument("--verify-report", type=Path, default=REPOSITORY / "build/poemfix-validation.json")
    parser.add_argument("--verify-only", action="store_true", help="Verify an existing --output without rebuilding it")
    args = parser.parse_args()
    source, output, patch_path = args.source.resolve(), args.output.resolve(), args.patch.resolve()
    report_path = args.verify_report.resolve()
    # Check report destinations before entering the error-reporting handler:
    # an invalid destination must never be written even as a failure report.
    if any(report_path == path or (report_path.exists() and path.exists()
                                  and report_path.samefile(path))
           for path in (source, output, patch_path)):
        parser.error("Validation report must not overwrite an input or output font")
    report = {"ok": False, "errors": [], "checks": {}, "source": public_path(source),
              "derived": public_path(output), "patch": public_path(patch_path),
              "fonttools_version": fontTools.__version__}
    try:
        if fontTools.__version__ != "4.67.0":
            raise ValueError("This reproducible build requires fontTools==4.67.0")
        if any(output == path or (output.exists() and path.exists() and output.samefile(path))
               for path in (source, patch_path)):
            raise ValueError("Refusing to overwrite the upstream source font or patch")
        report["source_sha256"] = sha256(source)
        if report["source_sha256"] != SOURCE_SHA256:
            raise ValueError("Upstream source SHA-256 mismatch; refusing to apply patch")
        payload, rows = load_patch(patch_path)
        report["patch_sha256"] = sha256(patch_path)
        report["reference_review_sha256"] = payload.get("review_font_sha256")
        if not args.verify_only:
            print("Building glyph corrections with fixed metadata...", flush=True)
            build(source, output, rows)
        print("Verifying every one of 65,535 glyphs and unchanged tables...", flush=True)
        report["checks"], report["errors"] = verify(source, output, rows)
        report["derived_sha256"] = sha256(output)
        report["checks"]["source_file_unchanged"] = sha256(source) == SOURCE_SHA256
        if not report["checks"]["source_file_unchanged"]:
            report["errors"].append("Upstream source font changed during build")
        report["ok"] = not report["errors"]
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        for path in (source, output, patch_path, report_path):
            message = message.replace(str(path), public_path(path))
        report["errors"].append(message)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "output": str(output), "report": str(report_path),
                      "errors": report["errors"]}, ensure_ascii=False), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
