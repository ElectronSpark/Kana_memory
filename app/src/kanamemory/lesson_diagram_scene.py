"""One vector scene for responsive native Canvas and standalone accessible SVG.

Coordinates are display-independent pixels. Text wraps before geometry is built;
larger system fonts increase the diagram's height instead of being cropped.
No network, scripts, images or external SVG references are emitted.
"""
from html import escape
import math
import unicodedata

from .lesson_diagrams import validate_diagram, accessible_description

INK = '#193D37'
GREEN = '#176A59'
BLUE = '#246B94'
ORANGE = '#9A510F'
MUTED = '#58716C'
PALE = '#E4F2EB'
WHITE = '#FFFFFF'


def estimated_width(text, size):
    return sum(1.03 if unicodedata.east_asian_width(c) in 'WF' else .67 for c in text) * size


def wrapped_lines(text, width, size, measure=None):
    measure = measure or estimated_width
    lines = []
    for paragraph in str(text).split('\n'):
        current = ''
        for char in paragraph:
            if current and measure(current + char, size) > width:
                lines.append(current); current = ''
            current += char
        lines.append(current)
    return lines


class Scene:
    def __init__(self, width, scale, measure=None):
        self.width = max(220, float(width))
        self.size = 14 * max(1, min(3, scale))
        self.measure = measure
        self.elements = []
        self.node_indices = {}
        self.edge = None

    def line(self, x1, y1, x2, y2, color=GREEN, weight=2, dashed=False, arrow=False):
        element = dict(kind='line', x1=x1, y1=y1, x2=x2, y2=y2,
                       color=color, weight=weight, dashed=dashed, arrow_tip=arrow)
        if self.edge is not None:
            element.update(edge_from=self.edge['from'],edge_to=self.edge['to'])
        self.elements.append(element)
        if arrow:
            angle = math.atan2(y2-y1, x2-x1)
            for turn in (-.52, .52):
                self.line(x2, y2, x2-8*math.cos(angle+turn), y2-8*math.sin(angle+turn), color, weight)

    def rect(self, x, y, w, h, fill=PALE, stroke=GREEN, dashed=False):
        element=dict(kind='rect', x=x, y=y, w=w, h=h, fill=fill, stroke=stroke, dashed=dashed)
        self.elements.append(element)
        return element

    def dot(self, x, y, r=4, fill=GREEN):
        element = dict(kind='ellipse', x=x, y=y, rx=r, ry=r, fill=fill, stroke=fill)
        self.elements.append(element)
        return element

    def text(self, value, x, y, width, size=None, color=INK, bold=False, align='left', role=None):
        size = size or self.size
        lines = wrapped_lines(value, max(size*1.2, width), size, self.measure)
        for i, line in enumerate(lines):
            measured = (self.measure or estimated_width)(line, size)
            left = x + max(0, (width-measured)/2) if align == 'center' else x
            self.elements.append(dict(kind='text', text=line, x=left, y=y+i*size*1.45,
                                      size=size, color=color, bold=bold, width=measured,
                                      family='serif' if role == 'study' or '「' in value else 'sans-serif'))
        return len(lines)*size*1.45

    def box(self, node, x, y, w, focus=False, index=None):
        title_height = len(wrapped_lines(node['text'], w-20, self.size, self.measure))*self.size*1.45
        detail_height = (len(wrapped_lines(node['detail'], w-20, self.size*.88, self.measure))*self.size*.88*1.45+5) if node.get('detail') else 0
        height = title_height+detail_height+20
        element=self.rect(x, y, w, height, '#D5EBFF' if focus else WHITE, BLUE if focus else GREEN, node.get('dashed', False))
        element['node_index']=self.node_indices.get(id(node)) if index is None else index
        self.text(node['text'], x+10, y+10, w-20, color=BLUE if focus else INK, bold=True, align='center', role=node.get('text_role'))
        if node.get('detail'):
            self.text(node['detail'], x+10, y+15+title_height, w-20, size=self.size*.88, color=MUTED, align='center')
        return height


