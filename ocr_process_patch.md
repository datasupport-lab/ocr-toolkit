
# Patch untuk ocr.py — tambah `progress_callback`

Ini bukan file utuh, hanya bagian `process()` yang perlu diganti di `ocr.py`
kamu yang sudah ada. Tujuannya: melaporkan progress per halaman ke job_store
lewat callback, tanpa mengubah kualitas OCR (Tesseract + cv2 tetap sama).

## Ganti method `process()` menjadi seperti ini

```python
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
```

## Catatan

- `progress_callback(completed, total)` dipanggil setiap halaman selesai,
  termasuk pada mode paralel (memakai `as_completed`).
- Kalau `progress_callback=None` (misalnya dipakai dari CLI/test), perilaku
  lama tetap jalan tanpa error.
- Tidak ada perubahan pada kualitas OCR; hanya menambah pelaporan progress.
