"""
streamlit_client.py — THIN FRONTEND that talks to the OCR API over HTTP.

Changes in this version:
  * TABLE MODE is a standalone toggle in the MAIN area (not inside Advanced).
  * OUTPUT: user picks one or more export formats; a download button appears
    for EACH produced format (txt/md/json/html/csv/xlsx/docx).
  * Sends speed flags (fast, workers) + shows elapsed time, so large files are
    faster and the user sees progress.
  * Visual pass: paper/document-themed styling, grouped sidebar sections,
    status badge, a clearer upload -> configure -> run -> results flow, and
    a light/dark mode toggle with a single coordinated color system driving
    every custom element (no functional behavior changed).

Run:
  pip install streamlit requests
  streamlit run streamlit_client.py
  # remote API:
  OCR_API_URL=http://SERVER_IP:8000 streamlit run streamlit_client.py

Note: the styling below uses st.container(border=True), which requires
Streamlit >= 1.28. If you're on an older version, run:
  pip install -U streamlit
"""
from __future__ import annotations

import base64
import os
import time

import requests
import streamlit as st

DEFAULT_API = os.environ.get("OCR_API_URL", "http://localhost:8000")
API_CONNECT_TIMEOUT = float(
    os.getenv(
        "API_CONNECT_TIMEOUT",
        "5",
    )
)

API_READ_TIMEOUT = float(
    os.getenv(
        "API_READ_TIMEOUT",
        "900",
    )
)

API_HEALTH_RETRIES = int(
    os.getenv(
        "API_HEALTH_RETRIES",
        "3",
    )
)

MANUAL_MODES = ["document", "photo", "lowcontrast", "inverted", "none"]
EXPORT_FORMATS = ["txt", "md", "json", "html", "csv", "xlsx", "docx"]

