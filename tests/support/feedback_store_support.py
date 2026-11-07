"""Shared feedback store fixtures and assertions; no collected scenarios."""
import struct
import zlib


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00\xff')) + chunk(b'IEND', b''))


def context():
    return {'format': 'kanamemory-feedback-context', 'version': 1,
            'page': {'name': 'learning_intro', 'title': '初次学习', 'scroll_position': [0, 328]},
            'fragments': [{'id': 'grammar:te_iru:progressive', 'text': '読んでいます', 'source_revision': 'v1'},
                          {'id': 'example:shared', 'spans': [{'start': 0, 'end': 2, 'quote': '読ん'}]}],
            'state': {'draft': 'きっ', 'revealed': False}, 'completeness': {'complete': True, 'warnings': []}}
