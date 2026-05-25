package cmd

import (
	"context"
	"errors"
	"fmt"
	"io"
	"strconv"
	"time"

	"github.com/spf13/cobra"

	"github.com/willem/newsbuddy/cli/internal/config"
	"github.com/willem/newsbuddy/cli/internal/output"
	"github.com/willem/newsbuddy/cli/internal/runtime"
)

type App struct {
	rootCmd *cobra.Command
	stdout  io.Writer
	stderr  io.Writer
	version string
	opts    rootOptions
}

type rootOptions struct {
	ConfigPath string
	ServerURL  string
	APIKey     string
	Output     string
	Timeout    time.Duration
}

type waitFlags struct {
	Wait     bool
	Interval time.Duration
	Timeout  time.Duration
}

type commandResult struct {
	Data any
	Job  any
}

type exitError struct {
	Code int
}

func (e *exitError) Error() string {
	return fmt.Sprintf("%d", e.Code)
}

func New(version string, stdout io.Writer, stderr io.Writer) *App {
	app := &App{
		stdout:  stdout,
		stderr:  stderr,
		version: version,
		opts: rootOptions{
			Output:  "json",
			Timeout: 30 * time.Second,
		},
	}

	rootCmd := &cobra.Command{
		Use:           "newsbuddy",
		Short:         "Newsbuddy API client",
		SilenceUsage:  true,
		SilenceErrors: true,
	}
	rootCmd.PersistentFlags().StringVar(&app.opts.ConfigPath, "config", "", "Override the CLI config path")
	rootCmd.PersistentFlags().StringVar(&app.opts.ServerURL, "server", "", "Override the Newsly server URL")
	rootCmd.PersistentFlags().StringVar(&app.opts.APIKey, "api-key", "", "Override the API key for this command")
	rootCmd.PersistentFlags().StringVar(&app.opts.Output, "output", "json", "Output format: json or text")
	rootCmd.PersistentFlags().DurationVar(&app.opts.Timeout, "timeout", 30*time.Second, "HTTP timeout")

	rootCmd.AddCommand(
		app.newConfigCommand(),
		app.newAuthCommand(),
		app.newJobsCommand(),
		app.newContentCommand(),
		app.newLibraryCommand(),
		app.newSearchCommand(),
		app.newSourcesCommand(),
		app.newOnboardingCommand(),
		app.newNewsCommand(),
		app.newCompletionCommand(rootCmd),
		app.newVersionCommand(),
	)
	app.rootCmd = rootCmd
	return app
}

func (a *App) Execute(ctx context.Context, args []string) int {
	a.rootCmd.SetArgs(args)
	if err := a.rootCmd.ExecuteContext(ctx); err != nil {
		var exit *exitError
		if errors.As(err, &exit) {
			return exit.Code
		}
		fmt.Fprintln(a.stderr, err)
		return 1
	}
	return 0
}

func (a *App) parseIntArg(commandName string, raw string) (int, error) {
	value, err := strconv.Atoi(raw)
	if err != nil {
		return 0, a.renderError(commandName, err)
	}
	return value, nil
}

func (a *App) resolveRuntimeConfig() (config.RuntimeConfig, error) {
	return config.ResolveRuntime(a.opts.ConfigPath, a.opts.ServerURL, a.opts.APIKey)
}

func (a *App) newRuntimeClient(runtimeCfg config.RuntimeConfig) (*runtime.Client, error) {
	return runtime.NewClient(runtimeCfg, a.opts.Timeout)
}

func (a *App) updateConfig(
	cmd *cobra.Command,
	commandName string,
	update func(config.FileConfig) config.FileConfig,
	data func(path string, cfg config.FileConfig) any,
) error {
	return a.runLocal(cmd, commandName, func(_ context.Context) (commandResult, error) {
		path := config.ResolvePath(a.opts.ConfigPath)
		cfg, err := config.Update(path, update)
		if err != nil {
			return commandResult{}, err
		}
		return commandResult{
			Data: data(path, cfg),
		}, nil
	})
}

func (a *App) runLocal(cmd *cobra.Command, commandName string, fn func(context.Context) (commandResult, error)) error {
	result, err := fn(cmd.Context())
	if err != nil {
		return a.renderError(commandName, err)
	}
	return a.renderSuccess(commandName, result)
}

func (a *App) runRemote(cmd *cobra.Command, commandName string, fn func(context.Context, *runtime.Client) (commandResult, error)) error {
	runtimeCfg, err := a.resolveRuntimeConfig()
	if err != nil {
		return a.renderError(commandName, err)
	}
	if err := runtimeCfg.ValidateRemote(); err != nil {
		return a.renderErrorWithPath(commandName, runtimeCfg.Path, err)
	}

	client, err := a.newRuntimeClient(runtimeCfg)
	if err != nil {
		return a.renderErrorWithPath(commandName, runtimeCfg.Path, err)
	}

	result, err := fn(cmd.Context(), client)
	if err != nil {
		return a.renderErrorWithPath(commandName, runtimeCfg.Path, err)
	}
	return a.renderSuccess(commandName, result)
}

func (a *App) renderSuccess(commandName string, result commandResult) error {
	data, err := output.Normalize(result.Data)
	if err != nil {
		return err
	}
	job, err := output.Normalize(result.Job)
	if err != nil {
		return err
	}
	return output.Emit(a.stdout, output.Envelope{
		Command: commandName,
		OK:      true,
		Data:    data,
		Job:     job,
	}, a.opts.Output)
}

func (a *App) renderError(commandName string, err error) error {
	path := config.ResolvePath(a.opts.ConfigPath)
	return a.renderErrorWithPath(commandName, path, err)
}

func (a *App) renderErrorWithPath(commandName string, path string, err error) error {
	envelopeError := &output.EnvelopeError{Message: err.Error()}
	if apiErr, ok := err.(*runtime.APIError); ok {
		envelopeError.StatusCode = apiErr.StatusCode
		envelopeError.Payload = apiErr.Payload
	}
	if emitErr := output.Emit(a.stdout, output.Envelope{
		Command:    commandName,
		OK:         false,
		Error:      envelopeError,
		ConfigPath: path,
	}, a.opts.Output); emitErr != nil {
		return emitErr
	}
	return &exitError{Code: 1}
}

func (a *App) addWaitFlags(command *cobra.Command, flags *waitFlags) {
	command.Flags().BoolVar(&flags.Wait, "wait", false, "Wait for the async operation to finish")
	command.Flags().DurationVar(&flags.Interval, "wait-interval", 2*time.Second, "Polling interval while waiting")
	command.Flags().DurationVar(&flags.Timeout, "wait-timeout", 2*time.Minute, "Maximum time to wait")
}

func waitOptions(flags waitFlags) (runtime.WaitOptions, error) {
	if flags.Interval <= 0 {
		return runtime.WaitOptions{}, errors.New("wait-interval must be greater than zero")
	}
	return runtime.WaitOptions{
		Interval: flags.Interval,
		Timeout:  flags.Timeout,
	}, nil
}

func optionalWaitOptions(flags waitFlags) (runtime.WaitOptions, bool, error) {
	if !flags.Wait {
		return runtime.WaitOptions{}, false, nil
	}
	options, err := waitOptions(flags)
	if err != nil {
		return runtime.WaitOptions{}, false, err
	}
	return options, true, nil
}