# nice labels + MIME for download buttons
_MIME = {
    "txt": "text/plain", "md": "text/markdown", "json": "application/json",
    "html": "text/html", "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# icons per format, purely cosmetic, used on download buttons
_ICON = {
    "txt": "📄", "md": "📝", "json": "🗂️", "html": "🌐",
    "csv": "📊", "xlsx": "📈", "docx": "📃",
}

# ----------------------------------------------------------------------
# Color system — one token set per mode, everything below reads from this
# so light/dark stay internally consistent instead of drifting.
# ----------------------------------------------------------------------
THEMES = {
    "light": {
        "paper": "#EFE7D8",        # page background — warm greige
        "panel": "#FAF4E8",        # card / sidebar surface — warm ivory, not stark white
        "panel_alt": "#E9DCC5",    # deeper warm tan — dropzone / inset surface, the "brown blend"
        "ink": "#24314F",          # headings
        "text": "#2C2A26",         # body text
        "muted": "#7A6F5C",        # secondary text
        "border": "#D8C9AC",
        "accent": "#A9673A",       # primary action (earthy terracotta)
        "accent_dark": "#8B5230",  # hover
        "accent_contrast": "#FFFFFF",
        "success_bg": "#E9EFE1", "success_text": "#3F6B3F", "success_border": "#C4D6B6",
        "danger_bg": "#F6E7DC", "danger_text": "#A8442A", "danger_border": "#E4BFA1",
        "shadow": "rgba(89, 61, 27, 0.10)",
    },
    "dark": {
        "paper": "#12151B",
        "panel": "#1B1F27",
        "panel_alt": "#20242D",
        "ink": "#EDE7DA",
        "text": "#D9D4C8",
        "muted": "#8B8778",
        "border": "#2B2F38",
        "accent": "#D98A54",
        "accent_dark": "#E39C6B",
        "accent_contrast": "#1B140D",
        "success_bg": "#1D2A20", "success_text": "#8FD1A0", "success_border": "#2E4530",
        "danger_bg": "#2B1E1A", "danger_text": "#E2967A", "danger_border": "#4A2E24",
        "shadow": "rgba(0, 0, 0, 0.35)",
    },
}


# ----------------------------------------------------------------------
# Styling
# ----------------------------------------------------------------------
def inject_style(mode: str):
    c = THEMES[mode]
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

    :root {{
        --ink: {c['ink']};
        --paper: {c['paper']};
        --panel: {c['panel']};
        --panel-alt: {c['panel_alt']};
        --accent: {c['accent']};
        --accent-dark: {c['accent_dark']};
        --accent-contrast: {c['accent_contrast']};
        --text: {c['text']};
        --muted: {c['muted']};
        --border: {c['border']};
        --success-bg: {c['success_bg']};
        --success-text: {c['success_text']};
        --success-border: {c['success_border']};
        --danger-bg: {c['danger_bg']};
        --danger-text: {c['danger_text']};
        --danger-border: {c['danger_border']};
        --shadow: {c['shadow']};
    }}

    html, body, [class*="stApp"] {{
        font-family: 'IBM Plex Sans', sans-serif;
        color: var(--text);
    }}

    .stApp {{ background-color: var(--paper); }}

    /* sidebar */
    section[data-testid="stSidebar"] {{
        background-color: var(--panel);
        border-right: 1px solid var(--border);
    }}
    section[data-testid="stSidebar"] .block-container {{ padding-top: 1.5rem; }}

    /* native Streamlit header / toolbar (the "Deploy" bar) — only the in-app
       header is reachable from here; if this is hosted on Streamlit
       Community Cloud, the outer platform chrome above it is added by the
       host and cannot be restyled from app code. */
    header[data-testid="stHeader"] {{
        background-color: var(--paper) !important;
        border-bottom: 1px solid var(--border);
    }}
    header[data-testid="stHeader"] * {{ color: var(--text) !important; fill: var(--text) !important; }}
    div[data-testid="stToolbar"] {{ background-color: transparent !important; }}
    div[data-testid="stDecoration"] {{
        background-image: none !important;
        background-color: var(--accent) !important;
        height: 3px;
    }}

    h1, h2, h3 {{ color: var(--ink); font-weight: 600; }}
    p, label, span, div {{ color: var(--text); }}

    /* masthead */
    .ocr-masthead {{
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        border-bottom: 2px solid var(--ink);
        padding-bottom: 0.6rem;
        margin-bottom: 0.4rem;
    }}
    .ocr-masthead h1 {{ margin: 0; font-size: 1.9rem; letter-spacing: -0.01em; }}
    .ocr-masthead .ocr-tag {{ font-size: 0.85rem; color: var(--muted); }}
    .ocr-sub {{ color: var(--muted); margin-top: 0.1rem; margin-bottom: 1.4rem; font-size: 0.95rem; }}

    /* status pill */
    .ocr-pill {{
        display: inline-flex; align-items: center; gap: 0.4rem;
        padding: 0.28rem 0.7rem; border-radius: 999px;
        font-size: 0.82rem; font-weight: 500; border: 1px solid var(--border);
    }}
    .ocr-pill.ok {{ color: var(--success-text); background: var(--success-bg); border-color: var(--success-border); }}
    .ocr-pill.bad {{ color: var(--danger-text); background: var(--danger-bg); border-color: var(--danger-border); }}
    .ocr-dot {{ width: 7px; height: 7px; border-radius: 50%; background: currentColor; }}

    .ocr-section-label {{ font-size: 0.78rem; color: var(--muted); font-weight: 500; margin-bottom: 0.35rem; }}

    .ocr-step {{
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.72rem; color: var(--accent-dark);
        background: var(--panel-alt);
        border: 1px solid var(--border); display: inline-block;
        padding: 0.1rem 0.45rem; border-radius: 4px; margin-bottom: 0.5rem;
    }}

    /* bordered containers -> cards */
    div[data-testid="stVerticalBlockBorderWrapper"] {{
        background-color: var(--panel);
        border: 1px solid var(--border) !important;
        border-radius: 10px;
        box-shadow: 0 1px 3px var(--shadow);
    }}

    /* buttons */
    .stButton button {{
        border-radius: 7px; font-weight: 500;
        background-color: var(--panel);
        border: 1px solid var(--border);
        color: var(--text);
    }}
    .stButton button[kind="primary"] {{
        background-color: var(--accent);
        border-color: var(--accent);
        color: var(--accent-contrast);
    }}
    .stButton button[kind="primary"]:hover {{
        background-color: var(--accent-dark);
        border-color: var(--accent-dark);
    }}
    .stDownloadButton button {{
        border-radius: 7px; border: 1px solid var(--border);
        font-weight: 500; background-color: var(--panel); color: var(--text);
    }}
    .stDownloadButton button:hover {{ border-color: var(--accent); color: var(--accent-dark); }}

    /* toggle / checkbox accent */
    [data-testid="stToggle"] label div[data-checked="true"],
    div[role="checkbox"][aria-checked="true"] {{ background-color: var(--accent) !important; }}

    /* form widgets: select / multiselect control */
    div[data-baseweb="select"] > div,
    div[data-baseweb="select"] > div > div,
    div[data-testid="stTextInput"] input,
    div[data-testid="stNumberInput"] input,
    textarea {{
        background-color: var(--panel-alt) !important;
        color: var(--text) !important;
        border-color: var(--border) !important;
    }}
    div[data-baseweb="select"] input {{ color: var(--text) !important; }}
    div[data-baseweb="select"] svg {{ fill: var(--muted) !important; }}

    /* dropdown menu that opens below select/multiselect */
    div[data-baseweb="popover"] div[data-baseweb="menu"],
    div[data-baseweb="popover"] ul,
    ul[role="listbox"] {{
        background-color: var(--panel) !important;
        border: 1px solid var(--border) !important;
    }}
    ul[role="listbox"] li,
    li[role="option"] {{ background-color: transparent !important; color: var(--text) !important; }}
    li[role="option"]:hover, li[aria-selected="true"] {{ background-color: var(--panel-alt) !important; }}

    /* multiselect chips/tags — Streamlit's default red, recolored to the accent */
    span[data-baseweb="tag"] {{
        background-color: var(--accent) !important;
        border-radius: 6px !important;
    }}
    span[data-baseweb="tag"] span,
    span[data-baseweb="tag"] div {{ color: var(--accent-contrast) !important; }}
    span[data-baseweb="tag"] svg {{ fill: var(--accent-contrast) !important; }}

    /* radio buttons — recolor the selected dot away from Streamlit's default red */
    div[data-testid="stRadio"] label div:first-child {{ border-color: var(--muted) !important; }}
    div[data-testid="stRadio"] label div[aria-checked="true"],
    div[data-testid="stRadio"] svg {{ fill: var(--accent) !important; color: var(--accent) !important; }}

    /* checkbox */
    div[data-testid="stCheckbox"] svg {{ fill: var(--accent) !important; }}

    /* file uploader "Browse files" button */
    div[data-testid="stFileUploader"] button {{
        background-color: var(--panel) !important;
        color: var(--accent-dark) !important;
        border: 1px solid var(--accent) !important;
    }}
    div[data-testid="stFileUploader"] button:hover {{
        background-color: var(--accent) !important;
        color: var(--accent-contrast) !important;
    }}

    /* slider */
    div[data-testid="stSlider"] [role="slider"] {{ background-color: var(--accent) !important; }}
    div[data-testid="stSlider"] > div > div > div {{ background-color: var(--accent) !important; }}

    /* tabs */
    button[data-baseweb="tab"] {{ color: var(--muted); }}
    button[data-baseweb="tab"][aria-selected="true"] {{ color: var(--accent-dark); }}
    div[data-baseweb="tab-highlight"] {{ background-color: var(--accent) !important; }}

    /* expander */
    details {{
        background-color: var(--panel-alt);
        border: 1px solid var(--border) !important;
        border-radius: 8px;
    }}

    /* metric */
    div[data-testid="stMetricValue"] {{ color: var(--ink); }}

    .ocr-mono {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.85rem; color: var(--muted); }}

    div[data-testid="stFileUploader"] section {{
        border: 1.5px dashed var(--border);
        border-radius: 10px;
        background-color: var(--panel-alt);
    }}

    hr {{ border-color: var(--border); }}
    </style>
    """, unsafe_allow_html=True)

def check_api_health(
    api_url: str,
    retries: int = API_HEALTH_RETRIES,
) -> tuple[bool, str]:
    """
    Check API readiness with short retries.

    Returns:
        (connected, status_message)
    """

    session = get_http_session()
    last_error = ""

    for attempt in range(1, retries + 1):
        try:
            response = session.get(
                f"{api_url}/ready",
                timeout=(
                    API_CONNECT_TIMEOUT,
                    API_CONNECT_TIMEOUT,
                ),
            )

            if response.status_code == 200:
                body = response.json()

                if body.get("status") == "ready":
                    return (
                        True,
                        "Connected",
                    )

                last_error = (
                    "API responded but is not ready."
                )

            else:
                last_error = (
                    "API readiness check returned "
                    f"HTTP {response.status_code}."
                )

        except requests.ConnectionError:
            last_error = (
                "The API connection was refused."
            )

        except requests.Timeout:
            last_error = (
                "The API readiness check timed out."
            )

        except requests.RequestException as exc:
            last_error = (
                f"API connection error: "
                f"{type(exc).__name__}"
            )

        except ValueError:
            last_error = (
                "The API returned an invalid "
                "health response."
            )

        if attempt < retries:
            time.sleep(1)

    return (
        False,
        last_error or "API is not reachable.",
    )
@st.cache_data(
    show_spinner=False,
    ttl=300,
)
def get_languages(
    api_url: str,
) -> list[str]:
    session = get_http_session()

    try:
        response = session.get(
            f"{api_url}/languages",
            timeout=(
                API_CONNECT_TIMEOUT,
                API_CONNECT_TIMEOUT,
            ),
        )

        response.raise_for_status()

        languages = response.json().get(
            "languages",
            [],
        )

        return languages or ["eng"]

    except requests.RequestException:
        return ["eng"]

    except ValueError:
        return ["eng"]
@st.cache_resource
def get_http_session() -> requests.Session:
    session = requests.Session()

    session.headers.update({
        "User-Agent": "OCR-Streamlit-Client/1.2",
    })

    return session

def main():
    st.set_page_config(page_title="OCR Toolkit", page_icon="🖹", layout="wide")

    st.session_state.setdefault("dark_mode", False)
    mode = "dark" if st.session_state["dark_mode"] else "light"
    inject_style(mode)
    st.session_state.setdefault(
    "api_connected",
    False,
    )

    st.session_state.setdefault(
        "api_status_message",
        "Not checked",
    )

    st.session_state.setdefault(
        "api_last_checked",
        None,
    )
    icon = "🌙" if not st.session_state["dark_mode"] else "☀️"
    label = "Dark mode" if not st.session_state["dark_mode"] else "Light mode"
    h_l, h_r = st.columns([6, 1])
    with h_l:
        st.markdown("""
        <div class="ocr-masthead">
            <h1>🖹 OCR Toolkit</h1>
            <span class="ocr-tag">web client</span>
        </div>
        <div class="ocr-sub">
            Frontend only — OCR runs on the API server, where Tesseract lives.
        </div>
        """, unsafe_allow_html=True)
    with h_r:
        st.toggle(f"{icon} {label}", key="dark_mode")

    # ---------------- sidebar: server + options ----------------
    with st.sidebar:
        st.markdown('<div class="ocr-section-label">Connection</div>', unsafe_allow_html=True)
        api_url = DEFAULT_API

        connected, connection_message = (
            check_api_health(
                api_url,
                retries=1,
            )
        )

        st.session_state["api_connected"] = (
            connected
        )

        st.session_state[
            "api_status_message"
        ] = connection_message

        st.session_state[
            "api_last_checked"
        ] = time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        ok = connected
        
        try:
            ok = requests.get(f"{api_url}/health", timeout=4).json().get("status") == "ok"
        except Exception:
            ok = False
        pill_class = "ok" if ok else "bad"
        pill_text = "Connected" if ok else "Not reachable"
        st.markdown(
            f'<span class="ocr-pill {pill_class}"><span class="ocr-dot"></span>{pill_text}</span>',
            unsafe_allow_html=True)
        if not ok:
            st.caption("Start api_server.py, or fix the API URL via OCR_API_URL.")
        if st.button(
            "Reconnect API",
            use_container_width=True,
            help="Recheck whether the OCR API is available.",
        ):
            with st.spinner("Reconnecting to the OCR service..."):
                get_languages.clear()

                connected, message = check_api_health(
                    api_url,
                    retries=API_HEALTH_RETRIES,
                )

                st.session_state["api_connected"] = connected
                st.session_state["api_status_message"] = message

            st.rerun()
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown('<div class="ocr-section-label">Recognition</div>', unsafe_allow_html=True)
            langs = get_languages(api_url)
            lang = st.selectbox("Language", langs, index=0)
            prep = st.radio("Preprocessing", ["Auto (recommended)", "Manual"], index=0)
            mode_val = "auto" if prep.startswith("Auto") else \
                st.selectbox("Manual mode", MANUAL_MODES, index=0)

        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown('<div class="ocr-section-label">Output</div>', unsafe_allow_html=True)
            export_formats = st.multiselect(
                "Export format(s)", EXPORT_FORMATS, default=["md", "xlsx", "json"],
                help="Pick one or more. A download button appears for each.")

        st.markdown("<br>", unsafe_allow_html=True)
        with st.expander("Advanced", expanded=False):
            psm = st.slider("Tesseract PSM", 1, 10, 6, 1)
            detect_tables = st.checkbox("Detect tables", value=True)
            locale = st.radio("Table number style", ["auto", "dot", "comma"],
                              index=0, horizontal=True)
            fast = st.checkbox("Fast mode (cap huge images)", value=True,
                               help="Big scans are downscaled for speed with no "
                                    "accuracy loss. Uncheck to disable.")
            

    # ---------------- main area ----------------
    st.markdown('<span class="ocr-step">STEP 1 · UPLOAD</span>', unsafe_allow_html=True)
    with st.container(border=True):
        up = st.file_uploader("Upload image or PDF",
                              type=["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "pdf"],
                              label_visibility="collapsed")

        table_mode = st.toggle(
            "📊 Table mode — reproduce the table exactly as in the image/PDF",
            value=False,
            help="Turn on when the file is mainly a table. Keeps the on-page "
                 "row/column layout and exports it.")

        if up is not None:
            st.divider()
            c1, c2 = st.columns([1, 1])
            with c1:
                if up.type and up.type.startswith("image"):
                    st.image(up, caption="Uploaded image", use_container_width=True)
                else:
                    st.info(f"📄 {up.name} ready ({up.size/1_000_000:.1f} MB).")
            with c2:
                st.markdown('<div class="ocr-section-label">File details</div>', unsafe_allow_html=True)
                st.markdown(
                    f'<div class="ocr-mono">name: {up.name}<br>'
                    f'type: {up.type or "unknown"}<br>'
                    f'size: {up.size/1_000_000:.2f} MB</div>',
                    unsafe_allow_html=True)

    st.markdown('<span class="ocr-step">STEP 2 · RUN</span>', unsafe_allow_html=True)
    run = st.button("Run OCR", type="primary", use_container_width=True)

    if run:
        if up is None:
            st.warning("⚠️ Please upload an image or PDF first."); return
        if not ok:
            with st.spinner(
                "Checking the OCR service..."
            ):
                reconnected, message = (
                    check_api_health(
                        api_url,
                        retries=API_HEALTH_RETRIES,
                    )
                )

            if not reconnected:
                st.session_state[
                    "api_connected"
                ] = False

                st.error(
                    "The OCR service is not available. "
                    f"{message} Use Reconnect API in "
                    "the sidebar after the service "
                    "has recovered."
                )

                return

            st.session_state[
                "api_connected"
            ] = True

            ok = True
        if not export_formats:
            st.warning("⚠️ Pick at least one output format."); return

        files = {"file": (up.name, up.getbuffer(), up.type or "application/octet-stream")}
        data = {
            "lang": lang, "mode": mode_val, "psm": int(psm),
            "detect_tables": str(bool(detect_tables)).lower(),
            "table_mode": str(bool(table_mode)).lower(),
            "locale": locale,
            "formats": ",".join(export_formats),
            "fast": str(bool(fast)).lower(),
            
        }

        t0 = time.time()
        with st.spinner("Sending to OCR server… (large files take longer)"):
            try:
                session = get_http_session()

                resp = session.post(
                    f"{api_url}/ocr",
                    files=files,
                    data=data,
                    timeout=(
                        API_CONNECT_TIMEOUT,
                        API_READ_TIMEOUT,
                    ),
                )
            except requests.ConnectionError:
                st.session_state[
                    "api_connected"
                ] = False

                st.error(
                    "The connection to the OCR service "
                    "was lost. The API container may be "
                    "restarting. Use Reconnect API in "
                    "the sidebar."
                )

                return

            except requests.Timeout:
                st.error(
                    "The OCR request exceeded the client "
                    "timeout. The API may still be busy. "
                    "Check the API status before submitting "
                    "the document again."
                )

                return

            except requests.RequestException as exc:
                st.error(
                    "The OCR request failed due to a "
                    "network error: "
                    f"{type(exc).__name__}."
                )

                return
        elapsed = time.time() - t0

        if resp.status_code != 200:
            st.error(f"❌ Server error {resp.status_code}: {resp.text[:300]}"); return
        out = resp.json()
        if not out.get("ok"):
            st.warning(out.get("status", "No result.")); return

        st.markdown('<span class="ocr-step">RESULTS</span>', unsafe_allow_html=True)
        with st.container(border=True):
            m1, m2 = st.columns([3, 1])
            with m1:
                st.success(out.get("status", "Done."))
            with m2:
                st.metric("Elapsed", f"{elapsed:.1f}s")

            t1, t2 = st.tabs(["📝 Text", "📊 Table preview"])
            with t1:
                st.text_area("Extracted text", out.get("text", ""), height=360,
                             label_visibility="collapsed")
            with t2:
                tbl = out.get("table")
                if tbl:
                    import pandas as pd
                    header, *rows = tbl
                    st.dataframe(pd.DataFrame(rows, columns=header),
                                 use_container_width=True)
                else:
                    st.info("No table detected in this file.")

        # ---- OUTPUT: one download button per produced format ----
        st.markdown('<span class="ocr-step">STEP 3 · DOWNLOAD</span>', unsafe_allow_html=True)
        with st.container(border=True):
            files_out = out.get("files", {})
            if not files_out:
                st.info("No output files were produced.")
            else:
                cols = st.columns(min(len(files_out), 4) or 1)
                for i, (fmt, info) in enumerate(files_out.items()):
                    if not info or not info.get("b64"):
                        continue
                    with cols[i % len(cols)]:
                        st.download_button(
                            f"{_ICON.get(fmt, '📁')} {fmt.upper()}",
                            base64.b64decode(info["b64"]),
                            file_name=info["name"],
                            mime=_MIME.get(fmt, "application/octet-stream"),
                            use_container_width=True)

    st.divider()
    st.caption("This app is not perfect — please also review and check the output manually after using it.")


if __name__ == "__main__":
    main()