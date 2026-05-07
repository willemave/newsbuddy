package cmd

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/willem/newsbuddy/cli/internal/config"
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

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})

	exitCode := cli.run(
		"jobs", "get", "77",
	)

	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	envelope := cli.envelope(t)
	if envelope["command"] != "jobs.get" {
		t.Fatalf("unexpected command: %#v", envelope["command"])
	}
	if envelope["ok"] != true {
		t.Fatalf("expected ok=true: %#v", envelope["ok"])
	}
}

func TestContentSubmitWaitAddsJobPayload(t *testing.T) {
	var jobPollCount atomic.Int32
	var contentPollCount atomic.Int32
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
			if jobPollCount.Add(1) > 1 {
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
		case r.Method == http.MethodGet && r.URL.Path == "/api/content/9":
			if contentPollCount.Add(1) == 1 {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusNotFound)
				if err := json.NewEncoder(w).Encode(map[string]any{"detail": "Content not found"}); err != nil {
					t.Fatalf("encode json: %v", err)
				}
				return
			}
			writeJSON(t, w, map[string]any{
				"body_available":      true,
				"bullet_points":       []any{},
				"can_subscribe":       false,
				"checked_out_at":      nil,
				"checked_out_by":      nil,
				"content_type":        "article",
				"created_at":          "2026-04-09T12:00:00Z",
				"discussion_url":      nil,
				"display_title":       "Example Story",
				"error_message":       nil,
				"full_markdown":       nil,
				"id":                  9,
				"image_url":           nil,
				"is_favorited":        false,
				"is_read":             false,
				"metadata":            map[string]any{},
				"news_article_url":    nil,
				"news_discussion_url": nil,
				"news_key_points":     nil,
				"news_summary":        nil,
				"processed_at":        "2026-04-09T12:00:03Z",
				"publication_date":    nil,
				"quotes":              []any{},
				"retry_count":         0,
				"short_summary":       nil,
				"source":              "self submission",
				"source_url":          "https://example.com/story",
				"status":              "completed",
				"structured_summary":  nil,
				"summary":             nil,
				"thumbnail_url":       nil,
				"title":               "Example Story",
				"topics":              []string{},
				"updated_at":          "2026-04-09T12:00:03Z",
				"url":                 "https://example.com/story",
			})
		case r.Method == http.MethodGet && r.URL.Path == "/api/content/submissions/list":
			if got := r.URL.Query().Get("limit"); got != "100" {
				t.Fatalf("unexpected submissions limit: %q", got)
			}
			writeJSON(t, w, map[string]any{
				"submissions": []map[string]any{
					{
						"id":            9,
						"status":        "processing",
						"error_message": nil,
					},
				},
			})
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})

	exitCode := cli.run(
		"content", "submit", "https://example.com/story",
		"--wait",
		"--wait-interval", "1ms",
		"--wait-timeout", "1s",
	)

	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	envelope := cli.envelope(t)
	if envelope["job"] == nil {
		t.Fatalf("expected job payload in envelope")
	}
}

func TestContentSubmitWaitReturnsErrorWhenSubmissionFails(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/content/submit":
			writeJSON(t, w, map[string]any{
				"content_id":     9,
				"content_type":   "article",
				"status":         "pending",
				"already_exists": false,
				"message":        "Content queued for processing",
				"task_id":        314,
			})
		case r.Method == http.MethodGet && r.URL.Path == "/api/jobs/314":
			writeJSON(t, w, map[string]any{
				"id":          314,
				"task_type":   "PROCESS_CONTENT",
				"status":      "completed",
				"queue_name":  "default",
				"payload":     map[string]any{},
				"retry_count": 0,
			})
		case r.Method == http.MethodGet && r.URL.Path == "/api/content/9":
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusNotFound)
			if err := json.NewEncoder(w).Encode(map[string]any{"detail": "Content not found"}); err != nil {
				t.Fatalf("encode json: %v", err)
			}
		case r.Method == http.MethodGet && r.URL.Path == "/api/content/submissions/list":
			writeJSON(t, w, map[string]any{
				"submissions": []map[string]any{
					{
						"id":            9,
						"status":        "failed",
						"error_message": "content extraction failed",
					},
				},
			})
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})

	exitCode := cli.run(
		"content", "submit", "https://example.com/story",
		"--wait",
		"--wait-interval", "1ms",
		"--wait-timeout", "1s",
	)

	if exitCode != 1 {
		t.Fatalf("expected exit 1, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	envelope := cli.envelope(t)
	if envelope["ok"] != false {
		t.Fatalf("expected ok=false: %#v", envelope["ok"])
	}
	requireErrorMessage(t, envelope, "content extraction failed")
}

func TestContentSummarizeSetsFavoriteAndMarkRead(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		if r.Method != http.MethodPost || r.URL.Path != "/api/content/submit" {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("read body: %v", err)
		}
		payload := string(body)
		if !strings.Contains(payload, `"url":"https://example.com/story"`) {
			t.Fatalf("expected submitted URL in payload: %s", payload)
		}
		if !strings.Contains(payload, `"save_to_knowledge_and_mark_read":true`) {
			t.Fatalf("expected save_to_knowledge_and_mark_read in payload: %s", payload)
		}
		writeJSON(t, w, map[string]any{
			"content_id":     9,
			"content_type":   "article",
			"status":         "new",
			"already_exists": false,
			"message":        "Content queued for analysis",
			"task_id":        314,
		})
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})

	exitCode := cli.run(
		"content", "summarize", "https://example.com/story",
	)

	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	envelope := cli.envelope(t)
	if envelope["command"] != "content.summarize" {
		t.Fatalf("unexpected command: %#v", envelope["command"])
	}
	if envelope["ok"] != true {
		t.Fatalf("expected ok=true: %#v", envelope["ok"])
	}
}

