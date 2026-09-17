from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

import table_export as TX
from preprocess import PreConfig, detect_mode, preprocess


TESSERACT_CMD = os.environ.get(
    "TESSERACT_CMD",
    "",
)

TESSDATA_PREFIX = os.environ.get(
    "TESSDATA_PREFIX",
    "",
)

LOG = logging.getLogger("ocr")

IMAGE_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
}

PDF_EXTS = {".pdf"}

MAX_DIM_DEFAULT = int(
    os.environ.get(
        "OCR_MAX_IMAGE_DIM",
        "3500",
    )
)

DEFAULT_WORKERS = max(
    1,
    (os.cpu_count() or 2) - 1,
)


def configure_tesseract() -> None:
    import pytesseract

    if TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = (
            TESSERACT_CMD
        )

    if TESSDATA_PREFIX:
        os.environ["TESSDATA_PREFIX"] = (
            TESSDATA_PREFIX
        )


def available_languages() -> list[str]:
    try:
        configure_tesseract()

        import pytesseract

        languages = pytesseract.get_languages(
            config=""
        )

        return sorted(
            language
            for language in languages
            if language != "osd"
        )

    except Exception:
        LOG.exception(
            "Unable to retrieve Tesseract languages."
        )
        return []


@dataclass
class Region:
    kind: str
    markdown: str = ""
    html: str = ""
    page: int = 1


@dataclass
class PageResult:
    page: int
    mode: str
    confidence: Optional[float]
    regions: list[Region] = field(
        default_factory=list
    )


@dataclass
class DocResult:
    source: str
    pages: int
    regions: list[Region] = field(
        default_factory=list
    )
    markdown: str = ""
    detected_mode: str = ""
    page_modes: dict[int, str] = field(
        default_factory=dict
    )
    page_confidences: dict[
        int,
        Optional[float],
    ] = field(default_factory=dict)

    @property
    def tables(self) -> list[Region]:
        return [
            region
            for region in self.regions
            if (
                region.kind == "table"
                and region.html
            )
        ]

    @property
    def mean_confidence(
        self,
    ) -> Optional[float]:
        values = [
            value
            for value in (
                self.page_confidences.values()
            )
            if value is not None
        ]

        if not values:
            return None

        return round(
            sum(values) / len(values),
            2,
        )

    @property
    def review_recommended(self) -> bool:
        confidence = self.mean_confidence

        return (
            confidence is not None
            and confidence < 75
        )


def _cap_size(
    image,
    max_dimension: int,
):
    import cv2

    height, width = image.shape[:2]

    if (
        max_dimension
        and max(height, width) > max_dimension
    ):
        scale = (
            max_dimension
            / max(height, width)
        )

        image = cv2.resize(
            image,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_AREA,
        )

    return image


def load_pages(
    path: str | Path,
    dpi: int = 300,
    fast: bool = True,
    max_dimension: int = MAX_DIM_DEFAULT,
) -> list:
    import cv2

    file_path = Path(path)
    extension = file_path.suffix.lower()

    if extension in IMAGE_EXTS:
        image = cv2.imread(
            str(file_path)
        )

        if image is None:
            raise ValueError(
                f"Could not read image: {file_path.name}"
            )

        if fast:
            image = _cap_size(
                image,
                max_dimension,
            )

        return [image]

    if extension in PDF_EXTS:
        import pymupdf as fitz 

        pages = []
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)

        try:
            with fitz.open(
                str(file_path)
            ) as document:
                if document.page_count == 0:
                    raise ValueError(
                        "The PDF contains no pages."
                    )

                for page in document:
                    pixmap = page.get_pixmap(
                        matrix=matrix,
                        alpha=False,
                    )

                    array = np.frombuffer(
                        pixmap.samples,
                        np.uint8,
                    ).reshape(
                        pixmap.height,
                        pixmap.width,
                        pixmap.n,
                    )

                    if pixmap.n == 3:
                        image = cv2.cvtColor(
                            array,
                            cv2.COLOR_RGB2BGR,
                        )
                    else:
                        image = cv2.cvtColor(
                            array,
                            cv2.COLOR_RGBA2BGR,
                        )

                    if fast:
                        image = _cap_size(
                            image,
                            max_dimension,
                        )

                    pages.append(image)

        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(
                "The PDF could not be read."
            ) from exc

        return pages

    raise ValueError(
        f"Unsupported file type: {extension}"
    )


