package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/willem/newsbuddy/cli/internal/api"
	"github.com/willem/newsbuddy/cli/internal/config"
)

var terminalJobStatuses = map[string]struct{}{
	"completed": {},
	"failed":    {},
	"skipped":   {},
}

func normalizeStatus(status string) string {
	return strings.ToLower(strings.TrimSpace(status))
}

func isTerminalStatus(status string) bool {
	_, ok := terminalJobStatuses[normalizeStatus(status)]
	return ok
}

func IsFailedOrSkippedStatus(status string) bool {
	normalized := normalizeStatus(status)
	return normalized == "failed" || normalized == "skipped"
}

type WaitOptions struct {
	Interval time.Duration
	Timeout  time.Duration
}

type APIError struct {
	Message    string
	StatusCode int
	Payload    any
}

func (e *APIError) Error() string {
	return e.Message
}

type Client struct {
	baseURL    string
	apiKey     string
	httpClient *http.Client
}

func NewClient(cfg config.RuntimeConfig, timeout time.Duration) (*Client, error) {
	if _, err := url.Parse(cfg.ServerURL); err != nil {
		return nil, err
	}
	return &Client{
		baseURL:    strings.TrimRight(cfg.ServerURL, "/"),
		apiKey:     cfg.APIKey,
		httpClient: &http.Client{Timeout: timeout},
	}, nil
}

func (c *Client) doJSON(
	ctx context.Context,
	method string,
	path string,
	body any,
	includeAuth bool,
	query url.Values,
	into any,
) error {
	endpoint := c.baseURL + path
	if query != nil && len(query) > 0 {
		endpoint += "?" + query.Encode()
	}

	var bodyReader io.Reader
	if body != nil {
		payload, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("%s %s encode request: %w", method, path, err)
		}
		bodyReader = bytes.NewReader(payload)
	}

	req, err := http.NewRequestWithContext(ctx, method, endpoint, bodyReader)
	if err != nil {
		return fmt.Errorf("%s %s build request: %w", method, path, err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("Accept", "application/json")
	if includeAuth && strings.TrimSpace(c.apiKey) != "" {
		req.Header.Set("Authorization", "Bearer "+c.apiKey)
	}

	res, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("%s %s request failed: %w", method, path, err)
	}
	defer res.Body.Close()

	if res.StatusCode < 200 || res.StatusCode >= 300 {
		payload, _ := decodeBody(res)
		message := fmt.Sprintf("request failed with status %d", res.StatusCode)
		if detail, ok := payload["detail"].(string); ok && detail != "" {
			message = detail
		}
		return &APIError{
			Message:    message,
			StatusCode: res.StatusCode,
			Payload:    payload,
		}
	}

	if into == nil {
		return nil
	}
	if err := json.NewDecoder(res.Body).Decode(into); err != nil && !errors.Is(err, io.EOF) {
		return fmt.Errorf("%s %s decode response: %w", method, path, err)
	}
	return nil
}

func decodeBody(res *http.Response) (map[string]any, error) {
	var payload map[string]any
	if err := json.NewDecoder(res.Body).Decode(&payload); err != nil {
		return map[string]any{}, err
	}
	return payload, nil
}

func (c *Client) GetJob(ctx context.Context, jobID int) (*api.JobStatusResponse, error) {
	var response api.JobStatusResponse
	if err := c.doJSON(ctx, http.MethodGet, "/api/jobs/"+strconv.Itoa(jobID), nil, true, nil, &response); err != nil {
		return nil, err
	}
	return &response, nil
}

func (c *Client) WaitForJob(ctx context.Context, jobID int, wait WaitOptions) (*api.JobStatusResponse, error) {
	deadline := time.Now().Add(wait.Timeout)
	for {
		job, err := c.GetJob(ctx, jobID)
		if err != nil {
			return nil, err
		}
		if isTerminalStatus(job.Status) {
			return job, nil
		}
		if time.Now().After(deadline) {
			payload, _ := normalize(job)
			return nil, &APIError{
				Message: fmt.Sprintf("timed out waiting for job %d", jobID),
				Payload: payload,
			}
		}
		if err := sleepContext(ctx, wait.Interval); err != nil {
			return nil, err
		}
	}
}

