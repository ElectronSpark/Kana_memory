"""Re-layout licensed SVG glyph paths; no fonts, guessed strokes or AI images.

Read audited glyph rectangles/path indices in research/raw/glyph-origins.
Emit editable SVG compositions and package-ready PNGs using Node/sharp.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'research/raw/glyph-origins'
OUT = ROOT / 'data/enrichment/glyph_origins'
SVG_OUT = ROOT / 'research/glyph-origin-diagrams'
NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', NS)
INK, ACCENT, FAINT = '#1E3430', '#185C50', '#BCC9C4'
WIDTH, HEIGHT = 960, 280
HIRAGANA_WIDTH = 640


def element(name, attrs=None):
    return ET.Element('{' + NS + '}' + name, attrs or {})


def recolor(value, mode):
    value = {'red':'#ff0000', 'black':'#000000', 'gray':'#808080', 'white':'#ffffff'}.get(value, value)
    if not value.startswith('#'):
        return value
    raw = value[1:]
    if len(raw) == 3:
        raw = ''.join(v * 2 for v in raw)
    if len(raw) != 6:
        return value
    r, g, b = [int(raw[i:i+2], 16) for i in (0, 2, 4)]
    if min(r, g, b) > 235:
        return value
    red = r > 100 and r > g * 1.6 and r > b * 1.6
    if mode == 'highlight':
        return ACCENT if red else FAINT
    return ACCENT if mode == 'cursive' else INK


def glyph_svg(record, layout, part_name, mode, column, *, panel_x=None):
    part = record['parts'][part_name]
    source = RAW / part.get('source_file', record.get('source_file', layout['source_file']))
    original = ET.parse(source).getroot()
    paths = list(original.iter('{' + NS + '}path'))
    rect = part['rect']
    width, height = rect[2:]
    # A common square around the supplied glyph bounds prevents distortion.
    side = max(width, height) * 1.12
    view_box = [rect[0] + width/2-side/2, rect[1]+height/2-side/2, side, side]
    panel = element('svg', {'x':str(18 + column*322 if panel_x is None else panel_x), 'y':'22', 'width':'236', 'height':'236',
        'viewBox':' '.join(f'{v:.4f}' for v in view_box), 'overflow':'hidden',
        'fill': FAINT if mode == 'highlight' else INK,
        'style':'fill-rule:evenodd;clip-rule:evenodd'})
    indices = part.get('path_indices_zero_based')
    selected = [paths[i] for i in indices] if indices is not None else list(original)
    clip_id = f'crop-{source.stem}-{column}'
    defs = element('defs')
    clip = element('clipPath', {'id':clip_id, 'clipPathUnits':'userSpaceOnUse'})
    clip.append(element('rect', dict(zip(('x','y','width','height'), map(str,rect)))))
    defs.append(clip)
    panel.append(defs)
    clipped = element('g', {'clip-path':f'url(#{clip_id})'})
    for original_node in selected:
        node = deepcopy(original_node)
        for child in node.iter():
            if child.tag == '{'+NS+'}style':
                child.text = ''  # The classic chart's usedpart class is inlined below.
            for attr in ('fill', 'stroke'):
                if attr in child.attrib:
                    child.set(attr, recolor(child.get(attr), mode))
            style = child.get('style', '')
            if style:
                declarations = []
                for declaration in style.split(';'):
                    if ':' not in declaration:
                        continue
                    key, value = declaration.split(':', 1)
                    if key.strip() in {'fill', 'stroke'}:
                        value = recolor(value.strip(), mode)
                    declarations.append(key+':'+value)
                child.set('style', ';'.join(declarations))
            if 'usedpart' in child.get('class','').split():
                fill = ACCENT if mode == 'highlight' else INK
                child.set('style', child.get('style','')+';fill:'+fill)
        clipped.append(node)
    panel.append(clipped)
    return panel


def compose(record, layout, *, teaching_reference=False):
    hiragana = record['script'] == 'hiragana'
    if teaching_reference and not hiragana:
        raise ValueError('Only hiragana has a separate teaching reference')
    width = HIRAGANA_WIDTH if hiragana and not teaching_reference else WIDTH
    svg = element('svg', {'width':str(width), 'height':str(HEIGHT), 'viewBox':f'0 0 {width} {HEIGHT}'})
    svg.append(element('rect', {'width':str(width), 'height':str(HEIGHT), 'fill':'#FFFFFF'}))
    if hiragana:
        # An unreferenced modern redraw is not an attested historical stage.
        # Keep its middle panel only in an explicitly separate reference image.
        panels = [('regular','plain'),('modern','plain')]
        if teaching_reference:
            panels.insert(1, ('cursive','cursive'))
    else:
        panels = [('regular','plain'),('regular','highlight'),('modern','plain')]
    for i, (part, mode) in enumerate(panels):
        panel_x = 42 + i*320 if hiragana and not teaching_reference else None
        svg.append(glyph_svg(record, layout, part, mode, i, panel_x=panel_x))
    if not hiragana:
        for x in (283, 605):
            svg.append(element('path', {'d':f'M{x-12} 140h28m-9 -9 9 9-9 9', 'fill':'none',
                                       'stroke':'#8B9F97', 'stroke-width':'3', 'stroke-linecap':'round',
                                       'stroke-linejoin':'round'}))
    return ET.tostring(svg, encoding='utf-8', xml_declaration=True)


def load_hiragana_reviews():
    path = ROOT / 'data/enrichment/hiragana_origin_review.json'
    document = json.loads(path.read_text(encoding='utf-8'))
    if document.get('schema_version') != 1:
        raise ValueError('Unsupported hiragana origin review schema')
    rows = document['records']
    reviews = {row['kana_id']: row for row in rows}
    if len(reviews) != len(rows):
        raise ValueError('Duplicate hiragana origin review')
    return reviews


def build(node, sharp):
    OUT.mkdir(parents=True, exist_ok=True)
    SVG_OUT.mkdir(parents=True, exist_ok=True)
    records, jobs = [], []
    sources = {r['id']:r for r in json.loads((ROOT/'data/enrichment/sources_glyph_origins.json').read_text(encoding='utf-8'))['sources']}
    reviews = load_hiragana_reviews()
    for script in ('hiragana', 'katakana'):
        layout_path = RAW / (script+'-layout'+('-with-paths' if script == 'katakana' else '')+'.json')
        layout = json.loads(layout_path.read_text(encoding='utf-8'))
        if script == 'hiragana' and set(reviews) != {r['kana_id'] for r in layout['records']}:
            raise ValueError('Hiragana origin reviews must cover the exact artwork inventory')
        for r in layout['records']:
            filename = script+'-'+r['kana_id'].removeprefix('kana-')
            svg_path = SVG_OUT/(filename+'.svg')
            svg_path.write_bytes(compose(r, layout))
            png_path = OUT/(filename+'.png')
            jobs.append([str(svg_path), str(png_path)])
            license_id = 'CC-BY-SA-3.0' if any(sources[s]['license'] == 'CC-BY-SA-3.0' for s in r['source_ids']) else 'CC0-1.0'
            record = {'kana_id':r['kana_id'],'script':script,'han_character':r['han_character'],
                'image':png_path.name, 'source_ids':list(r['source_ids']),
                'width':HIRAGANA_WIDTH if script == 'hiragana' else WIDTH, 'height':HEIGHT,
                'license':license_id,
                'source_layout_record':r, 'layout_sha256':hashlib.sha256(layout_path.read_bytes()).hexdigest()}
            if script == 'hiragana':
                review = reviews[r['kana_id']]
                reference_svg = SVG_OUT/(filename+'-teaching.svg')
                reference_svg.write_bytes(compose(r, layout, teaching_reference=True))
                reference_png = OUT/(filename+'-teaching.png')
                jobs.append([str(reference_svg), str(reference_png)])
                record.update(reference_image=reference_png.name, reference_width=WIDTH,
                              reference_height=HEIGHT, diagram_kind='source_character_comparison',
                              intermediate_status='unverified_modern_illustration', review=review)
                record['source_ids'] = list(dict.fromkeys([*r['source_ids'], *review['source_ids']]))
            records.append(record)
    runner = "const fs=require('fs'); const sharp=require(process.argv[1]); const jobs=JSON.parse(fs.readFileSync(0,'utf8')); (async()=>{for(const [src,dst] of jobs){await sharp(src).png().toFile(dst)}})().catch(e=>{console.error(e);process.exit(1)});"
    subprocess.run([node, '-e', runner, sharp], input=json.dumps(jobs), text=True, check=True)
    for r in records:
        r['sha256'] = hashlib.sha256((OUT/r['image']).read_bytes()).hexdigest()
        if r.get('reference_image'):
            r['reference_sha256'] = hashlib.sha256((OUT/r['reference_image']).read_bytes()).hexdigest()
    assert len(records) == 96 and len({(r['kana_id'],r['script']) for r in records}) == 96
    manifest = {'schema_version':1, 'records':records, 'record_count':len(records),
        'changes':'Original SVG paths isolated and rearranged horizontally; recolored. Hiragana primary images compare source characters and modern kana without an intermediate stage or arrows. Separate hiragana teaching references retain the modern illustration of a cursive form without arrows; no historical specimen is claimed. Katakana diagrams are unchanged. No stroke geometry reconstructed.',
        'derivative_display':'Voiced and small single kana use the unmodified basic-kana origin diagram; no claim of marks descending from the source Han character.'}
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'images':len(records),'reference_images':sum('reference_image' in r for r in records),
        'bytes':sum(Path(dst).stat().st_size for _, dst in jobs)},ensure_ascii=False))


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--node', required=True)
    p.add_argument('--sharp', required=True)
    args=p.parse_args()
    build(args.node,args.sharp)
