# -*- coding: utf-8 -*-
"""
Copyright 2023-2026 Maen Artimy

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import re
import unicodedata



_NUMBERED = re.compile(r"(?P<base>.*)_(?P<number>\d+)")


def _split_number(item):
    """("Why_3") -> ("Why", 3); ("Why") -> ("Why", None). Only a trailing _<digits> counts."""
    match = _NUMBERED.fullmatch(item)
    if match:
        return match["base"], int(match["number"])
    return item, None


def get_same_prefix(input_item, item_list):
    """
    The base name of an item (without a trailing _<number>) and the items
    that are that name or a numbered copy of it.
    """
    prefix, _ = _split_number(input_item)
    matching_items = []
    for item in item_list:
        base, number = _split_number(item)
        if item == prefix or (number is not None and base == prefix):
            matching_items.append(item)
    return prefix, matching_items


def get_highest_number(input_item, item_list):
    """The item named <input_item>_<number> with the highest number, or None."""
    pattern = re.compile(rf"{re.escape(input_item)}_(\d+)")
    highest_item = None
    highest_number = -1
    for item in item_list:
        match = pattern.fullmatch(item)
        if match and int(match[1]) > highest_number:
            highest_number = int(match[1])
            highest_item = item
    return highest_item


def get_next_item(input_item, item_list):
    prefix, _ = get_same_prefix(input_item, item_list)
    highest_item = get_highest_number(prefix, item_list)

    if highest_item:
        highest_number = int(highest_item.rsplit("_", 1)[1])
        return f"{prefix}_{highest_number + 1}"
    return f"{input_item}_1"


def parse_interfaces(items):
    """Normalize interface specs to pybatfish ``Interface`` objects.

    ``items`` may be ``None``, a single spec, or a list. Each spec is either
    a ``node[interface]`` string (for example ``"r1[Gi0/0]"``) or a mapping
    with ``hostname`` (or ``node``) and ``interface`` keys. Pybatfish's
    ``fork_snapshot`` expects ``Interface`` objects, not raw strings.

    Raises ``ValueError`` for a bare node name or any other unsupported value.
    """
    if items is None:
        return None

    from pybatfish.datamodel.primitives import Interface

    if isinstance(items, (str, dict, Interface)):
        items = [items]

    parsed = []
    for item in items:
        if isinstance(item, Interface):
            parsed.append(item)
            continue

        if isinstance(item, str):
            text = item.strip()
            if text.endswith("]") and "[" in text:
                hostname, _, interface = text[:-1].rpartition("[")
                hostname, interface = hostname.strip(), interface.strip()
                if hostname and interface:
                    parsed.append(Interface(hostname=hostname, interface=interface))
                    continue
        elif isinstance(item, dict):
            hostname = item.get("hostname", item.get("node"))
            interface = item.get("interface")
            if (
                isinstance(hostname, str)
                and hostname.strip()
                and isinstance(interface, str)
                and interface.strip()
            ):
                parsed.append(
                    Interface(
                        hostname=hostname.strip(),
                        interface=interface.strip(),
                    )
                )
                continue

        raise ValueError(
            f"Invalid interface specifier {item!r}: expected 'node[interface]' "
            "(for example 'r1[Gi0/0]') or "
            "{'hostname': 'r1', 'interface': 'Gi0/0'}; "
            "use failed_nodes for node-level failures."
        )

    return parsed


MAX_NAME_LENGTH = 100  # Batfish allows 150; stay well inside it


def safe_snapshot_name(file_name):
    """
    A snapshot name Batfish accepts, made from an uploaded file's name.

    Batfish only allows letters, digits, '-' and '_' (and rejects "settings"),
    but file names often contain spaces, dots or "(1)". The extension is
    dropped, accents are removed, and any other run of characters becomes '_'.

    >>> safe_snapshot_name("my network (1).zip")
    'my_network_1'
    """
    base = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_")[:MAX_NAME_LENGTH]
    name = name.strip("_") or "snapshot"
    if name.lower() == "settings":  # reserved by Batfish
        name += "_1"
    return name


def sort_qlist_by_schema(qlist, questions_data=None):
    """
    Sorts a qlist dictionary according to the order of categories and questions
    as they appear in the Batfish questions schema.
    """
    if not qlist:
        return {}

    if questions_data is None:
        try:
            from .catalog import read_questions

            questions_data = read_questions()["Batfish"]
        except Exception:
            return qlist

    order = {}
    idx = 0
    for cat in questions_data:
        for q in cat.get("questions", []):
            name = q.get("name")
            if name and name not in order:
                order[name] = idx
                idx += 1

    return dict(sorted(qlist.items(), key=lambda item: order.get(item[0], 9999)))


def convert_template(input_data):
    """
    Returns a list of option sets for a question
    """
    output = []
    sorted_data = sort_qlist_by_schema(input_data)

    for question_name, question_data in sorted_data.items():
        fun = question_data.get("fun", "")
        variants = question_data.get("variants", [])

        if variants:
            for idx, variant in enumerate(variants):
                name = f"{question_name}_{idx}" if idx > 0 else question_name
                variant_data = {"name": name, "fun": fun, "options": variant}
                output.append(variant_data)
        else:
            variant_data = {"name": question_name, "fun": fun}
            output.append(variant_data)

    return output


if __name__ == "__main__":
    items = ["How", "Why", "Why_3", "Why_12", "What"]
    for item in items:
        next_available_item = get_next_item(item, items)
        print(f"{item}: {next_available_item}")
