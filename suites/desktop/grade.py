"""Sealed behavior checks. Only the trusted scorer imports this file after both workers stop."""
from copy import deepcopy
from bench.guard import HostBusy


def add(id='a', key='a', rev=0, priority=1, title=' Work '):
    return dict(op='add', key=key, expectedRev=rev, id=id, title=title, priority=priority)


def mutation(op, id='a', key=None, rev=1, **extra):
    return dict(op=op, id=id, key=key or op, expectedRev=rev, **extra)


def checks(run, frontend):
    """Callbacks execute submitted artifacts in a fresh sandbox, without revealing tests to workers."""
    def call(*requests, snapshot=None):
        data = {'requests': list(requests)}
        if snapshot is not None:
            data['snapshot'] = snapshot
        return run(data)

    def reply(*requests):
        return call(*requests)['replies'][-1]

    def error(code, *requests):
        return reply(*requests) == {'ok': False, 'error': code}

    def normalization():
        v = call(add(title='  雨 <queue>  '))
        return v['state']['jobs'] == [dict(id='a', title='雨 <queue>', priority=1, status='queued', seq=1)]

    def order():
        v = reply(add('a'), add('b', 'b', 1, 3), add('c', 'c', 2, 3), {'op': 'list'})
        return [j['id'] for j in v['jobs']] == ['b', 'c', 'a'] and v['rev'] == 3

    def replay_key():
        r = add()
        v = call(r, mutation('start'), r)
        return v['replies'][2] == v['replies'][0] and v['state']['rev'] == 2 and v['state']['jobs'][0]['status'] == 'running'

    def transitions():
        v = call(add(), mutation('start'), mutation('finish', rev=2), mutation('retry', rev=3))
        j = v['state']['jobs'][0]
        return j['status'] == 'queued' and j['seq'] == 1 and v['state']['rev'] == 4

    def cancel():
        return reply(add(), mutation('start'), mutation('cancel', rev=2))['job']['status'] == 'cancelled'

    def failed_key():
        bad = mutation('finish', key='free')
        good = mutation('start', key='free')
        v = call(add(), bad, good)
        return v['replies'][1]['error'] == 'STATE' and v['replies'][2]['ok'] and v['state']['rev'] == 2

    def priority():
        v = reply(add(), add('b', 'b', 1, 3), mutation('reprioritize', rev=2, priority=3), {'op': 'list'})
        return [j['id'] for j in v['jobs']] == ['a', 'b']

    def events():
        v = reply(add(), mutation('start'), mutation('finish', rev=2), {'op': 'events', 'after': 1, 'limit': 1})
        return v == {'ok': True, 'rev': 3, 'events': [{'rev': 2, 'op': 'start', 'id': 'a'}]}

    def snapshot():
        v = call(add(), mutation('start'))
        r = call(mutation('finish', rev=2), snapshot=v['state'])
        return r['state']['rev'] == 3 and r['state']['jobs'][0]['status'] == 'done' and r['state']['events'][:2] == v['state']['events']

    def invalid_snapshot():
        valid = call(add())['state']
        variants = []
        for field, value in [('rev', -1), ('events', []), ('seen', []), ('jobs', [valid['jobs'][0]] * 2)]:
            s = deepcopy(valid)
            s[field] = value
            variants.append(s)
        s = deepcopy(valid)
        s['jobs'][0]['status'] = 'unknown'
        variants.append(s)
        return all(call(snapshot=s) == {'error': 'INVALID_SNAPSHOT'} for s in variants)

    def invalid_add():
        variants = [add(id='A!'), add(title=' '), add(title='雨' * 81), add(priority=True), add(priority=4)]
        return all(error('BAD_REQUEST', r) for r in variants)

    def query():
        v = reply(add(), mutation('cancel'), {'op': 'list', 'status': 'queued'})
        return v == {'ok': True, 'rev': 2, 'jobs': []}

    def integration():
        v = reply(add('low'), add('high', 'high', 1, 3, '<danger>&'), {'op': 'list'})
        return frontend(v)

    return {
        'normalize': normalization,
        'priority_fifo': order,
        'revision_conflict': lambda: error('CONFLICT', add(), mutation('start', rev=0)),
        'successful_key_replay': replay_key,
        'key_reuse': lambda: error('KEY_REUSED', add(), add(title='Different')),
        'duplicate_id': lambda: error('EXISTS', add(), add(key='other', rev=1)),
        'invalid_add': invalid_add,
        'transition_cycle': transitions,
        'cancel_running': cancel,
        'only_one_running': lambda: error('BUSY', add(), add('b', 'b', 1), mutation('start', rev=2), mutation('start', 'b', 'b-start', 3)),
        'queue_order_enforced': lambda: error('ORDER', add(), add('b', 'b', 1, 3), mutation('start', rev=2)),
        'invalid_transition': lambda: error('STATE', add(), mutation('finish')),
        'failed_key_not_reserved': failed_key,
        'reprioritize': priority,
        'event_cursor_limit': events,
        'status_query': query,
        'invalid_query': lambda: error('BAD_REQUEST', {'op': 'events', 'after': 0, 'limit': 0}),
        'missing_job': lambda: error('NOT_FOUND', mutation('start', rev=0)),
        'snapshot_recovery': snapshot,
        'snapshot_rejection': invalid_snapshot,
        'rust_bridge_reducer_react': integration,
    }


def score(run, frontend):
    outcomes = {}
    for name, check in checks(run, frontend).items():
        try:
            outcomes[name] = bool(check())
        except HostBusy:
            raise
        except Exception:
            outcomes[name] = False
    return {'score': sum(outcomes.values()) / len(outcomes), 'checks': outcomes}
