"""table_format.py — Table mode: reproduce a table as it appears in image/PDF."""
from dataclasses import dataclass
from typing import List, Optional
import cv2, numpy as np

@dataclass
class TableExtract:
    found: bool; method: str=""; grid: Optional[List[List[str]]]=None
    html: str=""; aligned_text: str=""; markdown: str=""
    def to_dataframe(self):
        import pandas as pd
        if not self.grid: return pd.DataFrame()
        header,*body=self.grid
        if body and any(h.strip() for h in header):
            return pd.DataFrame(body,columns=_dedupe(header),dtype=object)
        return pd.DataFrame(self.grid,dtype=object)

def extract_table(img,lang="eng",psm=6):
    grid=_ruled(img,lang,psm); method="ruled"
    if grid is None or len(grid)<2:
        grid=_borderless(img,lang); method="borderless"
    if not grid or not any(any(c for c in r) for r in grid):
        return TableExtract(found=False)
    grid=_norm(grid)
    return TableExtract(True,method,grid,_html(grid),_aligned(grid),_md(grid))

def _binz(g): return cv2.threshold(g,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[1]
def _lines(b):
    h,w=b.shape; hk,vk=max(10,w//40),max(10,h//40)
    return (cv2.morphologyEx(b,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(hk,1))),
            cv2.morphologyEx(b,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(1,vk))))
def _pos(mask,axis,mf=0.3):
    proj=(mask>0).sum(axis=axis); thr=proj.max()*mf if proj.max() else 0
    idx=np.where(proj>thr)[0]
    if len(idx)==0: return []
    g,s,p=[],idx[0],idx[0]
    for i in idx[1:]:
        if i-p>3: g.append((s+p)//2); s=i
        p=i
    g.append((s+p)//2); return g
def _edges(pos,size,m=8):
    pos=sorted(pos)
    if not pos or pos[0]>m: pos=[0]+pos
    if not pos or pos[-1]<size-1-m: pos=pos+[size-1]
    return pos
def _ruled(img,lang,psm):
    import pytesseract
    g=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY) if img.ndim==3 else img
    b=_binz(g); hz,vt=_lines(b); rows=_pos(hz,1); cols=_pos(vt,0)
    if len(rows)<2 or len(cols)<1: return None
    rows=_edges(rows,g.shape[0]); cols=_edges(cols,g.shape[1])
    if len(rows)<2 or len(cols)<2: return None
    cfg=f"--oem 3 --psm {psm}"; out=[]
    for r in range(len(rows)-1):
        y0,y1=rows[r]+2,rows[r+1]-2
        if y1-y0<8: continue
        row=[]
        for c in range(len(cols)-1):
            x0,x1=cols[c]+2,cols[c+1]-2
            if x1-x0<8: row.append(""); continue
            cell=g[y0:y1,x0:x1]
            if cell.shape[0]<40: cell=cv2.resize(cell,None,fx=2,fy=2,interpolation=cv2.INTER_CUBIC)
            row.append(pytesseract.image_to_string(cell,lang=lang,config=cfg).strip().replace("\n"," "))
        out.append(row)
    return out
def _borderless(img,lang):
    import pytesseract
    from pytesseract import Output
    g=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY) if img.ndim==3 else img
    d=pytesseract.image_to_data(g,lang=lang,config="--oem 3 --psm 6",output_type=Output.DICT)
    words=[{"t":d["text"][i].strip(),"x":d["left"][i],"y":d["top"][i],"w":d["width"][i],"h":d["height"][i]}
           for i in range(len(d["text"])) if d["text"][i].strip()]
    if len(words)<2: return None
    words.sort(key=lambda w:w["y"]); mh=int(np.median([w["h"] for w in words])) or 10
    rows=[]
    for w in words:
        placed=False
        for row in rows:
            ry=np.mean([r["y"]+r["h"]/2 for r in row])
            if abs((w["y"]+w["h"]/2)-ry)<mh*0.7: row.append(w); placed=True; break
        if not placed: rows.append([w])
    rows.sort(key=lambda row:np.mean([r["y"] for r in row]))
    xs=sorted(w["x"] for w in words); gap=max(mh,int(g.shape[1]*0.03))
    cand=[xs[0]]
    for a,b in zip(xs,xs[1:]):
        if b-a>gap: cand.append(b)
    ms=max(2,int(len(rows)*0.5)); starts=[]
    for cs in cand:
        sup=sum(1 for row in rows if any(abs(w["x"]-cs)<=gap for w in row))
        if sup>=ms or cs==cand[0]: starts.append(cs)
    if not starts: starts=cand
    def col_of(x):
        best=None
        for k,cs in enumerate(starts):
            if x>=cs-gap and (best is None or cs>=starts[best]): best=k
        return best if best is not None else 0
    grid=[]
    for row in rows:
        cells=[""]*len(starts)
        for w in sorted(row,key=lambda w:w["x"]):
            ci=col_of(w["x"]); cells[ci]=(cells[ci]+" "+w["t"]).strip()
        grid.append(cells)
    return grid
