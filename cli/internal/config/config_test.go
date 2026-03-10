package config

import "testing"

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
}

func TestMaskedAPIKey(t *testing.T) {
	masked := MaskedAPIKey("newsly_ak_1234567890")
	if masked == "" || masked == "newsly_ak_1234567890" {
		t.Fatalf("expected masked api key, got %q", masked)
	}
}
