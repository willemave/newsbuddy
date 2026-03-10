package config

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
)

const (
	EnvConfigPath       = "NEWSLY_AGENT_CONFIG"
	LegacyEnvConfigPath = "NEWSLY_AGENT_CONFIG_PATH"
	EnvServerURL        = "NEWSLY_AGENT_SERVER"
	EnvAPIKey           = "NEWSLY_AGENT_API_KEY"
)

type FileConfig struct {
	ServerURL string `json:"server_url,omitempty"`
	APIKey    string `json:"api_key,omitempty"`
}

type RuntimeConfig struct {
	Path      string
	ServerURL string
	APIKey    string
}

func DefaultPath() string {
	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		return ".newsly-agent.json"
	}
	return filepath.Join(home, ".config", "newsly-agent", "config.json")
}

func ResolvePath(explicit string) string {
	switch {
	case strings.TrimSpace(explicit) != "":
		return cleanPath(explicit)
	case strings.TrimSpace(os.Getenv(EnvConfigPath)) != "":
		return cleanPath(os.Getenv(EnvConfigPath))
	case strings.TrimSpace(os.Getenv(LegacyEnvConfigPath)) != "":
		return cleanPath(os.Getenv(LegacyEnvConfigPath))
	default:
		return DefaultPath()
	}
}

func Load(path string) (FileConfig, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return FileConfig{}, nil
	}
	if err != nil {
		return FileConfig{}, err
	}
	if strings.TrimSpace(string(data)) == "" {
		return FileConfig{}, nil
	}

	var cfg FileConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return FileConfig{}, err
	}
	cfg.ServerURL = strings.TrimSpace(cfg.ServerURL)
	cfg.APIKey = strings.TrimSpace(cfg.APIKey)
	return cfg, nil
}

func Save(path string, cfg FileConfig) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}

	payload, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	payload = append(payload, '\n')
	if err := os.WriteFile(path, payload, 0o600); err != nil {
		return err
	}
	return os.Chmod(path, 0o600)
}

func Update(path string, update func(FileConfig) FileConfig) (FileConfig, error) {
	cfg, err := Load(path)
	if err != nil {
		return FileConfig{}, err
	}
	cfg = update(cfg)
	cfg.ServerURL = strings.TrimSpace(cfg.ServerURL)
	cfg.APIKey = strings.TrimSpace(cfg.APIKey)
	if err := Save(path, cfg); err != nil {
		return FileConfig{}, err
	}
	return cfg, nil
}

func ResolveRuntime(pathOverride string, serverOverride string, apiKeyOverride string) (RuntimeConfig, error) {
	path := ResolvePath(pathOverride)
	fileCfg, err := Load(path)
	if err != nil {
		return RuntimeConfig{}, err
	}

	runtimeCfg := RuntimeConfig{
		Path:      path,
		ServerURL: fileCfg.ServerURL,
		APIKey:    fileCfg.APIKey,
	}

	if value := strings.TrimSpace(os.Getenv(EnvServerURL)); value != "" {
		runtimeCfg.ServerURL = value
	}
	if value := strings.TrimSpace(os.Getenv(EnvAPIKey)); value != "" {
		runtimeCfg.APIKey = value
	}
	if value := strings.TrimSpace(serverOverride); value != "" {
		runtimeCfg.ServerURL = value
	}
	if value := strings.TrimSpace(apiKeyOverride); value != "" {
		runtimeCfg.APIKey = value
	}

	return runtimeCfg, nil
}

func (c RuntimeConfig) ValidateRemote() error {
	if strings.TrimSpace(c.ServerURL) == "" {
		return errors.New("missing server_url; run `newsly-agent config set server ...` first")
	}
	if strings.TrimSpace(c.APIKey) == "" {
		return errors.New("missing api_key; run `newsly-agent config set api-key ...` first")
	}
	return nil
}

func MaskedAPIKey(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return ""
	}
	if len(raw) <= 8 {
		return "********"
	}
	return raw[:4] + strings.Repeat("*", len(raw)-8) + raw[len(raw)-4:]
}

func cleanPath(path string) string {
	if path == "" {
		return path
	}
	expanded := path
	if strings.HasPrefix(path, "~/") {
		if home, err := os.UserHomeDir(); err == nil && home != "" {
			expanded = filepath.Join(home, path[2:])
		}
	}
	absolute, err := filepath.Abs(expanded)
	if err != nil {
		return expanded
	}
	return absolute
}
