from __future__ import annotations
import os,re,json,mimetypes,html
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlparse,unquote
import requests
from notion_reader import query_database,get_blocks,load_tree
from content_converter import plain,render_blocks,wrap_html

ROOT=Path(__file__).resolve().parents[1]; DRAFTS=ROOT/"Naver_Drafts"; TRACKER=DRAFTS/"processed_pages.json"
TITLE=os.getenv("NOTION_PROPERTY_TITLE","제목"); DATE=os.getenv("NOTION_PROPERTY_DATE","날짜")
MEMO=os.getenv("NOTION_PROPERTY_MEMO","간단메모"); TAGS=os.getenv("NOTION_PROPERTY_TAGS","태그")
LIMIT=int(os.getenv("MAX_DOWNLOAD_MB","50"))*1024*1024
UPDATE=os.getenv("UPDATE_EXISTING_DRAFTS","false").lower()=="true"

def title_of(p):
    props=p.get("properties",{}); v=plain((props.get(TITLE) or {}).get("title") or [])
    if not v:
        for x in props.values():
            if x.get("type")=="title": v=plain(x.get("title") or []); break
    return v.strip() or "제목없음"
def date_of(p):
    v=(p.get("properties",{}).get(DATE,{}).get("date") or {}).get("start")
    return str(v)[:10] if v else datetime.now().strftime("%Y-%m-%d")
def memo_of(p):
    x=p.get("properties",{}).get(MEMO,{})
    return plain(x.get("rich_text") or x.get("title") or []).strip()
def tags_of(p):
    x=p.get("properties",{}).get(TAGS,{})
    return [t["name"].strip() for t in x.get("multi_select",[]) if t.get("name","").strip()]
def slug(s):
    s=re.sub(r'[\\\\/*?:"<>|]',"",s); return re.sub(r"\\s+","_",s).strip("._")[:90] or "untitled"
def media_blocks(blocks):
    out=[]
    for b in blocks:
        if b.get("type") in ("image","file","pdf"): out.append(b)
        out.extend(media_blocks(b.get("_children") or []))
    return out
def save_media(b,folder,index):
    d=b.get(b.get("type"),{}) or {}; m=d.get("file") or d.get("external") or {}; url=m.get("url")
    if not url or not url.startswith("https://"): return None
    folder.mkdir(parents=True,exist_ok=True)
    name=d.get("name") or f"media_{index}"
    ext=Path(unquote(urlparse(url).path)).suffix.lower()
    if ext not in (".png",".jpg",".jpeg",".gif",".webp",".bmp",".pdf"):
        ext=mimetypes.guess_extension(m.get("mime_type","")) or ".bin"
    dest=folder/f"{index:02d}_{slug(Path(name).stem)}{ext}"
    try:
        with requests.get(url,stream=True,timeout=30) as r:
            r.raise_for_status()
            if int(r.headers.get("Content-Length","0") or 0)>LIMIT: return None
            total=0
            with dest.open("wb") as f:
                for chunk in r.iter_content(65536):
                    if not chunk: continue
                    total+=len(chunk)
                    if total>LIMIT: raise ValueError("파일 크기 제한 초과")
                    f.write(chunk)
        if not dest.stat().st_size: dest.unlink(missing_ok=True); return None
        return dest
    except Exception as e:
        dest.unlink(missing_ok=True); print(f"[WARN] 미디어 다운로드 실패: {e}"); return None

def main():
    DRAFTS.mkdir(parents=True,exist_ok=True)
    state=json.loads(TRACKER.read_text(encoding="utf-8")) if TRACKER.exists() else {"version":1,"pages":{}}
    known=state.setdefault("pages",{}); count=0
    pages=sorted(query_database(),key=lambda p:(date_of(p),title_of(p)))
    for page in pages:
        pid=page["id"]; edited=page.get("last_edited_time",""); prev=known.get(pid)
        if prev and (not UPDATE or prev.get("last_edited_time")==edited): continue
        if prev and prev.get("status")=="published": print("[SKIP] 발행 완료 글 보호:",title_of(page)); continue
        title,date,memo,tags=title_of(page),date_of(page),memo_of(page),tags_of(page)
        folder_name=slug(date+"_"+title); folder=DRAFTS/folder_name
        if folder.exists() and (not prev or prev.get("folder")!=folder_name):
            folder_name += "_"+pid.replace("-","")[:6]; folder=DRAFTS/folder_name
        blocks=load_tree(get_blocks(pid)); imgs={}; image_rows=[]
        for i,b in enumerate(media_blocks(blocks),1):
            saved=save_media(b,folder/"images",i)
            if saved and b.get("type")=="image":
                imgs[b["id"]]="images/"+saved.name; image_rows.append(saved.name)
            elif saved: image_rows.append(saved.name)
        lines=[]; body=render_blocks(blocks,imgs,lines)
        folder.mkdir(parents=True,exist_ok=True)
        (folder/"01_네이버_원고.html").write_text(wrap_html(title,memo,body,tags),encoding="utf-8")
        text_body="\\n".join([title,"="*min(len(title),60),memo,"",*lines,""," ".join("#"+t for t in tags)])
        (folder/"02_네이버_원고.txt").write_text(text_body,encoding="utf-8")
        (folder/"03_이미지_목록.txt").write_text("\\n".join(image_rows) or "다운로드된 이미지가 없습니다.",encoding="utf-8")
        checklist=["네이버 블로그 발행 전 체크리스트","[ ] 제목/본문 오탈자 확인","[ ] 이미지 위치와 표시 확인","[ ] 이미지 출처 및 사용 권한 확인","[ ] 링크 확인","[ ] 태그 입력","[ ] 공개 범위 확인","[ ] 미리보기 후 직접 발행"]
        (folder/"04_발행_체크리스트.txt").write_text("\\n".join(checklist),encoding="utf-8")
        meta={"notion_page_id":pid,"title":title,"date":date,"memo":memo,"tags":tags,"last_edited_time":edited,"notion_url":page.get("url"),"exported_at":datetime.now(timezone.utc).isoformat(),"status":"draft"}
        (folder/"metadata.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
        known[pid]={"folder":folder_name,"last_edited_time":edited,"status":"draft","title":title}; count+=1
        print("[DRAFT]",folder_name)
    TRACKER.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"완료: {count}개 초안 생성/갱신")
if __name__=="__main__": main()
