# Pull Requests

## Pull requests should be

1. Made against the `master` branch.
1. Made from a git feature branch.

## Pull requests will not be accepted that

1. Are not made from a git feature branch
1. Do not pass PEP 8 / flake8 checks
1. Do not work with supported Python versions (Python 3.8+)
1. Add external dependencies (all code must rely strictly on the Python standard library)
1. Are made by editing files via the GitHub website

# Coding Guidelines

All code should follow PEP 8 standards.

## Guidelines

1. Do not use `\` for line continuations; wrap long expressions in parentheses `()`.
1. String quoting should be done with single quotes `'`, except where escaping an internal single quote is required.
1. Docstrings should use triple double quotes `"""`.
1. All public functions, classes, and modules should have informative docstrings.
1. Inline comments should be used only where logic is non-obvious.

# Supported Python Versions

All code must support Python 3.8+ (including Python 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, and 3.14+). Support for Python 2.x and obsolete Python 3 versions (< 3.8) has been dropped.

# Permitted Python Modules

Only modules included in the Python standard library are permitted. The application must not depend on any third-party runtime dependencies.

# Testing

Unit tests are located in `tests/`. Before submitting a pull request, verify that tests pass cleanly with zero warnings:

```bash
python -W error -m unittest discover tests
```

You can also run tests across supported environments using `tox`:

```bash
tox
```

Repository: https://github.com/DownloaderZone/Speedtest-Cli
