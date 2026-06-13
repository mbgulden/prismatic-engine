# Static Mirror Image Optimization (Pillow Quality-Stepping)

Compress images in bulk for Cloudflare Pages deployment.
Target: max 1920px width, ~300KB file size. Proven on 94 images, 51.1MB saved.

## Strategy

- **Resize**: max 1920px wide (Lanczos resampling)
- **Quality stepping**: start at 85, drop by 5 until target met or q=40
- **PNG→JPEG**: convert when no transparency (PNGs with alpha kept as optimized PNG)
- **Min threshold**: only compress images >500KB (or configurable)
- **In-place**: replace originals (git tracks the delta)

## Full Script

```python
#!/usr/bin/env python3
import os, sys
from PIL import Image

SITE = os.path.expanduser("~/work/project/site")
MIN_SIZE = 500_000
MAX_WIDTH = 1920
QUALITY_START = 85
QUALITY_MIN = 40

targets = []
for root, dirs, files in os.walk(SITE):
    for f in files:
        if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
            fp = os.path.join(root, f)
            size = os.path.getsize(fp)
            if size > MIN_SIZE:
                targets.append((fp, size))

targets.sort(key=lambda x: -x[1])

for fp, orig_size in targets:
    img = Image.open(fp)
    fmt = img.format or 'JPEG'
    w, h = img.size

    if w > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, int(h * MAX_WIDTH / w)), Image.LANCZOS)

    # Quality stepping
    for q in range(QUALITY_START, QUALITY_MIN - 1, -5):
        if fp.lower().endswith('.png'):
            if img.mode == 'RGBA':
                alpha = img.getchannel('A')
                if alpha.getextrema() == (255, 255):
                    img = img.convert('RGB')  # No transparency → JPEG
                else:
                    # Has transparency → optimize PNG
                    fp_tmp = fp + '.tmp'
                    img.save(fp_tmp, 'PNG', optimize=True)
                    new_size = os.path.getsize(fp_tmp)
                    if new_size < orig_size * 0.7:
                        os.replace(fp_tmp, fp)
                    else:
                        os.remove(fp_tmp)
                    break

        fp_tmp = fp + '.tmp'
        if fp.lower().endswith('.webp'):
            img.save(fp_tmp, 'WEBP', quality=q)
        else:
            img.save(fp_tmp, 'JPEG', quality=q, optimize=True)

        new_size = os.path.getsize(fp_tmp)
        if new_size < MIN_SIZE or new_size < orig_size * 0.4 or q <= QUALITY_MIN:
            os.replace(fp_tmp, fp)
            break
        else:
            os.remove(fp_tmp)
```

## Post-Compression Cleanup

```bash
find site/ -name "*.tmp" -delete
git add site/ && git commit -m "perf: compress N images, X MB saved"
```

## Pitfalls

- **`.tmp` files**: The script writes temp files alongside originals during quality stepping. Always `find -name '*.tmp' -delete` after the run. If `.tmp` files get committed, CF Pages may fail the deploy.
- **SVGs**: Skip vector files — they don't benefit from Pillow resizing. Filter by extension.
- **Memory**: Large images (4MB+ DJI drone shots) consume significant RAM during resize. The script processes one at a time to avoid OOM.
- **Quality floor**: Don't go below q=40 — visible artifacts appear. If an image can't compress below the threshold at q=40, skip it rather than degrading quality.
