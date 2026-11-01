"""Export selected, hash-verified graph extracts for human narrative authoring.

This is an authoring input, not approval to publish a graph or source file.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def export(graph, roots, prose, output):
    if output.exists():
        raise ValueError('Output exists.')
    refs = set(re.findall(r'\[([a-f0-9]{12,64})\]|^>([a-f0-9]{12,64})\|', prose, re.M))
    prefixes = {a or b for a, b in refs}
    with sqlite3.connect(f'{graph.resolve().as_uri()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute('SELECT n.*,s.artifact_path,s.sha256 FROM nodes n JOIN sources s ON n.source_id=s.id')]
    for prefix in prefixes:
        if sum(r['id'].startswith(prefix) for r in rows) != 1:
            raise ValueError(f'Unresolved or ambiguous source: {prefix}')
    selected = [r for r in rows if any(r['id'].startswith(p) for p in prefixes) or r['kind'] == 'board']
    records = {}
    labels = {'message': 'A message to its named recipient; not shared knowledge.',
              'decision': 'Saved returned rationale; not hidden provider reasoning or proof of execution.',
              'board': 'Board/adjudication record; use settled ownership for center gains.',
              'reflection': 'Saved retrospective account by this player.',
              'input_memory': 'Memory section evidenced in a saved prompt; not proof of subsequent use.'}
    for row in selected:
        root = roots[row['experiment_id']].resolve()
        path = (root / row['artifact_path']).resolve()
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Missing, changed or unsafe original source.')
        records[row['id']] = {k: row[k] for k in ['kind','title','text','experiment_id','game_id','artifact_path','pointer','sha256']}
        records[row['id']].update(context=labels.get(row['kind'], 'Selected original source extract.'),
                                 raw=json.dumps({'kind':row['kind'],'extract':row['text']},ensure_ascii=False))
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(records,ensure_ascii=False)+'\n',encoding='utf-8')
    return {'records':len(records),'verified_sources':len({(r['experiment_id'],r['artifact_path']) for r in selected})}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--graph',type=Path,required=True)
    p.add_argument('--experiment-root',action='append',required=True,help='experiment-id=original-root')
    p.add_argument('--episodes',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(export(a.graph,dict((x.split('=',1)[0],Path(x.split('=',1)[1])) for x in a.experiment_root),a.episodes.read_text(encoding='utf-8-sig'),a.output)))
