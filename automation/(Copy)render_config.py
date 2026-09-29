import sys
import yaml
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

BASE = Path(__file__).resolve().parent.parent

device = sys.argv[1]

yaml_file = BASE / "inventory" / "host_vars" / f"{device}.yml"
template_dir = BASE / "templates" / "arista"
output_dir = BASE / "configs" / "generated"

output_dir.mkdir(parents=True, exist_ok=True)

with open(yaml_file) as f:
    data = yaml.safe_load(f)

env = Environment(
    loader=FileSystemLoader(template_dir),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

template = env.get_template("ceos.j2")

config = template.render(**data)

output_file = output_dir / f"{device}.cfg"
output_file.write_text(config.rstrip() + "\n")

print(f"Generated: {output_file}")
