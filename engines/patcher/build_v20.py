"""Regenerate the V20 descriptors from the four supported full reference images.

Run python -m engines.patcher.build_v20 <1429861.bin> <1437806.bin>
<1406464.bin> <SS1v2.bin>. The patcher consumes the checked-in JSON.
"""
import argparse
import hashlib
import json
from copy import deepcopy
from itertools import combinations
from pathlib import Path

from . import patch_ms41


REFERENCES = {
    "MS41.0": "c61674c5812f5fe0a4a6f86e96311e9a8e540a905f9e7f681fdd045254249521",
    "MS41.1": "6129b8e8884cd8321c94546e2beba4c8b4afcfca0d7749a341a82cea37f99aa3",
    "MS41.2": "2d3ab7db6fe0f9a1f4680339f416aeeef43871730cd468cfd990f6dc80c03208",
    "MS41.3": "89f31cbb70466ad39b23571e3c1c533251275182e8e2502c214a208e83f64487",
}


def successor(previous):
    result = deepcopy(previous)
    result.pop("deprecated", None)
    result.pop("superseded_by", None)
    result["version"] = "V20"
    for key in ("id", "family_id", "user_description"):
        result[key] = result[key].replace("v19", "v20").replace("V19", "V20")
    result["requires"] = [pid.replace("_v19", "_v20") for pid in previous["requires"]]
    for key in ("supersedes", "conflicts"):
        result[key].append(previous["id"])
    return result


def descriptors(family, stock):
    from . import v20_knock, v20_recovery, v20_launch

    if len(stock) != 0x40000 or hashlib.sha256(stock).hexdigest() != REFERENCES[family]:
        raise ValueError(f"{family} full reference image does not match its pinned SHA-256")
    suffix = "" if family == "MS41.3" else "_ms41" + family[-1]
    library = patch_ms41.load_patches()
    cut, launch = (successor(library[name + "_v19" + suffix])
                   for name in ("ignition_cut", "launch_control"))
    cut["description"] = (f"{family} Ignition Cut V20 retains the native crank-capture instructions, "
                          "preserves unread knock replies and tracks recovery from actual combustion changes.")
    launch["description"] = (f"{family} Launch Control V20 requires Ignition Cut V20. "
                             "The ECU evaluates spark cutoff at charge time and releases "
                             "Launch on a diagnosed vehicle-speed fault.")
    cut["verification"] = ("Focused reference-image tests cover coprocessor replies, capture timing, "
                           "misfire recovery and native faults. A simultaneous coil interrupt can delay "
                           "crank acknowledgment. Bench and on-car validation remain required.")
    launch["verification"] = ("Focused reference-image tests cover spark and fuel interventions, "
                              "release and native speed faults. Bench and on-car validation remain required.")
    for module in (v20_knock, v20_recovery, v20_launch):
        module.apply(cut, launch, family, stock)
    used = []
    for desc in (cut, launch):
        errors = patch_ms41.validate_splices(desc)
        if errors:
            raise ValueError(errors)
        for edit in desc["edits"]:
            start = edit["off"]
            original, installed = (bytes.fromhex(edit[key]) for key in ("expect", "data"))
            if len(original) != len(installed) or stock[start:start + len(original)] != original:
                raise ValueError(f"{desc['id']} invalid edit at {start:#07x}")
        used.extend(patch_ms41._ranges(desc))
        used.extend((addr, addr + (2 if name.endswith("IPW") else 1))
                    for name, addr in desc["cave"]["cals"].items())
    for a, b in combinations(used, 2):
        if patch_ms41._overlap(a, b):
            raise ValueError(f"{family} overlapping edits or settings at {a} and {b}")
    for other in library.values():
        if (other.get("deprecated") or other["id"] in (cut["id"], launch["id"])
                or not patch_ms41.supports_target(other, family)):
            continue
        for a in used:
            for b in patch_ms41._ranges(other):
                if patch_ms41._overlap(a, b):
                    raise ValueError(f"{family} overlaps {other['id']} at {max(a[0], b[0]):#07x}")
    return cut, launch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", type=Path, nargs=4)
    parser.add_argument("--check", action="store_true", help="compare with the checked-in descriptors")
    args = parser.parse_args()
    outputs = {}
    for family, path in zip(REFERENCES, args.images):
        for desc in descriptors(family, path.read_bytes()):
            dest = Path(patch_ms41.PATCH_DIR) / (desc["id"] + ".json")
            outputs[dest] = json.dumps(desc, indent=2) + "\n"
    for dest, content in outputs.items():
        if args.check:
            if dest.read_text(encoding="utf-8") != content:
                raise ValueError(f"{dest.name} differs from its assembled sources")
        else:
            dest.write_text(content, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
