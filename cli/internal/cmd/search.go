package cmd

import (
	"context"

	"github.com/spf13/cobra"

	"github.com/willem/news_app/cli/internal/api"
	"github.com/willem/news_app/cli/internal/runtime"
)

func (a *App) newSearchCommand() *cobra.Command {
	var args struct {
		Limit           int
		IncludePodcasts bool
	}

	command := &cobra.Command{
		Use:   "search <query>",
		Short: "Search provider-backed sources",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, positional []string) error {
			request := &api.AgentSearchRequest{Query: positional[0]}
			request.Limit.SetTo(args.Limit)
			request.IncludePodcasts.SetTo(args.IncludePodcasts)

			return a.runRemote(cmd, "search", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				data, err := client.SearchAgent(ctx, request)
				if err != nil {
					return commandResult{}, err
				}
				return commandResult{Data: data}, nil
			})
		},
	}
	command.Flags().IntVar(&args.Limit, "limit", 10, "Max results to return")
	command.Flags().BoolVar(&args.IncludePodcasts, "include-podcasts", true, "Include podcast results")
	return command
}
