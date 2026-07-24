"""ATF v3.2 §7.2 item 0 — RFC 8785 JCS conformance check.

The existing `sdk/acp_client/receipts.py::canonical_json` uses
``json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)``
which matches JCS on the common object/array/string/int subset. Known
edge-case deviations from full RFC 8785:

  * numeric serialization diverges from ECMAScript §7.1.12.1 for
    subnormals and for integers > 2^53 (Python emits decimal, JCS
    emits scientific for the same values)
  * key sort uses codepoint order (Python) vs UTF-16 code-unit order
    (JCS); differs only for non-BMP keys

Both are non-issues for the entry schema in §7.1 (keys are ASCII,
values are strings + small ints + null). This module documents that
via runnable vectors, so a future regression on the common subset
fails loudly instead of silently corrupting a receipt.
"""
from __future__ import annotations

from sdk.acp_client.receipts import canonical_json

# Vectors: (input, expected canonical bytes) — every case is a subset
# of the entry schema in §7.1, so passing them proves conformance for
# the domain that matters.
_VECTORS: list[tuple[dict, bytes]] = [
    ({}, b"{}"),
    ({"a": 1}, b'{"a":1}'),
    ({"b": 2, "a": 1}, b'{"a":1,"b":2}'),  # sort
    ({"": ""}, b'{"":""}'),
    ({"nested": {"z": 1, "a": 2}}, b'{"nested":{"a":2,"z":1}}'),
    ({"list": [3, 2, 1]}, b'{"list":[3,2,1]}'),  # arrays NOT sorted
    ({"euro": "€", "yen": "¥"}, b'{"euro":"\xe2\x82\xac","yen":"\xc2\xa5"}'),
    ({"null_field": None}, b'{"null_field":null}'),
    ({"bool_true": True, "bool_false": False},
     b'{"bool_false":false,"bool_true":true}'),
]


def check_all() -> None:
    for obj, expected in _VECTORS:
        actual = canonical_json(obj)
        assert actual == expected, (
            f"JCS drift on {obj!r}: got {actual!r}, expected {expected!r}"
        )


if __name__ == "__main__":
    check_all()
    print("jcs_check OK — RFC 8785 conformance for §7.1 domain vectors")
