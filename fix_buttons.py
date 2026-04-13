import os
from pathlib import Path

for path in Path('src/ui').rglob('*.py'):
    text = path.read_text()
    if 'setFixedHeight(26)' in text:
        text = text.replace('setFixedHeight(26)', 'setMinimumHeight(28)')
        path.write_text(text)
        print(f"Fixed {path}")
