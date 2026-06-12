# `edmgctl`

`edmgctl` is the support-plane CLI for EDMG NVIDIA Workshop.

It does not replace the Python backend or Electron/React UI. It wraps the
Workshop diagnostics, release, supervisor, and support-bundle surfaces with a
small cross-platform CLI.

## Commands

From `studio/edmg-studio/tools/edmgctl/`:

```powershell
$env:GOCACHE="$PWD\.gocache"
$env:GOMODCACHE="$PWD\.gomodcache"
go run ./cmd/edmgctl doctor
go run ./cmd/edmgctl bootstrap show
go run ./cmd/edmgctl release status
go run ./cmd/edmgctl artifact list --hashes
go run ./cmd/edmgctl support export --out .\edmg-support.zip
go run ./cmd/edmgctl supervisor start --port 0 --wait --timeout 90s
go run ./cmd/edmgctl supervisor status
go run ./cmd/edmgctl supervisor stop
```

It reads the user bootstrap config from
`%APPDATA%\EDMG NVIDIA Workshop\bootstrap.json` on Windows and starts managed
backend processes with Workshop storage roots plus NVIDIA profile environment.

Ollama paths and environment variables are intentionally not part of this
support surface.