def build_scene(diagram, width=360, font_scale=1, measure=None):
    validate_diagram(diagram)
    s = Scene(width, font_scale, measure)
    w, fs, nodes = s.width, s.size, diagram['nodes']
    s.node_indices={id(node):i for i,node in enumerate(nodes)}
    kind, n = diagram['kind'], len(nodes)
    y = 12
    if kind == 'timeline':
        # Callout columns do not overlap even when events are near each other.
        order = sorted(range(n), key=lambda i: nodes[i]['at'])
        gap, bw = 6, (w-24-6*(n-1))/n
        compact_axis = bw < fs*4.5
        bottoms = []
        if compact_axis:
            axis_y = y+fs*2
            for number, index in enumerate(order,1):
                x = 16+(w-32)*nodes[index]['at']
                s.text(str(number), max(0,x-fs), y, fs*2, color=GREEN, align='center')
                bottoms.append((x,y+fs*1.5,x,nodes[index]))
        else:
            for column, index in enumerate(order):
                x = 12+column*(bw+gap)
                h = s.box(nodes[index], x, y, bw, diagram.get('focus') == index)
                bottoms.append((x+bw/2, y+h, 16+(w-32)*nodes[index]['at'], nodes[index]))
            axis_y = max(b[1] for b in bottoms)+32
        s.line(14, axis_y, w-12, axis_y, MUTED, arrow=True)
        for x, bottom, event_x, node in bottoms:
            s.line(x, bottom, event_x, axis_y, MUTED, 1, node.get('dashed', False))
            s.dot(event_x, axis_y, 4, GREEN)
        y = axis_y+18
        ref = diagram.get('reference')
        if ref:
            rx = 16+(w-32)*ref['at']
            s.line(rx, axis_y-14, rx, y, BLUE, 2, True)
            y += s.text('观察点：'+ref['text'], 12, y+3, w-24, color=BLUE, align='center')+10
        for span in sorted(diagram.get('spans', []), key=lambda x: x.get('lane', 0)):
            x1, x2 = 16+(w-32)*span['start'], 16+(w-32)*span['end']
            s.line(x1, y+4, x2, y+4, ORANGE, 5, span.get('dashed', False))
            s.line(x1, y-2, x1, y+10, ORANGE); s.line(x2, y-2, x2, y+10, ORANGE)
            y += 16+s.text(span['text'], 12, y+16, w-24, color=ORANGE, align='center')+10
        if compact_axis:
            for number,index in enumerate(order,1):
                node = {**nodes[index], 'text':str(number)+'  '+nodes[index]['text']}
                y += s.box(node,12,y,w-24,diagram.get('focus')==index,index=index)+8
    elif kind == 'space':
        relation = diagram['relation']
        # The coloured dot is the subject; the outlined region/object is its
        # reference. The subject marker and text are both present, not colour alone.
        h = max(155, fs*7)
        rx, ry, rw, rh = w*.27, y+h*.27, w*.46, h*.44
        if relation == 'deixis':
            # Spatial proximity is schematic, never a metric distance.
            bw=(w-36)/2
            far_h=s.box(nodes[2],w*.23,y,w*.54)
            lower=y+far_h+32
            s.line(w*.5,y+far_h+4,w*.25,lower-4,MUTED,1,True)
            s.line(w*.5,y+far_h+4,w*.75,lower-4,MUTED,1,True)
            ah=s.box(nodes[0],12,lower,bw)
            bh=s.box(nodes[1],24+bw,lower,bw)
            y=lower+max(ah,bh)+10
            a=s.text('说话者附近',12,y,bw,size=fs*.9,color=GREEN,align='center')
            b=s.text('听话者附近',24+bw,y,bw,size=fs*.9,color=BLUE,align='center')
            y+=max(a,b)+10
            y+=s.text('上方：远离双方',12,y,w-24,size=fs*.9,color=MUTED,align='center')
        elif relation in {'inside','outside','at'}:
            label_h = len(wrapped_lines(nodes[1]['text'],rw-10,fs*.9,measure))*fs*.9*1.45
            h=max(h,label_h+fs*4+32)
            s.rect(rx, y+8, rw, h-16, '#EDF4F8', BLUE)
            s.text(nodes[1]['text'], rx+5, y+16, rw-10, size=fs*.9, color=BLUE, align='center',role=nodes[1].get('text_role'))
            px, py = (w*.5, y+label_h+fs*2.5+16) if relation != 'outside' else (w*.12, y+h*.65)
        elif relation == 'between':
            label_h=max(len(wrapped_lines(node['text'],76,fs*.85,measure))*fs*.85*1.45 for node in nodes[1:])
            h=max(h,label_h+fs*4+20)
            for cx, node in zip((max(50,w*.13),min(w-50,w*.87)), nodes[1:]):
                s.rect(cx-25, y+label_h+10, 50, fs*3, '#EDF4F8', BLUE)
                s.text(node['text'], cx-38, y, 76, size=fs*.85, color=BLUE, align='center',role=node.get('text_role'))
            px, py = w*.5, y+label_h+10+fs*1.5
        else:
            if relation in {'near', 'beside'}:
                rx, rw = w*.52, w*.36
            label_h=len(wrapped_lines(nodes[1]['text'],rw-14,fs*.9,measure))*fs*.9*1.45
            rh=max(rh,label_h+20)
            ry=y+fs*2.5
            h=rh+fs*5
            s.rect(rx, ry, rw, rh, '#EDF4F8', BLUE)
            s.text(nodes[1]['text'], rx+7, ry+10, rw-14, size=fs*.9, color=BLUE, align='center',role=nodes[1].get('text_role'))
            px, py = {'above':(w*.5,y+fs),'below':(w*.5,ry+rh+fs*1.2),
                      'beside':(w*.27,ry+rh*.55),'near':(w*.34,ry+rh*.64)}[relation]
        if relation != 'deixis':
            s.dot(px, py, 7)
            s.line(px, py, px+10, py-10, GREEN, 2)
            y += h+8
            relation_text = {'inside':'里面', 'outside':'外面', 'above':'上方', 'below':'下方', 'beside':'旁边', 'between':'之间', 'near':'附近', 'at':'位置'}[relation]
            y += s.text('● '+nodes[0]['text']+'  ·  '+relation_text, 12, y, w-24, bold=True,role=nodes[0].get('text_role'))+6
            for node in nodes:
                if node.get('detail'):
                    y += s.text(node['detail'], 12, y, w-24, size=fs*.9, color=MUTED)+5
        if diagram.get('action'):
            y += s.text(diagram['action'], 12, y, w-24, color=GREEN)+5
    elif kind == 'beats':
        # A mora is an indivisible visual unit. Wrap whole cells when needed,
        # never split a small-kana combination or replace it with a number-only
        # legend. Notes use the full line below, outside the mora's own box.
        measure_text = measure or estimated_width
        kana_size = fs*1.6
        desired = max(measure_text(node['text'], kana_size) for node in nodes)+24
        columns = max(1, min(n, int((w-24)/desired)))
        cell_w = (w-24)/columns
        number_size = fs*.68
        for start in range(0, n, columns):
            row = nodes[start:start+columns]
            label_h = max(len(wrapped_lines(node['text'], cell_w-16, kana_size, measure))*kana_size*1.45
                          for node in row)
            cell_h = number_size*1.45+label_h+18
            for offset, node in enumerate(row):
                i, x = start+offset, 12+offset*cell_w
                cell = s.rect(x+2, y, cell_w-4, cell_h,
                              PALE if i in diagram.get('highlight', []) else WHITE)
                cell['node_index'] = i
                s.text(str(i+1), x+8, y+5, cell_w-16, size=number_size,
                       color=MUTED, align='center')
                s.text(node['text'], x+8, y+10+number_size*1.45, cell_w-16,
                       size=kana_size, bold=True, align='center', role=node.get('text_role', 'study'))
            track_y = y+cell_h+12
            for offset in range(len(row)):
                cx = 12+(offset+.5)*cell_w
                if offset:
                    s.line(cx-cell_w, track_y, cx, track_y, MUTED, 1)
                s.dot(cx, track_y, 4, GREEN)
            y = track_y+16
        y += s.text(str(n)+' 拍', 12, y, w-24, size=fs*1.15,
                    color=GREEN, bold=True, align='center')+5
        for i, node in enumerate(nodes):
            if node.get('detail'):
                y += s.text(str(i+1)+' · '+node['detail'], 12, y, w-24,
                            size=fs*.9, color=MUTED)+5
    elif kind == 'pitch':
        # Keep mora and pitch on one continuous track. Show actual kana in
        # their cells whenever they fit at the selected font size; use a
        # numbered legend only when shrinking or wrapping would split a mora.
        cell_w = (w-24)/n
        direct_labels = all('\n' not in node['text'] and
                            (measure or estimated_width)(node['text'], fs) <= cell_w-12
                            for node in nodes)
        top = y+fs*1.6
        if kind == 'pitch':
            levels = diagram['levels']
            high, low = top, top+max(36, fs*2)
            for i, level in enumerate(levels):
                x = 12+cell_w*(i+.5); py = high if level else low
                if i:
                    last_y = high if levels[i-1] else low
                    s.line(x-cell_w, last_y, x, py, GREEN, 3)
                s.dot(x, py)
            boundary = diagram.get('particle_from')
            if boundary is not None:
                bx = 12+cell_w*boundary
                s.line(bx, top-8, bx, low+fs*1.4, MUTED, 1, True)
            y = low+16
        if direct_labels:
            detail_h = max((len(wrapped_lines(node['detail'], cell_w-12, fs*.85, measure))*fs*.85*1.45+5
                            for node in nodes if node.get('detail')), default=0)
            cell_h = fs*1.45+12+detail_h
            for i, node in enumerate(nodes):
                x = 12+i*cell_w
                cell = s.rect(x+2, y, cell_w-4, cell_h, PALE if i in diagram.get('highlight', []) else WHITE)
                cell['node_index'] = i
                s.text(node['text'], x+6, y+6, cell_w-12, align='center', role=node.get('text_role'))
                if node.get('detail'):
                    s.text(node['detail'], x+6, y+11+fs*1.45, cell_w-12, size=fs*.85, color=MUTED, align='center')
            y += cell_h+8
        else:
            for i, node in enumerate(nodes):
                x = 12+i*cell_w
                cell = s.rect(x+2, y, cell_w-4, fs*1.9, PALE if i in diagram.get('highlight', []) else WHITE)
                cell['node_index'] = i
                s.text(str(i+1), x+2, y+3, cell_w-4, size=min(11,fs*.7), color=MUTED, align='center')
            y += fs*1.9+8
            # The legend preserves full-size kana when a cell would be too
            # narrow. Small kana stay in the same mora instead of a new cell.
            for i, node in enumerate(nodes):
                y += s.text(str(i+1)+'  '+node['text'], 12, y, w-24,role=node.get('text_role'))+4
                if node.get('detail'):
                    y += s.text(node['detail'], 25, y, w-37, size=fs*.85, color=MUTED)+4
    elif kind == 'branch':
        edges = diagram.get('edges') or [{'from':0,'to':i} for i in range(1,n)]
        levels = {}
        while len(levels)<n:
            ready=[i for i in range(n) if i not in levels and all(e['from'] in levels for e in edges if e['to']==i)]
            if not ready:
                raise ValueError('A conditional diagram must not contain a cycle')
            for i in ready:
                levels[i]=max((levels[e['from']]+1 for e in edges if e['to']==i),default=0)
        groups=[[i for i in range(n) if levels[i]==level] for level in range(max(levels.values())+1)]
        widest=max(map(len,groups))
        if (w-24-10*(widest-1))/widest < fs*4.5:
            # Preserve directed relationships when a many-way branch cannot
            # fit readable labels in side-by-side columns at the user's size.
            for edge in edges:
                y+=s.box(nodes[edge['from']],12,y,w-24,diagram.get('focus')==edge['from'])+8
                s.edge=edge
                s.line(w/2,y,w/2,y+18,BLUE,2,edge.get('dashed',False),True)
                s.edge=None
                y+=24
                if edge.get('text'):
                    y+=s.text(edge['text'],12,y,w-24,color=BLUE,align='center')+5
                y+=s.box(nodes[edge['to']],12,y,w-24,diagram.get('focus')==edge['to'])+16
        else:
            boxes={}
            gaps={}
            for level,group in enumerate(groups):
                bw=(w-24-10*(len(group)-1))/len(group)
                heights=[]
                for col,index in enumerate(group):
                    x=12+col*(bw+10)
                    h=s.box(nodes[index],x,y,bw,diagram.get('focus')==index)
                    boxes[index]=(x+bw/2,y,h,bw)
                    heights.append(h)
                bottom=y+max(heights)
                incoming=[e for e in edges if levels[e['from']]==level]
                label_h=max((len(wrapped_lines(e.get('text',''),(w-36)/2,fs*.85,measure))*fs*.85*1.45 for e in incoming),default=0)
                gaps[level]=(bottom,bottom+22+label_h)
                y=bottom+label_h+50
            labels=[]
            for edge in edges:
                sx,sy,sh,sw=boxes[edge['from']]
                tx,ty,th,tw=boxes[edge['to']]
                middle=(sy+sh+ty)/2
                if edge.get('text'):
                    ew=min(sw,tw)-10
                    lh=len(wrapped_lines(edge['text'],ew,fs*.85,measure))*fs*.85*1.45
                    # Large labels must not erase the horizontal join of a
                    # split/merge when their opaque backing masks the stroke.
                    middle=max(middle,sy+sh+lh+16)
                s.edge=edge
                s.line(sx,sy+sh,sx,middle,BLUE,1.7,edge.get('dashed',False))
                s.line(sx,middle,tx,middle,BLUE,1.7,edge.get('dashed',False))
                s.line(tx,middle,tx,ty-3,BLUE,1.7,edge.get('dashed',False),True)
                s.edge=None
                if edge.get('text'):
                    cx=tx if len(groups[levels[edge['to']]])>1 else sx
                    ew=min(sw,tw)-10
                    labels.append((edge['text'],cx-ew/2,sy+sh+7,ew))
            # Draw labels after every connector, with a light backing so a
            # solid or hypothetical branch never passes through its words.
            for label,lx,ly,lw in labels:
                lh=len(wrapped_lines(label,lw,fs*.85,measure))*fs*.85*1.45
                s.rect(lx-2,ly-2,lw+4,lh+4,WHITE,WHITE)
                s.text(label,lx,ly,lw,size=fs*.85,color=BLUE,align='center')
            y-=24
    elif kind == 'scale':
        gap=6
        bw=(w-24-gap*(n-1))/n
        compact=bw < fs*4.5
        axis_y=y+14
        s.line(16,axis_y,w-12,axis_y,MUTED,2,arrow=True)
        bottom=axis_y+30
        for i,node in enumerate(nodes):
            x=12+i*(bw+gap)
            cx=x+bw/2
            marker=s.dot(cx,axis_y,5,BLUE if diagram.get('focus')==i else GREEN)
            marker['node_index']=i
            s.line(cx,axis_y+5,cx,axis_y+30,MUTED,1)
            if compact:
                h=s.text(str(i+1),x,axis_y+30,bw,size=fs*.8,align='center')
            else:
                h=s.box(node,x,axis_y+30,bw,diagram.get('focus')==i)
            bottom=max(bottom,axis_y+30+h)
        y=bottom+12
        if compact:
            for i,node in enumerate(nodes):
                numbered=dict(node,text=str(i+1)+'  '+node['text'])
                y+=s.box(numbered,12,y,w-24,diagram.get('focus')==i,index=i)+8
    else:
        # Directed roles need one explicit row per relationship, so recipient
        # viewpoint and subject focus cannot accidentally reverse an arrow.
        nonsequential = any(e['from'] != i or e['to'] != i+1 for i,e in enumerate(diagram.get('edges',[])))
        if kind == 'roles' or (nonsequential and kind in {'composition','flow','route'}):
            for edge in diagram['edges']:
                left, right = nodes[edge['from']], nodes[edge['to']]
                bw = (w-64)/2
                a = s.box(left, 12, y, bw, diagram.get('focus')==edge['from'])
                b = s.box(right, w-12-bw, y, bw, diagram.get('focus')==edge['to'])
                s.edge=edge
                s.line(16+bw, y+min(a,b)/2, w-16-bw, y+min(a,b)/2, BLUE, 2, edge.get('dashed',False), True)
                s.edge=None
                y += max(a,b)+6
                if edge.get('text'):
                    y += s.text(edge['text'], 12, y, w-24, color=BLUE, align='center')+6
                y += 10
        else:
            for i,node in enumerate(nodes):
                if i:
                    edge = next((e for e in diagram.get('edges', []) if e['from']==i-1 and e['to']==i), {})
                    if kind in {'flow','route','scale'}:
                        s.line(w/2, y-4, w/2, y+14, GREEN, 2, edge.get('dashed',False), True)
                        y += 20
                    elif kind == 'composition':
                        y += s.text(edge.get('text','＋'), 12, y, w-24, color=ORANGE, align='center')+5
                    else:
                        y += 12
                    if edge.get('text') and kind != 'composition':
                        y += s.text(edge['text'], 12, y, w-24, color=BLUE, align='center')+5
                y += s.box(node, 12, y, w-24, diagram.get('focus')==i)+6
    if diagram.get('axis'):
        left, right = diagram['axis']
        y += 5+s.text(left+' → '+right, 12, y+5, w-24, size=fs*.85, color=MUTED, align='center')
    if diagram.get('caption'):
        y += 10+s.text(diagram['caption'], 12, y+10, w-24, size=fs*.9, color=MUTED)
    return {'width':w,'height':math.ceil(y+12),'elements':s.elements,
            'description':accessible_description(diagram), 'kind':kind,
            'edges':[dict(edge) for edge in diagram.get('edges',[])]}


