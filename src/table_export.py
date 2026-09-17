"""table_export.py — tables -> validated .xlsx. Self-test: python table_export.py"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


def html_to_dataframes(html: str) -> List[pd.DataFrame]:
    if not html or "<table" not in html.lower():
        return []
    from bs4 import BeautifulSoup
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    dfs = []
    for table in soup.find_all("table"):
        grid = _grid(table)
        if not grid:
            continue
        header, *body = grid
        if body and any(h.strip() for h in header):
            dfs.append(pd.DataFrame(body, columns=_dedupe(header), dtype=object))
        else:
            dfs.append(pd.DataFrame(grid, dtype=object))
    return dfs


def _grid(table) -> List[List[str]]:
    grid, pending = [], {}
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        row, col, ci = [], 0, 0
        while ci < len(cells) or col in pending:
            if col in pending:
                text, rem = pending[col]; row.append(text)
                if rem - 1 > 0: pending[col] = (text, rem - 1)
                else: del pending[col]
                col += 1; continue
            cell = cells[ci]; ci += 1
            text = cell.get_text(strip=True)
            cs = int(cell.get("colspan", 1) or 1); rs = int(cell.get("rowspan", 1) or 1)
            for _ in range(cs):
                row.append(text)
                if rs > 1: pending[col] = (text, rs - 1)
                col += 1
        grid.append(row)
    w = max((len(r) for r in grid), default=0)
    return [[(c if c is not None else "") for c in r] + [""] * (w - len(r)) for r in grid]


def _dedupe(header):
    seen, out = {}, []
    for i, h in enumerate(header):
        name = h.strip() or f"col_{i}"
        if name in seen: seen[name] += 1; name = f"{name}_{seen[name]}"
        else: seen[name] = 0
        out.append(name)
    return out


_NUM = re.compile(r"[^\d\.,\-]")
def parse_number(raw, locale="auto"):
    if raw is None: return None
    s = str(raw).strip()
    if s == "" or s.lower() in {"nan", "none", "-", "n/a"}: return None
    s = _NUM.sub("", s)
    if s in {"", "-", ".", ","}: return None
    hd, hc = "." in s, "," in s
    if locale == "auto":
        if hd and hc: locale = "dot" if s.rfind(".") > s.rfind(",") else "comma"
        elif hc and not hd: locale = "comma" if re.search(r",\d{1,2}$", s) else "dot"
        else: locale = "dot"
    try:
        s = s.replace(".", "").replace(",", ".") if locale == "comma" else s.replace(",", "")
        return float(s)
    except ValueError:
        return None


def coerce_numeric_columns(df, locale="auto"):
    out = df.copy(); ratios = {}
    for col in out.columns:
        parsed = out[col].map(lambda v: parse_number(v, locale))
        ratio = parsed.notna().sum() / (len(parsed) or 1)
        ratios[str(col)] = round(float(ratio), 3)
        if ratio >= 0.6: out[col] = parsed
    return out, ratios


@dataclass
class TableReport:
    index: int; n_rows: int; n_cols: int
    numeric_ratios: Dict[str, float] = field(default_factory=dict)
    issues: List[str] = field(default_factory=list)
    total_checks: List[str] = field(default_factory=list)
    ok: bool = True


def validate_table(df, index, locale="auto", reconcile_totals=True):
    rpt = TableReport(index=index, n_rows=int(df.shape[0]), n_cols=int(df.shape[1]))
    if df.shape[0] == 0 or df.shape[1] == 0: rpt.issues.append("empty table"); rpt.ok = False
    if df.isna().all(axis=1).any(): rpt.issues.append("fully-empty row(s)")
    if df.isna().all(axis=0).any(): rpt.issues.append("fully-empty column(s)")
    coerced, ratios = coerce_numeric_columns(df, locale)
    rpt.numeric_ratios = ratios
    for col, r in ratios.items():
        if 0 < r < 0.6: rpt.issues.append(f"column '{col}' mixed (ratio {r})")
    if reconcile_totals:
        rpt.total_checks = _reconcile(coerced, df.iloc[:, 0])
        if any("MISMATCH" in c for c in rpt.total_checks): rpt.ok = False
    if rpt.issues and rpt.ok: rpt.ok = not any("empty table" in i for i in rpt.issues)
    return coerced, rpt


def _reconcile(df, orig_labels=None):
    checks = []
    if df.shape[0] < 2: return checks
    src = orig_labels if orig_labels is not None else df.iloc[:, 0]
    labels = src.astype(str).str.strip().str.lower()
    mask = labels.str.contains(r"\b(?:total|sum|subtotal|grand total)\b", regex=True, na=False)
    mask = mask.set_axis(df.index)
    if not mask.any(): return checks
    tri = df.index[mask][-1]; body = df.loc[~mask]
    for col in df.columns[1:]:
        if pd.api.types.is_numeric_dtype(df[col]):
            rep = df.loc[tri, col]; comp = body[col].sum(skipna=True)
            if pd.isna(rep): continue
            if np.isclose(float(rep), float(comp), rtol=1e-3, atol=1e-6):
                checks.append(f"OK: column '{col}' total {rep} == sum {comp:g}")
            else:
                checks.append(f"MISMATCH: column '{col}' total {rep} != sum {comp:g}")
    return checks


def dataframes_to_xlsx(tables, reports, out_path, provenance=None):
    with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
        for i, df in enumerate(tables):
            df.to_excel(xw, sheet_name=f"Table_{i+1}"[:31], index=False)
        rows = [{"table": f"Table_{r.index+1}", "rows": r.n_rows, "cols": r.n_cols,
                 "status": "OK" if r.ok else "REVIEW", "issues": "; ".join(r.issues) or "none",
                 "total_checks": "; ".join(r.total_checks) or "n/a",
                 "numeric_ratios": "; ".join(f"{k}={v}" for k, v in r.numeric_ratios.items())}
                for r in reports]
        pd.DataFrame(rows).to_excel(xw, sheet_name="_Validation", index=False)
        if provenance:
            pd.DataFrame(provenance).to_excel(xw, sheet_name="_Provenance", index=False)
    return out_path


def html_tables_to_xlsx(html, out_path, locale="auto", provenance=None):
    coerced, reports = [], []
    for i, df in enumerate(html_to_dataframes(html)):
        cdf, rpt = validate_table(df, i, locale=locale)
        coerced.append(cdf); reports.append(rpt)
    dataframes_to_xlsx(coerced, reports, out_path, provenance)
    return out_path, reports


def _selftest():
    import tempfile, os
    fails = 0
    print("=" * 50, "\ntable_export self-test\n", "=" * 50)
    for raw, loc, exp in [("1,234,567.89","dot",1234567.89),("1.234.567,89","comma",1234567.89),
                          ("98.760","comma",98760.0),("abc","auto",None)]:
        got = parse_number(raw, loc); ok = got == exp; fails += (not ok)
        print(f"  {'PASS' if ok else 'FAIL'} {raw!r} -> {got}")
    tmp = tempfile.mkdtemp()
    h = ("<table><tr><th>C</th><th>Q</th><th>A</th></tr><tr><td>x</td><td>1,000</td>"
         "<td>2,500.00</td></tr><tr><td>Total</td><td>1,000</td><td>2,500.00</td></tr></table>")
    _, reps = html_tables_to_xlsx(h, os.path.join(tmp, "a.xlsx"), "auto")
    ok = reps[0].ok and any("OK" in c for c in reps[0].total_checks); fails += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'} table+reconciliation")
    print("RESULT:", "ALL TESTS PASSED" if fails == 0 else f"{fails} FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    import sys; sys.exit(_selftest())
