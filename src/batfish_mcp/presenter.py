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

"""
Headless serialization helpers for Batfish answers.

Pure DataFrame/trace transformations, kept so
MCP tool results can drop empty columns and flatten traces before being
serialized to JSON/Markdown for the LLM. The Streamlit display functions
(``display_*``, ``build_column_config``) were removed.
"""

import pandas as pd

nan = float("NaN")


def _is_empty_value(y):
    """
    Treat None/NaN, empty (or whitespace-only) strings, and empty
    list/tuple/set/dict values as "empty" so they can be caught when
    deciding whether a whole column is empty. Batfish frequently returns
    empty lists or blank strings instead of None, which plain isna()
    checks miss.
    """
    if y is None:
        return True
    if isinstance(y, (list, tuple, set, dict)):
        return len(y) == 0
    if isinstance(y, str):
        return y.strip() == ""
    try:
        return bool(pd.isna(y))
    except (TypeError, ValueError):
        return False


def format_result(result):
    """
    Format Pandas dataframe to eliminate empty columns.
    """
    for c in result.columns:
        result[c] = result[c].apply(lambda y: nan if _is_empty_value(y) else y)

    filtered_df = result.dropna(axis=1, how="all")
    filtered_df = filtered_df.replace(nan, "")
    removed = set(result.columns) - set(filtered_df.columns)
    return filtered_df, removed


def format_result_lite(df):
    all_empty = df.apply(lambda col: col.map(_is_empty_value).all())
    filtered_df = df.loc[:, ~all_empty]
    removed = set(df.columns) - set(filtered_df.columns)
    return filtered_df, removed


def dict_to_str(data: dict):
    if data:
        st_str = ""
        for key, value in data.items():
            st_str += f"{key} = {value}, "
        return st_str[:-2]
    return ""


def json_to_dataframe(trace):
    traces_table = pd.DataFrame(columns=["Node", "Type", "Action", "Detail"])
    for hop in trace["hops"]:
        node = hop["node"]["name"]
        for step in hop["steps"]:
            row = {
                "Node": node,
                "Type": step["type"],
                "Action": step["action"],
                "Detail": step["detail"],
            }
            # A dict or list detail becomes one row per entry; any other
            # value (text, number, None) is a single row.
            if isinstance(step["detail"], (dict, list, tuple)):
                new_trace = pd.DataFrame(row)
            else:
                new_trace = pd.DataFrame(row, index=[0])
            traces_table = pd.concat([traces_table, new_trace], ignore_index=True)
    return traces_table


def flatten_trace_data(data):
    flattened_data = []

    for item in data:
        common_fields = {
            "Node": item.get("Node"),
            "Filter_Name": item.get("Filter_Name"),
            "Flow": item.get("Flow"),
            "Action": item.get("Action"),
            "Line_Content": item.get("Line_Content"),
        }

        if not item.get("Trace"):
            flattened_data.append(
                {**common_fields, "Trace_Text": None, "Trace_Vendor_Structure": None}
            )
            continue

        for trace in item.get("Trace", []):
            trace_element = trace.get("traceElement", {})
            fragments = trace_element.get("fragments", [])

            trace_text = " ".join(f.get("text", "") for f in fragments if f.get("text"))
            vendor_structure = next(
                (
                    f.get("vendorStructureId")
                    for f in fragments
                    if "vendorStructureId" in f
                ),
                None,
            )

            flattened_data.append(
                {
                    **common_fields,
                    "Trace_Text": trace_text,
                    "Trace_Vendor_Structure": vendor_structure,
                }
            )

    return flattened_data
