package runtime

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/willem/newsbuddy/cli/internal/api"
)

func (c *Client) StartCLILink(ctx context.Context, deviceName string) (*api.CliLinkStartResponse, error) {
	payload := &api.CliLinkStartRequest{}
	if strings.TrimSpace(deviceName) != "" {
		payload.DeviceName = api.Ptr(deviceName)
	}
	var response api.CliLinkStartResponse
	if err := c.doJSON(ctx, http.MethodPost, "/api/agent/cli/link/start", payload, false, nil, &response); err != nil {
		return nil, err
	}
	return &response, nil
}

func (c *Client) PollCLILink(ctx context.Context, sessionID string, pollToken string) (*api.CliLinkPollResponse, error) {
	query := url.Values{}
	query.Set("poll_token", pollToken)
	var response api.CliLinkPollResponse
	if err := c.doJSON(ctx, http.MethodGet, "/api/agent/cli/link/"+url.PathEscape(sessionID), nil, false, query, &response); err != nil {
		return nil, err
	}
	return &response, nil
}

func (c *Client) WaitForCLILink(
	ctx context.Context,
	sessionID string,
	pollToken string,
	wait WaitOptions,
) (*api.CliLinkPollResponse, error) {
	deadline := time.Now().Add(wait.Timeout)
	for {
		polled, err := c.PollCLILink(ctx, sessionID, pollToken)
		if err != nil {
			return nil, err
		}
		switch normalizeStatus(string(polled.Status)) {
		case "approved":
			if polled.APIKey != nil && *polled.APIKey != "" {
				return polled, nil
			}
		case "claimed":
			return nil, errors.New("CLI link session was already claimed")
		case "expired":
			return nil, errors.New("CLI link session expired")
		}
		if time.Now().After(deadline) {
			return nil, errors.New("timed out waiting for CLI approval")
		}
		if err := sleepContext(ctx, wait.Interval); err != nil {
			return nil, err
		}
	}
}

func (c *Client) GetLibraryManifest(ctx context.Context, includeSource bool) (*api.AgentLibraryManifestResponse, error) {
	query := url.Values{}
	query.Set("include_source", strconv.FormatBool(includeSource))
	var response api.AgentLibraryManifestResponse
	if err := c.doJSON(ctx, http.MethodGet, "/api/agent/library/manifest", nil, true, query, &response); err != nil {
		return nil, err
	}
	return &response, nil
}

func (c *Client) GetLibraryFile(ctx context.Context, relativePath string) (*api.AgentLibraryFileResponse, error) {
	query := url.Values{}
	query.Set("path", relativePath)
	var response api.AgentLibraryFileResponse
	if err := c.doJSON(ctx, http.MethodGet, "/api/agent/library/file", nil, true, query, &response); err != nil {
		return nil, err
	}
	return &response, nil
}

func DefaultDeviceName() string {
	if host, err := os.Hostname(); err == nil {
		if trimmed := strings.TrimSpace(host); trimmed != "" {
			return trimmed
		}
	}
	return "Newsbuddy CLI"
}
