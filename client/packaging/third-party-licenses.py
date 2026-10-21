#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Writes the licenses of the Python packages an app build bundles, and of Python itself, as one
text file. The release workflow runs it after installing the app's packages, and puts the file in
every installer next to LICENSE, NOTICE and LICENSES/.

    python third-party-licenses.py <out-file> <package>...

The packages named are followed through their requirements, so naming the app's direct
dependencies (as in client/pyproject.toml) covers everything pip installed for them. Flet's own
window program, a Flutter app, carries the licenses of its Flutter and Dart parts inside itself.
"""

import re
import sys
import sysconfig
from importlib import metadata
from pathlib import Path

LICENSE_NAMES = re.compile(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.IGNORECASE)


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def closure(roots: list[str]) -> list[metadata.Distribution]:
    """The installed distributions behind `roots` and everything they require."""
    found: dict[str, metadata.Distribution] = {}
    todo = list(roots)
    while todo:
        name = canonical(todo.pop())
        if name in found:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue  # an optional requirement that isn't installed, so isn't bundled either
        found[name] = dist
        for requirement in dist.requires or []:
            if "extra ==" in requirement:
                continue  # only for an extra nobody asked for
            todo.append(re.match(r"[A-Za-z0-9._-]+", requirement).group(0))
    return sorted(found.values(), key=lambda d: canonical(d.metadata["Name"]))


def license_texts(dist: metadata.Distribution) -> list[tuple[str, str]]:
    texts = []
    for file in dist.files or []:
        if ".dist-info" in str(file) and LICENSE_NAMES.search(file.name):
            try:
                text = Path(file.locate()).read_text(encoding="utf-8", errors="replace").strip()
                texts.append((file.name, text))
            except OSError:
                pass
    return texts


def declared(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    if meta.get("License-Expression"):
        return meta["License-Expression"]
    classifiers = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    short = (meta.get("License") or "").strip()
    return ", ".join(classifiers) or (short if short and "\n" not in short else "see the text below")


def python_license() -> str:
    for base in (sysconfig.get_paths()["stdlib"], sys.base_prefix):
        path = Path(base) / "LICENSE.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace").strip()
    return "Python Software Foundation License: https://docs.python.org/3/license.html"


def main() -> None:
    out, roots = Path(sys.argv[1]), sys.argv[2:]
    if not roots:
        sys.exit(__doc__)
    rule = "=" * 100
    parts = ["Software bundled with the NanoBorealis app, and the licenses it comes under.",
             "NanoBorealis's own license is in LICENSE; NOTICE says which parts keep their own terms.", "",
             rule, f"Python {sys.version.split()[0]}", rule, "", python_license(), ""]
    for dist in closure(roots):
        meta = dist.metadata
        parts += [rule, f"{meta['Name']} {dist.version}: {declared(dist)}"]
        if meta.get("Home-page"):
            parts.append(meta["Home-page"])
        parts += [rule, ""]
        missing = ("(The package carries no license file. The Apache License 2.0's text is in "
                   "LICENSES/Apache-2.0.txt.)" if "Apache" in declared(dist)
                   else "(The package carries no license file; see its project page.)")
        for name, text in license_texts(dist) or [("", missing)]:
            parts += ([f"--- {name}"] if name else []) + [text, ""]
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out}: Python and {len(closure(roots))} package(s)")


if __name__ == "__main__":
    main()
