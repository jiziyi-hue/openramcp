"""小队右下→右上, 大部队 8s 后右上"""
from mcp_server.transport import OpenRATransport
import math, time

t = OpenRATransport(); t.connect()
r = t.send_command({'type': 'get_state', 'include_enemies': False})
mob = sorted([u['id'] for u in r['state']['self_units'] if u['kind'] in ('apc', '1tnk')])
print(f'mob={len(mob)}')
small = mob[:20]
big = mob[20:]
print(f'small={len(small)} big={len(big)}')

t.send_command({'type': 'cancel_squad'})
time.sleep(0.3)

# Step 1: 小队 → 右下 (78, 85), 大部队不动 (no squad)
print('\nstep 1: 小队 → 右下 (78, 85)')
r = t.send_command({'type': 'spawn_squad', 'squad_type': 'Assault',
                    'unit_ids': small, 'target_pos': {'x': 78, 'y': 85}})
print(f'  ok={r.get("ok")}')

# 等小队到右下
for sec in range(60):
    time.sleep(2)
    s = t.send_command({'type': 'get_state', 'include_enemies': False})
    units = {u['id']: u for u in s['state']['self_units']}
    alive = [units[i] for i in small if i in units]
    if not alive: break
    cx = sum(u['pos']['x'] for u in alive)/len(alive)
    cy = sum(u['pos']['y'] for u in alive)/len(alive)
    d = math.hypot(cx-78, cy-85)
    if d < 6:
        print(f'  小队到 ({cx:.1f},{cy:.1f}) dist={d:.1f}')
        break

# Step 2: 小队 → 右上 (78, 8), 启动 t_dispatch_small
print('\nstep 2: 小队 → 右上 (78, 8)')
t.send_command({'type': 'cancel_squad'})
time.sleep(0.3)
r = t.send_command({'type': 'spawn_squad', 'squad_type': 'Assault',
                    'unit_ids': small, 'target_pos': {'x': 78, 'y': 8}})
print(f'  小队 spawn ok={r.get("ok")}')
t_small = time.time()

# Step 3: 8s 后 大部队 → 右上
print('\nstep 3: 等 8s 后大部队 → 右上 (78, 8)')
time.sleep(8.0)
r = t.send_command({'type': 'spawn_squad', 'squad_type': 'Assault',
                    'unit_ids': big, 'target_pos': {'x': 78, 'y': 8}})
delay = time.time() - t_small
print(f'  大部队 spawn delay={delay:.2f}s ok={r.get("ok")}')
