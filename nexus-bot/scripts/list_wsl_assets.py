import json, subprocess, sys

out = subprocess.run(
    ["curl", "-s", "-m", "20", "https://api.github.com/repos/microsoft/WSL/releases/latest"],
    capture_output=True, text=True,
).stdout
try:
    d = json.loads(out)
except Exception as e:
    print("JSON parse failed:", e, out[:200]); sys.exit(1)

print("tag:", d.get("tag_name"))
for a in d.get("assets", []):
    print(f"{a['name']}\t{a['size'] // 1024 // 1024}MB\t{a['browser_download_url']}")
