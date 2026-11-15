import html
def plain(items):
    return "".join(x.get("plain_text") or (x.get("text") or {}).get("content","") for x in (items or []))
def rich_text(items):
    out=[]
    for x in items or []:
        v=html.escape(x.get("plain_text") or (x.get("text") or {}).get("content",""),quote=False)
        a=x.get("annotations") or {}
        if a.get("code"): v=f"<code>{v}</code>"
        if a.get("bold"): v=f"<strong>{v}</strong>"
        if a.get("italic"): v=f"<em>{v}</em>"
        if a.get("strikethrough"): v=f"<del>{v}</del>"
        href=x.get("href") or ((x.get("text") or {}).get("link") or {}).get("url")
        if href: v=f'<a href="{html.escape(href,quote=True)}">{v}</a>'
        out.append(v)
    return "".join(out)
def render_blocks(blocks,image_map,lines,depth=0):
    if depth>20: return ""
    out=[]
    for b in blocks:
        kind=b.get("type",""); d=b.get(kind) or {}; children=b.get("_children") or []
        rt=d.get("rich_text") or d.get("title") or []; val=rich_text(rt); raw=plain(rt).strip()
        if kind=="paragraph":
            if val: out.append(f"<p>{val}</p>"); lines.append(raw)
        elif kind in ("heading_1","heading_2","heading_3"):
            level=int(kind[-1])+1; out.append(f"<h{level}>{val}</h{level}>"); lines.extend(["",raw,""])
        elif kind in ("bulleted_list_item","numbered_list_item"):
            tag="ul" if kind.startswith("bulleted") else "ol"
            out.append(f"<{tag}><li>{val}</li></{tag}>"); lines.append(("• " if tag=="ul" else "1. ")+raw)
        elif kind=="to_do":
            mark="☑" if d.get("checked") else "☐"; out.append(f"<p>{mark} {val}</p>"); lines.append(f"{mark} {raw}")
        elif kind=="quote":
            out.append(f"<blockquote>{val}</blockquote>"); lines.append("> "+raw)
        elif kind=="callout":
            out.append(f"<blockquote>💡 {val}</blockquote>"); lines.append("💡 "+raw)
        elif kind=="code":
            code=plain(rt); out.append(f"<pre><code>{html.escape(code)}</code></pre>"); lines.extend(["",code,""])
        elif kind=="divider":
            out.append("<hr>"); lines.append("──────────")
        elif kind=="image":
            src=image_map.get(b.get("id"))
            caption=rich_text(d.get("caption") or [])
            if src: out.append(f'<figure><img src="{html.escape(src,quote=True)}"><figcaption>{caption}</figcaption></figure>')
            else: out.append("<p>[이미지 다운로드 실패]</p>")
            lines.append("[이미지: "+(plain(d.get("caption") or []) or "설명 없음")+"]")
        elif kind in ("file","pdf","video","audio","bookmark","embed"):
            m=d.get("file") or d.get("external") or {}; url=m.get("url") or d.get("url","")
            if url: out.append(f'<p><a href="{html.escape(url,quote=True)}">{kind} 링크</a></p>'); lines.append(f"[{kind}] {url}")
        elif kind=="table_row":
            cells=d.get("cells") or []; out.append("<p>"+" | ".join(rich_text(c) for c in cells)+"</p>"); lines.append(" | ".join(plain(c) for c in cells))
        elif kind=="equation":
            expr=d.get("expression",""); out.append(f"<p><code>{html.escape(expr)}</code></p>"); lines.append(expr)
        elif kind in ("toggle","synced_block","column","column_list","table","child_page"):
            if kind=="child_page":
                label=d.get("title","하위 페이지"); out.append(f"<p><strong>{html.escape(label)}</strong></p>"); lines.append(label)
        elif raw:
            out.append(f"<p>{val}</p>"); lines.append(raw)
        if children: out.append(render_blocks(children,image_map,lines,depth+1))
    return "\\n".join(x for x in out if x)
def wrap_html(title,memo,body,tags):
    tag_html=" ".join("#"+html.escape(t) for t in tags)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>body{{font-family:Arial,'Malgun Gothic',sans-serif;line-height:1.8;max-width:780px;margin:30px auto;padding:0 18px;color:#222}}img{{max-width:100%;height:auto}}pre{{white-space:pre-wrap;background:#f3f3f3;padding:14px}}blockquote{{border-left:4px solid #888;padding:8px 16px;background:#f7f7f7}}</style></head><body><h1>{html.escape(title)}</h1><p>{html.escape(memo)}</p><p>{tag_html}</p>{body}</body></html>"""
