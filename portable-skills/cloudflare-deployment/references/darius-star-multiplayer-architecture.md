# Darius Star — Multiplayer Architecture (GRO-958)

## Overview

Converted a single-player canvas shoot-em-up from one hardcoded ship to 1-4 player drop-in/drop-out co-op. The core insight: **per-player input binding via an `inputKeys` property on each Player instance**, replacing ALL hardcoded key checks.

## Input Binding Pattern

### Problem
The original `Player.update()` checked a global `keys` object with hardcoded literals:
```javascript
if (keys['w'] || keys['W'] || keys['ArrowUp']) dy -= 1;
if (keys[' '] || keys['j'] || keys['J']) this.shoot();
if (keys['Shift']) this.boost();
if (keys['k'] || keys['K']) this.activateSpecial();
if (keys['e'] || keys['E']) this.dodge();
```

This means ALL players share the same physical keys — no multiplayer input separation.

### Solution: `this.inputKeys` per Player
Each `Player` instance gets an `inputKeys` property mapping logical actions to physical keys:

```javascript
constructor(shipType, playerId = 1) {
    this.playerId = playerId;
    this.inputKeys = {
        1: { up:'w', down:'s', left:'a', right:'d', fire:' ', special:'k', dodge:'e', boost:'Shift' },
        2: { up:'ArrowUp', down:'ArrowDown', left:'ArrowLeft', right:'ArrowRight', fire:'0', special:'1', dodge:'2', boost:'Enter' },
        3: { up:'Gamepad1U', down:'Gamepad1D', left:'Gamepad1L', right:'Gamepad1R', fire:'Gamepad1A', special:'Gamepad1B', dodge:'Gamepad1X', boost:'Gamepad1L' },
        4: { up:'Gamepad2U', down:'Gamepad2D', left:'Gamepad2L', right:'Gamepad2R', fire:'Gamepad2A', special:'Gamepad2B', dodge:'Gamepad2X', boost:'Gamepad2L' },
    }[playerId];
}
```

Then replace ALL hardcoded key checks:
```javascript
// BEFORE:
if (keys['w'] || keys['W'] || keys['ArrowUp']) dy -= 1;
// AFTER:
if (keys[this.inputKeys.up]) dy -= 1;

// BEFORE:
if ((keys[' '] || keys['j'] || keys['J']) && this.shootTimer <= 0) this.shoot();
// AFTER:
if (keys[this.inputKeys.fire] && this.shootTimer <= 0) this.shoot();
```

### CRITICAL: Numpad `e.key` Mismatch
`KeyboardEvent.key` returns different values than expected for numpad keys:

| Key pressed | Expected `e.key` | Actual `e.key` |
|---|---|---|
| Numpad0 | `"Numpad0"` | `"0"` |
| Numpad1 | `"Numpad1"` | `"1"` |
| Numpad2 | `"Numpad2"` | `"2"` |
| NumpadEnter | `"NumpadEnter"` | `"Enter"` |

**Always use the ACTUAL `e.key` value in input maps.** The `keys[e.key] = true` handler stores whatever the browser reports, so the input map must match exactly. P2's fire key was initially `'Numpad0'` (broken) — fixed to `'0'`.

**Conflict awareness:** P2's boost key (`'Enter'`) is the same `e.key` as regular Enter. During gameplay this is fine (regular Enter isn't used). During pause menus, NumpadEnter WILL trigger menu actions — acceptable for v1, noted for future.

## Remote Player Integration

### Player Instance Array
The main `player` variable remains as P1 (host). P2-P4 live in `remotePlayers[]`:
```javascript
let player = new Player(initialShip);
let remotePlayers = [];  // P2-P4 ship instances
```

### ResetGame: Create Remote Instances
In `resetGame()`, after creating the main player, sync with the Multiplayer module:
```javascript
remotePlayers = [];
if (window.Multiplayer && Multiplayer.players.length > 1) {
    for (let i = 1; i < Multiplayer.players.length; i++) {
        const mp = Multiplayer.players[i];
        if (!mp.alive) continue;
        const rp = new Player(mp.ship || 'interceptor', mp.id);
        rp.x = mp.x || (80 + i * 40);
        rp.y = mp.y || (180 + i * 30);
        rp.isRemote = true;
        remotePlayers.push(rp);
    }
}
```

### Game Loop Integration
Extend the three critical loops:

**Update loop** — after `player.update(dt)`:
```javascript
for (const rp of remotePlayers) {
    if (!rp.isPulledOut || rp.pullOutTimer > 0) rp.update(dt);
}
```

**Draw loop** — after `player.draw()`:
```javascript
for (const rp of remotePlayers) rp.draw();
```

**Collision loops** — after the main player check in BOTH enemy bullet and enemy collision loops:
```javascript
// Enemy bullet hits remote player
let ebHitRemote = false;
for (const rp of remotePlayers) {
    if (checkCollision(ebBox, rp)) {
        rp.takeDamage(12);
        ebHitRemote = true;
        break;
    }
}
if (ebHitRemote) {
    enemyBullets.splice(i, 1);
    continue;
}

// Enemy body collision with remote player
let enemyHitRemote = false;
for (const rp of remotePlayers) {
    if (checkCollision(e, rp)) {
        rp.takeDamage(20);
        createExplosion(e.x + e.width/2, e.y + e.height/2, e.color, 10);
        enemyHitRemote = true;
        break;
    }
}
if (enemyHitRemote) {
    enemies.splice(i, 1);
    continue;
}
```

## Join-During-Gameplay
P2 joins by pressing Enter/NumpadEnter during gameplay. P3 by pressing Numpad3 (key `'3'`). The join logic runs each frame in the update loop:

```javascript
if (keys['Enter'] && Multiplayer.count < Multiplayer.maxPlayers 
    && !remotePlayers.find(rp => rp.playerId === 2)) {
    Multiplayer.requestJoin('interceptor');
    Multiplayer.processJoins(biomeLevel);
    const rp2 = new Player('interceptor', 2);
    rp2.x = 120; rp2.y = 200;
    rp2.isRemote = true;
    remotePlayers.push(rp2);
}
```

## State Sync: Player Instances ↔ Multiplayer Module
The `Multiplayer` module tracks player state objects. Remote `Player` instances must stay in sync:

```javascript
for (const rp of remotePlayers) {
    const mp = Multiplayer.players.find(p => p.id === rp.playerId);
    if (mp && mp.alive) {
        mp.shield = rp.shield;
        mp.x = rp.x;
        mp.y = rp.y;
        if (rp.isPulledOut && !mp._wasPulledOut) {
            mp._wasPulledOut = true;
            Multiplayer.requestLeave(rp.playerId);
        }
        if (!rp.isPulledOut) mp._wasPulledOut = false;
    }
}
```

## Known Limitations (v1)

- **Enemy targeting** still uses P1's position as primary target (enemies aim at closest player via distance calc — but this wasn't refactored for all players in v1)
- **P3/P4 gamepad support** not wired (input maps exist but no `gamepadconnected` listener)
- **No join UI** — players press their key to drop in, no visual prompt
- **P2 boost (Enter) conflicts** with pause menu navigation during pause (acceptable for v1)
- **HUD** only shows P1's shield/weapon/score (scrap is shared)
- **Overheat glow** only renders at P1's position

## Commit Reference
- Commit: `650c54c` on `staging` branch (Jun 2026)
- 135 insertions, 18 deletions in `index.html`
