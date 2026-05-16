
import re
from collections import Counter
import sys

file_path = sys.argv[1] if len(sys.argv) > 1 else 'templates/index.html'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

ids = re.findall(r'id="([^"]+)"', content)
counts = Counter(ids)

duplicates = {k: v for k, v in counts.items() if v > 1}

if duplicates:
    print(f"Found duplicate IDs in {file_path}:")
    for id_val, count in duplicates.items():
        print(f"{id_val}: {count} occurrences")
else:
    print(f"No duplicate IDs found in {file_path}.")