func (c *Client) WaitForSubmittedContent(ctx context.Context, contentID int, wait WaitOptions) (*api.ContentDetailResponse, error) {
	deadline := time.Now().Add(wait.Timeout)
	for {
		content, err := c.GetContent(ctx, contentID)
		if err == nil {
			return content, nil
		}

		var apiErr *APIError
		if !errors.As(err, &apiErr) || apiErr.StatusCode != http.StatusNotFound {
			return nil, err
		}

		submissions, err := c.ListContentSubmissionStatuses(ctx, api.ListContentSubmissionStatusesParams{
			Limit: api.Ptr(100),
		})
		if err != nil {
			return nil, err
		}
		for _, submission := range submissions.Submissions {
			if submission.ID != contentID {
				continue
			}
			status := string(submission.Status)
			if IsFailedOrSkippedStatus(status) {
				payload, _ := normalize(submission)
				message := fmt.Sprintf("submission %d %s", contentID, normalizeStatus(status))
				if submission.ErrorMessage != nil {
					errorMessage := strings.TrimSpace(*submission.ErrorMessage)
					if errorMessage != "" {
						message = errorMessage
					}
				}
				return nil, &APIError{
					Message: message,
					Payload: payload,
				}
			}
			break
		}

		if time.Now().After(deadline) {
			return nil, &APIError{
				Message: fmt.Sprintf("timed out waiting for content %d to become available", contentID),
				Payload: map[string]any{
					"content_id": contentID,
				},
			}
		}
		if err := sleepContext(ctx, wait.Interval); err != nil {
			return nil, err
		}
	}
}

func (c *Client) WaitForOnboarding(ctx context.Context, runID int, wait WaitOptions) (*api.OnboardingDiscoveryStatusResponse, error) {
	deadline := time.Now().Add(wait.Timeout)
	for {
		run, err := c.GetOnboarding(ctx, runID)
		if err != nil {
			return nil, err
		}
		status := normalizeStatus(run.RunStatus)
		if status == "completed" || status == "failed" {
			return run, nil
		}
		if time.Now().After(deadline) {
			payload, _ := normalize(run)
			return nil, &APIError{
				Message: fmt.Sprintf("timed out waiting for onboarding run %d", runID),
				Payload: payload,
			}
		}
		if err := sleepContext(ctx, wait.Interval); err != nil {
			return nil, err
		}
	}
}

func (c *Client) SearchAgent(ctx context.Context, request *api.AgentSearchRequest) (*api.AgentSearchResponse, error) {
	var response api.AgentSearchResponse
	if err := c.doJSON(ctx, http.MethodPost, "/api/agent/search", request, true, nil, &response); err != nil {
		return nil, err
	}
	return &response, nil
}

func (c *Client) StartOnboarding(ctx context.Context, request *api.AgentOnboardingStartRequest) (*api.AgentOnboardingStartResponse, error) {
	var response api.AgentOnboardingStartResponse
	if err := c.doJSON(ctx, http.MethodPost, "/api/agent/onboarding", request, true, nil, &response); err != nil {
		return nil, err
	}
	return &response, nil
}

func (c *Client) GetOnboarding(ctx context.Context, runID int) (*api.OnboardingDiscoveryStatusResponse, error) {
	var response api.OnboardingDiscoveryStatusResponse
	if err := c.doJSON(ctx, http.MethodGet, "/api/agent/onboarding/"+strconv.Itoa(runID), nil, true, nil, &response); err != nil {
		return nil, err
	}
	return &response, nil
}

func (c *Client) CompleteOnboarding(ctx context.Context, runID int, request *api.AgentOnboardingCompleteRequest) (*api.OnboardingCompleteResponse, error) {
	var response api.OnboardingCompleteResponse
	if err := c.doJSON(ctx, http.MethodPost, "/api/agent/onboarding/"+strconv.Itoa(runID)+"/complete", request, true, nil, &response); err != nil {
		return nil, err
	}
	return &response, nil
}

func (c *Client) ListContent(ctx context.Context, params api.ListContentsParams) (*api.ContentListResponse, error) {
	var response api.ContentListResponse
	if err := c.doJSON(ctx, http.MethodGet, "/api/content/", nil, true, listContentsQuery(params), &response); err != nil {
		return nil, messageForStatus(err, http.StatusNotFound, "content route not found")
	}
	return &response, nil
}

func (c *Client) GetContent(ctx context.Context, contentID int) (*api.ContentDetailResponse, error) {
	var response api.ContentDetailResponse
	if err := c.doJSON(ctx, http.MethodGet, "/api/content/"+strconv.Itoa(contentID), nil, true, nil, &response); err != nil {
		return nil, messageForStatus(err, http.StatusNotFound, "content not found")
	}
	return &response, nil
}

func (c *Client) SubmitContent(ctx context.Context, request *api.SubmitContentRequest) (*api.ContentSubmissionResponse, error) {
	var response api.ContentSubmissionResponse
	if err := c.doJSON(ctx, http.MethodPost, "/api/content/submit", request, true, nil, &response); err != nil {
		return nil, messageForStatus(err, http.StatusNotFound, "submit route not found")
	}
	return &response, nil
}

func (c *Client) ListContentSubmissionStatuses(ctx context.Context, params api.ListContentSubmissionStatusesParams) (*api.SubmissionStatusListResponse, error) {
	var response api.SubmissionStatusListResponse
	if err := c.doJSON(ctx, http.MethodGet, "/api/content/submissions/list", nil, true, submissionStatusesQuery(params), &response); err != nil {
		return nil, messageForStatus(err, http.StatusNotFound, "submission status route not found")
	}
	return &response, nil
}

