"""Snapshot audit inputs without modifying the production database or exposing keys."""
from pathlib import Path
import hashlib, json, os, sqlite3, sys, yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get('HANSER_AUDIT_DIR', str(ROOT / 'audit_artifacts' / 'phase6'))).resolve()

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    config = yaml.safe_load((ROOT / 'backend/config.yml').read_text(encoding='utf-8'))
    data = config['data']
    for key, value in data.items():
        data[key] = str((ROOT / 'backend' / value).resolve())
    source = Path(data['db_path'])
    target = OUT / 'audit.db'
    if target.exists():
        raise SystemExit('Audit snapshot already exists; use it or select a new run directory.')
    with sqlite3.connect(f'file:{source.as_posix()}?mode=ro', uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
        tables = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        counts = {t: src.execute('SELECT count(*) FROM "'+t+'"').fetchone()[0] for t in tables}
    data['db_path'] = str(target)
    def redact(obj):
        if isinstance(obj, dict):
            return {k: ('audit-placeholder' if 'api_key' in k else redact(v)) for k,v in obj.items()}
        if isinstance(obj,list): return [redact(v) for v in obj]
        return obj
    safe = redact(config)
    (OUT / 'isolated.yml').write_text(yaml.safe_dump(safe, allow_unicode=True),encoding='utf-8')
    files = [p for p in ROOT.rglob('*') if p.is_file() and (p.suffix in {'.py','.md','.yml','.yaml','.cs','.json'} or p.name=='requirements.txt') and not any(x in p.parts for x in ['.venv','__pycache__','audit_artifacts']) and p.name!='config.yml']
    inventory = [{'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
    meta = {'source_db':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'table_counts':counts,'python':sys.version,'files':inventory}
    (OUT / 'snapshot.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'counts':counts,'files':len(files),'snapshot':str(target)},ensure_ascii=False))

if __name__=='__main__': main()
