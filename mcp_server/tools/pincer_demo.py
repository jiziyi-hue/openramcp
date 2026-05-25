"""钳形: 右中集合 → 上下两队夹中央建筑"""
from mcp_server.transport import OpenRATransport
import math, time

t = OpenRATransport(); t.connect()

# step 1: 全员右中集合
print('\nstep 1: 全员推 (78, 46) 右中集合')
t.send_command({'type': 'cancel_squad'})
time.sleep(0.3)
r = t.send_command({'type': 'spawn_squad', 'squad_type': 'Assault',
                    'target_pos': {'x': 78, 'y': 46}})
print(f'  unit_count={r.get("unit_count")}')

# 等到位
for sec in range(50):
    time.sleep(2)
    s = t.send_command({'type': 'get_state', 'include_enemies': False})
    mob = [u for u in s['state']['self_units'] if u['kind'] in ('apc','1tnk')]
    if not mob: break
    cx = sum(u['pos']['x'] for u in mob)/len(mob)
    cy = sum(u['pos']['y'] for u in mob)/len(mob)
    d = math.hypot(cx-78, cy-46)
    if d < 6:
        print(f'  到位 ({cx:.1f},{cy:.1f}) dist={d:.1f}')
        break

# step 2: 拆 50/50 钳形 → 中央建筑 (35, 36)
# 上臂北接近 (35, 30), 下臂南接近 (35, 42)
print('\nstep 2: 钳形 → 中央建筑 (35, 36)')
s = t.send_command({'type': 'get_state', 'include_enemies': False})
mob = sorted([u for u in s['state']['self_units'] if u['kind'] in ('apc','1tnk')],
             key=lambda u: u['pos']['y'])
half = len(mob)//2
top = [u['id'] for u in mob[:half]]
bot = [u['id'] for u in mob[half:]]
print(f'  top {len(top)} → (35,30) 北接近')
print(f'  bot {len(bot)} → (35,42) 南接近')

t.send_command({'type': 'cancel_squad'})
time.sleep(0.3)
r = t.send_command({'type': 'spawn_squad_batch', 'squads': [
    {'type': 'spawn_squad', 'squad_type': 'Assault',
     'unit_ids': top, 'target_pos': {'x': 35, 'y': 30}},
    {'type': 'spawn_squad', 'squad_type': 'Assault',
     'unit_ids': bot, 'target_pos': {'x': 35, 'y': 42}},
]})
print(f'  pincer batch ok={r.get("ok")}')
