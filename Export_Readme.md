# Perubahan: Export multi-format + Percepat Tesseract untuk file besar

Dua fitur, dua file BARU + perubahan kecil di file lama. Semua sudah dites.

---

## FILE BARU (tambahkan ke `src/`)
| File | Fungsi |
|------|--------|
| `exporters.py` | Export hasil ke **txt, md, json, html, csv, xlsx, docx** sesuai pilihan user |
| `ocr.py` (ganti) | Versi baru dengan **size-cap** + **parallel pages** untuk file besar |

---

# BAGIAN 1 — Export multi-format (JSON/XLSX/MD/CSV/TXT/HTML/DOCX)

## Di mana & apa yang diubah

### A) `exporters.py` — FILE BARU (sudah jadi, tinggal taruh)
Berisi `export_all(outdir, stem, text, table_df, summary, formats)` yang menulis
hanya format yang dipilih user. Format `csv`/`xlsx` otomatis dilewati kalau tak
ada tabel. `ALL_FORMATS` = daftar semua pilihan untuk UI.

### B) `api_server.py` — tambah pilihan `formats` di endpoint `/ocr`
Tambahkan parameter form dan bangun file sesuai pilihan.

**Tambah import (atas):**
```python
import exporters as EX
```

**Tambah field di signature `ocr_endpoint`:**
```python
    formats: str = Form("md,xlsx"),   # <-- CSV list, mis. "json,xlsx,md,csv"
```

**Di blok normal OCR, GANTI bagian membangun `"files": {...}`** dengan:
```python
        # build every format the user asked for
        table_df = None
        if doc.tables:
            dfs = TX.html_to_dataframes(doc.tables[0].html)
            if dfs:
                table_df, _ = TX.validate_table(dfs[0], 0, locale=locale)

        chosen = [f.strip() for f in formats.split(",") if f.strip()]
        made = EX.export_all(job, local.stem, text=doc.markdown or "",
                             table_df=table_df, summary=written.get("summary"),
                             formats=chosen, locale=locale)
        files = {fmt: {"name": Path(p).name, "b64": _b64(p)}
                 for fmt, p in made.items()}
        return {"ok": True, "status": status,
                "detected_mode": doc.detected_mode,
                "text": doc.markdown or "", "table": table,
                "files": files}          # <-- now a dict keyed by format
```

### C) `streamlit_app.py` (versi lokal) — tambah pilihan format + tombol download
Import di atas: `import exporters as EX`

Di sidebar (mis. dalam expander Advanced), tambahkan:
```python
            export_formats = st.multiselect(
                "Export formats", EX.ALL_FORMATS,
                default=["md", "xlsx", "json"],
                help="Pilih satu atau beberapa format hasil.")
```

Setelah OCR selesai (di dalam `run_ocr` atau setelahnya), panggil:
```python
    made = EX.export_all(job, local.stem, text=out_text,
                         table_df=preview_df_or_None,
                         summary=summary_dict, formats=export_formats)
```
Lalu buat tombol download untuk tiap format:
```python
        for fmt, path in made.items():
            st.download_button(f"⬇️ {fmt.upper()}",
                               Path(path).read_bytes(),
                               file_name=Path(path).name,
                               use_container_width=True)
```

### D) `streamlit_client.py` (frontend API) — kirim pilihan & tampilkan semua download
Tambah di sidebar:
```python
        export_formats = st.multiselect(
            "Export formats", ["txt","md","json","html","csv","xlsx","docx"],
            default=["md","xlsx","json"])
```
Tambah ke `data` saat POST:
```python
        data["formats"] = ",".join(export_formats)
```
Ganti bagian download jadi loop atas semua file yang dikembalikan:
```python
        for fmt, info in out.get("files", {}).items():
            if info and info.get("b64"):
                import base64
                st.download_button(f"⬇️ {fmt.upper()}",
                                   base64.b64decode(info["b64"]),
                                   file_name=info["name"],
                                   use_container_width=True)
```

---

# BAGIAN 2 — Percepat Tesseract untuk file besar (tetap akurat)

Semua sudah ada di **`ocr.py` versi baru**. Tiga optimasi:

1. **Size cap (`fast=True`, default):** gambar/halaman raksasa di-resize agar sisi
   terpanjang ≤ `MAX_DIM` (default **3500 px**) memakai `INTER_AREA`. Di atas
   ~3500 px, Tesseract TIDAK bertambah akurat tapi jauh lebih lambat — jadi ini
   menghemat waktu **tanpa** mengorbankan akurasi. Nonaktifkan dengan
   `fast=False` / `--no-fast`.
2. **Parallel pages (`workers>1`, default = CPU-1):** PDF multi-halaman diproses
   paralel via thread (Tesseract melepas GIL saat mengenali teks, jadi scaling
   bagus di CPU multi-core). Urutan halaman tetap terjaga.
3. **Mode dideteksi SEKALI** dari halaman pertama → konsisten antar halaman.

### Apa yang perlu diubah agar UI/dipakai?
- **Sudah otomatis** — `OCR(...)` dan `run_file(...)` kini menerima `fast=` dan
  `workers=` dengan default aman. Tidak wajib ubah apa pun untuk mendapat speed-up.
- **Opsional**, ekspos ke user. Di `api_server.py` tambah field form:
  ```python
      fast: bool = Form(True),
      workers: int = Form(0),      # 0 = auto (CPU-1)
  ```
  lalu:
  ```python
      import os
      w = workers or max(1, (os.cpu_count() or 2) - 1)
      ocr = OCR(lang=lang, mode=mode, psm=int(psm),
                detect_tables=bool(detect_tables), locale=locale,
                fast=bool(fast), workers=w)
  ```
- **CLI** sudah punya `--no-fast` dan `--workers N`.

### Tuning (kalau perlu)
- Dokumen padat/kecil font → naikkan cap: `OCR(max_dim=4200)`.
- CPU terbatas → `workers=2`.
- Ingin identik dengan versi lama (tanpa cap) → `fast=False`.

---

## Hasil test (sudah diverifikasi)
- Export 7 format (txt/md/json/html/csv/xlsx/docx) → semua terbuat ✅
- Size cap: 6000×4000 → 3500×2333; gambar kecil tak disentuh ✅
- Parallel: 6 tugas 0.4s vs serial 1.2s (~3×), urutan terjaga ✅
- End-to-end OCR kecil + export multi-format ✅
