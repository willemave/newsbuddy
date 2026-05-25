package cmd

import (
	"crypto/sha256"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/willem/newsbuddy/cli/internal/config"
)

func TestAuthLoginPersistsAPIKey(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/agent/cli/link/start":
			writeJSON(t, w, map[string]any{
				"session_id":            "session-123",
				"status":                "pending",
				"poll_token":            "poll-123",
				"approve_url":           "newsly://cli-link?session_id=session-123&approve_token=approve-123",
				"expires_at":            "2026-04-04T18:00:00Z",
				"poll_interval_seconds": 2,
			})
		case r.Method == http.MethodGet && r.URL.Path == "/api/agent/cli/link/session-123":
			if got := r.URL.Query().Get("poll_token"); got != "poll-123" {
				t.Fatalf("unexpected poll token: %q", got)
			}
			writeJSON(t, w, map[string]any{
				"session_id": "session-123",
				"status":     "approved",
				"expires_at": "2026-04-04T18:00:00Z",
				"api_key":    "newsly_ak_linked_secret",
				"key_prefix": "newsly_ak_linked",
			})
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{})

	exitCode := cli.run(
		"--server", server.URL,
		"auth", "login",
		"--poll-interval", "1ms",
		"--poll-timeout", "1s",
	)

	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	cfg, err := config.Load(cli.configPath)
	if err != nil {
		t.Fatalf("load config: %v", err)
	}
	if cfg.ServerURL != server.URL {
		t.Fatalf("expected server to persist, got %q", cfg.ServerURL)
	}
	if cfg.APIKey != "newsly_ak_linked_secret" {
		t.Fatalf("expected linked API key to persist, got %q", cfg.APIKey)
	}
	if !strings.Contains(cli.stderr.String(), "Scan this QR code in the Newsbuddy app") {
		t.Fatalf("expected QR instructions on stderr, got %q", cli.stderr.String())
	}
	if !strings.Contains(cli.stderr.String(), "newsly://cli-link?") {
		t.Fatalf("expected QR/link instructions on stderr, got %q", cli.stderr.String())
	}
	if !strings.Contains(cli.stderr.String(), "▀") && !strings.Contains(cli.stderr.String(), "▄") {
		t.Fatalf("expected terminal QR glyphs on stderr, got %q", cli.stderr.String())
	}
}

