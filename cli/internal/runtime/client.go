package runtime

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
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
	raw        *api.Client
	baseURL    string
	apiKey     string
	httpClient *http.Client
}

type bearerSource struct {
	token string
}

func (s bearerSource) HTTPBearer(_ context.Context, _ api.OperationName) (api.HTTPBearer, error) {
	return api.HTTPBearer{Token: s.token}, nil
}

func NewClient(cfg config.RuntimeConfig, timeout time.Duration) (*Client, error) {
	httpClient := &http.Client{Timeout: timeout}
	rawClient, err := api.NewClient(
		cfg.ServerURL,
		bearerSource{token: cfg.APIKey},
		api.WithClient(httpClient),
	)
	if err != nil {
		return nil, err
	}
	return &Client{
		raw:        rawClient,
		baseURL:    strings.TrimRight(cfg.ServerURL, "/"),
		apiKey:     cfg.APIKey,
		httpClient: httpClient,
	}, nil
}

func (c *Client) GetJob(ctx context.Context, jobID int) (*api.JobStatusResponse, error) {
	res, err := c.raw.GetJob(ctx, api.GetJobParams{JobID: jobID})
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.JobStatusResponse:
		return value, nil
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
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

		params := api.ListContentSubmissionStatusesParams{}
		params.Limit.SetTo(100)
		submissions, err := c.ListContentSubmissionStatuses(ctx, params)
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
				if submission.ErrorMessage.IsSet() && !submission.ErrorMessage.Null {
					errorMessage := strings.TrimSpace(submission.ErrorMessage.Value)
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
	res, err := c.raw.SearchAgent(ctx, request)
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.AgentSearchResponse:
		return value, nil
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) StartOnboarding(ctx context.Context, request *api.AgentOnboardingStartRequest) (*api.AgentOnboardingStartResponse, error) {
	res, err := c.raw.StartOnboarding(ctx, request)
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.AgentOnboardingStartResponse:
		return value, nil
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) GetOnboarding(ctx context.Context, runID int) (*api.OnboardingDiscoveryStatusResponse, error) {
	res, err := c.raw.GetOnboarding(ctx, api.GetOnboardingParams{RunID: runID})
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.OnboardingDiscoveryStatusResponse:
		return value, nil
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) CompleteOnboarding(ctx context.Context, runID int, request *api.AgentOnboardingCompleteRequest) (any, error) {
	res, err := c.raw.CompleteOnboarding(ctx, request, api.CompleteOnboardingParams{RunID: runID})
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.CompleteOnboardingOK:
		return normalize(value)
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) ListContent(ctx context.Context, params api.ListContentsParams) (*api.ContentListResponse, error) {
	res, err := c.raw.ListContents(ctx, params)
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.ContentListResponse:
		return value, nil
	case *api.ListContentsNotFound:
		return nil, &APIError{Message: "content route not found", StatusCode: http.StatusNotFound}
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) GetContent(ctx context.Context, contentID int) (*api.ContentDetailResponse, error) {
	res, err := c.raw.GetContentDetail(ctx, api.GetContentDetailParams{ContentID: contentID})
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.ContentDetailResponse:
		return value, nil
	case *api.GetContentDetailNotFoundApplicationJSON:
		payload, _ := normalizeJSONRaw(value)
		return nil, &APIError{
			Message:    "content not found",
			StatusCode: http.StatusNotFound,
			Payload:    payload,
		}
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) SubmitContent(ctx context.Context, request *api.SubmitContentRequest) (*api.ContentSubmissionResponse, error) {
	res, err := c.raw.SubmitContent(ctx, request)
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.SubmitContentOK:
		response := api.ContentSubmissionResponse(*value)
		return &response, nil
	case *api.SubmitContentCreated:
		response := api.ContentSubmissionResponse(*value)
		return &response, nil
	case *api.SubmitContentNotFound:
		return nil, &APIError{Message: "submit route not found", StatusCode: http.StatusNotFound}
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) ListContentSubmissionStatuses(ctx context.Context, params api.ListContentSubmissionStatusesParams) (*api.SubmissionStatusListResponse, error) {
	res, err := c.raw.ListContentSubmissionStatuses(ctx, params)
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.SubmissionStatusListResponse:
		return value, nil
	case *api.ListContentSubmissionStatusesNotFound:
		return nil, &APIError{Message: "submission status route not found", StatusCode: http.StatusNotFound}
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) ListNewsItems(ctx context.Context, params api.ListNewsItemsParams) (*api.ContentListResponse, error) {
	res, err := c.raw.ListNewsItems(ctx, params)
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.ContentListResponse:
		return value, nil
	case *api.ListNewsItemsNotFound:
		return nil, &APIError{Message: "news route not found", StatusCode: http.StatusNotFound}
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) GetNewsItem(ctx context.Context, newsItemID int) (*api.ContentDetailResponse, error) {
	res, err := c.raw.GetNewsItem(ctx, api.GetNewsItemParams{NewsItemID: newsItemID})
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.ContentDetailResponse:
		return value, nil
	case *api.GetNewsItemNotFound:
		return nil, &APIError{Message: "news item not found", StatusCode: http.StatusNotFound}
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) ConvertNewsItemToArticle(ctx context.Context, newsItemID int) (*api.ConvertNewsItemResponse, error) {
	res, err := c.raw.ConvertNewsItemToArticle(ctx, api.ConvertNewsItemToArticleParams{NewsItemID: newsItemID})
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.ConvertNewsItemResponse:
		return value, nil
	case *api.ConvertNewsItemToArticleNotFound:
		return nil, &APIError{Message: "news item not found", StatusCode: http.StatusNotFound}
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) MarkNewsItemsRead(ctx context.Context, newsItemIDs []int) (any, error) {
	res, err := c.raw.MarkNewsItemsRead(ctx, &api.BulkMarkReadRequest{ContentIds: newsItemIDs})
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.MarkNewsItemsReadOK:
		return normalize(value)
	case *api.MarkNewsItemsReadNotFound:
		return nil, &APIError{Message: "news items not found", StatusCode: http.StatusNotFound}
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) ListSources(ctx context.Context, params api.ListScraperConfigsParams) ([]api.ScraperConfigResponse, error) {
	res, err := c.raw.ListScraperConfigs(ctx, params)
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.ListScraperConfigsOKApplicationJSON:
		return []api.ScraperConfigResponse(*value), nil
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) SubscribeSource(ctx context.Context, request *api.SubscribeToFeedRequest) (*api.ScraperConfigResponse, error) {
	var response api.ScraperConfigResponse
	if err := c.doJSON(ctx, http.MethodPost, "/api/scrapers/subscribe", request, true, nil, &response); err != nil {
		return nil, err
	}
	return &response, nil
}

func validationError(value *api.HTTPValidationError) error {
	payload, _ := normalize(value)
	return &APIError{
		Message:    "request validation failed",
		StatusCode: http.StatusUnprocessableEntity,
		Payload:    payload,
	}
}

func unexpectedResponse(value any) error {
	payload, _ := normalize(value)
	return &APIError{
		Message: fmt.Sprintf("unexpected API response type %T", value),
		Payload: payload,
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

func normalizeJSONRaw(value any) (any, error) {
	raw, ok := value.(*api.GetContentDetailNotFoundApplicationJSON)
	if !ok {
		return normalize(value)
	}
	var decoded any
	if err := json.Unmarshal([]byte(*raw), &decoded); err != nil {
		return string([]byte(*raw)), nil
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
