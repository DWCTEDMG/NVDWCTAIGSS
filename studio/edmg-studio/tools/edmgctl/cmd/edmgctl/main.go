package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/HIMOI890/DWCTGenerativeSoundStudio/studio/edmg-studio/tools/edmgctl/internal/support"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}

	command := os.Args[1]
	args := os.Args[2:]
	var err error

	switch command {
	case "doctor":
		err = runDoctor(args)
	case "bootstrap":
		err = runBootstrap(args)
	case "artifact":
		err = runArtifact(args)
	case "support":
		err = runSupport(args)
	case "supervisor":
		err = runSupervisor(args)
	case "release":
		err = runRelease(args)
	case "help", "-h", "--help":
		usage()
		return
	default:
		err = fmt.Errorf("unknown command %q", command)
	}

	if err != nil {
		fmt.Fprintln(os.Stderr, "edmgctl:", err)
		os.Exit(1)
	}
}

func runDoctor(args []string) error {
	fs := flag.NewFlagSet("doctor", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	repo := fs.String("repo", ".", "repo root or any path inside the repo")
	asJSON := fs.Bool("json", false, "emit JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}

	report, err := support.CollectDoctorReport(*repo)
	if err != nil {
		return err
	}
	if *asJSON {
		return printJSON(report)
	}

	fmt.Printf("EDMG NVIDIA Workshop Doctor\n")
	fmt.Printf("Repo: %s\n", report.RepoRoot)
	fmt.Printf("Workshop: %s\n", report.StudioDir)
	fmt.Printf("Package: %s %s\n", report.Package.Name, report.Package.Version)
	fmt.Printf("Platform: %s\n", report.Platform)
	fmt.Printf("Git: %s (%s)\n", cleanState(report.Git.Clean), report.Git.Head)
	fmt.Printf("Bootstrap: %s\n", boolWord(report.Bootstrap.Exists))
	fmt.Printf("Workshop Home: %s\n", report.Bootstrap.Resolved.StudioHome)
	fmt.Printf("Supervisor known: %s\n", boolWord(report.Supervisor.Known))
	fmt.Printf("Supervisor healthy: %s\n", boolWord(report.Supervisor.Healthy))
	if report.Supervisor.State != nil {
		fmt.Printf("Supervisor url: %s\n", report.Supervisor.State.BaseURL)
	}
	fmt.Printf("Release manifest: %s\n", boolWord(report.Release.BundleManifestOK))
	for _, tool := range report.Tools {
		status := "missing"
		if tool.Found {
			status = tool.Version
			if status == "" {
				status = tool.Path
			}
		}
		fmt.Printf("- tool %-6s %s\n", tool.Name+":", status)
		if tool.Note != "" {
			fmt.Printf("  note: %s\n", tool.Note)
		}
	}
	for _, artifact := range report.Release.Artifacts {
		fmt.Printf("- artifact %-16s %s\n", artifact.Label+":", presentWord(artifact.Exists))
		fmt.Printf("  path: %s\n", artifact.Path)
	}
	if len(report.Bootstrap.OutsideStudioHome) > 0 {
		fmt.Printf("Outside Workshop Home: %s\n", strings.Join(report.Bootstrap.OutsideStudioHome, ", "))
	}
	if len(report.Warnings) > 0 {
		fmt.Println("Warnings:")
		for _, warning := range report.Warnings {
			fmt.Printf("- %s\n", warning)
		}
	}
	return nil
}

func runBootstrap(args []string) error {
	if len(args) == 0 || args[0] == "show" {
		return runBootstrapShow(args[1:])
	}
	return fmt.Errorf("unknown bootstrap subcommand %q", args[0])
}

func runBootstrapShow(args []string) error {
	fs := flag.NewFlagSet("bootstrap show", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	asJSON := fs.Bool("json", false, "emit JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}

	report, err := support.ReadBootstrapReport()
	if err != nil {
		return err
	}
	if *asJSON {
		return printJSON(report)
	}

	fmt.Printf("Bootstrap path: %s\n", report.Path)
	fmt.Printf("Exists: %s\n", boolWord(report.Exists))
	fmt.Printf("Workshop Home: %s\n", report.Resolved.StudioHome)
	fmt.Printf("Data: %s\n", report.Resolved.DataDir)
	fmt.Printf("Models: %s\n", report.Resolved.ModelsDir)
	fmt.Printf("Cache: %s\n", report.Resolved.CacheRoot)
	fmt.Printf("Logs: %s\n", report.Resolved.LogsDir)
	fmt.Printf("External: %s\n", report.Resolved.ExternalDir)
	fmt.Printf("AI provider: %s / %s\n", fallback(report.Config.AISettings.Mode, "unset"), fallback(report.Config.AISettings.Provider, "unset"))
	fmt.Printf("Pending migration: %s\n", boolWord(report.PendingMigration))
	if report.LastMigrationOK != nil {
		fmt.Printf("Last migration ok: %t\n", *report.LastMigrationOK)
	}
	if len(report.OutsideStudioHome) > 0 {
		fmt.Printf("Outside Workshop Home: %s\n", strings.Join(report.OutsideStudioHome, ", "))
	}
	return nil
}

func runRelease(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("missing release subcommand")
	}
	switch args[0] {
	case "status":
		return runReleaseStatus(args[1:])
	case "build":
		return runReleaseBuild(args[1:])
	case "validate":
		return runReleaseValidate(args[1:])
	case "verify-manifest":
		return runReleaseVerifyManifest(args[1:])
	default:
		return fmt.Errorf("unknown release subcommand %q", args[0])
	}
}

func runArtifact(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("missing artifact subcommand")
	}
	switch args[0] {
	case "list":
		return runArtifactList(args[1:])
	case "manifest":
		return runArtifactManifest(args[1:])
	default:
		return fmt.Errorf("unknown artifact subcommand %q", args[0])
	}
}

func runSupport(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("missing support subcommand")
	}
	switch args[0] {
	case "export":
		return runSupportExport(args[1:])
	default:
		return fmt.Errorf("unknown support subcommand %q", args[0])
	}
}

func runArtifactList(args []string) error {
	fs := flag.NewFlagSet("artifact list", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	repo := fs.String("repo", ".", "repo root or any path inside the repo")
	asJSON := fs.Bool("json", false, "emit JSON")
	hashes := fs.Bool("hashes", false, "include sha256 digests")
	if err := fs.Parse(args); err != nil {
		return err
	}

	artifacts, err := support.CollectArtifactInventory(*repo, *hashes)
	if err != nil {
		return err
	}
	if *asJSON {
		return printJSON(artifacts)
	}

	for _, artifact := range artifacts {
		fmt.Printf("%s: %s\n", artifact.Label, presentWord(artifact.Exists))
		fmt.Printf("  path: %s\n", artifact.Path)
		if artifact.Exists {
			fmt.Printf("  size: %d bytes\n", artifact.Size)
			fmt.Printf("  modified: %s\n", artifact.Modified)
			if artifact.SHA256 != "" {
				fmt.Printf("  sha256: %s\n", artifact.SHA256)
			}
		}
	}
	return nil
}

func runArtifactManifest(args []string) error {
	fs := flag.NewFlagSet("artifact manifest", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	repo := fs.String("repo", ".", "repo root or any path inside the repo")
	out := fs.String("out", "", "write JSON manifest to this path")
	hashes := fs.Bool("hashes", true, "include sha256 digests")
	if err := fs.Parse(args); err != nil {
		return err
	}

	manifest, err := support.BuildArtifactManifest(*repo, *hashes)
	if err != nil {
		return err
	}
	if strings.TrimSpace(*out) == "" {
		return printJSON(manifest)
	}

	data, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(*out, append(data, '\n'), 0o644)
}

func runSupportExport(args []string) error {
	fs := flag.NewFlagSet("support export", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	repo := fs.String("repo", ".", "repo root or any path inside the repo")
	out := fs.String("out", "", "write the support bundle zip to this path")
	asJSON := fs.Bool("json", false, "emit JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}

	summary, err := support.ExportSupportBundle(*repo, *out)
	if *asJSON {
		return printJSON(summary)
	}
	if err != nil {
		return err
	}

	fmt.Printf("Bundle: %s\n", summary.OutputPath)
	fmt.Printf("Entries: %d\n", len(summary.Entries)+2)
	for _, entry := range summary.Entries {
		fmt.Printf("- %s\n", entry.Name)
	}
	fmt.Println("- README.txt")
	fmt.Println("- bundle-summary.json")
	return nil
}

func runSupervisor(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("missing supervisor subcommand")
	}
	switch args[0] {
	case "start":
		return runSupervisorStart(args[1:])
	case "status":
		return runSupervisorStatus(args[1:])
	case "stop":
		return runSupervisorStop(args[1:])
	default:
		return fmt.Errorf("unknown supervisor subcommand %q", args[0])
	}
}

func runSupervisorStart(args []string) error {
	fs := flag.NewFlagSet("supervisor start", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	repo := fs.String("repo", ".", "repo root or any path inside the repo")
	host := fs.String("host", "127.0.0.1", "backend host")
	port := fs.Int("port", 7863, "backend port (use 0 for automatic selection)")
	wait := fs.Bool("wait", true, "wait for /health before returning")
	timeout := fs.Duration("timeout", 90*time.Second, "health wait timeout when --wait is enabled")
	asJSON := fs.Bool("json", false, "emit JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}

	waitTimeout := time.Duration(0)
	if *wait {
		waitTimeout = *timeout
	}
	status, err := support.StartManagedBackend(*repo, *host, *port, waitTimeout)
	if *asJSON {
		_ = printJSON(status)
		return err
	}
	if err != nil {
		return err
	}
	fmt.Printf("Supervisor state file: %s\n", status.StateFile)
	fmt.Printf("Backend pid: %d\n", status.State.PID)
	fmt.Printf("Backend url: %s\n", status.State.BaseURL)
	if status.State.LogPath != "" {
		fmt.Printf("Log: %s\n", status.State.LogPath)
	}
	fmt.Printf("Healthy: %s\n", boolWord(status.Healthy))
	if status.HealthNote != "" {
		fmt.Printf("Note: %s\n", status.HealthNote)
	}
	return nil
}

func runSupervisorStatus(args []string) error {
	fs := flag.NewFlagSet("supervisor status", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	asJSON := fs.Bool("json", false, "emit JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}

	status, err := support.GetSupervisorStatus()
	if err != nil {
		return err
	}
	if *asJSON {
		return printJSON(status)
	}
	fmt.Printf("Supervisor state file: %s\n", status.StateFile)
	fmt.Printf("Known: %s\n", boolWord(status.Known))
	fmt.Printf("Process alive: %s\n", boolWord(status.ProcessAlive))
	fmt.Printf("Healthy: %s\n", boolWord(status.Healthy))
	if status.State != nil {
		fmt.Printf("Backend url: %s\n", status.State.BaseURL)
		fmt.Printf("PID: %d\n", status.State.PID)
		fmt.Printf("Workshop Home: %s\n", status.State.StudioHome)
		if status.State.LogPath != "" {
			fmt.Printf("Log: %s\n", status.State.LogPath)
		}
	}
	if status.HealthNote != "" {
		fmt.Printf("Note: %s\n", status.HealthNote)
	}
	return nil
}

func runSupervisorStop(args []string) error {
	fs := flag.NewFlagSet("supervisor stop", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	asJSON := fs.Bool("json", false, "emit JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}

	status, err := support.StopManagedBackend()
	if err != nil {
		return err
	}
	if *asJSON {
		return printJSON(status)
	}
	fmt.Printf("Supervisor state file: %s\n", status.StateFile)
	fmt.Printf("Stopped: %s\n", boolWord(status.Known))
	if status.State != nil {
		fmt.Printf("Last PID: %d\n", status.State.PID)
		fmt.Printf("Last URL: %s\n", status.State.BaseURL)
		if status.State.LogPath != "" {
			fmt.Printf("Last log: %s\n", status.State.LogPath)
		}
	}
	return nil
}

func runReleaseStatus(args []string) error {
	fs := flag.NewFlagSet("release status", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	repo := fs.String("repo", ".", "repo root or any path inside the repo")
	asJSON := fs.Bool("json", false, "emit JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}

	status, err := support.CollectReleaseStatus(*repo)
	if err != nil {
		return err
	}
	if *asJSON {
		return printJSON(status)
	}

	fmt.Printf("Workshop: %s\n", status.StudioDir)
	fmt.Printf("Package: %s %s\n", status.Package.Name, status.Package.Version)
	fmt.Printf("Windows release host: %s\n", boolWord(status.WindowsReleaseHost))
	fmt.Printf("Backend manifest: %s\n", presentWord(status.BundleManifestOK))
	if status.BundleSourceHash != "" {
		fmt.Printf("Bundle source hash: %s\n", status.BundleSourceHash)
	}
	for _, artifact := range status.Artifacts {
		fmt.Printf("- %s: %s\n", artifact.Label, presentWord(artifact.Exists))
		fmt.Printf("  %s\n", artifact.Path)
	}
	return nil
}

func runReleaseBuild(args []string) error {
	fs := flag.NewFlagSet("release build", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	repo := fs.String("repo", ".", "repo root or any path inside the repo")
	if err := fs.Parse(args); err != nil {
		return err
	}
	return support.RunReleaseBuild(*repo)
}

func runReleaseValidate(args []string) error {
	fs := flag.NewFlagSet("release validate", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	repo := fs.String("repo", ".", "repo root or any path inside the repo")
	if err := fs.Parse(args); err != nil {
		return err
	}
	return support.RunReleaseValidate(*repo)
}

func runReleaseVerifyManifest(args []string) error {
	fs := flag.NewFlagSet("release verify-manifest", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	repo := fs.String("repo", ".", "repo root or any path inside the repo")
	manifest := fs.String("manifest", "", "path to a saved artifact manifest JSON")
	asJSON := fs.Bool("json", false, "emit JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}

	result, err := support.VerifyArtifactManifest(*repo, *manifest)
	if *asJSON {
		_ = printJSON(result)
		return err
	}
	if err != nil {
		return err
	}

	fmt.Printf("Manifest: %s\n", result.ManifestPath)
	fmt.Printf("Matches: %s\n", boolWord(result.Matches))
	if len(result.Issues) > 0 {
		fmt.Println("Issues:")
		for _, issue := range result.Issues {
			fmt.Printf("- %s\n", issue)
		}
	}
	return nil
}

func printJSON(value any) error {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	return encoder.Encode(value)
}

func cleanState(clean bool) string {
	if clean {
		return "clean"
	}
	return "dirty"
}

func boolWord(value bool) string {
	if value {
		return "yes"
	}
	return "no"
}

func presentWord(value bool) string {
	if value {
		return "present"
	}
	return "missing"
}

func fallback(value, defaultValue string) string {
	if strings.TrimSpace(value) == "" {
		return defaultValue
	}
	return value
}

func usage() {
	fmt.Println(`edmgctl <command> [options]

Commands:
  doctor                 Summarize repo, toolchain, bootstrap, and release state
  bootstrap show         Print bootstrap config and resolved Workshop-managed roots
  artifact list          List release artifacts with size and optional hashes
  artifact manifest      Emit a JSON manifest for release artifacts
  support export         Package doctor/bootstrap/release data into one zip
  supervisor start       Start the packaged backend with Workshop-managed env
  supervisor status      Inspect the managed backend process and /health
  supervisor stop        Stop the managed backend process
  release status         Inspect bundled artifacts and packaged outputs
  release build          Run the existing Windows release build (pnpm run dist:win)
  release validate       Run the existing release proof (pnpm run validate:release)
  release verify-manifest
                        Compare current packaged artifacts with a saved manifest

Use --json on read-only commands for machine-readable output.`)
}