func TestLibrarySyncDownloadsAndPrunesFiles(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/api/agent/library/manifest":
			if got := r.URL.Query().Get("include_source"); got != "true" {
				t.Fatalf("unexpected include_source query: %q", got)
			}
			writeJSON(t, w, map[string]any{
				"generated_at":   "2026-04-04T18:00:00Z",
				"include_source": true,
				"documents": []map[string]any{
					{
						"relative_path":   "article/example/new-doc__2026-04-03__source__c1.md",
						"content_id":      1,
						"variant":         "source",
						"updated_at":      "2026-04-04T17:59:00Z",
						"size_bytes":      16,
						"checksum_sha256": testSHA256("Raw article body\n"),
					},
					{
						"relative_path":   "article/example/new-doc__2026-04-03__summary__c1.md",
						"content_id":      1,
						"variant":         "summary",
						"updated_at":      "2026-04-04T17:59:00Z",
						"size_bytes":      12,
						"checksum_sha256": testSHA256("# Hello\n"),
					},
				},
			})
		case r.Method == http.MethodGet && r.URL.Path == "/api/agent/library/file":
			switch got := r.URL.Query().Get("path"); got {
			case "article/example/new-doc__2026-04-03__source__c1.md":
				writeJSON(t, w, map[string]any{
					"relative_path":   got,
					"content_id":      1,
					"variant":         "source",
					"updated_at":      "2026-04-04T17:59:00Z",
					"checksum_sha256": testSHA256("Raw article body\n"),
					"text":            "Raw article body\n",
				})
			case "article/example/new-doc__2026-04-03__summary__c1.md":
				writeJSON(t, w, map[string]any{
					"relative_path":   got,
					"content_id":      1,
					"variant":         "summary",
					"updated_at":      "2026-04-04T17:59:00Z",
					"checksum_sha256": testSHA256("# Hello\n"),
					"text":            "# Hello\n",
				})
			default:
				t.Fatalf("unexpected file path query: %q", got)
			}
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()

	libraryRoot := filepath.Join(t.TempDir(), "library")
	stalePath := filepath.Join(libraryRoot, "article", "example", "old-doc.md")
	if err := os.MkdirAll(filepath.Dir(stalePath), 0o755); err != nil {
		t.Fatalf("mkdir stale dir: %v", err)
	}
	if err := os.WriteFile(stalePath, []byte("old"), 0o644); err != nil {
		t.Fatalf("write stale file: %v", err)
	}
	manifestPath := filepath.Join(libraryRoot, ".newsbuddy-manifest.json")
	if err := os.WriteFile(manifestPath, []byte(`{"files":{"article/example/old-doc.md":"old-sha"}}`), 0o644); err != nil {
		t.Fatalf("write stale manifest: %v", err)
	}

	cli := newTestCLI(t, config.FileConfig{
		ServerURL:   server.URL,
		APIKey:      "newsly_ak_test",
		LibraryRoot: libraryRoot,
	})

	exitCode := cli.run(
		"library", "sync",
	)

	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	newPath := filepath.Join(libraryRoot, "article", "example", "new-doc__2026-04-03__summary__c1.md")
	data, err := os.ReadFile(newPath)
	if err != nil {
		t.Fatalf("read synced file: %v", err)
	}
	if string(data) != "# Hello\n" {
		t.Fatalf("unexpected synced file contents: %q", string(data))
	}
	sourcePath := filepath.Join(libraryRoot, "article", "example", "new-doc__2026-04-03__source__c1.md")
	sourceData, err := os.ReadFile(sourcePath)
	if err != nil {
		t.Fatalf("read synced source file: %v", err)
	}
	if string(sourceData) != "Raw article body\n" {
		t.Fatalf("unexpected synced source file contents: %q", string(sourceData))
	}
	if _, err := os.Stat(stalePath); !os.IsNotExist(err) {
		t.Fatalf("expected stale file to be pruned, stat err=%v", err)
	}
	manifestBytes, err := os.ReadFile(manifestPath)
	if err != nil {
		t.Fatalf("read manifest: %v", err)
	}
	if !strings.Contains(string(manifestBytes), "new-doc__2026-04-03__summary__c1.md") {
		t.Fatalf("expected manifest to update, got %s", string(manifestBytes))
	}
	if cli.stderr.Len() != 0 {
		t.Fatalf("expected no stderr output, got %q", cli.stderr.String())
	}
}

func TestLibrarySyncRefusesToPruneAllTrackedFilesByDefault(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/api/agent/library/manifest":
			writeJSON(t, w, map[string]any{
				"generated_at":   "2026-04-04T18:00:00Z",
				"include_source": true,
				"documents":      []map[string]any{},
			})
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()

	libraryRoot := filepath.Join(t.TempDir(), "library")
	trackedPath := filepath.Join(libraryRoot, "article", "example", "old-doc.md")
	if err := os.MkdirAll(filepath.Dir(trackedPath), 0o755); err != nil {
		t.Fatalf("mkdir tracked dir: %v", err)
	}
	if err := os.WriteFile(trackedPath, []byte("old"), 0o644); err != nil {
		t.Fatalf("write tracked file: %v", err)
	}
	manifestPath := filepath.Join(libraryRoot, ".newsbuddy-manifest.json")
	if err := os.WriteFile(manifestPath, []byte(`{"files":{"article/example/old-doc.md":"old-sha"}}`), 0o644); err != nil {
		t.Fatalf("write manifest: %v", err)
	}

	cli := newTestCLI(t, config.FileConfig{
		ServerURL:   server.URL,
		APIKey:      "newsly_ak_test",
		LibraryRoot: libraryRoot,
	})

	exitCode := cli.run("library", "sync")
	if exitCode == 0 {
		t.Fatalf("expected nonzero exit, stdout=%s stderr=%s", cli.stdout.String(), cli.stderr.String())
	}
	requireErrorMessage(
		t,
		cli.envelope(t),
		"remote library manifest is empty; refusing to delete all tracked files without --allow-prune-all",
	)
	if _, err := os.Stat(trackedPath); err != nil {
		t.Fatalf("expected tracked file to remain, stat err=%v", err)
	}
}