class OCR:
    def __init__(
        self,
        lang: str = "eng",
        mode: str = "auto",
        psm: int = 6,
        detect_tables: bool = True,
        dpi: int = 300,
        locale: str = "auto",
        fast: bool = True,
        workers: int = DEFAULT_WORKERS,
        max_dim: int = MAX_DIM_DEFAULT,
    ):
        configure_tesseract()

        self.lang = lang
        self.mode = mode
        self.psm = int(psm)
        self.detect_tables = bool(
            detect_tables
        )
        self.dpi = int(dpi)
        self.locale = locale
        self.fast = bool(fast)
        self.workers = max(
            1,
            int(workers),
        )
        self.max_dim = int(max_dim)

    def _parse_page(
        self,
        image,
        page_number: int,
    ) -> PageResult:
        import pytesseract
        from pytesseract import Output

        selected_mode = (
            detect_mode(image)
            if self.mode == "auto"
            else self.mode
        )

        pre_config = PreConfig(
            mode=selected_mode
        )

        clean_image = preprocess(
            image,
            pre_config,
        )

        regions: list[Region] = []

        if self.detect_tables:
            try:
                from table_cpu import (
                    has_grid,
                    image_to_html_table,
                )

                if has_grid(image):
                    html = image_to_html_table(
                        image,
                        lang=self.lang,
                        psm=self.psm,
                    )

                    if html:
                        regions.append(
                            Region(
                                kind="table",
                                html=html,
                                page=page_number,
                            )
                        )

            except Exception:
                LOG.exception(
                    "Table detection failed "
                    "on page %s.",
                    page_number,
                )

        config = (
            f"--oem 3 --psm {self.psm}"
        )

        text = pytesseract.image_to_string(
            clean_image,
            lang=self.lang,
            config=config,
        )

        confidence_data = (
            pytesseract.image_to_data(
                clean_image,
                lang=self.lang,
                config=config,
                output_type=Output.DICT,
            )
        )

        confidence_values = []

        for value in confidence_data.get(
            "conf",
            [],
        ):
            try:
                score = float(value)
            except (TypeError, ValueError):
                continue

            if score >= 0:
                confidence_values.append(
                    score
                )

        mean_confidence = (
            round(
                sum(confidence_values)
                / len(confidence_values),
                2,
            )
            if confidence_values
            else None
        )

        regions.append(
            Region(
                kind="text",
                markdown=text,
                page=page_number,
            )
        )

        return PageResult(
            page=page_number,
            mode=selected_mode,
            confidence=mean_confidence,
            regions=regions,
        )

    def process(self, path, progress_callback=None):
        pages = load_pages(
            path,
            dpi=self.dpi,
            fast=self.fast,
            max_dimension=self.max_dim,
        )

        if not pages:
            raise ValueError(
                "The document contains no readable pages."
            )

        document = DocResult(
            source=Path(path).name,
            pages=len(pages),
        )

        total = len(pages)
        page_results = [None] * total

        # Report 0% at the start so the UI shows the correct total immediately.
        if progress_callback:
            progress_callback(0, total)

        completed = 0

        if self.workers > 1 and total > 1:
            with ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = {
                    executor.submit(self._parse_page, image, index + 1): index
                    for index, image in enumerate(pages)
                }

                # as_completed lets us update progress as each page finishes.
                from concurrent.futures import as_completed

                for future in as_completed(futures):
                    index = futures[future]
                    page_results[index] = future.result()
                    completed += 1
                    if progress_callback:
                        progress_callback(completed, total)
        else:
            for index, image in enumerate(pages):
                page_results[index] = self._parse_page(image, index + 1)
                completed += 1
                if progress_callback:
                    progress_callback(completed, total)

        for result in page_results:
            if result is None:
                continue
            document.regions.extend(result.regions)
            document.page_modes[result.page] = result.mode
            document.page_confidences[result.page] = result.confidence

        unique_modes = sorted(set(document.page_modes.values()))
        if len(unique_modes) == 1:
            document.detected_mode = unique_modes[0]
        elif unique_modes:
            document.detected_mode = "mixed"
        else:
            document.detected_mode = self.mode

        document.markdown = "\n\n".join(
            region.markdown.strip()
            for region in document.regions
            if region.kind == "text" and region.markdown.strip()
        )

        return document

    def export(
        self,
        document: DocResult,
        outdir: str | Path,
        stem: str,
    ) -> dict:
        output_directory = Path(outdir)
        output_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        written = {}

        markdown_path = (
            output_directory
            / f"{stem}.md"
        )

        markdown_path.write_text(
            document.markdown or "",
            encoding="utf-8",
        )

        written["text"] = str(
            markdown_path
        )

        if document.tables:
            dataframes = []
            reports = []

            for table_region in document.tables:
                extracted = (
                    TX.html_to_dataframes(
                        table_region.html
                    )
                )

                for dataframe in extracted:
                    cleaned, report = (
                        TX.validate_table(
                            dataframe,
                            len(dataframes),
                            locale=self.locale,
                        )
                    )

                    dataframes.append(cleaned)
                    reports.append(report)

            if dataframes:
                xlsx_path = (
                    output_directory
                    / f"{stem}_tables.xlsx"
                )

                TX.dataframes_to_xlsx(
                    dataframes,
                    reports,
                    str(xlsx_path),
                )

                written["xlsx"] = str(
                    xlsx_path
                )

                written[
                    "tables_needing_review"
                ] = sum(
                    1
                    for report in reports
                    if not report.ok
                )

        written["summary"] = {
            "source": document.source,
            "pages": document.pages,
            "n_tables": len(
                document.tables
            ),
            "lang": self.lang,
            "mode": self.mode,
            "detected_mode": (
                document.detected_mode
            ),
            "page_modes": (
                document.page_modes
            ),
            "page_confidences": (
                document.page_confidences
            ),
            "mean_confidence": (
                document.mean_confidence
            ),
            "review_recommended": (
                document.review_recommended
            ),
        }

        return written