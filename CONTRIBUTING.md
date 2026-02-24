# Contributing

## Before you start
- Open an issue first for non-trivial changes.
- Confirm scope, platform targets, and expected behavior before implementation.

## Development basics
- Use Python 3.13 and Node 20.
- Install dependencies:
```bash
python -m pip install -U pip
python -m pip install -e '.[voice,knowledge]' pyinstaller
npm --prefix ui-next ci
```
- Build UI:
```bash
npm --prefix ui-next run build
npm --prefix ui-next run deploy:voice-client
```

## Pull requests
- Keep PRs focused and small.
- Include clear reproduction and validation steps.
- Add or update docs for behavior changes.
- Ensure release workflows still pass for Linux, Windows, and macOS.

## Commit style
- Use concise, descriptive commit messages.
- Prefer imperative tense (example: `Add release workflow checksums`).
