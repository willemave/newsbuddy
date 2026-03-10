package cmd

import (
	"context"
	"errors"
	"strconv"

	"github.com/spf13/cobra"

	"github.com/willem/news_app/cli/internal/api"
	"github.com/willem/news_app/cli/internal/runtime"
)

func (a *App) newOnboardingCommand() *cobra.Command {
	onboardingCmd := &cobra.Command{
		Use:   "onboarding",
		Short: "Run simplified onboarding flows",
	}

	var startArgs struct {
		Brief     string
		SeedURLs  []string
		SeedFeeds []string
		Wait      waitFlags
	}
	startCmd := &cobra.Command{
		Use:     "start",
		Aliases: []string{"run"},
		Short:   "Start onboarding discovery",
		RunE: func(cmd *cobra.Command, _ []string) error {
			if startArgs.Wait.Wait && startArgs.Wait.Interval <= 0 {
				return a.renderError("onboarding.start", errors.New("wait-interval must be greater than zero"))
			}
			request := &api.AgentOnboardingStartRequest{Brief: startArgs.Brief}
			request.SeedUrls = startArgs.SeedURLs
			request.SeedFeeds = startArgs.SeedFeeds

			return a.runRemote(cmd, "onboarding.start", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				data, err := client.StartOnboarding(ctx, request)
				if err != nil {
					return commandResult{}, err
				}
				result := commandResult{Data: data}
				if startArgs.Wait.Wait {
					run, err := client.WaitForOnboarding(ctx, data.RunID, runtime.WaitOptions{
						Interval: startArgs.Wait.Interval,
						Timeout:  startArgs.Wait.Timeout,
					})
					if err != nil {
						return commandResult{}, err
					}
					result.Job = run
				}
				return result, nil
			})
		},
	}
	startCmd.Flags().StringVar(&startArgs.Brief, "brief", "", "Brief description of what the user wants in their feed")
	startCmd.Flags().StringSliceVar(&startArgs.SeedURLs, "seed-url", nil, "Optional seed URLs")
	startCmd.Flags().StringSliceVar(&startArgs.SeedFeeds, "seed-feed", nil, "Optional seed feeds")
	_ = startCmd.MarkFlagRequired("brief")
	a.addWaitFlags(startCmd, &startArgs.Wait)

	statusCmd := &cobra.Command{
		Use:   "status <run-id>",
		Short: "Fetch onboarding run status",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			runID, err := strconv.Atoi(args[0])
			if err != nil {
				return a.renderError("onboarding.status", err)
			}
			return a.runRemote(cmd, "onboarding.status", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				data, err := client.GetOnboarding(ctx, runID)
				if err != nil {
					return commandResult{}, err
				}
				return commandResult{Data: data}, nil
			})
		},
	}

	var completeArgs struct {
		AcceptAll  bool
		SourceIDs  []int
		Subreddits []string
	}
	completeCmd := &cobra.Command{
		Use:   "complete <run-id>",
		Short: "Complete onboarding selections",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			runID, err := strconv.Atoi(args[0])
			if err != nil {
				return a.renderError("onboarding.complete", err)
			}
			request := &api.AgentOnboardingCompleteRequest{}
			request.AcceptAll.SetTo(completeArgs.AcceptAll)
			request.SourceIds = completeArgs.SourceIDs
			request.SelectedSubreddits = completeArgs.Subreddits

			return a.runRemote(cmd, "onboarding.complete", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				data, err := client.CompleteOnboarding(ctx, runID, request)
				if err != nil {
					return commandResult{}, err
				}
				return commandResult{Data: data}, nil
			})
		},
	}
	completeCmd.Flags().BoolVar(&completeArgs.AcceptAll, "accept-all", false, "Accept all suggested sources")
	completeCmd.Flags().IntSliceVar(&completeArgs.SourceIDs, "source-id", nil, "Suggested source IDs to accept")
	completeCmd.Flags().StringSliceVar(&completeArgs.Subreddits, "subreddit", nil, "Subreddits to subscribe to")

	onboardingCmd.AddCommand(startCmd, statusCmd, completeCmd)
	return onboardingCmd
}