func TestLibrarySyncAllowsExplicitPruneAll(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/api/agent/library/manifest":
			writeJSON(t, w, map[string]any{
				"generated_at":   "2026-04-04T18:00:00Z",
				"include_source": true,
				"documents":      []map[string]any{},
			})
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()

	libraryRoot := filepath.Join(t.TempDir(), "library")
	trackedPath := filepath.Join(libraryRoot, "article", "example", "old-doc.md")
	if err := os.MkdirAll(filepath.Dir(trackedPath), 0o755); err != nil {
		t.Fatalf("mkdir tracked dir: %v", err)
	}
	if err := os.WriteFile(trackedPath, []byte("old"), 0o644); err != nil {
		t.Fatalf("write tracked file: %v", err)
	}
	manifestPath := filepath.Join(libraryRoot, ".newsbuddy-manifest.json")
	if err := os.WriteFile(manifestPath, []byte(`{"files":{"article/example/old-doc.md":"old-sha"}}`), 0o644); err != nil {
		t.Fatalf("write manifest: %v", err)
	}

	cli := newTestCLI(t, config.FileConfig{
		ServerURL:   server.URL,
		APIKey:      "newsly_ak_test",
		LibraryRoot: libraryRoot,
	})

	exitCode := cli.run("library", "sync", "--allow-prune-all")
	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}
	if _, err := os.Stat(trackedPath); !os.IsNotExist(err) {
		t.Fatalf("expected tracked file to be pruned, stat err=%v", err)
	}
}

func TestLibrarySyncRedownloadsCorruptLocalFile(t *testing.T) {
	remoteText := "# Correct\n"
	remoteChecksum := testSHA256(remoteText)
	fileRequests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/api/agent/library/manifest":
			writeJSON(t, w, map[string]any{
				"generated_at":   "2026-04-04T18:00:00Z",
				"include_source": true,
				"documents": []map[string]any{
					{
						"relative_path":   "article/example/doc__2026-04-03__summary__c1.md",
						"content_id":      1,
						"variant":         "summary",
						"updated_at":      "2026-04-04T17:59:00Z",
						"size_bytes":      len(remoteText),
						"checksum_sha256": remoteChecksum,
					},
				},
			})
		case r.Method == http.MethodGet && r.URL.Path == "/api/agent/library/file":
			fileRequests++
			writeJSON(t, w, map[string]any{
				"relative_path":   "article/example/doc__2026-04-03__summary__c1.md",
				"content_id":      1,
				"variant":         "summary",
				"updated_at":      "2026-04-04T17:59:00Z",
				"checksum_sha256": remoteChecksum,
				"text":            remoteText,
			})
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()

	libraryRoot := filepath.Join(t.TempDir(), "library")
	relativePath := "article/example/doc__2026-04-03__summary__c1.md"
	targetPath := filepath.Join(libraryRoot, filepath.FromSlash(relativePath))
	if err := os.MkdirAll(filepath.Dir(targetPath), 0o755); err != nil {
		t.Fatalf("mkdir target dir: %v", err)
	}
	if err := os.WriteFile(targetPath, []byte("corrupt\n"), 0o644); err != nil {
		t.Fatalf("write corrupt file: %v", err)
	}
	manifestPath := filepath.Join(libraryRoot, ".newsbuddy-manifest.json")
	manifestPayload := fmt.Sprintf(`{"files":{%q:%q}}`, relativePath, remoteChecksum)
	if err := os.WriteFile(manifestPath, []byte(manifestPayload), 0o644); err != nil {
		t.Fatalf("write manifest: %v", err)
	}

	cli := newTestCLI(t, config.FileConfig{
		ServerURL:   server.URL,
		APIKey:      "newsly_ak_test",
		LibraryRoot: libraryRoot,
	})

	exitCode := cli.run("library", "sync")
	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}
	if fileRequests != 1 {
		t.Fatalf("expected corrupt file to be redownloaded once, got %d requests", fileRequests)
	}
	data, err := os.ReadFile(targetPath)
	if err != nil {
		t.Fatalf("read target file: %v", err)
	}
	if string(data) != remoteText {
		t.Fatalf("expected repaired file, got %q", string(data))
	}
	envelope := cli.envelope(t)
	dataPayload := envelope["data"].(map[string]any)
	if int(dataPayload["repaired"].(float64)) != 1 {
		t.Fatalf("expected repaired=1, got %#v", dataPayload)
	}
}

