#!/usr/bin/env python3
"""Fast offline contract tests for region configs and config-draft helper."""
import json, subprocess, sys, tempfile
from asset_resolver import _rights_ok, BANNED, PROVIDERS, _candidate
from region_slug import normalize_region_slug
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1]
def check(path):
 d=yaml.safe_load(path.read_text(encoding="utf-8"))
 assert d.get("name") and d.get("country") and d.get("period_focus"), f"{path}: missing region metadata"
 assert 8<=len(d.get("scenes",[]))<=12, f"{path}: expected 8–12 scenes"
 assert len(d.get("story",{}).get("narration","").split()) in range(110,156), f"{path}: narration must be 110–155 words"
 assert len(d.get("sources",[]))>=2 and all(str(u).startswith("https://") for u in d["sources"]), f"{path}: source list missing/invalid"
 claims=d.get("claim_sources",{})
 assert len(claims)>=5, f"{path}: add claim_sources for key facts"
 for claim,urls in claims.items():
  assert claim.strip() and isinstance(urls,list) and urls and all(str(u).startswith("https://") for u in urls), f"{path}: invalid claim sources for {claim}"
 hooks=d.get("story",{}).get("hook_variants",{})
 assert set(hooks)=={"A","B"}, f"{path}: provide two named hook variants"
 assert all(3<=len(str(v).split())<=5 for v in hooks.values()), f"{path}: each opening hook must be 3–5 words"
 assert d.get("visual_style",{}).get("accent_color"), f"{path}: missing regional accent"
 assert d.get("map",{}).get("latitude") is not None and d.get("map",{}).get("longitude") is not None, f"{path}: missing map coordinates"
 assert any(s.get("image_type")=="map" for s in d["scenes"]), f"{path}: must contain a real map scene"
 assert any(s.get("image_type")=="modern" for s in d["scenes"]), f"{path}: must contain a present-day photo scene"
 for s in d["scenes"]:
  assert s.get("title") and s.get("narration_intent"), f"{path}: incomplete scene"
  assert len(s.get("image_search_terms",[]))>=3, f"{path}/{s.get('title')}: needs 3 search variants"
  assert s.get("required_keywords") and s.get("image_type") in ("historic","modern","map"), f"{path}/{s.get('title')}: missing image gates"
  assert not any(x in (s["title"]+" "+s["narration_intent"]).lower() for x in ("pending","placeholder","archival asset")), f"{path}: placeholder text"
 return d
def test_destination_slug_normalization():
 cases = {
  "Hong kong": "hong-kong",
  "HONG KONG": "hong-kong",
  "New York City": "new-york-city",
  " São Paulo ": "s-o-paulo",
  "tokyo": "tokyo",
 }
 for raw, expected in cases.items():
  actual=normalize_region_slug(raw)
  assert actual==expected, f"{raw!r}: expected {expected!r}, got {actual!r}"
 for raw in ("", " !!! ", "---"):
  try:
   normalize_region_slug(raw)
  except ValueError:
   pass
  else:
   raise AssertionError(f"invalid destination should fail: {raw!r}")
 print("PASS destination slug normalization: spaces, capitalization, punctuation and empty input")
def main():
 test_destination_slug_normalization()
 regions=sorted((ROOT/"regions").glob("*.yaml"))
 regions=[p for p in regions if p.name!="template.yaml"]
 for p in regions:
  d=check(p); print(f"PASS config: {p.name} ({len(d['scenes'])} scenes)")
 with tempfile.TemporaryDirectory() as tmp:
  out=Path(tmp)/"draft.yaml"
  subprocess.run([sys.executable,str(ROOT/"destination-prototype/make_region_config.py"),"Kano","--country","Nigeria","--output",str(out)],check=True,capture_output=True,text=True)
  d=yaml.safe_load(out.read_text())
  assert d["name"]=="Kano" and d["country"]=="Nigeria" and "[DRAFT ONLY]" in d["story"]["narration"]
  print("PASS helper: creates explicitly unverified draft config")
 # Rights/format gate contract: explicit permissive rights only; unknown, NC, ND and scans fail.
 assert _rights_ok("Public Domain")
 assert _rights_ok("CC0 1.0 Universal")
 assert _rights_ok("CC BY 4.0")
 assert _rights_ok("CC BY-SA 4.0")
 for rights in ("", "Unknown", "All rights reserved", "CC BY-NC 4.0", "CC BY-ND 4.0"):
  assert not _rights_ok(rights), f"rights must fail closed: {rights}"
 assert BANNED.search("Book cover — 1908")
 assert BANNED.search("scanned text page")
 numeric = _candidate("Test Provider", 123, 456, "https://example.org/image.jpg",
     "https://example.org/item/7", "CC0", "https://creativecommons.org/publicdomain/zero/1.0/",
     789, 1011)
 assert numeric and numeric["title"] == "123" and numeric["description"] == "456"
 assert numeric["author"] == "789" and numeric["identifier"] == "1011"
 assert len(PROVIDERS) >= 10, "multi-provider registry unexpectedly incomplete"
 print("PASS resolver contract: rights allowlist, metadata coercion, banned scans and provider registry")
 print("ALL REGION CONTRACT TESTS PASSED")
if __name__=="__main__": main()
