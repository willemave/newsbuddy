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

func TestOutputFlagRejectsUnsupportedFormatBeforeCommand(t *testing.T) {
	cli := newTestCLI(t, config.FileConfig{})

	exitCode := cli.run("--output", "yaml", "version")
	if exitCode != 1 {
		t.Fatalf("expected exit 1, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}
	if cli.stdout.Len() != 0 {
		t.Fatalf("expected no stdout, got %s", cli.stdout.String())
	}
	if !strings.Contains(cli.stderr.String(), "unsupported output format; expected one of: json, text") {
		t.Fatalf("unexpected stderr: %q", cli.stderr.String())
	}
}

func TestJSONFlagOverridesTextOutput(t *testing.T) {
	cli := newTestCLI(t, config.FileConfig{})

	exitCode := cli.run("--output", "text", "--json", "version")
	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	envelope := cli.envelope(t)
	if envelope["command"] != "version" {
		t.Fatalf("unexpected command: %#v", envelope["command"])
	}
}

func TestContentListAcceptsNullClassification(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		if r.Method != http.MethodGet || r.URL.Path != "/api/content/" {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		if got := r.URL.Query().Get("limit"); got != "5" {
			t.Fatalf("unexpected limit: %q", got)
		}
		if got := r.URL.Query()["content_type"]; len(got) != 1 || got[0] != "article" {
			t.Fatalf("unexpected content_type: %#v", got)
		}
		writeJSON(t, w, map[string]any{
			"available_dates": []string{"2026-05-23"},
			"content_types":   []string{"article"},
			"contents": []map[string]any{
				{
					"classification": nil,
					"content_type":   "article",
					"created_at":     "2026-05-23T12:00:00Z",
					"id":             42,
					"status":         "completed",
					"title":          "Rust Overtakes Go in Cloud Infrastructure Adoption",
					"url":            "https://example.com/article",
				},
			},
			"meta": map[string]any{
				"has_more":    false,
				"next_cursor": nil,
				"page_size":   1,
				"total":       1,
			},
		})
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})

	exitCode := cli.run("content", "list", "--content-type", "article", "--limit", "5")
	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	envelope := cli.envelope(t)
	if envelope["command"] != "content.list" {
		t.Fatalf("unexpected command: %#v", envelope["command"])
	}
	if envelope["ok"] != true {
		t.Fatalf("expected ok=true: %#v", envelope["ok"])
	}
	data, ok := envelope["data"].(map[string]any)
	if !ok {
		t.Fatalf("expected data object: %#v", envelope["data"])
	}
	contents, ok := data["contents"].([]any)
	if !ok || len(contents) != 1 {
		t.Fatalf("expected one content item: %#v", data["contents"])
	}
	item, ok := contents[0].(map[string]any)
	if !ok {
		t.Fatalf("expected content item object: %#v", contents[0])
	}
	if got := item["title"]; got != "Rust Overtakes Go in Cloud Infrastructure Adoption" {
		t.Fatalf("unexpected title: %#v", got)
	}
	if value, exists := item["classification"]; exists && value != nil {
		t.Fatalf("expected null classification to be absent or null, got %#v", value)
	}
}

func TestContentSubmissionsListOutputsEnvelope(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		if r.Method != http.MethodGet || r.URL.Path != "/api/content/submissions/list" {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		if got := r.URL.Query().Get("limit"); got != "3" {
			t.Fatalf("unexpected limit: %q", got)
		}
		if got := r.URL.Query().Get("cursor"); got != "next-1" {
			t.Fatalf("unexpected cursor: %q", got)
		}
		writeJSON(t, w, map[string]any{
			"submissions": []map[string]any{
				{
					"id":           9,
					"url":          "https://example.com/story",
					"content_type": "article",
					"created_at":   "2026-04-09T12:00:00Z",
					"status":       "processing",
				},
			},
			"meta": map[string]any{
				"has_more":    false,
				"next_cursor": nil,
				"page_size":   1,
				"total":       1,
			},
		})
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})

	exitCode := cli.run("content", "submissions", "list", "--limit", "3", "--cursor", "next-1")
	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	envelope := cli.envelope(t)
	if envelope["command"] != "content.submissions.list" {
		t.Fatalf("unexpected command: %#v", envelope["command"])
	}
	data := envelope["data"].(map[string]any)
	submissions := data["submissions"].([]any)
	if len(submissions) != 1 {
		t.Fatalf("expected one submission, got %#v", submissions)
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
						"content_type":  "article",
						"created_at":    "2026-04-09T12:00:00Z",
						"id":            9,
						"status":        "processing",
						"error_message": nil,
						"url":           "https://example.com/story",
					},
				},
				"meta": map[string]any{
					"has_more":    false,
					"next_cursor": nil,
					"page_size":   1,
					"total":       1,
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
						"content_type":  "article",
						"created_at":    "2026-04-09T12:00:00Z",
						"id":            9,
						"status":        "failed",
						"error_message": "content extraction failed",
						"url":           "https://example.com/story",
					},
				},
				"meta": map[string]any{
					"has_more":    false,
					"next_cursor": nil,
					"page_size":   1,
					"total":       1,
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

func TestContentSubmitRejectsInvalidURLBeforeRequest(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})

	exitCode := cli.run("content", "submit", "not-a-url")
	if exitCode != 1 {
		t.Fatalf("expected exit 1, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	requireErrorMessage(t, cli.envelope(t), "url must use http or https")
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

func TestOnboardingStartOutputsEnvelope(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		if r.Method != http.MethodPost || r.URL.Path != "/api/agent/onboarding" {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("read body: %v", err)
		}
		payload := string(body)
		if !strings.Contains(payload, `"brief":"ai infrastructure"`) {
			t.Fatalf("expected brief in payload: %s", payload)
		}
		if !strings.Contains(payload, `"seed_urls":["https://example.com/a"]`) {
			t.Fatalf("expected seed url in payload: %s", payload)
		}
		if !strings.Contains(payload, `"seed_feeds":["https://example.com/feed.xml"]`) {
			t.Fatalf("expected seed feed in payload: %s", payload)
		}
		writeJSON(t, w, map[string]any{
			"run_id": 17,
			"status": "pending",
			"job_id": 91,
		})
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})
	exitCode := cli.run(
		"onboarding", "start",
		"--brief", "ai infrastructure",
		"--seed-url", "https://example.com/a",
		"--seed-feed", "https://example.com/feed.xml",
	)
	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}
	envelope := cli.envelope(t)
	if envelope["command"] != "onboarding.start" {
		t.Fatalf("unexpected envelope: %s", cli.stdout.String())
	}
	data := envelope["data"].(map[string]any)
	if int(data["run_id"].(float64)) != 17 {
		t.Fatalf("unexpected data: %#v", data)
	}
}

