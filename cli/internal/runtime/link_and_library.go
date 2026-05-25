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
	"os"
	"strconv"
	"strings"
	"time"
)

type CLILinkStartResponse struct {
	SessionID  string `json:"session_id"`
	PollToken  string `json:"poll_token"`
	ApproveURL string `json:"approve_url"`
}

type CLILinkPollResponse struct {
	SessionID string `json:"session_id"`
	Status    string `json:"status"`
	APIKey    string `json:"api_key"`
	KeyPrefix string `json:"key_prefix"`
}

type AgentLibraryDocument struct {
	RelativePath   string `json:"relative_path"`
	ChecksumSHA256 string `json:"checksum_sha256"`
}

type AgentLibraryManifestResponse struct {
	Documents []AgentLibraryDocument `json:"documents"`
}

type AgentLibraryFileResponse struct {
	Text string `json:"text"`
}

func (c *Client) StartCLILink(ctx context.Context, deviceName string) (*CLILinkStartResponse, error) {
	payload := map[string]string{}
	if strings.TrimSpace(deviceName) != "" {
		payload["device_name"] = deviceName
	}
	var response CLILinkStartResponse
	if err := c.doJSON(ctx, http.MethodPost, "/api/agent/cli/link/start", payload, false, nil, &response); err != nil {
		return nil, err
	}
	return &response, nil
}

func (c *Client) PollCLILink(ctx context.Context, sessionID string, pollToken string) (*CLILinkPollResponse, error) {
	query := url.Values{}
	query.Set("poll_token", pollToken)
	var response CLILinkPollResponse
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
) (*CLILinkPollResponse, error) {
	deadline := time.Now().Add(wait.Timeout)
	for {
		polled, err := c.PollCLILink(ctx, sessionID, pollToken)
		if err != nil {
			return nil, err
		}
		switch normalizeStatus(polled.Status) {
		case "approved":
			if polled.APIKey != "" {
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

func (c *Client) GetLibraryManifest(ctx context.Context, includeSource bool) (*AgentLibraryManifestResponse, error) {
	query := url.Values{}
	query.Set("include_source", strconv.FormatBool(includeSource))
	var response AgentLibraryManifestResponse
	if err := c.doJSON(ctx, http.MethodGet, "/api/agent/library/manifest", nil, true, query, &response); err != nil {
		return nil, err
	}
	return &response, nil
}

func (c *Client) GetLibraryFile(ctx context.Context, relativePath string) (*AgentLibraryFileResponse, error) {
	query := url.Values{}
	query.Set("path", relativePath)
	var response AgentLibraryFileResponse
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
			return err
		}
		bodyReader = bytes.NewReader(payload)
	}

	req, err := http.NewRequestWithContext(ctx, method, endpoint, bodyReader)
	if err != nil {
		return err
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
		return err
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

	return json.NewDecoder(res.Body).Decode(into)
}

func decodeBody(res *http.Response) (map[string]any, error) {
	var payload map[string]any
	if err := json.NewDecoder(res.Body).Decode(&payload); err != nil {
		return map[string]any{}, err
	}
	return payload, nil
}