func TestLibrarySyncRejectsDownloadedChecksumMismatch(t *testing.T) {
	expectedChecksum := testSHA256("# Expected\n")
	manifestPath := "article/example/doc__2026-04-03__summary__c1.md"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/api/agent/library/manifest":
			writeJSON(t, w, map[string]any{
				"generated_at":   "2026-04-04T18:00:00Z",
				"include_source": true,
				"documents": []map[string]any{
					{
						"relative_path":   manifestPath,
						"content_id":      1,
						"variant":         "summary",
						"updated_at":      "2026-04-04T17:59:00Z",
						"size_bytes":      11,
						"checksum_sha256": expectedChecksum,
					},
				},
			})
		case r.Method == http.MethodGet && r.URL.Path == "/api/agent/library/file":
			writeJSON(t, w, map[string]any{
				"relative_path":   manifestPath,
				"content_id":      1,
				"variant":         "summary",
				"updated_at":      "2026-04-04T17:59:00Z",
				"checksum_sha256": expectedChecksum,
				"text":            "# Different\n",
			})
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()

	libraryRoot := filepath.Join(t.TempDir(), "library")
	cli := newTestCLI(t, config.FileConfig{
		ServerURL:   server.URL,
		APIKey:      "newsly_ak_test",
		LibraryRoot: libraryRoot,
	})

	exitCode := cli.run("library", "sync")
	if exitCode == 0 {
		t.Fatalf("expected nonzero exit, stdout=%s stderr=%s", cli.stdout.String(), cli.stderr.String())
	}
	requireErrorMessage(
		t,
		cli.envelope(t),
		"downloaded checksum mismatch for article/example/doc__2026-04-03__summary__c1.md",
	)
	if _, err := os.Stat(filepath.Join(libraryRoot, filepath.FromSlash(manifestPath))); !os.IsNotExist(err) {
		t.Fatalf("expected mismatched file not to be written, stat err=%v", err)
	}
}

