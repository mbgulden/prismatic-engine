from pathlib import Path

p = Path("prismatic/gateway/server.py")
lines = p.read_text(encoding="utf-8").splitlines()
trimmed = [l.rstrip() for l in lines]
p.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
print("Cleaned server.py whitespace!")
