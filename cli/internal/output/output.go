package output

import (
	"encoding/json"
	"fmt"
	"io"
)

type Envelope struct {
	Command    string         `json:"command,omitempty"`
	OK         bool           `json:"ok"`
	Data       any            `json:"data,omitempty"`
	Job        any            `json:"job,omitempty"`
	Error      *EnvelopeError `json:"error,omitempty"`
	ConfigPath string         `json:"config_path,omitempty"`
}

type EnvelopeError struct {
	Message    string `json:"message"`
	StatusCode int    `json:"status_code,omitempty"`
	Payload    any    `json:"payload,omitempty"`
}

func Emit(w io.Writer, envelope Envelope, format string) error {
	if format == "text" {
		return emitText(w, envelope)
	}
	encoder := json.NewEncoder(w)
	encoder.SetIndent("", "  ")
	return encoder.Encode(envelope)
}

func Normalize(value any) (any, error) {
	if value == nil {
		return nil, nil
	}
	payload, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	var normalized any
	if err := json.Unmarshal(payload, &normalized); err != nil {
		return string(payload), nil
	}
	return normalized, nil
}

func emitText(w io.Writer, envelope Envelope) error {
	if _, err := fmt.Fprintf(w, "command: %s\n", envelope.Command); err != nil {
		return err
	}
	if _, err := fmt.Fprintf(w, "ok: %t\n", envelope.OK); err != nil {
		return err
	}
	if envelope.Data != nil {
		if err := writeJSONBlock(w, envelope.Data); err != nil {
			return err
		}
	}
	if envelope.Job != nil {
		if _, err := io.WriteString(w, "job:\n"); err != nil {
			return err
		}
		if err := writeJSONBlock(w, envelope.Job); err != nil {
			return err
		}
	}
	if envelope.Error != nil {
		if _, err := io.WriteString(w, "error:\n"); err != nil {
			return err
		}
		if err := writeJSONBlock(w, envelope.Error); err != nil {
			return err
		}
	}
	return nil
}

func writeJSONBlock(w io.Writer, value any) error {
	payload, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	if _, err := fmt.Fprintln(w, string(payload)); err != nil {
		return err
	}
	return nil
}
