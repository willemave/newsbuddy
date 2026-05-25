package cmd

import (
	"errors"
	"fmt"
	"io"
	"time"

	"github.com/mdp/qrterminal/v3"
	"github.com/spf13/cobra"

	"github.com/willem/newsbuddy/cli/internal/config"
	"github.com/willem/newsbuddy/cli/internal/runtime"
)

func (a *App) newAuthCommand() *cobra.Command {
	authCmd := &cobra.Command{
		Use:   "auth",
		Short: "Authenticate and link the CLI to a Newsbuddy account",
	}

	var args struct {
		DeviceName string
		Wait       waitFlags
	}

	loginCmd := &cobra.Command{
		Use:   "login",
		Short: "Start QR login and persist the linked API key",
		RunE: func(cmd *cobra.Command, _ []string) error {
			runtimeCfg, err := a.resolveRuntimeConfig()
			if err != nil {
				return a.renderError("auth.login", err)
			}
			if err := runtimeCfg.ValidateServerOnly(); err != nil {
				return a.renderErrorWithPath("auth.login", runtimeCfg.Path, err)
			}

			client, err := a.newRuntimeClient(runtimeCfg)
			if err != nil {
				return a.renderErrorWithPath("auth.login", runtimeCfg.Path, err)
			}

			deviceName := args.DeviceName
			if deviceName == "" {
				deviceName = runtime.DefaultDeviceName()
			}

			started, err := client.StartCLILink(cmd.Context(), deviceName)
			if err != nil {
				return a.renderErrorWithPath("auth.login", runtimeCfg.Path, err)
			}

			renderCLILinkQRCode(a.stderr, started.ApproveURL)

			wait, err := waitOptions(args.Wait)
			if err != nil {
				return a.renderError("auth.login", errors.New("poll-interval must be greater than zero"))
			}
			polled, err := client.WaitForCLILink(cmd.Context(), started.SessionID, started.PollToken, wait)
			if err != nil {
				return a.renderErrorWithPath("auth.login", runtimeCfg.Path, err)
			}
			if polled.APIKey == "" {
				return a.renderError("auth.login", errors.New("CLI link completed without an API key"))
			}

			savedCfg, err := config.Update(runtimeCfg.Path, func(current config.FileConfig) config.FileConfig {
				current.ServerURL = runtimeCfg.ServerURL
				current.APIKey = polled.APIKey
				if current.LibraryRoot == "" {
					current.LibraryRoot = runtimeCfg.LibraryRoot
				}
				return current
			})
			if err != nil {
				return a.renderErrorWithPath("auth.login", runtimeCfg.Path, err)
			}

			return a.renderSuccess("auth.login", commandResult{
				Data: map[string]any{
					"config_path":  runtimeCfg.Path,
					"server_url":   savedCfg.ServerURL,
					"api_key_set":  savedCfg.APIKey != "",
					"key_prefix":   polled.KeyPrefix,
					"library_root": savedCfg.LibraryRoot,
				},
			})
		},
	}

	loginCmd.Flags().StringVar(&args.DeviceName, "device-name", "", "Optional display name for this CLI device")
	loginCmd.Flags().DurationVar(&args.Wait.Interval, "poll-interval", 2*time.Second, "Polling interval while waiting for approval")
	loginCmd.Flags().DurationVar(&args.Wait.Timeout, "poll-timeout", 2*time.Minute, "Maximum time to wait for approval")

	authCmd.AddCommand(loginCmd)
	return authCmd
}

func renderCLILinkQRCode(w io.Writer, approveURL string) {
	fmt.Fprintln(w, "Scan this QR code in the Newsbuddy app to approve CLI access:")
	qrterminal.GenerateHalfBlock(approveURL, qrterminal.M, w)
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Approval link:")
	fmt.Fprintln(w, approveURL)
	fmt.Fprintln(w)
}
