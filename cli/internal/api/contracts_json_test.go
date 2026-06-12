package api

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestContentDetailResponseUnmarshalJSONAcceptsNullOptionalFields(t *testing.T) {
	var response ContentDetailResponse

	err := json.Unmarshal([]byte(`{
		"body_available": true,
		"bullet_points": [],
		"can_subscribe": false,
		"content_type": "article",
		"created_at": "2025-07-04T17:35:53Z",
		"detected_feed": null,
		"display_title": "Title",
		"id": 28,
		"metadata": {},
		"quotes": [],
		"retry_count": 0,
		"source": "example.com",
		"status": "completed",
		"summary_kind": null,
		"summary_version": null,
		"title": "Title",
		"topics": [],
		"url": "https://example.com/article"
	}`), &response)
	if err != nil {
		t.Fatalf("unmarshal content detail response: %v", err)
	}

	if response.DetectedFeed != nil {
		t.Fatal("expected null detected_feed to decode as nil")
	}
	if response.SummaryKind != nil {
		t.Fatal("expected null summary_kind to decode as nil")
	}
	if response.SummaryVersion != nil {
		t.Fatal("expected null summary_version to decode as nil")
	}
}

func TestContentListResponseUnmarshalJSONAcceptsNullClassification(t *testing.T) {
	var response ContentListResponse

	err := json.Unmarshal([]byte(`{
		"available_dates": ["2026-05-23"],
		"content_types": ["article"],
		"contents": [
			{
				"classification": null,
				"content_type": "article",
				"created_at": "2026-05-23T12:00:00Z",
				"id": 42,
				"status": "completed",
				"title": "Rust Overtakes Go in Cloud Infrastructure Adoption",
				"url": "https://example.com/article"
			}
		],
		"meta": {
			"has_more": false,
			"next_cursor": null,
			"page_size": 1,
			"total": 1
		}
	}`), &response)
	if err != nil {
		t.Fatalf("unmarshal content list response: %v", err)
	}

	if len(response.Contents) != 1 {
		t.Fatalf("contents length = %d, want 1", len(response.Contents))
	}
	if response.Contents[0].Classification != nil {
		t.Fatal("expected null classification to decode as nil")
	}
}

func TestOnboardingDiscoveryStatusResponseUnmarshalJSONAcceptsNullSuggestions(t *testing.T) {
	var response OnboardingDiscoveryStatusResponse

	err := json.Unmarshal([]byte(`{
		"run_id": 17,
		"run_status": "pending",
		"topic_summary": "AI startups",
		"inferred_topics": ["AI", "startups"],
		"lanes": [],
		"suggestions": null,
		"error_message": null
	}`), &response)
	if err != nil {
		t.Fatalf("unmarshal onboarding discovery status response: %v", err)
	}

	if response.Suggestions != nil {
		t.Fatal("expected null suggestions to decode as nil")
	}
}

func TestOpenEnumUnmarshalJSONAcceptsUnknownValue(t *testing.T) {
	var response ContentSummaryResponse

	err := json.Unmarshal(readContractFixture(t, "content_summary_unknown_enum.json"), &response)
	if err != nil {
		t.Fatalf("unmarshal content summary response: %v", err)
	}

	if response.ContentType.Known() {
		t.Fatalf("expected future content type to be unknown, got %q", response.ContentType)
	}
	if response.Status.Known() {
		t.Fatalf("expected future content status to be unknown, got %q", response.Status)
	}
}

func TestSharedContentFixturesDecode(t *testing.T) {
	var summary ContentSummaryResponse
	if err := json.Unmarshal(readContractFixture(t, "content_summary_article.json"), &summary); err != nil {
		t.Fatalf("unmarshal shared summary fixture: %v", err)
	}
	if summary.ID != 101 || summary.ContentType != ContentTypeArticle {
		t.Fatalf("unexpected summary fixture decode: id=%d type=%q", summary.ID, summary.ContentType)
	}

	var detail ContentDetailResponse
	if err := json.Unmarshal(readContractFixture(t, "content_detail_long_read.json"), &detail); err != nil {
		t.Fatalf("unmarshal shared detail fixture: %v", err)
	}
	if detail.ID != 401 || detail.SummaryKind == nil || *detail.SummaryKind != SummaryKindLongInterleaved {
		t.Fatalf("unexpected detail fixture decode: id=%d summary_kind=%v", detail.ID, detail.SummaryKind)
	}

	var nullOptionals ContentDetailResponse
	if err := json.Unmarshal(readContractFixture(t, "content_detail_null_optionals.json"), &nullOptionals); err != nil {
		t.Fatalf("unmarshal null-optionals fixture: %v", err)
	}
	if nullOptionals.SummaryKind != nil || nullOptionals.SummaryVersion != nil {
		t.Fatal("expected null summary fields to decode as nil")
	}
	if nullOptionals.DetectedFeed == nil {
		t.Fatal("expected detected_feed object to decode")
	}
}

func readContractFixture(t *testing.T, name string) []byte {
	t.Helper()
	path := filepath.Join("..", "..", "..", "tests", "fixtures", "contracts", name)
	payload, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read contract fixture %s: %v", name, err)
	}
	return payload
}
