# Repository Guidelines

## Project Structure & Module Organization

Apart from this guide, the directory has no source code, test suite, asset directory, or Git metadata. As the project takes shape, keep application or library code in `src/`, tests in `tests/`, and static files in `assets/`. Mirror source paths in tests where practical (for example, `src/auth/session.py` and `tests/auth/test_session.py`). Document any different layout in `README.md`.

## Build, Test, and Development Commands

No build system or runnable commands are configured yet. When adding one, record the exact install, local run, build, and test commands in `README.md` and provide scripts or targets that execute them. For example, a Node.js project could expose `npm run dev`, `npm run build`, and `npm test` through `package.json`. Do not list a command as supported until it works from the repository root.

## Coding Style & Naming Conventions

Use the formatter and linter standard for the language introduced, and commit their configuration with the first source files. Until then, use spaces rather than tabs: two spaces in JSON, YAML, and JavaScript or TypeScript; four spaces in Python. Use descriptive names and follow language conventions, such as `snake_case` for Python functions and `camelCase` for JavaScript functions. Keep formatting changes separate from unrelated behavior changes.

## Testing Guidelines

Choose a test framework when adding the first executable code, and make its test command part of the normal development workflow. Name tests for the behavior they cover; for Python, use `test_*.py`. Add tests for new behavior and bug fixes. No coverage threshold exists yet; establish one only when the project has a meaningful test suite.

## Commit & Pull Request Guidelines

There is no Git history from which to infer an existing commit convention. Use short, imperative subjects, such as `Add initial project scaffold`. Pull requests should explain the change, list the commands run and their results, link relevant issues, and include screenshots for visible UI changes.

## Security & Configuration

Keep credentials out of source control. If environment variables are needed, document their names in a checked-in `.env.example` with safe placeholder values.
