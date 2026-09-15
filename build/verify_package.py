from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
pkg = json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
required = [ROOT / "desktop" / name for name in pkg["build"]["files"]]
missing = [str(p) for p in required if not p.exists()]
assert not missing, f"Missing desktop package files: {missing}"
resources = pkg["build"]["extraResources"]
resource_paths = {item["from"] for item in resources}
assert "../build/windows/backend/YouScraperBackend.exe" in resource_paths
assert "../build/windows/deno/deno.exe" in resource_paths
assert pkg["build"]["productName"] == "YouScraper"
assert pkg["build"]["nsis"]["artifactName"] == "YouScraper-Setup.${ext}"
print("PASS: Windows packaging configuration is internally consistent.")
