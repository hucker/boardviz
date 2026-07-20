
# Spec

The product/behavior spec is **SPEC.md** (repo root) — what the tool does for
its user. Requirements carry stable mnemonic IDs (`PREFIX-MNEMONIC`, e.g.
`TRN-UNIQ`) — names, never sequence numbers. Tag each test with the ID(s) it
verifies via `@pytest.mark.spec("TRN-UNIQ", ...)`; `test_spec_traceability.py`
fails if a tag names an ID that isn't in SPEC.md. Change behavior **spec-first**
— edit SPEC.md, then the code, then the tagged test, in the same commit.

# Testing

# PyTest Guidelines

This is a test file for the Claude model. It contains various examples and scenarios to evaluate the model's performance and capabilities.

1. Use pytest test for testing 
2. Use fixtures when necessary to set up test environments
3. Include edge cases and boundary conditions in the tests
4. Use the class structure for organizing related tests
5. Use AAA convention (Arrange, Act, Assert) for clarity in test cases (very short tests may skip this)
6. Asserts should be actual == expected (the value under test first, the expected value second) — matches pytest's idiom and its failure output shows both sides regardless
7. When naming operands, use actual_desc == expected_desc
8. Parameterize tests when applicable to cover multiple scenarios with the same test logic
9. Every test file has a >=1-line module docstring; every test has a >=1-line docstring

## Spec traceability

1. Tag every test with `@pytest.mark.spec("AREA-MNEMONIC", ...)` naming the SPEC.md requirement(s) it verifies (see the Spec section above)
2. `test/test_spec_traceability.py` fails if a tag names an ID that isn't in SPEC.md; it also reports SPEC IDs that have no test
3. When you add or change a requirement, add or retag the tests so the mapping stays complete
4. `TRACEABILITY.md` (repo root) is the generated SPEC-ID → tests matrix; it is **not** hand-edited. Regenerate with `uv run python test/test_spec_traceability.py` and commit it — the `test_traceability_matrix_is_current` test fails if it drifts from the tags/spec
5. As we add features we need to keep the spect updated and tag new tests accordking.

## doc strings

1. Use google style docstrings for all functions and classes
2. Include a brief summary for each function and class
3. For simple functions, a one-line summary is sufficient
4. Each file should have a module-level docstring describing its purpose and contents

## type hints

1. Use type hints for function parameters and return types
2. Use `Optional` from the `typing` module for parameters that can be `None`

## Tooling

1. Use `uv` for running tests and other scripts
2. Use 'ruff' for linting and code formatting
3. Use 'ty' for type checking and static analysis
4. Use 'pytest' for running tests and generating coverage reports
5. Use 'coverage' for measuring code coverage of tests
6. Use 'tox' for verifying code accross python 3.11->3.14

# Streamlit

1. Any non trivial userinterface widgets that support the (?) help icon should have a help icon with a tooltip that explains the widget's purpose and usage.
2. 