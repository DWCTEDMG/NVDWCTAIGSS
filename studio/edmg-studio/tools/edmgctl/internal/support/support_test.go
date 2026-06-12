package support

import (
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestDefaultStoragePaths(t *testing.T) {
	home := filepath.Join("D:\\", "EDMG-NVIDIA-Workshop")
	paths := DefaultStoragePaths(home)

	if paths.DataDir != filepath.Join(home, "data") {
		t.Fatalf("expected data dir under studio home, got %s", paths.DataDir)
	}
	if paths.ElectronUserData != filepath.Join(home, "electron") {
		t.Fatalf("expected electron user data under studio home, got %s", paths.ElectronUserData)
	}
}

func TestResolveStoragePathsWithOverrides(t *testing.T) {
	home := filepath.Join("D:\\", "EDMG-NVIDIA-Workshop")
	cfg := BootstrapConfig{
		StudioHome: home,
		StorageSettings: StorageOverrides{
			CacheRoot: filepath.Join(home, "cache-alt"),
			LogsDir:   filepath.Join(home, "logs-alt"),
		},
	}

	paths := ResolveStoragePaths(home, cfg)
	if paths.CacheRoot != filepath.Join(home, "cache-alt") {
		t.Fatalf("expected cache override, got %s", paths.CacheRoot)
	}
	if paths.LogsDir != filepath.Join(home, "logs-alt") {
		t.Fatalf("expected logs override, got %s", paths.LogsDir)
	}
	if paths.ModelsDir != filepath.Join(home, "models") {
		t.Fatalf("expected default models dir, got %s", paths.ModelsDir)
	}
}

func TestNewArtifactStatusWithHash(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "artifact.bin")
	content := []byte("edmg-artifact\n")
	if err := os.WriteFile(target, content, 0o644); err != nil {
		t.Fatalf("write artifact: %v", err)
	}

	status := newArtifactStatus("fixture", target, true)
	if !status.Exists {
		t.Fatalf("expected artifact to exist")
	}
	if status.Size != int64(len(content)) {
		t.Fatalf("expected size %d, got %d", len(content), status.Size)
	}
	expected := fmt.Sprintf("%x", sha256.Sum256(content))
	if status.SHA256 != expected {
		t.Fatalf("expected sha256 %s, got %s", expected, status.SHA256)
	}
	if status.Modified == "" {
		t.Fatalf("expected modified timestamp")
	}
}

func TestBuildManagedBackendEnvIncludesCoreKeys(t *testing.T) {
	home := filepath.Join(t.TempDir(), "workshop-home")
	cfg := BootstrapConfig{
		AISettings: AISettings{
			Mode:                "nvidia_nim",
			Provider:            "nvidia_nim",
			OpenAICompatBaseURL: "https://integrate.api.nvidia.com/v1",
			OpenAICompatModel:   "meta/llama-3.1-70b-instruct",
		},
	}
	paths := DefaultStoragePaths(home)
	env := BuildManagedBackendEnv(cfg, paths, "127.0.0.1", 5999, filepath.Join(home, "ffmpeg.exe"))
	joined := strings.Join(env, "\n")

	for _, expected := range []string{
		"EDMG_STUDIO_HOME=" + paths.StudioHome,
		"EDMG_STUDIO_DATA_DIR=" + paths.DataDir,
		"EDMG_STUDIO_MODELS_DIR=" + paths.ModelsDir,
		"EDMG_STUDIO_CACHE_DIR=" + paths.CacheRoot,
		"EDMG_STUDIO_LOGS_DIR=" + paths.LogsDir,
		"EDMG_STUDIO_EXTERNAL_DIR=" + paths.ExternalDir,
		"EDMG_STUDIO_BACKEND_HOST=127.0.0.1",
		"EDMG_STUDIO_BACKEND_PORT=5999",
		"EDMG_AI_MODE=nvidia_nim",
		"EDMG_AI_PROVIDER=nvidia_nim",
		"EDMG_AI_OPENAI_COMPAT_BASE_URL=https://integrate.api.nvidia.com/v1",
		"EDMG_FFMPEG_PATH=" + filepath.Join(home, "ffmpeg.exe"),
	} {
		if !strings.Contains(joined, expected) {
			t.Fatalf("expected env to contain %q", expected)
		}
	}
}

func TestCompareArtifactSetsMatch(t *testing.T) {
	expected := []ArtifactStatus{
		{Label: "backend bundle", Path: `D:\out\backend.exe`, Exists: true, Size: 10, SHA256: "abc"},
	}
	current := []ArtifactStatus{
		{Label: "backend bundle", Path: `D:\out\backend.exe`, Exists: true, Size: 10, SHA256: "abc"},
	}

	issues := compareArtifactSets(expected, current)
	if len(issues) != 0 {
		t.Fatalf("expected no issues, got %v", issues)
	}
}

func TestCompareArtifactSetsMismatch(t *testing.T) {
	expected := []ArtifactStatus{
		{Label: "backend bundle", Path: `D:\out\backend.exe`, Exists: true, Size: 10, SHA256: "abc"},
	}
	current := []ArtifactStatus{
		{Label: "backend bundle", Path: `D:\out\backend.exe`, Exists: true, Size: 11, SHA256: "def"},
		{Label: "installer", Path: `D:\out\installer.exe`, Exists: true},
	}

	issues := compareArtifactSets(expected, current)
	if len(issues) < 2 {
		t.Fatalf("expected multiple issues, got %v", issues)
	}
}

func TestSupportBundleFileName(t *testing.T) {
	name := supportBundleFileName(time.Date(2026, 3, 21, 15, 4, 5, 0, time.UTC))
	if name != "edmg-support-20260321-150405.zip" {
		t.Fatalf("unexpected bundle filename %s", name)
	}
}

func TestReleaseProofPointersIncludeCoreProofs(t *testing.T) {
	proofs := releaseProofPointers(`D:\DWCTGenerativeSoundStudio`)
	joined := make([]string, 0, len(proofs))
	for _, proof := range proofs {
		joined = append(joined, proof.Command)
	}
	commands := strings.Join(joined, "\n")
	for _, expected := range []string{
		"pnpm run validate:release",
		"pnpm run validate:packaged-customer-flow",
		"pnpm run validate:packaged-upgrade-proof",
		"pnpm run validate:packaged-nvidia-setup",
	} {
		if !strings.Contains(commands, expected) {
			t.Fatalf("expected release proofs to contain %q", expected)
		}
	}
}