func TestOnboardingStatusAndCompleteOutputEnvelopes(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/api/agent/onboarding/11":
			writeJSON(t, w, map[string]any{
				"run_id":          11,
				"run_status":      "completed",
				"topic_summary":   "AI infrastructure",
				"inferred_topics": []string{"ai", "infrastructure"},
				"lanes":           []any{},
			})
		case r.Method == http.MethodPost && r.URL.Path == "/api/agent/onboarding/11/complete":
			body, err := io.ReadAll(r.Body)
			if err != nil {
				t.Fatalf("read body: %v", err)
			}
			if !strings.Contains(string(body), `"accept_all":true`) {
				t.Fatalf("unexpected payload: %s", string(body))
			}
			writeJSON(t, w, map[string]any{
				"configured_source_count":         2,
				"has_completed_new_user_tutorial": true,
				"has_completed_onboarding":        true,
				"inbox_count_estimate":            5,
				"longform_status":                 "queued",
				"status":                          "completed",
				"task_id":                         91,
			})
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()

	statusCLI := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})
	statusExitCode := statusCLI.run("onboarding", "status", "11")
	if statusExitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", statusExitCode, statusCLI.stdout.String(), statusCLI.stderr.String())
	}
	if statusCLI.envelope(t)["command"] != "onboarding.status" {
		t.Fatalf("unexpected status envelope: %s", statusCLI.stdout.String())
	}

	completeCLI := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})
	completeExitCode := completeCLI.run("onboarding", "complete", "11", "--accept-all")
	if completeExitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", completeExitCode, completeCLI.stdout.String(), completeCLI.stderr.String())
	}
	if completeCLI.envelope(t)["command"] != "onboarding.complete" {
		t.Fatalf("unexpected complete envelope: %s", completeCLI.stdout.String())
	}
}

