package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/willem/news_app/cli/internal/config"
)

func TestJobsGetOutputsEnvelope(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		if r.Method != http.MethodGet || r.URL.Path != "/api/jobs/77" {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		writeJSON(t, w, map[string]any{
			"id":          77,
			"task_type":   "PROCESS_CONTENT",
			"status":      "completed",
			"queue_name":  "default",
			"payload":     map[string]any{},
			"retry_count": 0,
		})
	}))
	defer server.Close()

	configPath := filepath.Join(t.TempDir(), "config.json")
	if err := config.Save(configPath, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	}); err != nil {
		t.Fatalf("save config: %v", err)
	}

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	app := New("test", &stdout, &stderr)

	exitCode := app.Execute(context.Background(), []string{
		"--config", configPath,
		"jobs", "get", "77",
	})

	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, stdout.String(), stderr.String())
	}

	var envelope map[string]any
	if err := json.Unmarshal(stdout.Bytes(), &envelope); err != nil {
		t.Fatalf("decode output: %v", err)
	}
	if envelope["command"] != "jobs.get" {
		t.Fatalf("unexpected command: %#v", envelope["command"])
	}
	if envelope["ok"] != true {
		t.Fatalf("expected ok=true: %#v", envelope["ok"])
	}
}

func TestContentSubmitWaitAddsJobPayload(t *testing.T) {
	var pollCount atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/content/submit":
			body, err := io.ReadAll(r.Body)
			if err != nil {
				t.Fatalf("read body: %v", err)
			}
			if !strings.Contains(string(body), "https://example.com/story") {
				t.Fatalf("expected submitted URL in payload: %s", string(body))
			}
			writeJSON(t, w, map[string]any{
				"content_id":     9,
				"content_type":   "article",
				"status":         "pending",
				"already_exists": false,
				"message":        "Content queued for processing",
				"task_id":        314,
			})
		case r.Method == http.MethodGet && r.URL.Path == "/api/jobs/314":
			status := "pending"
			if pollCount.Add(1) > 1 {
				status = "completed"
			}
			writeJSON(t, w, map[string]any{
				"id":          314,
				"task_type":   "PROCESS_CONTENT",
				"status":      status,
				"queue_name":  "default",
				"payload":     map[string]any{},
				"retry_count": 0,
			})
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()

	configPath := filepath.Join(t.TempDir(), "config.json")
	if err := config.Save(configPath, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	}); err != nil {
		t.Fatalf("save config: %v", err)
	}

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	app := New("test", &stdout, &stderr)

	exitCode := app.Execute(context.Background(), []string{
		"--config", configPath,
		"content", "submit", "https://example.com/story",
		"--wait",
		"--wait-interval", "1ms",
		"--wait-timeout", "1s",
	})

	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, stdout.String(), stderr.String())
	}

	var envelope map[string]any
	if err := json.Unmarshal(stdout.Bytes(), &envelope); err != nil {
		t.Fatalf("decode output: %v", err)
	}
	if envelope["job"] == nil {
		t.Fatalf("expected job payload in envelope")
	}
}

func writeJSON(t *testing.T, w http.ResponseWriter, payload any) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(payload); err != nil {
		t.Fatalf("encode json: %v", err)
	}
}
