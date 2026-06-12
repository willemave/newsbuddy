package cmd

import (
	"context"

	"github.com/spf13/cobra"

	"github.com/willem/newsbuddy/cli/internal/api"
	"github.com/willem/newsbuddy/cli/internal/runtime"
)

func (a *App) newNewsCommand() *cobra.Command {
	newsCmd := &cobra.Command{
		Use:   "news",
		Short: "List, inspect, and convert visible news items",
	}

	var listArgs struct {
		Limit      int
		Cursor     string
		ReadFilter string
	}
	listCmd := &cobra.Command{
		Use:   "list",
		Short: "List visible news items",
		RunE: func(cmd *cobra.Command, _ []string) error {
			return a.runRemote(cmd, "news.list", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				params := api.ListNewsItemsParams{
					Limit:      api.Ptr(listArgs.Limit),
					ReadFilter: api.Ptr(listArgs.ReadFilter),
				}
				if listArgs.Cursor != "" {
					params.Cursor = api.Ptr(listArgs.Cursor)
				}
				data, err := client.ListNewsItems(ctx, params)
				if err != nil {
					return commandResult{}, err
				}
				return commandResult{Data: data}, nil
			})
		},
	}
	listCmd.Flags().IntVar(&listArgs.Limit, "limit", 25, "Max items to return")
	listCmd.Flags().StringVar(&listArgs.Cursor, "cursor", "", "Pagination cursor")
	listCmd.Flags().StringVar(&listArgs.ReadFilter, "read-filter", "unread", "Read filter: unread, read, or all")

	getCmd := &cobra.Command{
		Use:   "get <news-item-id>",
		Short: "Fetch one news item",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			newsItemID, err := a.parseIntArg("news.get", args[0])
			if err != nil {
				return err
			}
			return a.runRemote(cmd, "news.get", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				data, err := client.GetNewsItem(ctx, newsItemID)
				if err != nil {
					return commandResult{}, err
				}
				return commandResult{Data: data}, nil
			})
		},
	}

	convertCmd := &cobra.Command{
		Use:   "convert <news-item-id>",
		Short: "Convert one news item into an article",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			newsItemID, err := a.parseIntArg("news.convert", args[0])
			if err != nil {
				return err
			}
			return a.runRemote(cmd, "news.convert", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				data, err := client.ConvertNewsItemToArticle(ctx, newsItemID)
				if err != nil {
					return commandResult{}, err
				}
				return commandResult{Data: data}, nil
			})
		},
	}

	markReadCmd := &cobra.Command{
		Use:   "mark-read <news-item-id>...",
		Short: "Mark visible news items as read",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			newsItemIDs := make([]int, 0, len(args))
			for _, arg := range args {
				newsItemID, err := a.parseIntArg("news.mark-read", arg)
				if err != nil {
					return err
				}
				newsItemIDs = append(newsItemIDs, newsItemID)
			}
			return a.runRemote(cmd, "news.mark-read", func(ctx context.Context, client *runtime.Client) (commandResult, error) {
				data, err := client.MarkNewsItemsRead(ctx, newsItemIDs)
				if err != nil {
					return commandResult{}, err
				}
				return commandResult{Data: data}, nil
			})
		},
	}

	newsCmd.AddCommand(listCmd, getCmd, convertCmd, markReadCmd)
	return newsCmd
}
