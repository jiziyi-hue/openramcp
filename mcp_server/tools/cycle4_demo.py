"""4-squad cycle: 顺时针循环 4 corners"""
from mcp_server.transport import OpenRATransport
import math, time

corners = [(8, 8), (78, 8), (78, 85), (8, 85)]  # NW, NE, SE, SW
ARRIVAL = 6
DURATION = 180  # seconds

t = OpenRATransport()
t.connect()

# 等当前 4 队到位 — 它们各自在 corner. 起始 cursor 各队该 corner 的索引
r = t.send_command({'type': 'list_squads'})
squads_info = r.get('squads', [])
print(f'squads now: {len(squads_info)}')
if len(squads_info) < 4:
    raise SystemExit(f'need 4 squads, got {len(squads_info)}')

# Map current squad → starting corner by centroid
state = t.send_command({'type': 'get_state', 'include_enemies': False})
units = {u['id']: u for u in state['state']['self_units']}

squads = []
for s in squads_info[:4]:
    ids = s['unit_ids']
    alive = [units[i] for i in ids if i in units]
    if not alive:
        continue
    cx = sum(u['pos']['x'] for u in alive) / len(alive)
    cy = sum(u['pos']['y'] for u in alive) / len(alive)
    # find nearest corner
    best = min(range(4), key=lambda k: math.hypot(cx - corners[k][0], cy - corners[k][1]))
    squads.append({'unit_ids': ids, 'cursor': best, 'centroid': (cx, cy)})
    print(f"  sq{s['squad_index']}: centroid=({cx:.1f},{cy:.1f}) → corner {best} {corners[best]}")

started = time.time()
while time.time() - started < DURATION:
    time.sleep(2)
    state = t.send_command({'type': 'get_state', 'include_enemies': False})
    if not state.get('ok'):
        continue
    units = {u['id']: u for u in state['state']['self_units']}
    to_rebatch = []
    for i, sq in enumerate(squads):
        alive = [units[u] for u in sq['unit_ids'] if u in units]
        if not alive:
            continue
        cx = sum(u['pos']['x'] for u in alive) / len(alive)
        cy = sum(u['pos']['y'] for u in alive) / len(alive)
        wp = corners[sq['cursor']]
        if math.hypot(cx - wp[0], cy - wp[1]) <= ARRIVAL:
            sq['cursor'] = (sq['cursor'] + 1) % 4
            to_rebatch.append(i)
            print(f"  sq{i} 到 {wp}, 下个 → {corners[sq['cursor']]}")
    if to_rebatch:
        t.send_command({'type': 'cancel_squad'})
        time.sleep(0.2)
        payloads = [
            {'type': 'spawn_squad', 'squad_type': 'Assault',
             'unit_ids': sq['unit_ids'],
             'target_pos': {'x': corners[sq['cursor']][0], 'y': corners[sq['cursor']][1]}}
            for sq in squads
        ]
        r = t.send_command({'type': 'spawn_squad_batch', 'squads': payloads})
        print(f'  rebatch ok={r.get("ok")}')

print(f'\n=== done {DURATION}s ===')
