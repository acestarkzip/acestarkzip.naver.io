import json
from pathlib import Path
from notion_reader import query_database
from exporter import title_of
ROOT=Path(__file__).resolve().parents[1]; FILE=ROOT/"Naver_Drafts"/"processed_pages.json"
def main():
    FILE.parent.mkdir(parents=True,exist_ok=True); state={"version":1,"pages":{}}
    for p in query_database():
        state["pages"][p["id"]]={"folder":None,"last_edited_time":p.get("last_edited_time",""),"status":"baseline_skipped","title":title_of(p)}
    FILE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"기존 페이지 {len(state['pages'])}개를 기준점으로 기록했습니다.")
if __name__=="__main__": main()
