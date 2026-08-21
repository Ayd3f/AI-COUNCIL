"""JSON extraction / repair (requirement #12, step 1)."""

from __future__ import annotations

from ..services.json_repair import extract_json


def test_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_code_fence():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_prose_around_json():
    text = 'Sure! Here is my answer:\n{"answer": "x", "confidence": 0.7}\nHope that helps.'
    assert extract_json(text) == {"answer": "x", "confidence": 0.7}


def test_trailing_comma():
    assert extract_json('{"a": 1, "b": [1, 2,],}') == {"a": 1, "b": [1, 2]}


def test_truncated_object_is_closed():
    parsed = extract_json('{"answer": "hello", "key_points": ["a"')
    assert parsed is not None
    assert parsed["answer"] == "hello"


def test_braces_inside_strings_do_not_confuse_the_scanner():
    parsed = extract_json('{"answer": "use {braces} carefully", "confidence": 1}')
    assert parsed == {"answer": "use {braces} carefully", "confidence": 1}


def test_json_array_of_objects_takes_first():
    assert extract_json('[{"a": 1}, {"b": 2}]') == {"a": 1}


def test_garbage_returns_none():
    assert extract_json("I refuse to answer.") is None
    assert extract_json("") is None
