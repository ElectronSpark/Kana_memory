"""The analyser side of tools/annotate_readings.py: run ONLY under the separate interpreter that has pyopenjtalk-plus.

usage: <analyser python> -I -X utf8 tools/annotate_readings_analyser.py <texts.json> <tokens.json>

Reads a JSON list of texts and writes {text: [[surface, lemma, reading, pos, pos1, pos2, cform], ...]} (reading in
katakana as the analyser gives it). Offline: no network, no optional model. The app and its tests never import
this file; tools/annotate_readings.py reaches it through its AnalyserAdapter. Exit 3 when pyopenjtalk is missing.
"""
import json
import sys


def main(argv):
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        import pyopenjtalk
    except ImportError as error:
        print("pyopenjtalk is not installed in this interpreter: " + str(error), file=sys.stderr)
        return 3
    with open(argv[1], encoding="utf-8") as handle:
        texts = json.load(handle)
    result = {}
    for text in texts:
        rows = pyopenjtalk.run_frontend(text) if text else []
        result[text] = [[row.get("string", ""), row.get("orig", ""), row.get("read", ""), row.get("pos", ""),
                         row.get("pos_group1", ""), row.get("pos_group2", ""), row.get("cform", "")]
                        for row in rows]
    with open(argv[2], "w", encoding="utf-8") as handle:
        json.dump({"engine": getattr(pyopenjtalk, "__version__", ""), "tokens": result}, handle, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
