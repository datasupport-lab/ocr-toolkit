"""table_cpu.py — OpenCV ruled-table extraction -> HTML for table_export."""
import cv2, numpy as np
def _binarize(g): return cv2.threshold(g,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[1]
def _lines(b):
    h,w=b.shape; hk,vk=max(10,w//40),max(10,h//40)
    hz=cv2.morphologyEx(b,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(hk,1)))
    vt=cv2.morphologyEx(b,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(1,vk)))
    return hz,vt
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
def has_grid(img):
    g=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY) if img.ndim==3 else img
    b=_binarize(g); hz,vt=_lines(b)
    return len(_pos(hz,1))>=3 and len(_pos(vt,0))>=2
def image_to_html_table(img,lang="eng",psm=6):
    import pytesseract
    g=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY) if img.ndim==3 else img
    b=_binarize(g); hz,vt=_lines(b)
    rows=_edges(_pos(hz,1),g.shape[0]); cols=_edges(_pos(vt,0),g.shape[1])
    if len(rows)<2 or len(cols)<2: return ""
    cfg=f"--oem 3 --psm {psm}"; html=["<table>"]
    for r in range(len(rows)-1):
        y0,y1=rows[r]+2,rows[r+1]-2
        if y1-y0<8: continue
        html.append("<tr>")
        for c in range(len(cols)-1):
            x0,x1=cols[c]+2,cols[c+1]-2
            t="" if x1-x0<8 else pytesseract.image_to_string(
                cv2.resize(g[y0:y1,x0:x1],None,fx=2,fy=2,interpolation=cv2.INTER_CUBIC) if (y1-y0)<40 else g[y0:y1,x0:x1],
                lang=lang,config=cfg).strip().replace("\n"," ")
            tag="th" if r==0 else "td"; html.append(f"<{tag}>{_esc(t)}</{tag}>")
        html.append("</tr>")
    html.append("</table>"); return "\n".join(html)
def _esc(s): return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
