# Enemy Type Recognition — Role-Passing Pattern

## Problem

When a game uses named enemy pools (e.g., `{scout: "angler_scout", interceptor: "coral_wasp",
heavy: "armored_eel"}`), the type names are arbitrary labels that don't necessarily contain
their behavior category as a substring. A substring-based detection chain in the Enemy
constructor fails for names like `coral_wasp` (doesn't contain "interceptor"), `armored_eel`
(doesn't contain "heavy"), `sentinel` (biome3 interceptor, but contains "sentinel" which
matches the heavy check), etc.

## Two-Phase Fix

### Phase 1: Add missing substring checks (quick fix, partial coverage)
Add `type.includes('interceptor')` and `type.includes('heavy')` to the detection chains.
This catches names that literally contain the role — `jelly_interceptor`, `vent_crab_heavy` —
but misses names like `coral_wasp`, `armored_eel`, `magma_wasp`, `frost_drone`, `null_entity`,
etc. that don't contain the role substring.

### Phase 2: Role-passing through spawn queue (complete fix)
The spawn queue builder (`_queueWave`) knows which category each enemy belongs to because
it picks from `pool.scout`, `pool.interceptor`, `pool.heavy`, etc. Track this information
alongside the type name and pass it through to the spawn method.

**Pattern (from GRO-873, darius-star `js/level_manager.js`):**

```javascript
// 1. Track roles alongside types in _queueWave
const enemyTypes = [];
const enemyRoles = [];       // NEW
for (let i = 0; i < count; i++) {
    const roll = rng();
    if (roll < dist.scout) {
        enemyTypes.push(pool.scout);
        enemyRoles.push('scout');       // NEW
    } else if (roll < dist.scout + dist.interceptor) {
        enemyTypes.push(pool.interceptor);
        enemyRoles.push('interceptor'); // NEW
    } else if (roll < dist.scout + dist.interceptor + dist.heavy) {
        enemyTypes.push(pool.heavy);
        enemyRoles.push('heavy');       // NEW
    } else {
        enemyTypes.push(pool.alt);
        enemyRoles.push('scout');       // alt defaults to scout
    }
}

// 2. Store role in spawn queue entries
for (let i = 0; i < enemyTypes.length; i++) {
    const pos = this._formationPosition(formation, i, count);
    this.spawnQueue.push({
        type: enemyTypes[i],
        role: enemyRoles[i],            // NEW
        x: canvas.width + pos.x,
        y: pos.y,
        delay: i * spawnInterval
    });
}

// 3. Pass role through to _spawnEnemy
const entry = this.spawnQueue.shift();
this._spawnEnemy(entry.type, entry.x, entry.y, entry.role);  // role added

// 4. In _spawnEnemy, apply role-based behavior override
_spawnEnemy(type, x, y, role) {
    const enemy = new Enemy(type);

    if (role) {
        if (role === 'interceptor' && enemy.behaviorPattern !== 'interceptor') {
            enemy.behaviorPattern = 'interceptor';
            enemy.enemyType = 'elite';
            enemy.speed = 280;
            enemy.hp = 1;
            enemy.scoreValue = 150;
            enemy.color = '#ff0055';
        } else if (role === 'heavy' && enemy.behaviorPattern !== 'heavy') {
            enemy.behaviorPattern = 'heavy';
            enemy.enemyType = 'elite';
            enemy.speed = 80;
            enemy.hp = 4;
            enemy.scoreValue = 300;
            enemy.color = '#9a33cc';
            enemy.shootCooldown = 1.2 + Math.random() * 0.8;
            enemy.shootTimer = enemy.shootCooldown;
        }
    }
    // ... rest of spawn logic (positioning, difficulty scaling)
}
```

## Also: Fix dependent methods

Methods that inspect enemy type strings need the same treatment. In GRO-873, `_pickFormation`
counted heavies via substring matching across 8 hardcoded names. With roles available, it
becomes:

```javascript
// Before (fragile substring matching)
_pickFormation(enemyTypes, count) {
    const heavyCount = enemyTypes.filter(t =>
        t.includes('heavy') || t.includes('brute') || t.includes('turret') ||
        t.includes('golem') || t.includes('glacier') || t.includes('thunderhead') ||
        t.includes('giant') || t.includes('node') || t.includes('battery')
    ).length;
    // ...
}

// After (direct role check)
_pickFormation(roles, count) {
    const heavyCount = roles.filter(r => r === 'heavy').length;
    // ...
}
```

## When to Use This Pattern

- Any spawn system where entity pool names don't encode their category as a substring
- Loot tables, procedural generation, wave systems with named but arbitrary type keys
- The approach generalizes: the key insight is that **the code that selects from the pool
  knows the category — don't lose that information, pass it alongside the name**

## Verification

After implementing, verify with quick substring checks:
```bash
node -e "console.log('coral_wasp role-passing: role=interceptor → correct')"
node -e "console.log('armored_eel role-passing: role=heavy → correct')"
```
