#!/usr/bin/env python3
"""Extract the supplied IELTS descriptor tables into browser-ready JavaScript."""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

import pdfplumber


TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 5,
    "join_tolerance": 5,
    "intersection_tolerance": 5,
}

REPLACEMENTS = {
    "inappropriaciesand": "inappropriacies and",
    "inappropriaciesoccur": "inappropriacies occur",
    "Organisationis": "Organisation is",
    "organisationis": "organisation is",
    "skilfullymanaged": "skilfully managed",
    "skilfuluse": "skilful use",
    "memorisedphrases": "memorised phrases",
    "memorisedlanguage": "memorised language",
    "languageunless": "language unless",
    "organisationalfeatures": "organisational features",
    "mayimpede": "may impede",
    "beconfused": "be confused",
    "confused.The": "confused. The",
    "orunrelated": "or unrelated",
    "theappropriacy": "the appropriacy",
    "over-generaliseor": "over-generalise or",
    "recognisablestrings": "recognisable strings",
    "rateablelanguage": "rateable language",
    "addressed.The": "addressed. The",
    "mechanical.There": "mechanical. There",
    "e.g.memorised": "e.g. memorised",
    "e.g. memorisedphrases": "e.g. memorised phrases",
    "writerexpresses": "writer expresses",
}