func TestLibrarySyncIsIdempotentAndPrunesEmptyDirectories(t *testing.T) {
	type syncPhase struct {
		documents []map[string]any
		files     map[string]string
	}

	phases := []syncPhase{
		{
			documents: []map[string]any{
				{
					"relative_path":   "article/example/doc__2026-04-03__summary__c1.md",
					"content_id":      1,
					"variant":         "summary",
					"updated_at":      "2026-04-04T17:59:00Z",
					"size_bytes":      8,
					"checksum_sha256": testSHA256("# Hello\n"),
				},
			},
			files: map[string]string{
				"article/example/doc__2026-04-03__summary__c1.md": "# Hello\n",
			},
		},
		{
			documents: []map[string]any{
				{
					"relative_path":   "article/example/doc__2026-04-03__summary__c1.md",
					"content_id":      1,
					"variant":         "summary",
					"updated_at":      "2026-04-04T17:59:00Z",
					"size_bytes":      8,
					"checksum_sha256": testSHA256("# Hello\n"),
				},
			},
			files: map[string]string{
				"article/example/doc__2026-04-03__summary__c1.md": "# Hello\n",
			},
		},
		{
			documents: []map[string]any{},
			files:     map[string]string{},
		},
	}

	phaseIndex := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}

		current := phases[phaseIndex]
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/api/agent/library/manifest":
			writeJSON(t, w, map[string]any{
				"generated_at":   "2026-04-04T18:00:00Z",
				"include_source": true,
				"documents":      current.documents,
			})
		case r.Method == http.MethodGet && r.URL.Path == "/api/agent/library/file":
			path := r.URL.Query().Get("path")
			text, ok := current.files[path]
			if !ok {
				t.Fatalf("unexpected file path query: %q", path)
			}
			writeJSON(t, w, map[string]any{
				"relative_path":   path,
				"content_id":      1,
				"variant":         "summary",
				"updated_at":      "2026-04-04T17:59:00Z",
				"checksum_sha256": testSHA256(text),
				"text":            text,
			})
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()

	libraryRoot := filepath.Join(t.TempDir(), "library")
	cli := newTestCLI(t, config.FileConfig{
		ServerURL:   server.URL,
		APIKey:      "newsly_ak_test",
		LibraryRoot: libraryRoot,
	})

	runAndDecode := func() map[string]any {
		cli.stdout.Reset()
		cli.stderr.Reset()
		exitCode := cli.run("library", "sync")
		if exitCode != 0 {
			t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
		}
		return cli.envelope(t)
	}

	first := runAndDecode()
	firstData := first["data"].(map[string]any)
	if int(firstData["downloaded"].(float64)) != 1 || int(firstData["unchanged"].(float64)) != 0 {
		t.Fatalf("unexpected first sync data: %#v", firstData)
	}

	phaseIndex = 1
	second := runAndDecode()
	secondData := second["data"].(map[string]any)
	if int(secondData["downloaded"].(float64)) != 0 || int(secondData["unchanged"].(float64)) != 1 {
		t.Fatalf("unexpected second sync data: %#v", secondData)
	}

	phaseIndex = 2
	cli.stdout.Reset()
	cli.stderr.Reset()
	exitCode := cli.run("library", "sync", "--allow-prune-all")
	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}
	third := cli.envelope(t)
	thirdData := third["data"].(map[string]any)
	if int(thirdData["deleted"].(float64)) != 1 || int(thirdData["document_count"].(float64)) != 0 {
		t.Fatalf("unexpected third sync data: %#v", thirdData)
	}

	if _, err := os.Stat(filepath.Join(libraryRoot, "article", "example")); !os.IsNotExist(err) {
		t.Fatalf("expected empty article/example directory to be pruned, stat err=%v", err)
	}

	manifestBytes, err := os.ReadFile(filepath.Join(libraryRoot, ".newsbuddy-manifest.json"))
	if err != nil {
		t.Fatalf("read manifest: %v", err)
	}
	if string(manifestBytes) != "{\n  \"files\": {}\n}\n" {
		t.Fatalf("unexpected manifest contents: %s", string(manifestBytes))
	}
}

func testSHA256(text string) string {
	sum := sha256.Sum256([]byte(text))
	return fmt.Sprintf("%x", sum[:])
}

func TestLibrarySyncCanExcludeSourceDocuments(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/api/agent/library/manifest":
			if got := r.URL.Query().Get("include_source"); got != "false" {
				t.Fatalf("unexpected include_source query: %q", got)
			}
			writeJSON(t, w, map[string]any{
				"generated_at":   "2026-04-04T18:00:00Z",
				"include_source": false,
				"documents": []map[string]any{
					{
						"relative_path":   "article/example/summary-only__2026-04-03__summary__c1.md",
						"content_id":      1,
						"variant":         "summary",
						"updated_at":      "2026-04-04T17:59:00Z",
						"size_bytes":      12,
						"checksum_sha256": testSHA256("# Summary\n"),
					},
				},
			})
		case r.Method == http.MethodGet && r.URL.Path == "/api/agent/library/file":
			if got := r.URL.Query().Get("path"); got != "article/example/summary-only__2026-04-03__summary__c1.md" {
				t.Fatalf("unexpected file path query: %q", got)
			}
			writeJSON(t, w, map[string]any{
				"relative_path":   "article/example/summary-only__2026-04-03__summary__c1.md",
				"content_id":      1,
				"variant":         "summary",
				"updated_at":      "2026-04-04T17:59:00Z",
				"checksum_sha256": testSHA256("# Summary\n"),
				"text":            "# Summary\n",
			})
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()

	libraryRoot := filepath.Join(t.TempDir(), "library")
	cli := newTestCLI(t, config.FileConfig{
		ServerURL:   server.URL,
		APIKey:      "newsly_ak_test",
		LibraryRoot: libraryRoot,
	})

	exitCode := cli.run("library", "sync", "--include-source=false")
	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	files, err := filepath.Glob(filepath.Join(libraryRoot, "article", "example", "*.md"))
	if err != nil {
		t.Fatalf("glob files: %v", err)
	}
	if len(files) != 1 || !strings.HasSuffix(files[0], "__summary__c1.md") {
		t.Fatalf("expected only summary file, got %#v", files)
	}
}
