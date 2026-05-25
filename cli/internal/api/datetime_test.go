package api

import (
	"testing"
	"time"

	"github.com/go-faster/jx"
)

func TestScraperConfigResponseUnmarshalJSONAcceptsNaiveDateTime(t *testing.T) {
	var response ScraperConfigResponse

	err := response.UnmarshalJSON([]byte(`{
		"config": {},
		"created_at": "2026-03-13T14:14:14.205954",
		"id": 7,
		"is_active": true,
		"scraper_type": "rss"
	}`))
	if err != nil {
		t.Fatalf("unmarshal scraper config response: %v", err)
	}

	want := time.Date(2026, time.March, 13, 14, 14, 14, 205954000, time.UTC)
	if !response.CreatedAt.Equal(want) {
		t.Fatalf("created_at = %s, want %s", response.CreatedAt.Format(time.RFC3339Nano), want.Format(time.RFC3339Nano))
	}
}

func TestDecodeFlexibleDateTimeAcceptsRFC3339(t *testing.T) {
	got, err := decodeFlexibleDateTimeString("2026-03-13T14:14:14Z")
	if err != nil {
		t.Fatalf("decode RFC3339: %v", err)
	}

	want := time.Date(2026, time.March, 13, 14, 14, 14, 0, time.UTC)
	if !got.Equal(want) {
		t.Fatalf("decoded time = %s, want %s", got.Format(time.RFC3339Nano), want.Format(time.RFC3339Nano))
	}
}

func TestDecodeFlexibleDateTimeRejectsInvalidInput(t *testing.T) {
	if _, err := decodeFlexibleDateTimeString("not-a-time"); err == nil {
		t.Fatal("expected invalid datetime to fail")
	}
}

func TestContentDetailResponseUnmarshalJSONAcceptsNullDetectedFeed(t *testing.T) {
	var response ContentDetailResponse

	err := response.UnmarshalJSON([]byte(`{
		"body_available": true,
		"bullet_points": [],
		"can_subscribe": false,
		"content_type": "article",
		"created_at": "2025-07-04T17:35:53.202115",
		"detected_feed": null,
		"display_title": "Title",
		"id": 28,
		"metadata": {},
		"quotes": [],
		"retry_count": 0,
		"source": "example.com",
		"status": "completed",
		"title": "Title",
		"topics": [],
		"url": "https://example.com/article"
	}`))
	if err != nil {
		t.Fatalf("unmarshal content detail response: %v", err)
	}

	if response.DetectedFeed.IsSet() {
		t.Fatal("expected null detected_feed to decode as unset")
	}
}

func TestContentDetailResponseUnmarshalJSONAcceptsNullSummaryMetadata(t *testing.T) {
	var response ContentDetailResponse

	err := response.UnmarshalJSON([]byte(`{
		"body_available": true,
		"bullet_points": [],
		"can_subscribe": false,
		"content_type": "article",
		"created_at": "2025-07-04T17:35:53.202115",
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
	}`))
	if err != nil {
		t.Fatalf("unmarshal content detail response: %v", err)
	}

	if response.SummaryKind.IsSet() {
		t.Fatal("expected null summary_kind to decode as unset")
	}
	if response.SummaryVersion.IsSet() {
		t.Fatal("expected null summary_version to decode as unset")
	}
}

func TestContentListResponseUnmarshalJSONAcceptsNullClassification(t *testing.T) {
	var response ContentListResponse

	err := response.UnmarshalJSON([]byte(`{
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
	}`))
	if err != nil {
		t.Fatalf("unmarshal content list response: %v", err)
	}

	if len(response.Contents) != 1 {
		t.Fatalf("contents length = %d, want 1", len(response.Contents))
	}
	if response.Contents[0].Classification.IsSet() {
		t.Fatal("expected null classification to decode as unset")
	}
}

func TestOnboardingDiscoveryStatusResponseUnmarshalJSONAcceptsNullSuggestions(t *testing.T) {
	var response OnboardingDiscoveryStatusResponse

	err := response.UnmarshalJSON([]byte(`{
		"run_id": 17,
		"run_status": "pending",
		"topic_summary": "AI startups",
		"inferred_topics": ["AI", "startups"],
		"lanes": [],
		"suggestions": null,
		"error_message": null
	}`))
	if err != nil {
		t.Fatalf("unmarshal onboarding discovery status response: %v", err)
	}

	if response.Suggestions.IsSet() {
		t.Fatal("expected null suggestions to decode as unset")
	}
}

func decodeFlexibleDateTimeString(raw string) (time.Time, error) {
	return decodeFlexibleDateTime(jx.DecodeBytes([]byte(`"` + raw + `"`)))
}
