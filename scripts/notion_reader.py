import os, time, requests
API="https://api.notion.com/v1"
TOKEN=os.getenv("NOTION_TOKEN","").strip()
DB=os.getenv("NOTION_DATABASE_ID","").strip()
HEADERS={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json","Notion-Version":"2022-06-28"}
_last=0.0
def request(method,path,**kwargs):
    global _last
    if not TOKEN: raise RuntimeError("NOTION_TOKEN이 설정되지 않았습니다.")
    for attempt in range(5):
        delay=.35-(time.monotonic()-_last)
        if delay>0: time.sleep(delay)
        r=requests.request(method,API+path,headers=HEADERS,timeout=30,**kwargs); _last=time.monotonic()
        if r.status_code==429 or r.status_code>=500:
            if attempt==4: r.raise_for_status()
            time.sleep(float(r.headers.get("Retry-After",min(2**attempt,15)))); continue
        r.raise_for_status(); return r.json()
    raise RuntimeError("Notion API 요청 실패")
def query_database():
    if not DB: raise RuntimeError("NOTION_DATABASE_ID가 설정되지 않았습니다.")
    out=[]; cursor=None
    while True:
        payload={"page_size":100}
        if cursor: payload["start_cursor"]=cursor
        data=request("POST",f"/databases/{DB}/query",json=payload); out.extend(data.get("results",[]))
        if not data.get("has_more") or not data.get("next_cursor"): break
        cursor=data["next_cursor"]
    return out
def get_blocks(block_id):
    out=[]; cursor=None
    while True:
        params={"page_size":100}
        if cursor: params["start_cursor"]=cursor
        data=request("GET",f"/blocks/{block_id}/children",params=params); out.extend(data.get("results",[]))
        if not data.get("has_more") or not data.get("next_cursor"): break
        cursor=data["next_cursor"]
    return out
def load_tree(blocks,depth=0):
    if depth>=20: return blocks
    for b in blocks:
        b["_children"]=load_tree(get_blocks(b["id"]),depth+1) if b.get("has_children") else []
    return blocks
