"""Monitor one explicitly named validation child; never touch unrelated processes.

Persistent elapsed-time ledger includes startup and final audit. A cooperative
stop request precedes the hard limit; a killed child is inconclusive, not passed.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

import psutil

ROOT=Path(__file__).resolve().parents[1]


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.replace(path)


def size(paths):
    return sum(p.stat().st_size for root in paths if root.exists() for p in
               ([root] if root.is_file() else root.rglob('*')) if p.is_file() and not p.is_symlink())


def monitor(command,*,ledger,report,log,seconds,total_seconds=7200,memory_bytes=2*1024**3,
            storage_bytes=8*1024**3,paths=(),stop_file=None,grace_seconds=30):
    ledger,report,log=map(Path,(ledger,report,log));paths=list(map(Path,paths))
    for path in [ledger,report,log,*paths,*([Path(stop_file)] if stop_file else [])]:
        if not path.resolve().is_relative_to(ROOT): raise ValueError('Validation paths must stay in this workspace')
    if seconds<=0 or seconds>7200 or not 0<total_seconds<=7200:raise ValueError('Invalid finite validation budget')
    state=json.loads(ledger.read_text(encoding='utf-8')) if ledger.exists() else {'limit_seconds':total_seconds,'runs':[]}
    if state['limit_seconds']!=total_seconds or any(r['status']=='running' for r in state['runs']):raise ValueError('Unfinished/different budget ledger')
    consumed=sum(r['seconds'] for r in state['runs'])
    cap=min(seconds,total_seconds-consumed)
    if cap<=0:raise ValueError('Total validation budget exhausted')
    if report.exists() or log.exists():raise ValueError('Use a new report/log; old evidence is immutable')
    row={'status':'running','seconds':0,'cap_seconds':cap,'report':str(report.relative_to(ROOT)),
         'scope':'child process/resource boundary only; inspect child evidence before claiming validation passed',
         'command':command,'peak_private_bytes':0,'peak_threads':0,'peak_storage_bytes':0}
    state['runs'].append(row);write(ledger,state)
    begun=time.monotonic();reason=None;last=-30;child=None
    log.parent.mkdir(parents=True,exist_ok=True)
    try:
        with log.open('xb') as stream:
            child=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT,cwd=ROOT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            process=psutil.Process(child.pid)
            try:process.cpu_affinity(process.cpu_affinity()[:2])
            except (AttributeError,psutil.Error):pass
            while child.poll() is None:
                elapsed=time.monotonic()-begun
                children=[process]
                try:
                    children=[process,*process.children(recursive=True)]
                    private=sum(getattr(p.memory_info(),'private',p.memory_info().rss) for p in children)
                    threads=sum(p.num_threads() for p in children)
                except psutil.Error:private=threads=0
                disk=size(paths)
                row['peak_private_bytes']=max(row['peak_private_bytes'],private)
                row['peak_threads']=max(row['peak_threads'],threads)
                row['peak_storage_bytes']=max(row['peak_storage_bytes'],disk)
                reason=('memory_cap' if private>memory_bytes else 'storage_cap' if disk>storage_bytes
                        else 'wall_cap' if elapsed>=cap else reason)
                if stop_file and (reason or elapsed>=cap-min(grace_seconds,cap/3)):
                    marker=Path(stop_file)
                    if marker.parent.exists():marker.write_text('bounded validation stop\n',encoding='utf-8')
                if reason:
                    for p in reversed(children):
                        try:p.terminate()
                        except psutil.Error:pass
                    try:child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        for p in reversed(children):
                            try:p.kill()
                            except psutil.Error:pass
                        child.wait(timeout=10)
                    break
                if elapsed-last>=30:
                    last=elapsed;row['seconds']=round(elapsed,3);write(ledger,state)
                    print(json.dumps({'phase':report.stem,'elapsed':round(elapsed,1),'private_mib':round(private/1024**2,1)}),flush=True)
                time.sleep(.25)
        row.update(status='inconclusive' if reason else 'passed' if child.returncode==0 else 'failed',
                   seconds=round(time.monotonic()-begun,3),reason=reason,exit_code=child.returncode)
    finally:
        if child and child.poll() is None:
            child.terminate();child.wait(timeout=10)
        if row['status']=='running':row.update(status='inconclusive',reason='monitor_exception',seconds=round(time.monotonic()-begun,3))
        write(ledger,state);write(report,row)
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('ledger','report','log'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--seconds',type=float,required=True);p.add_argument('--total-seconds',type=float,default=7200)
    p.add_argument('--path',action='append',default=[],type=Path);p.add_argument('--stop-file',type=Path)
    p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    command=a.command[1:] if a.command[:1]==['--'] else a.command
    if not command:p.error('Child command required')
    row=monitor(command,ledger=a.ledger.resolve(),report=a.report.resolve(),log=a.log.resolve(),seconds=a.seconds,
                total_seconds=a.total_seconds,paths=[r.resolve() for r in a.path],stop_file=a.stop_file)
    print(json.dumps(row),flush=True)
    raise SystemExit(0 if row['status']=='passed' else 2)


if __name__=='__main__':main()
