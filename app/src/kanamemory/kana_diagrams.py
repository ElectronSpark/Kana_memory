"""Modern spelling composition only; never invent historical intermediate glyphs."""
from unicodedata import normalize

from .lesson_diagrams import FORMAT, VERSION

SMALL = dict(zip('ぁぃぅぇぉっゃゅょゎゕゖ','あいうえおつやゆよわかけ'))
SMALL.update({chr(ord(a)+0x60):chr(ord(b)+0x60) for a,b in tuple(SMALL.items())})


def kana_diagram(item, script):
    if script not in {'hiragana','katakana'}:
        return None
    form = normalize('NFC', item.get(script) or '')
    category = item.get('category')
    node = lambda text, **kw: {'text':text,'text_role':'study',**kw}
    caption = ''
    if category in {'yoon','foreign_combination','foreign_variant'} and len(form)==2 and form[1] in SMALL:
        nodes = [node(form[0]),node(form[1],detail='小假名'),node(form,detail='组合为一拍')]
        caption = '小假名与前面的假名合为一拍；不能改写为独立的大假名。'
    elif category in {'dakuten','handakuten','foreign_voiced','rare_legacy_katakana'}:
        parts = normalize('NFD',form)
        if len(parts)!=2 or parts[1] not in '\u3099\u309a':
            return None
        mark = '゛' if parts[1]=='\u3099' else '゜'
        nodes = [node(parts[0]),node(mark,detail='浊音符号' if mark=='゛' else '半浊音符号'),node(form)]
        caption = '图示现代字形的加符号关系，具体读音以本项为准。'
    elif category=='small' and form in SMALL:
        return {'format':FORMAT,'version':VERSION,'kind':'contrast',
                'label':('平假名' if script=='hiragana' else '片假名')+' · 大小写区别',
                'nodes':[node(SMALL[form],detail='普通大小'),node(form,detail='小假名')],
                'caption':'小写字形有自己的用法；并不只是把声音读轻，具体按所在词或组合判断。'}
    else:
        return None
    return {'format':FORMAT,'version':VERSION,'kind':'composition',
            'label':('平假名' if script=='hiragana' else '片假名')+' · 组合关系',
            'nodes':nodes,'edges':[{'from':0,'to':1,'text':'＋'},{'from':1,'to':2,'text':'＝'}],
            'caption':caption}