func TestSearchOutputsEnvelope(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		if r.Method != http.MethodPost || r.URL.Path != "/api/agent/search" {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("read body: %v", err)
		}
		payload := string(body)
		if !strings.Contains(payload, `"query":"ai agents"`) {
			t.Fatalf("expected query in payload: %s", payload)
		}
		if !strings.Contains(payload, `"include_podcasts":false`) {
			t.Fatalf("expected include_podcasts=false in payload: %s", payload)
		}
		writeJSON(t, w, map[string]any{
			"results": []map[string]any{
				{
					"kind":     "web",
					"title":    "AI Agents",
					"url":      "https://example.com/agents",
					"provider": "exa",
				},
			},
		})
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})
	exitCode := cli.run("search", "ai agents", "--limit", "2", "--include-podcasts=false")
	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}
	if cli.envelope(t)["command"] != "search" {
		t.Fatalf("unexpected envelope: %s", cli.stdout.String())
	}
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

func TestNewsMarkReadOutputsEnvelope(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		if r.Method != http.MethodPost || r.URL.Path != "/api/news/items/mark-read" {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("read body: %v", err)
		}
		if !strings.Contains(string(body), `"content_ids":[7,8]`) {
			t.Fatalf("unexpected payload: %s", string(body))
		}
		writeJSON(t, w, map[string]any{
			"failed_ids":      []int{},
			"marked_count":    2,
			"status":          "success",
			"total_requested": 2,
		})
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})

	exitCode := cli.run("news", "mark-read", "7", "8")
	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	envelope := cli.envelope(t)
	if envelope["command"] != "news.mark-read" {
		t.Fatalf("unexpected command: %#v", envelope["command"])
	}
	data := envelope["data"].(map[string]any)
	if int(data["marked_count"].(float64)) != 2 {
		t.Fatalf("unexpected data: %#v", data)
	}
}

func TestSourcesListOutputsEnvelope(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer newsly_ak_test" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		if r.Method != http.MethodGet || r.URL.Path != "/api/scrapers/" {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		if got := r.URL.Query().Get("type"); got != "atom" {
			t.Fatalf("unexpected type: %q", got)
		}
		writeJSON(t, w, []map[string]any{
			{
				"id":           1,
				"scraper_type": "atom",
				"display_name": "Example Feed",
				"config":       map[string]any{"feed_url": "https://example.com/feed.xml"},
				"feed_url":     "https://example.com/feed.xml",
				"limit":        25,
				"is_active":    true,
				"created_at":   "2026-04-04T18:00:00Z",
			},
		})
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})
	exitCode := cli.run("sources", "list", "--type", "atom")
	if exitCode != 0 {
		t.Fatalf("expected exit 0, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}
	if cli.envelope(t)["command"] != "sources.list" {
		t.Fatalf("unexpected envelope: %s", cli.stdout.String())
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

func TestSourcesAddRejectsInvalidURLBeforeRequest(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
	}))
	defer server.Close()

	cli := newTestCLI(t, config.FileConfig{
		ServerURL: server.URL,
		APIKey:    "newsly_ak_test",
	})

	exitCode := cli.run(
		"sources", "add", "not-a-url",
		"--feed-type", "atom",
	)

	if exitCode != 1 {
		t.Fatalf("expected exit 1, got %d stdout=%s stderr=%s", exitCode, cli.stdout.String(), cli.stderr.String())
	}

	requireErrorMessage(t, cli.envelope(t), "url must use http or https")
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