def _norm(grid):
    w=max((len(r) for r in grid),default=0)
    n=[[(c if c is not None else "") for c in r]+[""]*(w-len(r)) for r in grid]
    while n and w and all(row[-1].strip()=="" for row in n): n=[row[:-1] for row in n]; w-=1
    return [r for r in n if any(c.strip() for c in r)]
def _esc(s): return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
def _html(grid):
    h=["<table>"]
    for r,row in enumerate(grid):
        tag="th" if r==0 else "td"
        h.append("<tr>"+"".join(f"<{tag}>{_esc(c)}</{tag}>" for c in row)+"</tr>")
    h.append("</table>"); return "\n".join(h)
def _aligned(grid):
    if not grid: return ""
    nc=max(len(r) for r in grid); wd=[0]*nc
    for row in grid:
        for i in range(nc): wd[i]=max(wd[i],len(row[i] if i<len(row) else ""))
    def f(row): return " | ".join((row[i] if i<len(row) else "").ljust(wd[i]) for i in range(nc)).rstrip()
    lines=[f(grid[0]),"-+-".join("-"*wd[i] for i in range(nc))]+[f(r) for r in grid[1:]]
    return "\n".join(lines)
def _md(grid):
    if not grid: return ""
    nc=max(len(r) for r in grid)
    def c(row): return [(row[i] if i<len(row) else "").replace("|","\\|") for i in range(nc)]
    return "\n".join(["| "+" | ".join(c(grid[0]))+" |","| "+" | ".join(["---"]*nc)+" |"]+
                     ["| "+" | ".join(c(r))+" |" for r in grid[1:]])
def _dedupe(header):
    seen,out={},[]
    for i,h in enumerate(header):
        name=h.strip() or f"col_{i}"
        if name in seen: seen[name]+=1; name=f"{name}_{seen[name]}"
        else: seen[name]=0
        out.append(name)
    return out
def extract_table_from_file(path,lang="eng",psm=6,dpi=300,xlsx_out="",locale="auto"):
    import os
    if os.path.splitext(path)[1].lower()==".pdf":
        import pymupdf as fitz
        doc=fitz.open(path); pix=doc[0].get_pixmap(matrix=fitz.Matrix(dpi/72.,dpi/72.),alpha=False)
        arr=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,pix.n)
        img=cv2.cvtColor(arr,cv2.COLOR_RGB2BGR) if pix.n==3 else cv2.cvtColor(arr,cv2.COLOR_RGBA2BGR); doc.close()
    else:
        img=cv2.imread(path)
    res=extract_table(img,lang=lang,psm=psm)
    if res.found and xlsx_out:
        import table_export as TX; TX.html_tables_to_xlsx(res.html,xlsx_out,locale=locale)
    return res
