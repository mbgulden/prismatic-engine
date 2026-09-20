from pathlib import Path

for path in Path("prismatic/review_factory").rglob("*"):
    if path.is_file() and not str(path).endswith(".pyc"):
        lines = path.read_text(encoding="utf-8").splitlines()
        trimmed = [line.rstrip() for line in lines]
        content = "\n".join(trimmed) + "\n"
        path.write_text(content, encoding="utf-8")

print("Cleaned trailing whitespace in all review_factory files!")