func (c *Client) ListNewsItems(ctx context.Context, params api.ListNewsItemsParams) (*api.ContentListResponse, error) {
	var response api.ContentListResponse
	if err := c.doJSON(ctx, http.MethodGet, "/api/news/items", nil, true, listNewsItemsQuery(params), &response); err != nil {
		return nil, messageForStatus(err, http.StatusNotFound, "news route not found")
	}
	return &response, nil
}

func (c *Client) GetNewsItem(ctx context.Context, newsItemID int) (*api.ContentDetailResponse, error) {
	var response api.ContentDetailResponse
	if err := c.doJSON(ctx, http.MethodGet, "/api/news/items/"+strconv.Itoa(newsItemID), nil, true, nil, &response); err != nil {
		return nil, messageForStatus(err, http.StatusNotFound, "news item not found")
	}
	return &response, nil
}

func (c *Client) ConvertNewsItemToArticle(ctx context.Context, newsItemID int) (*api.ConvertNewsItemResponse, error) {
	var response api.ConvertNewsItemResponse
	if err := c.doJSON(ctx, http.MethodPost, "/api/news/items/"+strconv.Itoa(newsItemID)+"/convert-to-article", nil, true, nil, &response); err != nil {
		return nil, messageForStatus(err, http.StatusNotFound, "news item not found")
	}
	return &response, nil
}

func (c *Client) MarkNewsItemsRead(ctx context.Context, newsItemIDs []int) (*api.BulkMarkReadResponse, error) {
	request := &api.BulkMarkReadRequest{ContentIDs: newsItemIDs}
	var response api.BulkMarkReadResponse
	if err := c.doJSON(ctx, http.MethodPost, "/api/news/items/mark-read", request, true, nil, &response); err != nil {
		return nil, messageForStatus(err, http.StatusNotFound, "news items not found")
	}
	return &response, nil
}

func (c *Client) ListSources(ctx context.Context, params api.ListScraperConfigsParams) ([]api.ScraperConfigResponse, error) {
	var response []api.ScraperConfigResponse
	if err := c.doJSON(ctx, http.MethodGet, "/api/scrapers/", nil, true, listScraperConfigsQuery(params), &response); err != nil {
		return nil, err
	}
	return response, nil
}

func (c *Client) SubscribeSource(ctx context.Context, request *api.SubscribeToFeedRequest) (*api.ScraperConfigResponse, error) {
	var response api.ScraperConfigResponse
	if err := c.doJSON(ctx, http.MethodPost, "/api/scrapers/subscribe", request, true, nil, &response); err != nil {
		return nil, err
	}
	return &response, nil
}

func messageForStatus(err error, statusCode int, message string) error {
	var apiErr *APIError
	if errors.As(err, &apiErr) && apiErr.StatusCode == statusCode {
		apiErr.Message = message
	}
	return err
}

func submissionStatusesQuery(params api.ListContentSubmissionStatusesParams) url.Values {
	query := url.Values{}
	addString(query, "cursor", params.Cursor)
	addInt(query, "limit", params.Limit)
	return query
}

func listContentsQuery(params api.ListContentsParams) url.Values {
	query := url.Values{}
	for _, contentType := range params.ContentType {
		query.Add("content_type", contentType)
	}
	addString(query, "date", params.Date)
	addString(query, "read_filter", params.ReadFilter)
	addString(query, "cursor", params.Cursor)
	addInt(query, "limit", params.Limit)
	addBool(query, "include_available_dates", params.IncludeAvailableDates)
	return query
}

func listNewsItemsQuery(params api.ListNewsItemsParams) url.Values {
	query := url.Values{}
	addString(query, "read_filter", params.ReadFilter)
	addString(query, "cursor", params.Cursor)
	addInt(query, "limit", params.Limit)
	return query
}

func listScraperConfigsQuery(params api.ListScraperConfigsParams) url.Values {
	query := url.Values{}
	addString(query, "type", params.Type)
	addString(query, "types", params.Types)
	addBool(query, "include_stats", params.IncludeStats)
	return query
}

func addString(query url.Values, name string, value *string) {
	if value != nil {
		query.Set(name, *value)
	}
}

func addInt(query url.Values, name string, value *int) {
	if value != nil {
		query.Set(name, strconv.Itoa(*value))
	}
}

func addBool(query url.Values, name string, value *bool) {
	if value != nil {
		query.Set(name, strconv.FormatBool(*value))
	}
}

func normalize(value any) (any, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	var decoded any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		return string(raw), nil
	}
	return decoded, nil
}

func ParseURL(rawURL string) (url.URL, error) {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return url.URL{}, err
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return url.URL{}, errors.New("url must use http or https")
	}
	if parsed.Host == "" {
		return url.URL{}, errors.New("url must include a host")
	}
	return *parsed, nil
}

func sleepContext(ctx context.Context, duration time.Duration) error {
	timer := time.NewTimer(duration)
	defer timer.Stop()

	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}
