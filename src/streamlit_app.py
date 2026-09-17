"""
streamlit_app.py — Web interface (Streamlit) for the Tesseract OCR toolkit.

Preprocessing has TWO default choices:
  * Auto (recommended) — inspects the uploaded file and picks the best mode.
  * Manual            — you choose one of document/photo/lowcontrast/inverted/none.

Includes optional "Table mode" that reproduces a table as it appears in the file.

Run:
    pip install streamlit
    streamlit run streamlit_app.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

import table_export as TX
import table_format as TF
from ocr import OCR, available_languages, IMAGE_EXTS, PDF_EXTS
import exporters as EX
MANUAL_MODES = ["document", "photo", "lowcontrast", "inverted", "none"]
ALLOWED = sorted({e.lstrip(".") for e in (IMAGE_EXTS | PDF_EXTS)})


@st.cache_data(show_spinner=False)
def _langs():
    return available_languages()


@st.cache_resource
def _workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="ocr_ui_"))


def run_ocr(local: Path, job: Path, lang, mode, psm, detect_tables, locale,
            table_mode, export_formats=None):
    src = local
    export_formats = export_formats or ["md", "xlsx"]
    if table_mode:
        xlsx_path = str(job / f"{src.stem}_tables.xlsx")
        res = TF.extract_table_from_file(str(local), lang=lang, psm=int(psm),
                                         xlsx_out=xlsx_path, locale=locale)
        if not res.found:
            return {"kind": "warn",
                    "status": "⚠️ Table mode: no table detected. "
                              "Uncheck Table mode for normal OCR."}
        md_path = str(job / f"{src.stem}_table.md")
        Path(md_path).write_text(res.aligned_text + "\n\n" + res.markdown,
                                 encoding="utf-8")
        made = EX.export_all(job, f"{src.stem}_table", text=res.aligned_text,
                             table_df=res.to_dataframe(),
                             summary={"source": src.name, "method": res.method},
                             formats=export_formats, locale=locale)
        return {"kind": "ok",
                "status": f"✅ Table mode ({res.method}) — layout preserved.",
                "text": res.aligned_text, "preview": res.to_dataframe(),
                "md_path": md_path, "xlsx_path": xlsx_path, "made": made}

    ocr = OCR(lang=lang, mode=mode, psm=int(psm),
              detect_tables=bool(detect_tables), locale=locale)
    doc = ocr.process(local)
    written = ocr.export(doc, job, src.stem)
    preview = None
    if doc.tables:
        dfs = TX.html_to_dataframes(doc.tables[0].html)
        if dfs:
            preview, _ = TX.validate_table(dfs[0], 0, locale=locale)
    n = written["summary"]["n_tables"]
    review = written.get("tables_needing_review", 0)
    mode_note = (f" · auto → **{doc.detected_mode}**" if mode == "auto"
                 else f" · mode **{mode}**")
    status = (f"✅ Done — {doc.pages} page(s), {n} table(s){mode_note}"
              + (f", {review} flagged for review" if review else "") + ".")
    if written.get("xlsx") is None:
        status += "  (No tables → text only.)"
    made = EX.export_all(job, src.stem, text=doc.markdown or "",
                         table_df=preview, summary=written.get("summary"),
                         formats=export_formats, locale=locale)
    return {"kind": "ok", "status": status,
            "text": doc.markdown or "(no text detected)", "preview": preview,
            "md_path": written.get("text"), "xlsx_path": written.get("xlsx"),
            "made": made}


def main():
    st.set_page_config(page_title="OCR Toolkit", page_icon="🖹", layout="wide")
    st.title("🖹 OCR Toolkit")
    st.caption("Upload a **photo or PDF**, run **Tesseract OCR**, and download "
               "the extracted **text** and **tables (Excel)**.")

    langs = _langs()
    workdir = _workdir()
    
    with st.sidebar:
        st.header("Options")
        lang = st.selectbox("Language", langs, index=0,
                            help="Installed Tesseract language pack(s).")

        # --- TWO default preprocessing choices: Auto vs Manual ---
        prep = st.radio("Preprocessing", ["Auto (recommended)", "Manual"],
                        index=0,
                        help="Auto inspects the uploaded file and picks the best "
                             "mode; Manual lets you choose.")
        if prep.startswith("Auto"):
            mode = "auto"
        else:
            mode = st.selectbox("Manual mode", MANUAL_MODES, index=0,
                                help="document=scans · photo=phone pics · "
                                     "lowcontrast=faint · inverted=light-on-dark")

        with st.expander("Advanced", expanded=False):
            psm = st.slider("Tesseract PSM (page-seg mode)", 3, 13, 6, 1)
            detect_tables = st.checkbox("Detect tables", value=True)
            table_mode = st.checkbox(
                "Table mode (reproduce the table as in the image/PDF)",
                value=False,
                help="Force table extraction and keep the on-page row/column layout.")
            locale = st.radio("Table number style", ["auto", "dot", "comma"],
                              index=0, horizontal=True,
                              help="dot=1,234.56 · comma=1.234,56")
            export_formats = st.multiselect(
                "Export formats", EX.ALL_FORMATS,
                default=["md", "xlsx", "json"],
                help="Pilih satu atau beberapa format hasil.")

    up = st.file_uploader("Upload image or PDF", type=ALLOWED)
    c1, c2 = st.columns([1, 1])
    with c1:
        run = st.button("Run OCR", type="primary", use_container_width=True)
    if up is not None:
        with c2:
            if up.type and up.type.startswith("image"):
                st.image(up, caption="Uploaded image", use_container_width=True)
            else:
                st.info(f"📄 {up.name} ready.")

    if run:
        if up is None:
            st.warning("⚠️ Please upload an image or PDF first."); return
        ext = Path(up.name).suffix.lower()
        if ext not in (IMAGE_EXTS | PDF_EXTS):
            st.error(f"❌ Unsupported file type '{ext}'."); return
        job = workdir / Path(up.name).stem
        job.mkdir(parents=True, exist_ok=True)
        local = job / up.name
        with open(local, "wb") as f:
            f.write(up.getbuffer())
        with st.spinner("Running OCR…"):
            try:
                out = run_ocr(local, job, lang, mode, psm, detect_tables,
                              locale, table_mode, export_formats)
            except Exception as e:
                msg = str(e)
                hint = ("\n\nIf this mentions 'tessdata'/'Failed loading language', "
                        "set TESSERACT_CMD / TESSDATA_PREFIX (env vars or ocr.py).") \
                    if ("tess" in msg.lower() or "language" in msg.lower()) else ""
                st.error(f"❌ OCR failed: {msg}{hint}"); return
        if out["kind"] == "warn":
            st.warning(out["status"]); return
        st.success(out["status"])
        t1, t2 = st.tabs(["📝 Text", "📊 Table preview"])
        with t1:
            st.text_area("Extracted text", out.get("text", ""), height=380)
        with t2:
            p = out.get("preview")
            if p is not None and not p.empty:
                st.dataframe(p, use_container_width=True)
            else:
                st.info("No table detected in this file.")
        made = out.get("made") or {}
        if made:
            cols = st.columns(min(4, len(made)) or 1)
            for i, (fmt, path) in enumerate(made.items()):
                p = Path(path)
                if p.exists():
                    with cols[i % len(cols)]:
                        st.download_button(f"⬇️ {fmt.upper()}",
                                           p.read_bytes(), p.name,
                                           use_container_width=True)
        else:
            d1, d2 = st.columns(2)
            mp, xp = out.get("md_path"), out.get("xlsx_path")
            with d1:
                if mp and Path(mp).exists():
                    st.download_button("⬇️ Download text (.md)",
                                       Path(mp).read_bytes(), Path(mp).name,
                                       "text/markdown", use_container_width=True)
            with d2:
                if xp and Path(xp).exists():
                    st.download_button("⬇️ Download tables (.xlsx)",
                                       Path(xp).read_bytes(), Path(xp).name,
                                       "application/vnd.openxmlformats-officedocument."
                                       "spreadsheetml.sheet", use_container_width=True)

    st.divider()
    st.caption("Preprocessing **Auto** picks the mode from your file. Turn on "
               "**Table mode** to reproduce a table exactly as in the image/PDF.")


if __name__ == "__main__":
    main()