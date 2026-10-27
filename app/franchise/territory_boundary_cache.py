"""Bounded public-boundary cache; unavailable publishers never become invented polygons."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import os
import tempfile


def cached_boundary_json(fetch, url, params, root):
    root=Path(root)
    identity=json.dumps([url,params],sort_keys=True,separators=(',',':'))
    key=hashlib.sha256(identity.encode()).hexdigest()
    path=root/(key+'.json')
    def checked_copy(max_age):
        try:
            if path.stat().st_size>13000000:raise ValueError('Oversized boundary cache')
            saved=json.loads(path.read_text(encoding='utf-8'))
            body=saved['body']
            age=datetime.now(timezone.utc)-datetime.fromisoformat(saved['stored_at'])
            checksum=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if saved['identity']!=identity or checksum!=saved['sha256'] or not timedelta(0)<=age<=max_age:
                raise ValueError('Boundary cache failed source/date checks')
            return {**body,'_boundary_cache':saved['stored_at']}
        except (OSError,ValueError,KeyError,TypeError):
            return None

    # A warm request must not wait for an unavailable publisher. The identity,
    # checksum and source date checks apply equally to fresh and fallback copies.
    fresh = checked_copy(timedelta(days=1))
    if fresh is not None:
        return fresh
    try:
        result=fetch(url,params)
        if result.get('error'):raise RuntimeError('Boundary publisher query failed')
    except Exception:
        fallback = checked_copy(timedelta(days=30))
        if fallback is not None:
            return fallback
        raise RuntimeError('Boundary publisher unavailable and no recent checked copy is available') from None
    try:
        root.mkdir(parents=True,exist_ok=True)
        encoded=json.dumps(result,sort_keys=True,separators=(',',':'))
        if len(encoded.encode())<=12000000 and (path.exists() or len(list(root.glob('*.json')))<64):
            saved=dict(identity=identity,stored_at=datetime.now(timezone.utc).isoformat(),body=result,
                sha256=hashlib.sha256(encoded.encode()).hexdigest())
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=root,suffix='.tmp',delete=False) as tmp:
                json.dump(saved,tmp);temporary=tmp.name
            os.replace(temporary,path)
    except OSError:pass  # Cache failure must not discard a working publisher response.
    return result