func TestContentSubmitRejectsNonPositiveWaitIntervalBeforeRequest(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})

	exitCode := cli.run(
		"content", "submit", "https://example.com/story",
		"--wait",
		"--wait-interval", "0s",
	)

	if exitCode != 1 {
		t.Fatalf("expected exit 1, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	requireErrorMessage(t, cli.envelope(t), "wait-interval must be greater than zero")
}

func TestOnboardingStartRejectsNonPositiveWaitIntervalBeforeRequest(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})

	exitCode := cli.run(
		"onboarding", "start",
		"--brief", "AI news",
		"--wait",
		"--wait-interval", "0s",
	)

	if exitCode != 1 {
		t.Fatalf("expected exit 1, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	requireErrorMessage(t, cli.envelope(t), "wait-interval must be greater than zero")
}

func TestNewsListOutputsEnvelope(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		if r.Method != http.MethodGet || r.URL.Path != "/api/news/items" {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		if got := r.URL.Query().Get("limit"); got != "2" {
			t.Fatalf("unexpected limit query: %q", got)
		}
		if got := r.URL.Query().Get("read_filter"); got != "read" {
			t.Fatalf("unexpected read_filter query: %q", got)
		}
		writeJSON(t, w, map[string]any{
			"available_dates": []string{},
			"content_types":   []string{},
			"contents":        []any{},
			"meta": map[string]any{
				"has_more":    false,
				"next_cursor": nil,
				"page_size":   0,
				"total":       nil,
			},
		})
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})

	exitCode := cli.run(
		"news", "list",
		"--limit", "2",
		"--read-filter", "read",
	)

	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	envelope := cli.envelope(t)
	if envelope["command"] != "news.list" {
		t.Fatalf("unexpected command: %#v", envelope["command"])
	}
	if envelope["ok"] != true {
		t.Fatalf("expected ok=true: %#v", envelope["ok"])
	}
}

func TestNewsConvertOutputsEnvelope(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		if r.Method != http.MethodPost || r.URL.Path != "/api/news/items/7/convert-to-article" {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		writeJSON(t, w, map[string]any{
			"status":         "success",
			"news_item_id":   7,
			"new_content_id": 42,
			"already_exists": false,
			"message":        "Article created and queued for processing",
		})
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})

	exitCode := cli.run(
		"news", "convert", "7",
	)

	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	envelope := cli.envelope(t)
	if envelope["command"] != "news.convert" {
		t.Fatalf("unexpected command: %#v", envelope["command"])
	}
	if envelope["ok"] != true {
		t.Fatalf("expected ok=true: %#v", envelope["ok"])
	}
}

func TestSourcesAddReturnsBackendDetailOnConflict(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		if r.Method != http.MethodPost || r.URL.Path != "/api/scrapers/subscribe" {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(t, w, map[string]any{
			"detail": "Scraper config already exists for this feed",
		})
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})

	exitCode := cli.run(
		"sources", "add", "https://example.com/feed",
		"--feed-type", "atom",
	)

	if exitCode != 1 {
		t.Fatalf("expected exit 1, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	envelope := cli.envelope(t)
	if envelope["ok"] != false {
		t.Fatalf("expected ok=false: %#v", envelope["ok"])
	}
	requireErrorMessage(t, envelope, "Scraper config already exists for this feed")
	errorPayload := envelope["error"].(map[string]any)
	if int(errorPayload["status_code"].(float64)) != http.StatusBadRequest {
		t.Fatalf("unexpected status_code: %#v", errorPayload["status_code"])
	}
}

func TestSourcesAddRejectsUnsupportedFeedTypeLocally(t *testing.T) {
	cli := newTestCLI(t, config.FileConfig{
		ServerURL: "http://example.com",
		APIKey:    "newsly_ak_test",
	})

	exitCode := cli.run(
		"sources", "add", "https://example.com/feed",
		"--feed-type", "rss",
	)

	if exitCode != 1 {
		t.Fatalf("expected exit 1, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	envelope := cli.envelope(t)
	if envelope["ok"] != false {
		t.Fatalf("expected ok=false: %#v", envelope["ok"])
	}
	requireErrorMessage(t, envelope, `unsupported feed type "rss"; expected one of: atom, substack, podcast_rss`)
}

func writeJSON(t *testing.T, w http.ResponseWriter, payload any) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(payload); err != nil {
		t.Fatalf("encode json: %v", err)
	}
}
