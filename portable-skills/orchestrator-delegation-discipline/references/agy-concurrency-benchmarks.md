# AGY Concurrency Benchmarks (Jun 12, 2026)

Tested on: 62GB RAM, 4+ core server, load average ~3-5 baseline.

## Concurrent AGY Sessions

| Instances | CPU | RAM | Load | Notes |
|-----------|-----|-----|------|-------|
| 1 | ~30% | 0.6% | +0.5 | Single foreground |
| 3 | ~120% | 1.5% | +1.5 | All completed within 60s |
| 7 | 219% | 2.4% | 11.27 | Peak load, no crashes |
| 0 (idle) | 0% | 0% | 3-5 | Baseline |

## Per-Instance Profile
- CPU: ~7-65% depending on task complexity
- RAM: ~0.3-0.6% (negligible)
- Duration: 30-300s (task-dependent)
- Research tasks: 30-60s
- Review tasks: 60-120s
- Complex generation: 120-300s

## Ceiling Estimate
- CPU-limited: ~12-14 instances before 100% CPU saturation
- RAM is never the bottleneck (55GB free)
- AGY's own `--print-timeout 300s` is the real constraint for long tasks

## Recommended Configurations
- **Sustained throughput**: 3-5 concurrent
- **Backlog clearing**: 5-7 concurrent
- **Burst maximum**: 7 (tested safe)

## Launch Pattern
```python
# Each AGY session:
terminal(pty=true, background=true, command='agy --print "Read /tmp/task.txt..." --print-timeout 300s --add-dir /tmp')

# Poll results:
process(action='poll', session_id='proc_xxx')

# Verify deliverables exist:
ls -la /tmp/agy-dispatch-GRO-XXXX-result.md
```

## Key Discovery
Background+PTY was previously believed to fail with SIGTERM 143. On Jun 12, 2026, 7 concurrent background+PTY AGY sessions all completed successfully. The earlier failures were **background WITHOUT PTY**. PTY is the critical ingredient — not foreground vs background.