def svg_for_scene(scene):
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {scene["width"]} {scene["height"]}" role="img" aria-labelledby="title desc">',
           '<title id="title">学习图解</title><desc id="desc">'+escape(scene['description'])+'</desc>']
    for e in scene['elements']:
        dash = ' stroke-dasharray="5 4"' if e.get('dashed') else ''
        if e['kind']=='line':
            out.append(f'<line x1="{e["x1"]}" y1="{e["y1"]}" x2="{e["x2"]}" y2="{e["y2"]}" stroke="{e["color"]}" stroke-width="{e["weight"]}"{dash}/>')
        elif e['kind']=='rect':
            out.append(f'<rect x="{e["x"]}" y="{e["y"]}" width="{e["w"]}" height="{e["h"]}" rx="6" fill="{e["fill"]}" stroke="{e["stroke"]}"{dash}/>')
        elif e['kind']=='ellipse':
            out.append(f'<ellipse cx="{e["x"]}" cy="{e["y"]}" rx="{e["rx"]}" ry="{e["ry"]}" fill="{e["fill"]}"/>')
        else:
            weight = '600' if e['bold'] else '400'
            out.append(f'<text x="{e["x"]}" y="{e["y"]+e["size"]}" font-family="{e["family"]}" font-size="{e["size"]}" font-weight="{weight}" fill="{e["color"]}">{escape(e["text"])}</text>')
    return ''.join(out)+'</svg>'
