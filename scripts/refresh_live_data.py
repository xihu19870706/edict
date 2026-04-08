#!/usr/bin/env python3
"""
Live status 刷新脚本（快速版）

只做本地文件读写，不调用任何 CLI subprocess。
完全不依赖 OpenClaw gateway / runtime 状态。
"""
import json, pathlib, datetime, logging, sys

BASE = pathlib.Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

DATA = BASE / 'data'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [refresh] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('refresh')


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
    except Exception:
        return default


def output_meta(path):
    if not path:
        return {"exists": False, "lastModified": None}
    p = pathlib.Path(path)
    if not p.exists():
        return {"exists": False, "lastModified": None}
    ts = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
    return {"exists": True, "lastModified": ts}


def main():
    officials_data = read_json(DATA / 'officials_stats.json', {})
    officials = officials_data.get('officials', []) if isinstance(officials_data, dict) else officials_data

    tasks = read_json(DATA / 'tasks_source.json', [])
    if not tasks:
        tasks = read_json(DATA / 'tasks.json', [])

    sync_status = read_json(DATA / 'sync_status.json', {})
    if not isinstance(sync_status, dict):
        sync_status = {}

    org_map = {o.get('label', o.get('name', '')): o.get('label', '') for o in officials if o.get('label')}

    now_ts = datetime.datetime.now(datetime.timezone.utc)

    for t in tasks:
        t['org'] = t.get('org') or org_map.get(t.get('official', ''), '')
        t['outputMeta'] = output_meta(t.get('output', ''))

        if t.get('state') in ('Doing', 'Assigned', 'Review'):
            updated_raw = t.get('updatedAt') or (t.get('sourceMeta') or {}).get('updatedAt')
            age_sec = None
            if updated_raw:
                try:
                    if isinstance(updated_raw, (int, float)):
                        updated_dt = datetime.datetime.fromtimestamp(updated_raw / 1000, tz=datetime.timezone.utc)
                    else:
                        updated_dt = datetime.datetime.fromisoformat(str(updated_raw).replace('Z', '+00:00'))
                    age_sec = (now_ts - updated_dt).total_seconds()
                except Exception:
                    pass
            if age_sec is None:
                t['heartbeat'] = {'status': 'unknown', 'label': '⚪ 未知', 'ageSec': None}
            elif age_sec < 300:
                t['heartbeat'] = {'status': 'active', 'label': f'🟢 活跃 {int(age_sec//60)}分钟前', 'ageSec': int(age_sec)}
            elif age_sec < 900:
                t['heartbeat'] = {'status': 'warn', 'label': f'🟡 可能停滞 {int(age_sec//60)}分钟前', 'ageSec': int(age_sec)}
            else:
                t['heartbeat'] = {'status': 'stalled', 'label': f'🔴 已停滞 {int(age_sec//60)}分钟', 'ageSec': int(age_sec)}
        else:
            t['heartbeat'] = None

    today_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
    total_done = sum(1 for t in tasks if t.get('state') == 'Done')
    in_progress = sum(1 for t in tasks if t.get('state') in ['Doing', 'Review', 'Next', 'Blocked', 'Assigned', 'Menxia', 'Zhongshu'])
    blocked = sum(1 for t in tasks if t.get('state') == 'Blocked')

    payload = {
        'generatedAt': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'taskSource': 'tasks_source.json',
        'officials': officials,
        'tasks': tasks,
        'history': [],
        'metrics': {
            'officialCount': len(officials),
            'todayDone': 0,
            'totalDone': total_done,
            'inProgress': in_progress,
            'blocked': blocked,
        },
        'syncStatus': {
            'ok': True,
            'source': 'tasks_source_json',
            'lastSyncAt': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'durationMs': 0,
            'recordCount': len(tasks),
            'missingFields': {},
            'error': None,
        },
        'health': {
            'syncOk': True,
            'syncLatencyMs': 0,
            'missingFieldCount': 0,
        },
    }

    out_path = DATA / 'live_status.json'
    tmp_path = DATA / ('.live_status_tmp_' + str(datetime.datetime.now().timestamp()) + '.json')
    try:
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp_path.rename(out_path)
        log.info(f'updated live_status.json ({len(tasks)} tasks)')
    except Exception as e:
        log.error(f'写入失败: {e}')

if __name__ == '__main__':
    main()
