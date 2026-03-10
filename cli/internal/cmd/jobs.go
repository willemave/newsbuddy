package cmd

import (
	"context"
	"errors"
	"strconv"
	"time"

	"github.com/spf13/cobra"

	"github.com/willem/news_app/cli/internal/runtime"
)

func (a *App) newJobsCommand() *cobra.Command {
	jobsCmd := &cobra.Command{
		Use:   "jobs",
		Short: "Inspect async jobs",
	}

	getCmd := &cobra.Command{
		Use:   "get <job-id>",
		Short: "Fetch one async job",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			jobID, err := strconv.Atoi(args[0])
			if err != nil {
				return a.renderError("jobs.get", err)
			}
			return a.runRemote(cmd, "jobs.get", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				job, err := client.GetJob(ctx, jobID)
				if err != nil {
					return commandResult{}, err
				}
				return commandResult{Data: job}, nil
			})
		},
	}

	var waitInterval time.Duration
	var waitTimeout time.Duration
	waitCmd := &cobra.Command{
		Use:   "wait <job-id>",
		Short: "Poll a job until it reaches a terminal state",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			jobID, err := strconv.Atoi(args[0])
			if err != nil {
				return a.renderError("jobs.wait", err)
			}
			if waitInterval <= 0 {
				return a.renderError("jobs.wait", errors.New("wait-interval must be greater than zero"))
			}
			return a.runRemote(cmd, "jobs.wait", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				job, err := client.WaitForJob(ctx, jobID, runtime.WaitOptions{
					Interval: waitInterval,
					Timeout:  waitTimeout,
				})
				if err != nil {
					return commandResult{}, err
				}
				return commandResult{Data: job}, nil
			})
		},
	}
	waitCmd.Flags().DurationVar(&waitInterval, "wait-interval", 2*time.Second, "Polling interval while waiting")
	waitCmd.Flags().DurationVar(&waitTimeout, "wait-timeout", 2*time.Minute, "Maximum time to wait")

	jobsCmd.AddCommand(getCmd, waitCmd)
	return jobsCmd
}
