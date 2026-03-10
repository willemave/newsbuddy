package runtime

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/willem/news_app/cli/internal/api"
	"github.com/willem/news_app/cli/internal/config"
)

var terminalJobStatuses = map[string]struct{}{
	"completed": {},
	"failed":    {},
	"skipped":   {},
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
	raw *api.Client
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
	return &Client{raw: rawClient}, nil
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
		if _, ok := terminalJobStatuses[strings.ToLower(job.Status)]; ok {
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

func (c *Client) WaitForOnboarding(ctx context.Context, runID int, wait WaitOptions) (*api.OnboardingDiscoveryStatusResponse, error) {
	deadline := time.Now().Add(wait.Timeout)
	for {
		run, err := c.GetOnboarding(ctx, runID)
		if err != nil {
			return nil, err
		}
		status := strings.ToLower(run.RunStatus)
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

func (c *Client) GenerateDigest(ctx context.Context, request *api.AgentDigestRequest) (*api.AgentDigestResponse, error) {
	res, err := c.raw.GenerateDigest(ctx, request)
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.AgentDigestResponse:
		return value, nil
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) ListContent(ctx context.Context, params api.ListContentParams) (*api.ContentListResponse, error) {
	res, err := c.raw.ListContent(ctx, params)
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.ContentListResponse:
		return value, nil
	case *api.ListContentNotFound:
		return nil, &APIError{Message: "content route not found", StatusCode: http.StatusNotFound}
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) GetContent(ctx context.Context, contentID int) (*api.ContentDetailResponse, error) {
	res, err := c.raw.GetContent(ctx, api.GetContentParams{ContentID: contentID})
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.ContentDetailResponse:
		return value, nil
	case *api.GetContentNotFoundApplicationJSON:
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

func (c *Client) ListDigests(ctx context.Context, params api.ListDigestsParams) (*api.DailyNewsDigestListResponse, error) {
	res, err := c.raw.ListDigests(ctx, params)
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.DailyNewsDigestListResponse:
		return value, nil
	case *api.ListDigestsNotFound:
		return nil, &APIError{Message: "digest route not found", StatusCode: http.StatusNotFound}
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) ListSources(ctx context.Context, params api.ListSourcesParams) ([]api.ScraperConfigResponse, error) {
	res, err := c.raw.ListSources(ctx, params)
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.ListSourcesOKApplicationJSON:
		return []api.ScraperConfigResponse(*value), nil
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
}

func (c *Client) SubscribeSource(ctx context.Context, request *api.SubscribeToFeedRequest) (*api.ScraperConfigResponse, error) {
	res, err := c.raw.SubscribeSource(ctx, request)
	if err != nil {
		return nil, err
	}
	switch value := res.(type) {
	case *api.ScraperConfigResponse:
		return value, nil
	case *api.HTTPValidationError:
		return nil, validationError(value)
	default:
		return nil, unexpectedResponse(value)
	}
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
	raw, ok := value.(*api.GetContentNotFoundApplicationJSON)
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
