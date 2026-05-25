package config

import (
	"path/filepath"
	"testing"
)

func TestResolveRuntimePrecedence(t *testing.T) {
	t.Setenv(EnvServerURL, "https://env.example.com")
	t.Setenv(EnvAPIKey, "env-key")

	path := t.TempDir() + "/config.json"
	if err := Save(path, FileConfig{
		ServerURL: "https://file.example.com",
		APIKey:    "file-key",
	}); err != nil {
		t.Fatalf("save config: %v", err)
	}

	runtimeCfg, err := ResolveRuntime(path, "https://flag.example.com", "flag-key")
	if err != nil {
		t.Fatalf("resolve runtime: %v", err)
	}

	if runtimeCfg.ServerURL != "https://flag.example.com" {
		t.Fatalf("server precedence mismatch: %q", runtimeCfg.ServerURL)
	}
	if runtimeCfg.APIKey != "flag-key" {
		t.Fatalf("api key precedence mismatch: %q", runtimeCfg.APIKey)
	}
	if runtimeCfg.LibraryRoot == "" {
		t.Fatalf("expected default library root")
	}
}

func TestResolvePathSupportsConfigEnvAliases(t *testing.T) {
	cases := []struct {
		name    string
		envName string
	}{
		{name: "canonical", envName: EnvConfigPath},
		{name: "canonical path alias", envName: EnvConfigPathAlias},
		{name: "legacy", envName: LegacyEnvConfigPath},
		{name: "legacy path alias", envName: LegacyEnvConfigPathAlt},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			want := filepath.Join(t.TempDir(), "config.json")
			t.Setenv(tc.envName, want)

			if got := ResolvePath(""); got != want {
				t.Fatalf("expected %s to resolve to %q, got %q", tc.envName, want, got)
			}
		})
	}
}

func TestResolveRuntimeSupportsLegacyEnvAliases(t *testing.T) {
	t.Setenv(LegacyEnvServerURL, "https://legacy.example.com")
	t.Setenv(LegacyEnvAPIKey, "legacy-key")

	runtimeCfg, err := ResolveRuntime(filepath.Join(t.TempDir(), "config.json"), "", "")
	if err != nil {
		t.Fatalf("resolve runtime: %v", err)
	}

	if runtimeCfg.ServerURL != "https://legacy.example.com" {
		t.Fatalf("server legacy env mismatch: %q", runtimeCfg.ServerURL)
	}
	if runtimeCfg.APIKey != "legacy-key" {
		t.Fatalf("api key legacy env mismatch: %q", runtimeCfg.APIKey)
	}
}

func TestMaskedAPIKey(t *testing.T) {
	masked := MaskedAPIKey("newsly_ak_1234567890")
	if masked == "" || masked == "newsly_ak_1234567890" {
		t.Fatalf("expected masked api key, got %q", masked)
	}
}
