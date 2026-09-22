import sys
import yaml
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

BASE = Path(__file__).resolve().parent.parent
device = sys.argv[1]

yaml_file = BASE / "inventory" / "host_vars" / f"{device}.yml"

with open(yaml_file) as f:
    data = yaml.safe_load(f)

vendor = data["vendor"]
platform = data["platform"]

if vendor == "arista" and platform == "ceos":
    template_dir = BASE / "templates" / "arista"
    template_name = "ceos.j2"
elif vendor == "nokia" and platform == "srlinux":
    template_dir = BASE / "templates" / "nokia"
    template_name = "srlinux.j2"
else:
    raise ValueError(f"Unsupported platform: {vendor}/{platform}")

env = Environment(
    loader=FileSystemLoader(template_dir),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

template = env.get_template(template_name)

config = template.render(**data)

output_dir = BASE / "configs" / "generated"
output_dir.mkdir(parents=True, exist_ok=True)

suffix = ".cfg"
output_file = output_dir / f"{device}{suffix}"

output_file.write_text(config.rstrip() + "\n")

print(f"Generated: {output_file}")