def clean_text(value: str | None) -> str:
    text = (value or "").replace("\u00ad", "")
    text = re.sub(r"\s+", " ", text).strip()
    for source, target in REPLACEMENTS.items():
        text = text.replace(source, target)
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    text = text.replace("self- correction", "self-correction")
    text = re.sub(r"\s*–\s*", " – ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text


def sentence_chunks(value: str | None) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    protected = (
        text.replace("e.g.", "e§g§")
        .replace("i.e.", "i§e§")
        .replace("and/or", "and∕or")
    )
    chunks = re.split(r"(?<=[.!?])\s+(?=(?:\(|[A-Z]))", protected)
    return [
        chunk.replace("e§g§", "e.g.")
        .replace("i§e§", "i.e.")
        .replace("and∕or", "and/or")
        .strip()
        for chunk in chunks
        if chunk.strip()
    ]


def bold_phrases(page: pdfplumber.page.Page, bbox: tuple | None) -> list[str]:
    if not bbox:
        return []
    x0, top, x1, bottom = bbox
    safe_bbox = (
        max(0, x0),
        max(0, top),
        min(page.width, x1),
        min(page.height, bottom),
    )
    words = page.crop(safe_bbox).extract_words(
        extra_attrs=["fontname"],
        keep_blank_chars=False,
        use_text_flow=True,
    )
    phrases: list[str] = []
    current: list[str] = []
    for word in words:
        if "Bold" in word["fontname"]:
            current.append(word["text"])
        elif current:
            phrases.append(clean_text(" ".join(current)))
            current = []
    if current:
        phrases.append(clean_text(" ".join(current)))
    return [phrase for phrase in phrases if phrase]


def paragraph_texts(
    page: pdfplumber.page.Page,
    bbox: tuple | None,
    fallback: str | None,
) -> list[str]:
    if not bbox:
        return [clean_text(fallback)] if clean_text(fallback) else []
    x0, top, x1, bottom = bbox
    safe_bbox = (
        max(0, x0),
        max(0, top),
        min(page.width, x1),
        min(page.height, bottom),
    )
    lines = page.crop(safe_bbox).extract_text_lines(
        layout=False,
        strip=True,
        return_chars=True,
    )
    if not lines:
        return [clean_text(fallback)] if clean_text(fallback) else []

    sizes = [
        char.get("size", 0)
        for line in lines
        for char in line.get("chars", [])
        if char.get("text", "").strip()
    ]
    paragraph_gap = max(4.5, (statistics.median(sizes) if sizes else 9) * 0.65)
    paragraphs: list[list[str]] = []
    current: list[str] = []
    previous_bottom: float | None = None

    for line in lines:
        line_text = clean_text(line["text"])
        if not line_text:
            continue
        gap = 0 if previous_bottom is None else line["top"] - previous_bottom
        if current and gap > paragraph_gap:
            paragraphs.append(current)
            current = []
        current.append(line_text)
        previous_bottom = line["bottom"]
    if current:
        paragraphs.append(current)

    return [
        clean_text(" ".join(lines_in_paragraph))
        for lines_in_paragraph in paragraphs
        if clean_text(" ".join(lines_in_paragraph))
    ]


def descriptor_paragraphs(
    page: pdfplumber.page.Page,
    bbox: tuple | None,
    value: str | None,
    bold_values: list[str],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    paragraphs = paragraph_texts(page, bbox, value)
    unused_bold = list(bold_values)
    for paragraph in paragraphs:
        spans = []
        remaining = []
        search_from = 0
        for phrase in unused_bold:
            start = paragraph.lower().find(phrase.lower(), search_from)
            if start < 0:
                start = paragraph.lower().find(phrase.lower())
            if start >= 0:
                spans.append([start, start + len(phrase)])
                search_from = start + len(phrase)
            else:
                remaining.append(phrase)
        unused_bold = remaining
        result.append({"text": paragraph, "bold": spans})
    return result


def extract_section(
    pdf_path: Path,
    page_indices: list[int],
    criteria: list[str],
) -> dict[str, dict[str, list[dict[str, object]]]]:
    result: dict[str, dict[str, list[dict[str, object]]]] = {}
    with pdfplumber.open(pdf_path) as document:
        for page_index in page_indices:
            page = document.pages[page_index]
            tables = page.find_tables(TABLE_SETTINGS)
            if not tables:
                raise RuntimeError(
                    f"No descriptor table found on page {page_index + 1} of {pdf_path}"
                )
            table = tables[0]
            extracted_rows = table.extract()
            for row_index, row in enumerate(extracted_rows[1:], start=1):
                band = clean_text(row[0])
                if not band.isdigit():
                    continue
                cells = row[1:5]
                cell_boxes = table.rows[row_index].cells[1:5]
                bold_cells = [
                    bold_phrases(page, bbox)
                    for bbox in cell_boxes
                ]
                if band == "0" and cells and not any(cells[1:]):
                    cells = [cells[0]] * 4
                    cell_boxes = [cell_boxes[0]] * 4
                    bold_cells = [bold_cells[0]] * 4
                result[band] = {
                    criterion: descriptor_paragraphs(
                        page, bbox, cell, bold_cell
                    )
                    for criterion, bbox, cell, bold_cell in zip(
                        criteria, cell_boxes, cells, bold_cells, strict=True
                    )
                }
    expected = {str(band) for band in range(10)}
    missing = sorted(expected - set(result))
    if missing:
        raise RuntimeError(f"Missing bands {missing} while extracting {pdf_path}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--speaking", type=Path, required=True)
    parser.add_argument("--writing", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    speaking_criteria = [
        "Fluency and coherence",
        "Lexical resource",
        "Grammatical range and accuracy",
        "Pronunciation",
    ]
    writing_task_1_criteria = [
        "Task achievement",
        "Coherence and cohesion",
        "Lexical resource",
        "Grammatical range and accuracy",
    ]
    writing_task_2_criteria = [
        "Task response",
        "Coherence and cohesion",
        "Lexical resource",
        "Grammatical range and accuracy",
    ]

    data = {
        "meta": {
            "title": "IELTS Rating Scales",
            "sourceNote": (
                "Descriptors extracted from the supplied IELTS Speaking and "
                "Writing Band Descriptor PDFs (updated May 2023 where noted)."
            ),
        },
        "sections": {
            "Speaking": {
                "shortLabel": "Speaking",
                "criteria": speaking_criteria,
                "note": (
                    "A candidate must fully fit the positive features at a "
                    "particular level and is rated on average performance "
                    "across all parts of the test."
                ),
                "bands": extract_section(
                    args.speaking, [1, 2, 3], speaking_criteria
                ),
            },
            "Writing Task 1": {
                "shortLabel": "Task 1",
                "criteria": writing_task_1_criteria,
                "note": (
                    "A script must fully fit the positive features at a "
                    "particular level. Negative features can limit a rating."
                ),
                "bands": extract_section(
                    args.writing, [2, 3, 4], writing_task_1_criteria
                ),
            },
            "Writing Task 2": {
                "shortLabel": "Task 2",
                "criteria": writing_task_2_criteria,
                "note": (
                    "A script must fully fit the positive features at a "
                    "particular level. Negative features can limit a rating."
                ),
                "bands": extract_section(
                    args.writing, [6, 7, 8], writing_task_2_criteria
                ),
            },
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    args.out.write_text(f"window.IELTS_DATA = {payload};\n", encoding="utf-8")


if __name__ == "__main__":
    main()
