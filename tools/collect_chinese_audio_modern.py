"""Cache official Wikimedia evidence for redistributable Chinese character audio.

No requests are made without --download. All MediaWiki responses are preserved.
Audio downloads are serial and stop at the first HTTP 429; no mirrors or retries.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
from urllib.error import HTTPError
from urllib.parse import urlencode, quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/chinese_audio_modern"
OUT = ROOT / "data/enrichment"
UA = "KanaMemoryResearch/0.1 (personal open pronunciation recordings study)"

def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def api(host, params, download=False):
    params = {"format":"json", "formatversion":"2", "maxlag":5, **params}
    url = "https://" + host + "/w/api.php?" + urlencode(params)
    path = RAW / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["response"]
    if not download:
        raise RuntimeError("Uncached response: " + url)
    with urlopen(Request(url, headers={"User-Agent":UA}), timeout=45) as resp:
        data = json.load(resp)
    if "error" in data:
        raise RuntimeError(data["error"])
    dump(path, {"url":url, "retrieved_at":"2026-10-10", "response":data})
    time.sleep(3)
    return data

def targets():
    con = sqlite3.connect(ROOT / "app/src/kanamemory/resources/content.sqlite3")
    origins = set()
    for (raw,) in con.execute("SELECT payload FROM origin"):
        d = json.loads(raw)
        for kind in ("hiragana_origins", "katakana_origins"):
            origins.update(v["han_character"] for v in d.get(kind, []))
    variants = {b for a,b in con.execute("SELECT character,related_character FROM orthographic_relation") if a in origins}
    chars = origins | variants
    readings = defaultdict(list)
    for (raw,) in con.execute("SELECT payload FROM dialect"):
        d = json.loads(raw)
        if d["character"] in chars and d["locality_id"] in {"cmn-standard-mainland", "yue-unihan-reference"}:
            readings[d["character"]].append(d)
    return origins, variants, chars, readings

def templates(text, name):
    """Read a named balanced wikitext template without expanding nested markup."""
    for m in re.finditer(r"\{\{" + re.escape(name) + r"(?=[\s|}])", text):
        start=m.start();depth=0;i=start
        while i<len(text)-1:
            pair=text[i:i+2]
            if pair=="{{":depth+=1;i+=2
            elif pair=="}}":
                depth-=1;i+=2
                if depth==0:
                    body=text[start+2:i-2]
                    yield {a.strip():v.strip() for a,v in re.findall(r"(?:^|\n)\s*\|([^=|\n]+)=([^\n]*)", body)}
                    break
            else:i+=1

def plain(value):
    return html.unescape(re.sub("<[^>]*>", "", value or "")).strip()

def wiki_pages(chars, download):
    pages={}
    for start in range(0,len(chars),30):
        q=api("en.wiktionary.org", {"action":"query", "prop":"revisions", "rvprop":"content|ids|timestamp", "rvslots":"main", "titles":"|".join(chars[start:start+30])},download)
        for p in q["query"]["pages"]:
            if "revisions" in p:pages[p["title"]]=p
    return pages

def chinese_section(page):
    text=page["revisions"][0]["slots"]["main"]["content"]
    match=re.search(r"^==Chinese==\s*$",text,re.M)
    if not match:return ""
    section=text[match.end():]
    return re.split(r"^==[^=\n]+==\s*$",section,maxsplit=1,flags=re.M)[0]

def category(category, download):
    members=[];more={}
    for unused in range(30):
        q=api("commons.wikimedia.org",{"action":"query", "list":"categorymembers", "cmtitle":category,"cmlimit":500,**more},download)
        members+=q["query"]["categorymembers"]
        if "continue" not in q:break
        more=q["continue"]
    return members

def discover(download):
    origins,variants,chars,readings=targets()
    pages=wiki_pages(sorted(chars),download)
    evidence=[];titles=set()
    for ch,page in pages.items():
        revision=page["revisions"][0]
        for n,t in enumerate(templates(chinese_section(page),"zh-pron")):
            for field,audio,lang in (("m","ma","cmn"),("c","ca","yue")):
                if not t.get(field):continue
                item={"character":ch,"language":lang,"romanization":t[field],"template_index":n,"page_id":page["pageid"],"revision_id":revision["revid"],"source_url":"https://en.wiktionary.org/w/index.php?oldid="+str(revision["revid"]),"raw_parameters":t}
                if t.get(audio):
                    item["audio_files"]=[s.strip() for s in t[audio].split(",") if re.search(r"\.(ogg|oga|wav|opus)$",s.strip(),re.I)]
                    titles.update("File:"+s for s in item["audio_files"])
                if lang=="cmn":
                    for syllable in t[field].split(","):
                        if re.fullmatch(r"[a-züāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜêńňǹ]+",syllable):
                            titles.update("File:Zh-"+syllable+ext for ext in [".ogg",".oga"])
                evidence.append(item)
    # Filename-based candidates still require matching file descriptions and author evidence.
    for ch,rs in readings.items():
        for r in rs:
            roman=r.get("romanization")
            if not roman:continue
            if r["locality_id"]=="cmn-standard-mainland":
                titles.update("File:Zh-"+roman+ext for ext in [".ogg",".oga"])
            else:titles.update("File:Yue-"+roman+ext for ext in [".ogg",".oga",".opus"])
    # Enumerate known official Cantonese categories, including Lingua Libre's renamed language category.
    cats=["Category:Cantonese pronunciation","Category:Lingua Libre pronunciation-other (Q9186)"]
    members=[]
    for cat in cats:members+=category(cat,download)
    for m in members:
        if m["ns"]!=6:continue
        stem=re.sub(r"\.(ogg|oga|wav|opus)$","",m["title"],flags=re.I)
        tail=re.split(r"[-_–]",stem)[-1].strip()
        if tail in chars:titles.add(m["title"])
    metadata={}
    ordered=sorted(titles)
    pending=[]
    for start in range(0,len(ordered),20):
        try:
            q=api("commons.wikimedia.org",{"action":"query","prop":"imageinfo|revisions","redirects":1,"iiprop":"url|extmetadata|size|sha1|mime","rvprop":"ids|timestamp|content","rvslots":"main","titles":"|".join(ordered[start:start+20])},download)
        except (HTTPError, RuntimeError) as ex:
            pending=ordered[start:]
            print("Metadata paused:",str(ex),flush=True)
            break
        for p in q["query"]["pages"]:
            if "imageinfo" in p:metadata[p["title"]]=p
    doc={"origin_characters":sorted(origins),"variant_characters":sorted(variants),"target_characters":sorted(chars),"wiktionary_pronunciation_evidence":evidence,"dictionary_readings":dict(readings),"commons_files":metadata,"category_members":members,"pending_metadata_titles":pending}
    dump(RAW/"discovery.json",doc)
    print(json.dumps({"targets":len(chars),"pronunciation_evidence":len(evidence),"candidate_titles":len(titles),"existing_files":len(metadata),"category_members":len(members)},ensure_ascii=False),flush=True)
    for title,p in metadata.items():
        m=p["imageinfo"][0].get("extmetadata",{})
        print(title,plain(m.get("Artist",{}).get("value")),plain(m.get("ImageDescription",{}).get("value"))[:180],m.get("LicenseShortName",{}).get("value"),flush=True)

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--download",action="store_true");args=parser.parse_args();discover(args.download)
