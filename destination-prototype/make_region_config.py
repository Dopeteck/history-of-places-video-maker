#!/usr/bin/env python3
"""Draft a region YAML config for human review; it does not invent verified facts."""
import argparse, re
from pathlib import Path
import yaml
TEMPLATE = Path(__file__).resolve().parents[1] / "regions" / "template.yaml"
def slugify(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
def main():
 p=argparse.ArgumentParser(description=__doc__); p.add_argument("region"); p.add_argument("--country",default="Country"); p.add_argument("--slug",default=""); p.add_argument("--output",default=""); a=p.parse_args(); slug=a.slug or slugify(a.region); out=Path(a.output) if a.output else Path(__file__).resolve().parents[1]/"regions"/f"{slug}.yaml"; d=yaml.safe_load(TEMPLATE.read_text(encoding="utf-8")); d["name"]=a.region; d["slug"]=slug; d["country"]=a.country; d["map"]["label"]=a.region.upper(); d["story"]["hook"]=f"What made {a.region} become the place we know today? [REVIEW]"; d["story"]["closing_tip"]=f"Choose one heritage site in {a.region} and visit respectfully. [ADD SOURCED TIP]"; d["story"]["narration"]=f"[DRAFT ONLY] Write a sourced 120–145 word history of {a.region}, with a hook, chronological arc, and visitor tip. Verify every factual claim before approval."; out.parent.mkdir(parents=True,exist_ok=True); out.write_text(yaml.safe_dump(d,sort_keys=False,allow_unicode=True),encoding="utf-8"); print(f"Draft written to {out}. It is unverified; review all facts and add 8–12 scenes before rendering.")
if __name__=="__main__": main()
