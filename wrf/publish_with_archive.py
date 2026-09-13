#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def run(*args, check=True, cwd=None, capture=False):
    result = subprocess.run(
        list(args),
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        raise RuntimeError(f"Comando falhou ({result.returncode}): {' '.join(args)}\n{detail}")
    return result


def parse_time(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


def run_id(meta):
    date = str(meta.get('runDate') or '').replace('-', '')
    cycle = ''.join(ch for ch in str(meta.get('runCycle') or '') if ch.isdigit()).zfill(2)
    if len(date) == 8 and cycle:
        return f"{date}{cycle[:2]}"
    stamp = parse_time(meta.get('initTime'))
    if stamp:
        return stamp.strftime('%Y%m%d%H')
    raise RuntimeError('Nao foi possivel identificar a rodada pelo metadata.json')


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def git_ref_exists(ref):
    return run('git', 'rev-parse', '--verify', ref, check=False, capture=True).returncode == 0


def archive_paths(ref, paths, destination):
    paths = [p for p in paths if p and '..' not in Path(p).parts and not str(p).startswith('/')]
    if not paths:
        return False
    destination.mkdir(parents=True, exist_ok=True)
    proc1 = subprocess.Popen(['git', 'archive', ref, *paths], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc2 = subprocess.Popen(['tar', '-x', '-C', str(destination)], stdin=proc1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc1.stdout.close()
    _, tar_err = proc2.communicate()
    _, git_err = proc1.communicate()
    if proc1.returncode != 0 or proc2.returncode != 0:
        shutil.rmtree(destination, ignore_errors=True)
        destination.mkdir(parents=True, exist_ok=True)
        return False
    return True


def copy_previous_latest(ref, destination):
    meta_result = run('git', 'show', f'{ref}:metadata.json', check=False, capture=True)
    if meta_result.returncode != 0:
        return None
    try:
        meta = json.loads(meta_result.stdout)
    except Exception:
        return None
    paths = ['metadata.json']
    for frame in meta.get('frames') or []:
        file_name = frame.get('file') if isinstance(frame, dict) else None
        if file_name:
            paths.append(file_name)
    if not archive_paths(ref, paths, destination):
        return None
    return meta


def build_index(history, retention_hours):
    rows = []
    for folder in history.iterdir() if history.exists() else []:
        if not folder.is_dir():
            continue
        meta_path = folder / 'metadata.json'
        try:
            meta = load_json(meta_path)
        except Exception:
            continue
        rows.append({
            'id': folder.name,
            'runDate': meta.get('runDate'),
            'runCycle': meta.get('runCycle'),
            'initTime': meta.get('initTime'),
            'generatedAt': meta.get('generatedAt'),
            'frameCount': meta.get('frameCount'),
            'reflectivitySource': meta.get('reflectivitySource'),
            'resolutionKm': meta.get('resolutionKm'),
            'metadata': f'runs/{folder.name}/metadata.json',
            'basePath': f'runs/{folder.name}',
        })
    rows.sort(key=lambda item: item.get('initTime') or '', reverse=True)
    return {
        'schema': 'sideral-wrf-run-index-v1',
        'retentionHours': retention_hours,
        'latest': rows[0]['id'] if rows else None,
        'runs': rows,
    }


def main():
    parser = argparse.ArgumentParser(description='Publica WRF mantendo historico recente por rodada.')
    parser.add_argument('--source', required=True)
    parser.add_argument('--branch', required=True)
    parser.add_argument('--readme-title', default='Sideral WRF Data')
    parser.add_argument('--readme-text', default='Dados WRF publicados automaticamente.')
    parser.add_argument('--retention-hours', type=int, default=96)
    parser.add_argument('--max-runs', type=int, default=8)
    args = parser.parse_args()

    source = Path(args.source).resolve()
    meta_path = source / 'metadata.json'
    if not meta_path.is_file():
        raise SystemExit(f'metadata.json ausente em {source}')
    current_meta = load_json(meta_path)
    current_id = run_id(current_meta)

    run('git', 'config', 'user.name', 'sideral-wrf-bot')
    run('git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')

    remote_ref = f'refs/remotes/origin/{args.branch}'
    run('git', 'fetch', 'origin', f'{args.branch}:{remote_ref}', check=False)

    with tempfile.TemporaryDirectory(prefix='sideral-wrf-publish-') as temp_dir:
        temp = Path(temp_dir)
        history = temp / 'history' / 'runs'
        history.mkdir(parents=True, exist_ok=True)

        if git_ref_exists(remote_ref):
            existing_runs = temp / 'existing-runs'
            if archive_paths(remote_ref, ['runs'], existing_runs):
                archived = existing_runs / 'runs'
                if archived.exists():
                    for item in archived.iterdir():
                        target = history / item.name
                        if target.exists():
                            shutil.rmtree(target, ignore_errors=True)
                        shutil.move(str(item), str(target))

            previous_root = temp / 'previous-root'
            previous_meta = copy_previous_latest(remote_ref, previous_root)
            if previous_meta:
                previous_id = run_id(previous_meta)
                previous_target = history / previous_id
                if not previous_target.exists():
                    shutil.copytree(previous_root, previous_target)
                    print('Rodada anterior arquivada:', previous_id)

        current_target = history / current_id
        if current_target.exists():
            shutil.rmtree(current_target)
        shutil.copytree(source, current_target)
        print('Rodada atual arquivada:', current_id)

        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=max(1, args.retention_hours))
        candidates = []
        for folder in list(history.iterdir()):
            if not folder.is_dir():
                continue
            try:
                meta = load_json(folder / 'metadata.json')
                stamp = parse_time(meta.get('initTime'))
            except Exception:
                stamp = None
            if stamp is None or stamp < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
                print('Rodada expirada removida:', folder.name)
                continue
            candidates.append((stamp, folder))

        candidates.sort(key=lambda item: item[0], reverse=True)
        for _, folder in candidates[max(1, args.max_runs):]:
            shutil.rmtree(folder, ignore_errors=True)
            print('Rodada excedente removida:', folder.name)

        target = temp / 'worktree'
        run('git', 'worktree', 'add', '--detach', str(target))
        try:
            run('git', 'checkout', '--orphan', f'{args.branch}-next', cwd=target)
            run('git', 'rm', '-rf', '.', check=False, cwd=target)
            run('git', 'clean', '-fdx', check=False, cwd=target)

            for item in source.iterdir():
                dest = target / item.name
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
            shutil.copytree(history, target / 'runs')

            index = build_index(target / 'runs', args.retention_hours)
            (target / 'runs.json').write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')

            root_meta = load_json(target / 'metadata.json')
            root_meta['archiveIndex'] = 'runs.json'
            root_meta['archiveRetentionHours'] = args.retention_hours
            root_meta['archivedRunCount'] = len(index['runs'])
            (target / 'metadata.json').write_text(json.dumps(root_meta, ensure_ascii=False, indent=2), encoding='utf-8')

            (target / 'README.md').write_text(
                f'# {args.readme_title}\n{args.readme_text}\n\nRodadas recentes: `runs.json` e `runs/<YYYYMMDDHH>/`.\n',
                encoding='utf-8',
            )

            run('git', 'add', '.', cwd=target)
            status = run('git', 'status', '--porcelain', cwd=target, capture=True)
            if not status.stdout.strip():
                print('Nenhuma alteracao para publicar.')
                return
            run('git', 'commit', '-m', f'{args.branch}: publica e arquiva {current_id} [skip render]', cwd=target)
            run('git', 'push', '--force', 'origin', f'HEAD:refs/heads/{args.branch}', cwd=target)
            print('PUBLICADO:', args.branch, 'rodadas=', [row['id'] for row in index['runs']])
        finally:
            run('git', 'worktree', 'remove', '--force', str(target), check=False)


if __name__ == '__main__':
    main()
