package cmd

import (
	"context"
	"fmt"
	"slices"
	"strings"

	"github.com/spf13/cobra"

	"github.com/willem/newsbuddy/cli/internal/api"
	"github.com/willem/newsbuddy/cli/internal/runtime"
)

var supportedFeedTypes = []string{"atom", "substack", "podcast_rss"}

func (a *App) newSourcesCommand() *cobra.Command {
	sourcesCmd := &cobra.Command{
		Use:   "sources",
		Short: "Manage runtime feed subscriptions",
	}

	var listType string
	listCmd := &cobra.Command{
		Use:   "list",
		Short: "List configured sources",
		RunE: func(cmd *cobra.Command, _ []string) error {
			return a.runRemote(cmd, "sources.list", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				params := api.ListScraperConfigsParams{}
				if listType != "" {
					params.Type = api.Ptr(listType)
				}
				data, err := client.ListSources(ctx, params)
				if err != nil {
					return commandResult{}, err
				}
				return commandResult{Data: data}, nil
			})
		},
	}
	listCmd.Flags().StringVar(&listType, "type", "", "Filter by source type")

	var addArgs struct {
		FeedType    string
		DisplayName string
	}
	addCmd := &cobra.Command{
		Use:   "add <feed-url>",
		Short: "Subscribe to a feed",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, positional []string) error {
			feedURL, err := runtime.ParseURL(positional[0])
			if err != nil {
				return a.renderError("sources.add", err)
			}
			if !slices.Contains(supportedFeedTypes, addArgs.FeedType) {
				return a.renderError(
					"sources.add",
					fmt.Errorf(
						"unsupported feed type %q; expected one of: %s",
						addArgs.FeedType,
						strings.Join(supportedFeedTypes, ", "),
					),
				)
			}
			request := &api.SubscribeToFeedRequest{
				FeedURL:  feedURL.String(),
				FeedType: addArgs.FeedType,
			}
			if addArgs.DisplayName != "" {
				request.DisplayName = api.Ptr(addArgs.DisplayName)
			}
			return a.runRemote(cmd, "sources.add", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				data, err := client.SubscribeSource(ctx, request)
				if err != nil {
					return commandResult{}, err
				}
				return commandResult{Data: data}, nil
			})
		},
	}
	addCmd.Flags().StringVar(
		&addArgs.FeedType,
		"feed-type",
		"",
		"Feed type to subscribe to (atom, substack, podcast_rss)",
	)
	addCmd.Flags().StringVar(&addArgs.DisplayName, "display-name", "", "Optional display name")
	_ = addCmd.MarkFlagRequired("feed-type")

	sourcesCmd.AddCommand(listCmd, addCmd)
	return sourcesCmd
}
