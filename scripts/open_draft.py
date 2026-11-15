import os,sys,webbrowser
from pathlib import Path
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[1]; load_dotenv(ROOT/".env")
DRAFTS=ROOT/"Naver_Drafts"
def main():
    folders=sorted([p for p in DRAFTS.iterdir() if p.is_dir() and (p/"01_네이버_원고.html").exists()],reverse=True)
    if not folders: print("초안이 없습니다. 저장소를 pull 한 뒤 다시 실행하세요."); return
    for i,p in enumerate(folders,1): print(f"{i}. {p.name}")
    raw=input("열 초안 번호 (Enter=1): ").strip(); idx=int(raw)-1 if raw else 0
    if idx<0 or idx>=len(folders): print("잘못된 번호"); return
    folder=folders[idx]; html_file=folder/"01_네이버_원고.html"
    if sys.platform=="win32": os.startfile(folder)
    else: webbrowser.open(folder.as_uri())
    webbrowser.open(html_file.as_uri())
    blog_id=os.getenv("NAVER_BLOG_ID","").strip()
    if blog_id: webbrowser.open(f"https://blog.naver.com/{blog_id}?Redirect=Write")
    else: webbrowser.open("https://blog.naver.com/")
    print("HTML 미리보기와 폴더를 열었습니다. 이미지 파일은 네이버 에디터에서 직접 업로드하세요.")
    print("최종 검토와 발행은 직접 진행해야 합니다.")
if __name__=="__main__": main()
