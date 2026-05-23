# RA mod actor 名速查

> Claude 端用. 玩家 NL ("建发电厂") → MCP `build(structure='Powr')`.
> 完整定义见 `OpenRA/mods/ra/rules/{structures,infantry,vehicles,aircraft,ships}.yaml`.
> 大小写不敏感 (trait `Equals(StringComparison.OrdinalIgnoreCase)`).

---

## 建筑 (build)

### 基础
| 中/英 | actor name |
|---|---|
| 建造场 / Construction Yard | `fact` |
| 发电厂 / Power Plant | `powr` |
| 高级发电厂 / Advanced Power Plant | `apwr` |
| 矿场 / Refinery | `proc` |
| 矿场仓库 / Storage Silo | `silo` |
| 雷达 / Radar Dome | `dome` |
| 维修厂 / Service Depot | `fix` |

### 生产
| 中/英 | actor name |
|---|---|
| 兵营 (盟) / Barracks Allied | `barr` |
| 兵营 (苏) / Barracks Soviet | `tent` |
| 战车工厂 / War Factory | `weap` |
| 直升机坪 / Helipad | `hpad` |
| 机场 / Airfield Soviet | `afld` |
| 海军船坞 (盟) / Naval Yard | `syrd` |
| 潜艇船坞 (苏) / Sub Pen | `spen` |

### 防御
| 中/英 | actor name |
|---|---|
| 步兵掩体 / Pillbox | `pbox` |
| 伪装掩体 / Camo Pillbox | `hbox` |
| 反车炮塔 / Turret | `gun` |
| 防空炮 / AA Gun | `agun` |
| 防空导弹 / SAM Site | `sam` |
| 火焰塔 / Flame Tower (苏) | `ftur` |
| 特斯拉线圈 / Tesla Coil (苏) | `tsla` |

### 高级
| 中/英 | actor name |
|---|---|
| 科技中心 (盟) / Tech Center | `atek` |
| 科技中心 (苏) / Tech Center | `stek` |
| 核弹井 / Missile Silo (苏) | `mslo` |
| 铁幕 / Iron Curtain (苏) | `iron` |
| 超时空 / Chronosphere (盟) | `pdox` |
| 干扰塔 / Gap Generator (盟) | `gap` |

### 墙/障碍
| 中/英 | actor name |
|---|---|
| 沙袋 / Sandbag | `sbag` |
| 砖墙 / Concrete | `brik` |
| 铁丝网 / Barbed Wire | `barb` |
| 链栏 / Chain Link Fence | `cycl` |

---

## 步兵 (train)

| 中/英 | actor name |
|---|---|
| 步枪兵 / Rifleman | `e1` |
| 掷弹兵 / Grenadier | `e2` |
| 火箭兵 / Rocket Soldier | `e3` |
| 火焰兵 (苏) / Flamethrower | `e4` |
| 工程师 / Engineer | `e6` |
| 谭雅 (盟) / Tanya | `e7` |
| 沃尔科夫 (苏) / Volkov | `vlkv` |
| 卡尔 (苏) | `chan` |
| 间谍 (盟) / Spy | `spy` |
| 小偷 (盟) / Thief | `thf` |
| 医生 (盟) / Medic | `medi` |
| 机械师 (盟) / Mechanic | `mech` |
| 摄影师 / Camera (映入用) | `dog` |
| 警犬 (苏) / Attack Dog | `dog` |

---

## 载具 (train + WarFactory)

| 中/英 | actor name |
|---|---|
| MCV 基地车 | `mcv` |
| 轻型坦克 / Light Tank (盟) | `1tnk` |
| 中型坦克 / Medium Tank (盟) | `2tnk` |
| 重型坦克 / Heavy Tank (苏) | `3tnk` |
| 猛犸坦克 / Mammoth Tank (苏) | `4tnk` |
| 突击者 / Ranger Jeep (盟) | `jeep` |
| 装甲运兵车 / APC | `apc` |
| 布雷车 / Minelayer | `mnly` |
| 采矿车 / Harvester | `harv` |
| V2 火箭 (苏) | `v2rl` |
| 牵引炮 / Artillery (苏) | `arty` |
| 特斯拉坦克 (苏) | `ttnk` |
| 防空车 / Mobile Flak (苏) | `ftrk` |
| 雷达干扰车 (苏) | `mrj` |
| 移动干扰塔 (盟) | `mgg` |
| 炸药卡车 / Demo Truck (苏) | `dtrk` |
| 移动雷达 / MAD Tank (苏) | `qtnk` |

---

## 海军 (train + Naval Yard / Sub Pen)

| 中/英 | actor name |
|---|---|
| 驱逐舰 / Destroyer (盟) | `dd` |
| 巡洋舰 / Cruiser (盟) | `ca` |
| 巡逻艇 / PT Boat | `pt` |
| 潜艇 / Submarine (苏) | `ss` |
| 导弹潜艇 / Missile Sub (苏) | `mssb` |
| 炮艇 / Gunboat | `pt` |
| 运输舰 / Transport | `lst` |

---

## 飞机 (train + Helipad / Airfield)

| 中/英 | actor name |
|---|---|
| 罗刹直升机 (苏) / Hind | `hind` |
| 长弓直升机 (盟) / Apache | `heli` |
| 奇努克运输 / Chinook (盟) | `tran` |
| 雅克战机 (苏) / Yak | `yak` |
| 米格战机 (苏) / Mig | `mig` |
| 间谍机 / Spy Plane (盟) | `u2` |
| 巴顿轰炸机 / Badger (苏 ParaBomb) | `badr` |
| C17 运输机 (盟 paradrop) | `c17` |

---

## 阵营 (faction internal)

| 内部 | 显示 |
|---|---|
| `allies` | Allied Forces |
| `soviet` | Soviet Union |
| `england` | England |
| `france` | France |
| `germany` | Germany |
| `russia` | Russia |
| `ukraine` | Ukraine |

---

## NL → 命令 翻译速查 (我用)

| 玩家说 | 工具调用 |
|---|---|
| "建个发电厂" | `build('powr')` |
| "造 2 个矿场" | `build('proc', count=2)` |
| "出 5 个步兵" | `train('e1', count=5)` |
| "出 3 个坦克" | `train('2tnk', count=3)` (默认中坦) |
| "做台 MCV 部署一下" | `train('mcv') ... [等出来] ... deploy(ids)` |
| "派 X 去 (Y, Z)" | `move(ids, target_x=Y, target_y=Z)` |
| "集火打 X" | `attack(ids, target_id=X)` |
| "全员防御" | `set_stance(all_ids, 'Defend')` |
| "停" / "撤" / "停止" | `stop(ids)` |
| "暂停" | `pause()` |
| "看看现在" | `get_state()` (+ 可选 `screenshot()`) |

OpenRA 名都是 4-letter 内部代号. Trait 用 `Equals(IgnoreCase)` 比对, 所以大小写都行.
